"""程序内查看器的界面实现。

每个查看器都是一个 `QWidget`，构造签名为 `(path, parent=None)`，并可选地提供
`caption` 字符串（显示在弹窗标题栏）。插件通过 `core.viewers.Viewer.factory` 引用这些类，
并用 `viewer.host` 指定由哪个扩展接口负责显示页面；弹窗外壳见 `window.py`。
"""

from .window import DEFAULT_HOST, ViewerWindow, host_api, open_viewer

__all__ = ["DEFAULT_HOST", "ViewerWindow", "host_api", "open_viewer"]
