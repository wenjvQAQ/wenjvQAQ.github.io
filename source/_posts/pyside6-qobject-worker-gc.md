---
title: PySide6 最隐蔽的 bug：QObject worker 被垃圾回收，程序「什么都没发生」
cover: imgs/covers/cover3.jpg
date: 2026-09-25 12:10:00
tags:
  - PySide6
  - Qt
  - Python
  - 多线程
categories:
  - 踩坑记录
---

这是我这段时间遇到最难查的 bug。

**症状是：点击搜索，界面不报错，不卡死，就是没有任何反应。** 没有异常、没有日志、没有崩溃——搜索结果永远是 0 条。

查了很久才发现，是我自己写的线程封装把 worker 对象给「弄丢」了。

<!-- more -->

## 症状

写了一个网易云音乐的 GUI，搜索功能这样实现：网络请求放后台线程，结果通过信号回主线程更新表格。

```python
def do_search(self):
    self._run(Worker(self.svc.search, keyword, limit), self._on_search)

def _run(self, worker, on_done):
    th = QThread()
    worker.moveToThread(th)
    th.started.connect(worker.run)
    worker.done.connect(on_done)      # 看起来没问题
    th.start()
```

点击搜索后：

```
搜索结果: 0 条
```

**没有任何报错。** 换成解析、歌单、下载，全都一样——所有功能都是「静默无响应」。

## 排查：问题在网络还是在线程？

第一反应是网络或 API 有问题。但直接在命令行调用同样的函数：

```python
svc.search("周杰伦", 10)     # → 正常返回 10 条
```

**API 没问题。** 那问题就在线程封装上。

## 关键一步：隔离测试

与其盯着界面，不如把机制单独拎出来测：

```python
# 测试 1：裸的 Worker
w = Worker(lambda: "RESULT")
th = QThread()
w.moveToThread(th)
th.started.connect(w.run)
got = []
w.done.connect(lambda r: got.append(r))
w.done.connect(th.quit)
th.start()
loop.exec()

print("worker result:", got)
```

```
worker result: ['RESULT']        ← 正常！
```

裸的机制没问题。再测 `MainWindow._run`：

```python
# 测试 2：经过 MainWindow._run
win._run(Worker(lambda: "hello"), on_done)
```

```
collected: []
pending threads: 2               ← 信号一次都没触发
```

**同一个 `Worker`，裸用可以，套进 `_run` 就不行。**

差距在哪里？

## 真正的原因

看 `_run` 的代码：

```python
def _run(self, worker, on_done):
    th = QThread()
    worker.moveToThread(th)      # worker 是「局部变量」
    th.started.connect(worker.run)
    worker.done.connect(on_done)
    th.start()
    # 函数返回 → worker 失去引用
```

`worker` 是**函数参数**，函数返回后**引用计数归零**。

`worker` 是一个 `QObject` 子类，但它**同时也是一个 Python 对象**。Python 的垃圾回收会把它回收掉——**而此时线程还没来得及调用 `run()`**。

对象没了 → `th.started` 发出的信号连到一个已经销毁的对象 → slot 不执行 → `done` 信号永远不发 → 静默失败。

> 之所以不崩溃，是因为 Qt 的信号槽机制对已销毁的对象是「安全地什么都不做」，而不是抛异常。

## 修复

用一个列表持有强引用，任务结束后再释放：

```python
def __init__(self):
    self._threads = []
    self._workers = []          # ← 关键：保持强引用

def _run(self, worker, on_done):
    th = QThread()
    worker.moveToThread(th)
    self._workers.append(worker)      # 防止被 GC

    th.started.connect(worker.run)
    worker.done.connect(on_done)
    worker.done.connect(th.quit)

    def cleanup():
        if th in self._threads:
            self._threads.remove(th)
        if worker in self._workers:
            self._workers.remove(worker)   # 结束后释放

    th.finished.connect(cleanup)
    self._threads.append(th)
    th.start()
```

修完立刻见效：

```
collected: ['hello-from-thread']
```

搜索也能返回结果了。

## 为什么这个 bug 特别难查

对比一下常见的 Qt 线程问题：

| 问题 | 表现 | 好查吗 |
|---|---|---|
| 在子线程更新 UI | **直接崩溃** | 好查 |
| 忘记 `moveToThread` | 界面卡死 | 好查 |
| 退出时线程还在跑 | `QThread: Destroyed while thread is still running` | 好查 |
| **worker 被 GC** | **什么都不发生** | **极难** |

前三种都有明显的错误信号。最后一种**完全静默**，而且：

- 没有异常，看日志看不出问题
- 界面响应正常，不卡
- 功能「跑到一半」——连上了线程、发出了信号，但 slot 没执行

**「不报错但不工作」比「崩溃」难查得多。**

## 附带修掉的另一个问题

同一个项目里还有第二个线程 bug：**关窗口时崩溃**。

```
QThread: Destroyed while thread '' is still running
```

原因是窗口关闭时没有等待后台线程。修复是加 `closeEvent`：

```python
def closeEvent(self, event):
    running = [t for t in self._threads if t.isRunning()]
    for t in running:
        t.quit()
        if not t.wait(3000):      # 3 秒宽限
            t.terminate()
            t.wait(1000)
    event.accept()
```

修复后退出码从 1 变成 0。

## 小结

1. **`QObject` 用 `moveToThread` 后，必须由 Python 侧保持强引用**，否则可能在 slot 执行前被回收。

2. **「静默失败」要优先怀疑对象生命周期**，而不是逻辑错误。逻辑错通常会报错；生命周期错往往什么都不发生。

3. **调试时把机制单独拎出来测**。如果裸的组件能用、封装后用不了，问题一定在封装层。

4. Qt 的信号槽对已销毁对象是**静默忽略**的——这是设计上的安全，但也让这类 bug 极难发现。

如果你也遇到过「代码看起来没问题但就是不执行」，可以先检查一下：**你的对象还活着吗？**
