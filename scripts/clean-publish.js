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
 * 想恢复主题自带音频就删掉本文件。
 */
const fs = require('fs');
const path = require('path');

// public/ 下的相对路径黑名单
const BLOCKED = [
  'music',                       // 主题自带的 mp3 目录，整个不要
];

// 额外按扩展名兜底拦截（防止别处又冒出音频）
const BLOCKED_EXT = ['.mp3', '.flac', '.wav', '.m4a', '.ape'];

function rm(p) {
  if (!fs.existsSync(p)) return false;
  fs.rmSync(p, { recursive: true, force: true });
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

    for (const rel of BLOCKED) {
      if (rm(path.join(pub, rel))) {
        hexo.log.info('清理发布目录: %s', rel);
        removed++;
      }
    }

    // 兜底：扫一遍 public 里残留的音频文件
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
