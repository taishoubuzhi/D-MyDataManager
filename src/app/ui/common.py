"""界面通用工具：格式化、图标映射、提示条。"""

from __future__ import annotations

import datetime as dt
import sys

from PyQt6.QtCore import Qt
from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, MessageBox, StateToolTip

from ..db.models import DATA_TYPE_NAMES, DataType

TYPE_ICONS: dict[str, FluentIcon] = {
    DataType.IMAGE.value: FluentIcon.PHOTO,
    DataType.VIDEO.value: FluentIcon.VIDEO,
    DataType.AUDIO.value: FluentIcon.MUSIC,
    DataType.DOCUMENT.value: FluentIcon.DOCUMENT,
    DataType.SPREADSHEET.value: FluentIcon.DOCUMENT,
    DataType.PRESENTATION.value: FluentIcon.VIEW,
    DataType.ARCHIVE.value: FluentIcon.ZIP_FOLDER,
    DataType.CODE.value: FluentIcon.CODE,
    DataType.TEXT.value: FluentIcon.FONT,
    DataType.OTHER.value: FluentIcon.LABEL,
}


def type_key(value) -> str:
    """把 DataType 或数据库里读回的字符串统一成枚举值字符串。"""
    return str(value).split(".")[-1]


def type_name(value) -> str:
    try:
        return DATA_TYPE_NAMES[DataType(type_key(value))]
    except (ValueError, KeyError):
        return "其他"


def type_icon(value) -> FluentIcon:
    return TYPE_ICONS.get(type_key(value), FluentIcon.LABEL)


def format_size(size: int | None) -> str:
    value = float(size or 0)
    if value < 1024:
        return f"{value:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def format_datetime(value: dt.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return value.strftime(fmt) if isinstance(value, dt.datetime) else ""


def elide(text: str, limit: int = 60) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def toast_success(parent, title: str, content: str = "") -> None:
    InfoBar.success(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=2500,
        parent=parent,
    )


def toast_warning(parent, title: str, content: str = "") -> None:
    InfoBar.warning(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=3000,
        parent=parent,
    )


def toast_error(parent, title: str, content: str = "") -> None:
    InfoBar.error(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=4000,
        parent=parent,
    )


def confirm(parent, title: str, content: str) -> bool:
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


def restart_application(delay_ms: int = 400) -> None:
    """退出当前进程并用相同参数启动一个新实例（用于恢复初始化之后）。"""
    from PyQt6.QtCore import QProcess, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    def _relaunch() -> None:
        QProcess.startDetached(sys.executable, list(sys.argv))
        app.quit()

    QTimer.singleShot(delay_ms, _relaunch)
