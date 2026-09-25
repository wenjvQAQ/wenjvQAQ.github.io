---
title: AirPlay 投屏「能搜到但连不上」——路由跃点数把广播送到了错误的网卡
cover: imgs/covers/cover5.jpg
date: 2026-09-25 14:00:00
tags:
  - 网络
  - AirPlay
  - Windows
categories:
  - 踩坑记录
---

用 UxPlay 给 iPad 投屏到 Windows。iPad 能在「屏幕镜像」里**看到**电脑，点下去却一直转圈，连不上。

这类「搜索得到但连接失败」的问题，通常指向**设备广播了错误的地址**。查下去果然是——但原因有点反直觉。

<!-- more -->

## 环境

- Windows 11，WLAN `192.168.3.4`
- 装了 **Radmin VPN**（虚拟网卡，`26.28.222.208`）
- VMware 虚拟网卡两块
- UxPlay 1.74

## 排查：先看它到底广播了什么

AirPlay 靠 **mDNS（Bonjour）** 发现设备。所以第一步是看 UxPlay 实际广播的是哪个地址。

写个 Python 脚本浏览 `_airplay._tcp` 服务：

```python
from zeroconf import Zeroconf, ServiceBrowser

class L:
    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            print(name, "->", info.parsed_addresses())

zc = Zeroconf()
ServiceBrowser(zc, "_airplay._tcp.local.", L())
time.sleep(10)
```

结果：

```
AirPlay._airplay._tcp.local.  ->  ['26.28.222.208']
```

**它广播的是 `26.28.222.208` —— Radmin VPN 的地址！**

iPad 拿到的就是这个地址。而在 iPad 所在的局域网里，`26.28.222.208` 是**完全不可达**的，所以连不上。

## 为什么 UxPlay 选了那个网卡

关键问题：UxPlay **没有选择网卡的参数**。

我一开始以为 `-n` 是选网卡，其实是设置**服务显示名**：

```bash
# -n 只是给服务起名字，不是选网卡！
uxplay -n "MyAirPlay"
```

UxPlay 会**自己按 Windows 的路由跃点数（interface metric）挑一个网卡**。

查各网卡的 metric：

```powershell
Get-NetIPInterface -AddressFamily IPv4 |
  Sort-Object InterfaceMetric |
  Select-Object InterfaceAlias, InterfaceMetric
```

```
InterfaceAlias                InterfaceMetric
--------------                ---------------
Radmin VPN                                  1     ← 最低，被优先选中
VMware Network Adapter VMnet1              35
VMware Network Adapter VMnet8              35
WLAN                                       50     ← 真正的网卡
```

**Radmin VPN 的 metric 是 1，比 WLAN 的 50 低得多**，所以 Windows 认为它是「最优」路径，UxPlay 就选了它。

> 跃点数越低优先级越高。VPN 类软件经常把自己设成 1，来确保流量走它。

## 尝试 1：改 metric（失败）

最直接的想法是把 Radmin 的 metric 调高：

```powershell
# 管理员权限
Set-NetIPInterface -InterfaceAlias "Radmin VPN" -AddressFamily IPv4 -InterfaceMetric 5000
```

**无效。** 执行后一查还是 1：

```
InterfaceAlias   InterfaceMetric
Radmin VPN                   1     ← 被改回去了
WLAN                        50
```

原因是 **Radmin 的控制服务会把自己的 metric 改回来**。这类 VPN 软件都有类似的自保护机制。

## 解决方案：停用网卡

既然改不了 metric，就**停用整个网卡**：

```powershell
# 管理员权限
Disable-NetAdapter -Name "Radmin VPN" -Confirm:$false
```

停用后再看广播：

```
AirPlay._airplay._tcp.local.  ->  ['192.168.3.4']      ← 正确了
```

**iPad 立刻就能连上。**

## 另一个坑：主机名解析

排查过程中还发现一个隐患。

UxPlay 广播时会附带 `server=<hostname>.local.` 的 A 记录。而**这台机器的主机名解析出 4 个地址**：

```python
socket.gethostbyname_ex(socket.gethostname())
# → ('cry', [], ['26.28.222.208',           ← Radmin 排在最前
#                 '169.254.4.255',           ← link-local
#                '169.254.194.200',
#                '192.168.3.4'])            ← 真正的地址
```

也就是说，即使主地址对了，**A 记录里仍然会带上那几个不可达的地址**，客户端可能先试错的那个。

解决办法是在广播时**用一个专用的 server 名，而不是机器主机名**：

```python
info = ServiceInfo(
    "_airplay._tcp.local.",
    "AirPlay._airplay._tcp.local.",
    addresses=[socket.inet_aton(real_ip)],   # 只给真实地址
    server="airplay-advert.local.",          # 不用 hostname
)
```

## 「能搜到但连不上」的通用排查思路

这个问题本质上是**「发现成功，但地址错误」**。遇到类似情况可以这样排：

| 步骤 | 方法 |
|---|---|
| 1 | **看它广播了什么地址**（zeroconf / `dns-sd -B` / Wireshark） |
| 2 | 对比这个地址和客户端所在的网段 |
| 3 | 列所有网卡和 metric，找「优先级异常高」的虚拟网卡 |
| 4 | VPN / VMware / 虚拟网卡**先停用再测** |
| 5 | 检查主机名是否解析出多个地址 |

**「能搜到」说明 mDNS 通了，「连不上」说明地址或端口不对。** 这两件事要分开验证。

## 顺带：验证 mDNS 是否工作的最小方法

在动 UxPlay 之前，可以先确认网络层没问题——自己广播一个假的 AirPlay 服务：

```python
info = ServiceInfo(
    "_airplay._tcp.local.",
    "TEST._airplay._tcp.local.",
    addresses=[socket.inet_aton(ip)],
    port=7000,
    properties={b"model": b"AppleTV3,2"},
)
zc.register_service(info)
```

如果 iPad 能在「屏幕镜像」列表里看到这个假服务，说明**发现链路是通的**，问题只在真正的接收端。

这个方法在排查时很有用：**把「网络能不能发现」和「服务能不能连接」分开验证。**

## 小结

1. **UxPlay 不能选网卡**，它按路由跃点数自动挑。`-n` 只是服务名，别搞混。

2. **VPN 类软件通常把 metric 设为 1**，会抢走所有「按最优路径选择」的行为。`Get-NetIPInterface` 可以查。

3. **改 metric 可能被服务改回来**，最可靠的方案是直接停用网卡。

4. **主机名可能解析出多个地址**，广播时用专用名称更安全。

5. **「能搜到」和「能连上」是两个独立环节**，分开验证能快速定位问题在哪一层。
