"""程序内查看器的界面实现。

每个查看器都是一个 `QWidget`，构造签名为 `(path, parent=None)`，并可选地提供
`caption` 字符串（显示在查看器窗口的标题栏）。插件通过 `core.viewers.Viewer.factory`
引用这些类；窗口外壳见 `window.py`。
"""

from .window import ViewerWindow, open_viewer

__all__ = ["ViewerWindow", "open_viewer"]
