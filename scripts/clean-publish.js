/*!
 * 生成后清理：把不该发布的东西从 public/ 里删掉。
 *
 * 背景：Hexo 会把主题 source/ 下的所有文件一并拷进 public/，
 * hexo-theme-cola 自带了两个 mp3（kabuda.mp3 是有版权的特摄原声，
 * 八连杀.mp3 是低俗网络歌曲），它们会被一起发布到 GitHub Pages。
 * 之前用 git filter-branch 清过一次历史，但只要重新 hexo g 就会再生成。
 *
 * ⚠️ 时机：不能挂在 after_generate —— 实测那个阶段 public/ 还没落盘
 *    （DIAG 显示 exists=false），删了等于没删。这里挂在进程退出时，
 *    此时文件才真正写完。
 *
 * ⚠️ 用黑名单而不是「删掉整个 music/」：博客自己的歌就放在
 *    themes/cola/source/music/ 下（网络直链会过期或者被混合内容拦，
 *    详见 theme 配置里的说明），不能一起删掉。
 */
const fs = require('fs');
const path = require('path');

// 只删这些明确的文件（public/ 下的相对路径）
const BLOCKED_FILES = [
  'music/kabuda.mp3',
  'music/八连杀.mp3',
];

// 按扩展名兜底：这些后缀一律不发布
const BLOCKED_EXT = ['.flac', '.wav', '.m4a', '.ape', '.wma'];

function rm(p) {
  if (!fs.existsSync(p)) return false;
  if (fs.statSync(p).isDirectory()) {
    fs.rmSync(p, { recursive: true, force: true });
  } else {
    fs.unlinkSync(p);
  }
  return true;
}

function walk(dir) {
  let out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out = out.concat(walk(full));
    else if (BLOCKED_EXT.includes(path.extname(e.name).toLowerCase())) out.push(full);
  }
  return out;
}

hexo.on('exit', function () {
  const pub = hexo.public_dir;
  let removed = 0;
  try {
    if (!fs.existsSync(pub)) return;

    for (const rel of BLOCKED_FILES) {
      if (rm(path.join(pub, rel))) {
        hexo.log.info('清理发布目录: %s', rel);
        removed++;
      }
    }

    // 兜底：扫一遍 public 里不该发布的其它音频格式
    for (const f of walk(pub)) {
      hexo.log.info('清理发布目录: %s', path.relative(pub, f).replace(/\\/g, '/'));
      rm(f);
      removed++;
    }
  } catch (e) {
    hexo.log.error('发布目录清理失败: %s', e.message);
    return;
  }

  if (removed) hexo.log.info('发布目录清理完成，共移除 %d 项', removed);
});
