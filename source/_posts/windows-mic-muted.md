---
title: 麦克风没声音：注册表和音量合成器都查不到的那个静音标志
cover: imgs/covers/cover1.jpg
date: 2026-09-25 10:30:00
tags:
  - Windows
  - 音频
  - 排查
categories:
  - 踩坑记录
---

QQ 发语音对方听不到。第一反应是 QQ 的问题，结果查下去发现**整个系统的麦克风都没在采集声音**——所有程序都一样。

顺便说一句，这个问题藏得比较深：**注册表里查不到，音量合成器里看着也正常**。

<!-- more -->

## 症状

QQ 语音没声音。但更关键的是，用任何工具录音都是**数字静音**。

## 排查过程

### 第一步：确认默认设备对不对

先看系统默认录音设备。用 GStreamer 枚举：

```bash
gst-device-monitor-1.0 Audio/Source
```

```
Default Audio Capture Device
  device.actual-name = 麦克风阵列 (英特尔智音技术)
```

**设备是对的**，不是选错了。

### 第二步：实际录一段看看

设备对不代表有信号。用 Python 录 4 秒，看振幅：

```python
import sounddevice as sd
import numpy as np

rec = sd.rec(int(4 * 16000), samplerate=16000, channels=1, dtype="int16")
sd.wait()
print("peak:", int(np.abs(rec).max()), "/ 32767")
```

结果：

```
peak : 1 / 32767
```

**peak = 1**，满量程是 32767。这就是数字静音，一点信号都没有。

### 第三步：换了所有设备试

我扫了机器上全部 9 个录音设备：

```
SILENT [0]  Microsoft 声音映射器           peak=1
SILENT [1]  麦克风阵列 (英特尔智音技术)      peak=1
SILENT [2]  麦克风阵列 (网易虚拟音频设备)    peak=1
SILENT [7]  麦克风阵列 (英特尔智音技术)      peak=1
SILENT [15] 麦克风阵列 (英特尔智音技术)@48k peak=0
...
```

**全部静音**。真实硬件和虚拟设备都一样，说明不是单个设备的问题。

### 第四步：查静音状态（关键的坑）

先查注册表里录音设备的属性：

```powershell
$k = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'
Get-ChildItem $k | ForEach-Object {
  $p = Get-ItemProperty (Join-Path $_.PSPath 'Properties')
  $p.'{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},4'   # Mute
  $p.'{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},3'   # Volume
}
```

**全是空的。**

原因是：**这两个值根本不存在注册表里**。它们只活在音频服务的内存中，必须通过 Core Audio API 才能读写。

所以「翻注册表」和「看音量合成器」这两条常见思路，**都会漏掉这个静音标志**。

## 真正的原因

用 Core Audio API 读默认录音设备的真实状态：

```
mute = True      ← 静音标志是开的
volume = 100%    ← 但音量其实是满的
```

**音量 100%，静音却开着。** 这就是所有程序都录不到声音的原因。

## 解决办法

写一个 PowerShell 脚本，用 COM 互操作调用 `IAudioEndpointVolume`：

```powershell
Add-Type -Language CSharp @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class MMDeviceEnumeratorComObject { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    int NotImpl1();
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams,
                 [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
}

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
    // ... 省略中间方法，顺序不能错
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
    int GetMute(out bool pbMute);
}

public static class Mic {
    static IAudioEndpointVolume Get() {
        var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice dev;
        // dataFlow: 0=播放 1=录音    role: 0=Console
        Marshal.ThrowExceptionForHR(en.GetDefaultAudioEndpoint(1, 0, out dev));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object o;
        Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, IntPtr.Zero, out o));
        return (IAudioEndpointVolume)o;
    }

    public static string Report() {
        var v = Get();
        bool mute; v.GetMute(out mute);
        float vol; v.GetMasterVolumeLevelScalar(out vol);
        return string.Format("mute={0} volume={1:P0}", mute, vol);
    }

    public static string Fix() {
        var v = Get();
        v.SetMute(false, Guid.Empty);
        v.SetMasterVolumeLevelScalar(1.0f, Guid.Empty);
        return Report();
    }
}
"@

[Mic]::Report()
[Mic]::Fix()
```

> 注意 `IAudioEndpointVolume` 的方法**顺序必须和 vtable 一致**，不能随便省略中间的方法名——把不需要的保留为声明即可，但顺序不能变。

执行后：

```
修复前:  mute=True  volume=100%
修复后:  mute=False volume=100%
```

再录音验证：

```
peak : 20176 / 32767      ← 修复前是 1
RMS  : 1693.9
```

**信号强度提升了约 2 万倍**，麦克风恢复正常。

## 小结

几个值得记住的点：

1. **「设备选对了」不等于「有信号」**。要用实际录音的振幅来判断，别只看设备名。

2. **静音标志不在注册表里**。`MMDevices\Audio\Capture` 下的 `Mute`/`Volume` 属性是空的，值在音频服务内存中。必须用 Core Audio API。

3. **音量合成器也可能骗你**。它显示音量正常，但真正的 mute 标志是另一个维度。

4. **虚拟设备的静音是正常的**。网易虚拟音频设备这类没有真实输入的设备，静音是预期行为，别去动它。

排查麦克风问题的顺序建议：

| 顺序 | 检查什么 | 怎么看 |
|---|---|---|
| 1 | 默认设备对不对 | `gst-device-monitor` 或声音设置 |
| 2 | **实际能不能录到** | 录一段看 peak 值 |
| 3 | **mute 标志** | Core Audio API（本文的方法） |
| 4 | 应用自己的设备选择 | QQ/微信等各自的设置 |

前两步能排除大部分问题，第三步是最容易被漏掉的。
