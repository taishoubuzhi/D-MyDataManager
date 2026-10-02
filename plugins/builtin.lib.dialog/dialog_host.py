"""弹窗外壳：独立窗口（PopupWindow）与 dialog 扩展接口（DialogApi）。

其他插件用 ctx.require("dialog") 拿到 DialogApi，再用 open_page() 把自己的页面放进
这个外壳；窗口没有父控件，因此与程序主体相互独立。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel, TransparentToolButton

#: 扩展接口名：查看器等插件用 ctx.require(EXTENSION_NAME) 取到 DialogApi
EXTENSION_NAME = "dialog"


class PopupWindow(QWidget):
    """独立弹窗：标题栏 + 内容区；关闭按钮或 Esc 关闭，关闭后自动销毁。"""

    def __init__(
        self,
        title: str,
        content_factory: Callable[[QWidget], QWidget],
        meta: str = "",
        buttons: Sequence[tuple[str, Callable[[], None]]] = (),
        width: int = 980,
        height: int = 700,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("popupWindow")
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(width, height)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)
        self.title_label = StrongBodyLabel(title, bar)
        bar_layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(meta, bar)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        for caption, callback in buttons:
            button = PushButton(caption, bar)
            button.clicked.connect(callback)
            bar_layout.addWidget(button)
        self.close_button = TransparentToolButton(FluentIcon.CLOSE, bar)
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self.close)
        bar_layout.addWidget(self.close_button)
        root.addWidget(bar)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        try:
            widget = content_factory(self.content)
        except Exception as exc:  # 插件页面出错不应该影响主界面
            logger.exception("创建弹窗内容失败：{}", title)
            widget = CaptionLabel(f"页面无法显示：{exc}", self.content)
        self.content_widget = widget
        if widget is not None:
            self.content_layout.addWidget(widget, 1)
        QShortcut(QKeySequence("Escape"), self, activated=self.close)


class DialogApi:
    """dialog 扩展接口：为其他插件创建独立弹窗页面。"""

    def __init__(self) -> None:
        self._windows: list[PopupWindow] = []

    def open_page(
        self,
        title: str,
        content_factory: Callable[[QWidget], QWidget],
        meta: str = "",
        buttons: Sequence[tuple[str, Callable[[], None]]] = (),
        width: int = 980,
        height: int = 700,
    ) -> PopupWindow:
        window = PopupWindow(title, content_factory, meta, buttons, width, height)
        self._windows.append(window)
        window.destroyed.connect(lambda *_: self._forget(window))
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def windows(self) -> tuple[PopupWindow, ...]:
        return tuple(self._windows)

    def close_all(self) -> int:
        count = 0
        for window in list(self._windows):
            window.close()
            count += 1
        return count

    def _forget(self, window: PopupWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)


__all__ = ["EXTENSION_NAME", "DialogApi", "PopupWindow"]
