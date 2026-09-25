# wenjvQAQ 的技术笔记

个人博客源码。用的是 [Hexo](https://hexo.io/) + [hexo-theme-cola](https://github.com/Aizener/hexo-theme-cola)。

线上地址：**https://wenjvQAQ.github.io/**
（`main` 分支放生成好的静态页面，这个 `source` 分支放源码）

## 目录结构

```
├─ source/
│  ├─ _posts/           文章（Markdown）
│  ├─ about/            关于自己
│  ├─ log/  link/  tools/  categories/  tags/
├─ themes/cola/         主题（含我自己改的部分）
├─ scripts/
│  └─ clean-publish.js  构建后清理，防止主题自带的音频被发布
├─ blog-editor/         博客便捷编辑工具（本地网页应用）
├─ _config.yml          Hexo 配置
└─ .gitignore
```

`node_modules/`、`public/`、`.deploy_git/`、`db.json`、`_theme_backup/` 都不进仓库，
它们要么能重新装、要么能重新生成。

## 本地跑起来

```bash
npm install            # 装依赖（国内建议加 --registry=https://registry.npmmirror.com）
npx hexo server -p 4000   # 本地预览 http://localhost:4000
```

发布：

```bash
npx hexo clean && npx hexo generate && npx hexo deploy
```

用 SSH 推送（本机 git-over-HTTPS 会被重置，所以 `_config.yml` 里 `repo` 写的是
`git@github.com:...`）。

## 博客编辑工具

不想开命令行的时候用它。双击 `blog-editor/启动.bat`，浏览器自动打开
http://127.0.0.1:4100/，可以：

- 左侧选文章 / 搜索 / 新建
- 改 front-matter（标题、日期、标签、分类、封面）
- 左写 Markdown、右实时预览（用博客自己的 marked 渲染，和 `hexo g` 结果一致）
- 一键「保存」「生成」「一键发布」，带实时日志
- 删除文章

纯 Python 标准库实现，不需要额外装包。

## 主题上做过的修改

主题是从原仓库拿的（当时 git clone 连不上，用的 codeload tarball），
在它基础上改了几处：

| 位置 | 改动 |
|---|---|
| `layout/_partial/head.ejs` | 标题里写死的主题作者字样改成读 `config.title`；Valine 脚本改成按需加载 |
| `layout/_partial/main-left.ejs` | 修了头像 `class` 属性被多一个引号吞掉的 bug；统计块加 `show_statics` 开关 |
| `layout/_partial/main-right.ejs` | 主题作者失效的默认音乐播放器改成按配置渲染；备案号按配置显示 |
| `layout/_partial/about.ejs` | `likes` 支持 `[[文字](链接)]` 语法，且只放行 http/https |
| `layout/_partial/index.ejs` | 文章卡片加 `background-image` 占位，图片没加载出来时也是糊的封面而不是白块 |
| `layout/_partial/tools.ejs` | 封面留空时回退到主题默认图 |
| `source/css/layout.styl` | 顶部栏毛玻璃（两层都做模糊，避免穿帮）；`statics` 的 `.none` 样式 |
| `source/css/partial/index.styl` | 卡片封面柔焦 + 标题条毛玻璃 |
| `_config.yml` | 全部配置写在这里 |

> ⚠️ 主题配置**必须**改 `themes/cola/_config.yml`，不能建 `_config.cola.yml` 覆盖。
> 这个主题的配置项大多是「数组的数组」，Hexo 合并时会对嵌套数组做逐元素合并，
> 结果是内容错乱（`likes` 会变成主题作者的资料混进来）。

## 两个踩过的坑

**1. 主题自带的音频会被发布出去**

Hexo 会把主题 `source/` 下的所有文件一并拷进 `public/`。cola 主题自带
`source/music/kabuda.mp3` 和 `八连杀.mp3`，会一起发布到 GitHub Pages。

`scripts/clean-publish.js` 在构建结束时把它们删掉。注意它挂的是 `hexo.on('exit')`
而不是 `after_generate` —— 后者执行时 `public/` 还没落盘（实测 `exists=false`），
删了等于没删。

**2. 网易云音乐播放器的链接**

不要写 `https://music.163.com/song/media/outer/url?id=xxx.mp3`。它在 curl 里能 302，
但在浏览器里会 `MEDIA_ELEMENT_ERROR: Format error` ——302 的 `Location` 是
`http://mXXX.music.126.net/...`（明文），https 页面下会被按混合内容处理。

要用 CDN 直链，并把协议改成 `https`。这些直链去掉 `?vuutv=` 签名参数也照样 200。
