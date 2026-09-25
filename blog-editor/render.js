/* 用博客自己装的 marked 渲染 Markdown，保证预览和 hexo g 结果一致。
   stdin -> stdout，出错时非 0 退出，让 Python 端退回内置渲染。 */
const path = require('path');

let marked = null;
const candidates = [
  path.join(__dirname, '..', 'node_modules', 'marked'),
  'marked',
];
for (const c of candidates) {
  try {
    marked = require(c);
    if (marked && (marked.parse || marked.marked)) break;
  } catch (e) { /* 试下一个 */ }
}

if (!marked) {
  process.exit(2);
}
const parse = marked.parse || (marked.marked && marked.marked.parse);
if (typeof parse !== 'function') {
  process.exit(3);
}

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { buf += d; });
process.stdin.on('end', () => {
  try {
    process.stdout.write(parse(buf, { headerIds: false, mangle: false }));
  } catch (e) {
    process.exit(4);
  }
});
