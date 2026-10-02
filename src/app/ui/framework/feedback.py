"""统一的用户反馈：提示条、确认框、忙碌提示与控件回收。

页面不允许自己 `new InfoBar` 或 `MessageBox`：位置、时长、图标都在这里定死，
这样同类操作在全站看起来才是同一件事。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import InfoBar, InfoBarPosition, MessageBox, StateToolTip

#: 提示条停留时长（毫秒）：成功短、警告中、错误长、信息最短。
DURATION_SUCCESS = 2500
DURATION_INFO = 2500
DURATION_WARNING = 3000
DURATION_ERROR = 4000


def _show(kind: str, parent, title: str, content: str, duration: int) -> None:
    factory = getattr(InfoBar, kind)
    factory(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=duration,
        parent=parent,
    )


def toast_success(parent, title: str, content: str = "") -> None:
    _show("success", parent, title, content, DURATION_SUCCESS)


def toast_info(parent, title: str, content: str = "") -> None:
    _show("info", parent, title, content, DURATION_INFO)


def toast_warning(parent, title: str, content: str = "") -> None:
    _show("warning", parent, title, content, DURATION_WARNING)


def toast_error(parent, title: str, content: str = "") -> None:
    _show("error", parent, title, content, DURATION_ERROR)


def confirm(parent, title: str, content: str) -> bool:
    """统一样式的确认框；确认返回 True。"""
    box = MessageBox(title, content, parent.window() if parent else None)
    return bool(box.exec())


def release_widget(widget) -> None:
    """把控件从界面上摘下来并排队销毁。

    必须先 hide()：直接 `setParent(None)` 之后控件在 Windows 上仍是「可见的顶层窗口」，
    列表每次重渲染都会让每个旧条目以一闪而过的小窗口冒出来，所以先隐藏再断开父级。
    """
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def clear_layout(container) -> None:
    """清空普通布局中的控件（`FlowArea` 见 `components/flow_area.py`）。"""
    widgets = []
    while container.count():
        entry = container.takeAt(0)
        widgets.append(entry.widget() if hasattr(entry, "widget") else entry)
    for widget in widgets:
        if widget is not None:
            release_widget(widget)


class BusyTip:
    """长耗时操作的进行中提示，完成时转为完成状态并自动淡出。"""

    def __init__(self, parent, title: str, content: str = "正在处理…") -> None:
        window = parent.window() if parent is not None else None
        self._tip = None if window is None else StateToolTip(title, content, window)
        if self._tip is not None:
            self._tip.move(self._tip.getSuitablePos())
            self._tip.show()

    def update(self, content: str) -> None:
        if self._tip is not None:
            self._tip.setContent(content)

    def finish(self, content: str = "已完成") -> None:
        if self._tip is not None:
            self._tip.setContent(content)
            self._tip.setState(True)


__all__ = [
    "DURATION_ERROR",
    "DURATION_INFO",
    "DURATION_SUCCESS",
    "DURATION_WARNING",
    "BusyTip",
    "clear_layout",
    "confirm",
    "release_widget",
    "toast_error",
    "toast_info",
    "toast_success",
    "toast_warning",
]
