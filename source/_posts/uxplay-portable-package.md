---
title: 把 UxPlay 做成便携版：一次完整的依赖闭包打包
cover: imgs/covers/cover2.jpg
date: 2026-09-25 11:40:00
tags:
  - Windows
  - 打包
  - GStreamer
  - 依赖分析
categories:
  - 踩坑记录
---

想把自己编译的 `uxplay.exe` 拷到 U 盘，这样换台电脑也能用。

直接拷过去的结果是**一堆「找不到 xxx.dll」**。这篇文章记录了怎么把依赖一条条找全，最后做成真正能跑的便携版。

<!-- more -->

## 问题：一个 693 KB 的 exe，拷过去不能用

`uxplay.exe` 本身只有 693 KB。听起来很轻量，但它是用 MSYS2/MinGW 编译的，依赖全部在 `C:\msys64\ucrt64\bin` 里。

先看它直接依赖什么：

```bash
objdump -p uxplay.exe | grep "DLL Name:"
```

```
ADVAPI32.dll          ← 系统
KERNEL32.dll          ← 系统
libgcc_s_seh-1.dll    ← MinGW 运行库
libwinpthread-1.dll   ← MinGW 线程库
libstdc++-6.dll       ← C++ 标准库
libcrypto-3-x64.dll   ← OpenSSL
libglib-2.0-0.dll     ← GLib
libgobject-2.0-0.dll
libgstreamer-1.0-0.dll  ← GStreamer 核心
libgstapp-1.0-0.dll
libgstvideo-1.0-0.dll
libplist-2.0.dll      ← Apple plist 解析
...
```

**27 个直接依赖**，而且它们还会继续拉依赖。

## 第一层：递归解析依赖闭包

思路很简单：把 exe 放进去，然后对每个新加进来的 DLL 再跑一遍 `objdump`，直到没有新的为止。

```python
def resolve_closure(roots, bin_dir):
    found = {}
    queue = list(roots)
    seen = set()

    while queue:
        cur = queue.pop()
        if cur in seen or not cur.is_file():
            continue
        seen.add(cur)

        for name in deps_of(cur):          # objdump -p
            key = name.lower()
            if is_system_dll(name) or key in found:
                continue                    # 系统 DLL 不拷
            candidate = bin_dir / name
            if candidate.is_file():
                found[key] = candidate
                queue.append(candidate)     # 新 DLL 继续解析
    return found
```

关键是**要区分系统 DLL 和自己带的 DLL**。系统 DLL 绝对不能拷——必须用目标机器自己的，否则会出各种诡异问题。

我用前缀白名单来排除：

```python
SYS_RE = re.compile(
    r"^(api-ms-win-|ext-ms-|kernel32|user32|advapi32|ws2_32|"
    r"ntdll|gdi32|shell32|ole32|crypt32|...|d3d12|dxgi)\.dll$",
    re.IGNORECASE)
```

`api-ms-win-*` 这些是 **Windows 的 API Set**，它们**并不真实存在于 System32**，由加载器虚拟解析。所以不用管它们，任何 Win10+ 都有。

这一步跑完：**17 个 DLL**。

## 第二层：插件是运行时加载的，objdump 根本看不到

这是最容易漏的地方。

GStreamer 的架构是**插件式**的：核心库在运行时扫描插件目录，动态 `LoadLibrary`。

**这意味着 `objdump` 分析 uxplay.exe 时，一个插件都看不到。** 但缺了插件，程序能启动、能连上，就是**出不了画面**。

所以插件目录必须整体拷贝：

```
C:\msys64\ucrt64\lib\gstreamer-1.0\   →   240 个插件 DLL
```

而且插件**自己也有依赖**。把插件也纳入依赖分析后，又补了一大堆：

```
round 1: added 117
round 2: added 52
round 3: added 20
round 4: added 0        ← 收敛
```

**一共补了 189 个 DLL。**

> 迭代到「没有新增」为止很重要。因为新拷进来的 DLL 可能又依赖别的 DLL，一轮是不行的。

## 最终结构

```
uxplay-portable/
├── start_uxplay.bat      启动脚本
├── uxplay.exe            693 KB
├── bin/                  依赖 DLL       ≈203 MB
├── lib/gstreamer-1.0/    240 个插件      ≈25 MB
├── share/                GLib schema
└── gst-registry.bin      插件缓存（首次运行自动重建）
```

总计 **230 MB / 490 个文件**。

## 启动脚本：不能依赖盘符

U 盘在不同电脑上可能是 E:，也可能是 F:、G:。所以脚本必须用 `%~dp0` 自定位：

```bat
set "ROOT=%~dp0"
set "PATH=%ROOT%bin;%PATH%"
set "GST_PLUGIN_PATH=%ROOT%lib\gstreamer-1.0"
set "GST_PLUGIN_SYSTEM_PATH="
set "GST_REGISTRY=%ROOT%gst-registry.bin"

"%ROOT%uxplay.exe" -n "AirPlay" -nh -vs d3d12videosink -as wasapi2sink
```

几个要点：

- **`GST_PLUGIN_PATH`** 指向自带插件目录
- **`GST_PLUGIN_SYSTEM_PATH` 要清空**，否则会去扫目标机器上的 GStreamer，可能版本冲突
- **`GST_REGISTRY`** 放在 U 盘上，否则每次换机器都要重建缓存

## 验证：模拟一台干净电脑

光在本机测试没意义——本机的 MSYS2 在 PATH 里，DLL 怎么都能找到。

所以测试时**把 MSYS2 完全从 PATH 里移除**，只保留 U 盘目录和系统目录：

```powershell
$psi.EnvironmentVariables["PATH"] = "$root\bin;C:\Windows\System32;C:\Windows"
```

结果：

| 检查 | 结果 |
|---|---|
| UxPlay 启动 | ✅ 无报错 |
| GStreamer 插件 | ✅ 注册 **1270 个元素** |
| 关键组件 | ✅ `d3d12videosink`、`wasapi2sink`、`avdec_h264` 都在 |
| 实际播放音频 | ✅ exit code 0 |

## 附带发现：exFAT 不支持硬链接

写文档到 U 盘时报了个奇怪的错：

```
EISDIR: illegal operation on a directory, link '...tmpdir\file.tmp' -> 'file'
```

原因是**现代编辑器保存文件用的是「原子保存」**：先写临时文件，再 `rename` 覆盖。

而 **exFAT 文件系统不支持硬链接**，`rename` 在目标已存在时会失败。

**不影响程序运行**，只是往 U 盘写文本文件时要注意——先写到本地硬盘再拷过去就行。

## 用到的两个脚本

```python
# 1. 解析直接依赖 + 拷贝 exe 和插件
python build_portable_uxplay.py E:\uxplay-portable

# 2. 补齐插件的传递依赖（迭代到收敛）
python complete_deps.py E:\uxplay-portable
```

## 小结

打包一个「看似只有一个 exe」的程序，要注意：

1. **用 `objdump` 递归解析闭包**，别只看第一层
2. **插件是运行时加载的**，静态分析看不到——必须整体拷贝并单独解析它们的依赖
3. **迭代到收敛**，新加的 DLL 会带来新依赖
4. **验证时要模拟干净环境**，把开发环境的 PATH 去掉，否则测了个寂寞
5. 系统 DLL 绝不能拷，`api-ms-win-*` 是虚拟的，不用管

这套方法对所有 MinGW 编译的程序都通用——GIMP、Inkscape 之类的便携版也是这个思路。
