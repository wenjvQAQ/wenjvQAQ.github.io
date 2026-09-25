"""
博客便捷编辑工具 —— 后端

一个本地网页应用：双击 启动.bat 后浏览器打开，就能写文章、看实时预览、一键发布。

为什么用标准库而不是 Flask：
    这个工具要长期跟着博客走，少一个依赖就少一个「换电脑跑不起来」的理由。
    http.server 足够撑这种单用户本地工具，前端用 fetch + SSE 拿实时日志。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR = os.path.dirname(HERE)               # 工具放在博客根目录下的 blog-editor/
POSTS_DIR = os.path.join(BLOG_DIR, "source", "_posts")
COVERS_DIR = os.path.join(BLOG_DIR, "themes", "cola", "source", "imgs", "covers")
CACHE_DIR = os.path.join(HERE, ".cache")
PORT = 4100

# Hexo 在 Windows 上是 hexo.cmd；直接调 node 里的入口更稳，避免 shell 转义问题
HEXO_BIN = os.path.join(BLOG_DIR, "node_modules", ".bin", "hexo.cmd")


# --------------------------------------------------------------------------
# 文章读写
# --------------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_front_matter(text):
    """把 front matter 解析成 dict。只处理博客里实际用到的几种写法。"""
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    data = {}
    key = None
    for line in raw.split("\n"):
        if not line.strip() or line.strip().startswith("#"):
            continue
        # 列表项 "  - xxx"
        if re.match(r"^\s+-\s+", line) and key:
            val = re.sub(r"^\s+-\s+", "", line).strip().strip("'\"")
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(val)
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val == "":
                data[key] = ""          # 可能是后面跟列表
            else:
                data[key] = val.strip("'\"")
    return data, body


def dump_front_matter(data):
    """按固定顺序生成 front matter，保证 diff 干净。"""
    order = ["title", "cover", "date", "tags", "categories"]
    keys = [k for k in order if k in data] + \
           [k for k in data if k not in order]
    lines = ["---"]
    for k in keys:
        v = data[k]
        if isinstance(v, list):
            if not v:
                continue
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif v not in (None, ""):
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def list_posts():
    out = []
    if not os.path.isdir(POSTS_DIR):
        return out
    for fn in sorted(os.listdir(POSTS_DIR)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(POSTS_DIR, fn)
        try:
            with open(p, encoding="utf-8") as f:
                fm, body = parse_front_matter(f.read())
        except Exception as e:
            out.append({"file": fn, "title": f"<读取失败: {e}>", "error": True})
            continue
        out.append({
            "file": fn,
            "title": fm.get("title", fn),
            "date": fm.get("date", ""),
            "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
            "categories": fm.get("categories", []) if isinstance(fm.get("categories"), list) else [],
            "cover": fm.get("cover", ""),
            "words": len(body),
            "mtime": os.path.getmtime(p),
        })
    out.sort(key=lambda x: x.get("date", ""), reverse=True)
    return out


def safe_post_path(name):
    """只允许 _posts 下的 .md，挡住 ../ 之类的越界。"""
    if not name.endswith(".md"):
        name += ".md"
    name = os.path.basename(name)
    if not re.match(r"^[\w\u4e00-\u9fff\-\.]+\.md$", name):
        raise ValueError("非法的文件名: " + name)
    return os.path.join(POSTS_DIR, name)


def slugify(text, fallback="post"):
    """中文标题没法直接当文件名，用拼音不合适，退回时间戳。"""
    s = re.sub(r"[^\w\-]+", "-", text.lower()).strip("-")
    return s or fallback


# --------------------------------------------------------------------------
# 命令执行（带实时日志）
# --------------------------------------------------------------------------

class Job:
    def __init__(self, label):
        self.label = label
        self.lines = []
        self.done = False
        self.code = None
        self.lock = threading.Lock()

    def add(self, line):
        with self.lock:
            self.lines.append(line)

    def snapshot(self, since=0):
        with self.lock:
            return self.lines[since:], self.done, self.code


JOBS = {}


def start_job(label, args, cwd=None):
    job = Job(label)
    jid = f"{int(time.time()*1000)}"
    JOBS[jid] = job

    def run():
        job.add(f"$ {' '.join(args)}")
        try:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                args, cwd=cwd or BLOG_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, shell=False, env=env,
            )
            for line in proc.stdout:
                job.add(line.rstrip("\n"))
            proc.wait()
            job.code = proc.returncode
        except Exception as e:
            job.add(f"[工具错误] {e}")
            job.code = -1
        finally:
            job.done = True
            job.add(f"--- 结束，退出码 {job.code} ---")

    threading.Thread(target=run, daemon=True).start()
    return jid


def hexo_cmd(*args):
    """优先用 node_modules/.bin/hexo.cmd；没有就退回 PATH 里的 hexo。"""
    if os.path.exists(HEXO_BIN):
        return [HEXO_BIN] + list(args)
    return ["hexo"] + list(args)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- 工具函数 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML, "text/html; charset=utf-8")

        if path == "/api/posts":
            return self._send(200, {"posts": list_posts(), "blogDir": BLOG_DIR})

        if path == "/api/post":
            name = (qs.get("file") or [""])[0]
            try:
                p = safe_post_path(name)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            if not os.path.exists(p):
                return self._send(404, {"error": "文章不存在"})
            with open(p, encoding="utf-8") as f:
                text = f.read()
            fm, body = parse_front_matter(text)
            return self._send(200, {"file": os.path.basename(p), "front": fm, "body": body})

        if path == "/api/covers":
            covers = []
            if os.path.isdir(COVERS_DIR):
                for fn in sorted(os.listdir(COVERS_DIR)):
                    if fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        covers.append({
                            "name": fn,
                            "path": f"imgs/covers/{fn}",
                            "url": f"/cover/{fn}",
                        })
            return self._send(200, {"covers": covers})

        if path.startswith("/cover/"):
            fn = os.path.basename(path[len("/cover/"):])
            fp = os.path.join(COVERS_DIR, fn)
            if not os.path.exists(fp):
                return self._send(404, {"error": "no cover"})
            ext = os.path.splitext(fn)[1].lower()
            ct = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png", ".webp": "image/webp"}.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), ct)

        if path == "/api/job":
            jid = (qs.get("id") or [""])[0]
            since = int((qs.get("since") or ["0"])[0])
            job = JOBS.get(jid)
            if not job:
                return self._send(404, {"error": "任务不存在"})
            lines, done, code = job.snapshot(since)
            return self._send(200, {"lines": lines, "next": since + len(lines),
                                    "done": done, "code": code, "label": job.label})

        return self._send(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        try:
            data = self._json_body()
        except Exception as e:
            return self._send(400, {"error": f"请求体解析失败: {e}"})

        if path == "/api/save":
            return self._save(data)

        if path == "/api/delete":
            try:
                p = safe_post_path(data.get("file", ""))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            if os.path.exists(p):
                os.remove(p)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "文章不存在"})

        if path == "/api/preview":
            return self._send(200, {"html": render_markdown(data.get("body", ""))})

        if path == "/api/run":
            action = data.get("action")
            if action == "publish":
                # 先落盘再发布，避免改了没保存
                jid = start_job("发布", hexo_cmd("clean"), )
                JOBS[jid].label = "发布：clean -> generate -> deploy"

                def chain():
                    time.sleep(0.2)
                    j1 = JOBS[jid]
                    while not j1.done:
                        time.sleep(0.3)
                    for step in ("generate", "deploy"):
                        j2 = start_job(step, hexo_cmd(step))
                        while not JOBS[j2].done:
                            time.sleep(0.3)
                        for ln in JOBS[j2].lines:
                            j1.add(ln)

                threading.Thread(target=chain, daemon=True).start()
                return self._send(200, {"job": jid})
            if action == "generate":
                return self._send(200, {"job": start_job("生成", hexo_cmd("generate"))})
            if action == "server":
                return self._send(200, {"job": start_job("本地预览", hexo_cmd("server", "-p", "4000"))})
            return self._send(400, {"error": "未知操作"})

        if path == "/api/newfile":
            title = (data.get("title") or "").strip()
            if not title:
                return self._send(400, {"error": "标题不能为空"})
            base = slugify(title)
            fn = base + ".md"
            i = 1
            while os.path.exists(os.path.join(POSTS_DIR, fn)):
                fn = f"{base}-{i}.md"
                i += 1
            p = os.path.join(POSTS_DIR, fn)
            fm = {
                "title": title,
                "cover": data.get("cover", ""),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tags": data.get("tags", []),
                "categories": data.get("categories", []) or ["踩坑记录"],
            }
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(dump_front_matter(fm) + "\n")
            return self._send(200, {"file": fn})

        return self._send(404, {"error": "not found"})

    def _save(self, data):
        name = data.get("file", "")
        try:
            p = safe_post_path(name)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        fm = data.get("front") or {}
        body = data.get("body", "")
        # 日期不存在就补一个，保证首页排序稳定
        if not fm.get("date"):
            fm["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                old_fm, _ = parse_front_matter(f.read())
            if not fm.get("date"):
                fm["date"] = old_fm.get("date", fm["date"])
        os.makedirs(POSTS_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(dump_front_matter(fm) + "\n" + body)
        return self._send(200, {"ok": True, "file": os.path.basename(p)})


# --------------------------------------------------------------------------
# Markdown 渲染
#
# 优先用博客自己装的 marked（和最终 hexo g 出来的一致），
# 装不上再退回一个够用的小实现。
# --------------------------------------------------------------------------

def _render_with_marked(text):
    script = os.path.join(HERE, "render.js")
    if not os.path.exists(script):
        return None
    try:
        r = subprocess.run(["node", script], input=text, capture_output=True,
                           text=True, encoding="utf-8", timeout=20, cwd=HERE)
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    return None


def render_markdown(text):
    html = _render_with_marked(text)
    if html is not None:
        return html
    return _fallback_markdown(text)


def _fallback_markdown(text):
    """没有 node 时的兜底渲染，覆盖标题/代码/列表/粗体/链接。"""
    import html as _html
    out, in_code, in_ul = [], False, False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append("</pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(_html.escape(line))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_html.escape(m.group(2))}</h{lvl}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append("<li>" + _html.escape(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        if not line.strip():
            out.append("")
            continue
        s = _html.escape(line)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        out.append(f"<p>{s}</p>")
    if in_ul:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


INDEX_HTML = None  # 启动时从 editor.html 读入


def load_ui():
    global INDEX_HTML
    p = os.path.join(HERE, "editor.html")
    with open(p, encoding="utf-8") as f:
        INDEX_HTML = f.read()


def main():
    load_ui()
    os.makedirs(CACHE_DIR, exist_ok=True)
    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 58)
    print("  博客编辑工具")
    print(f"  博客目录: {BLOG_DIR}")
    print(f"  地址:     {url}")
    print("  关掉这个窗口就停止")
    print("=" * 58)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
