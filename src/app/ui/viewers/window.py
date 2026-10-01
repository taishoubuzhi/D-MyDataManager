"""查看器窗口：程序内查看文件的统一外壳。

界面层拿到 `OpenDecision` 后调用 `open_viewer()`：把查看器插件的控件放进带工具栏的
窗口，并提供「用系统程序打开 / 定位文件」两个出口。查看器创建失败时窗口里显示原因，
而不是抛异常打断调用方。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel

from ...core import shell
from ...core.viewers import Viewer

_WINDOWS: "list[ViewerWindow]" = []


class ViewerWindow(QWidget):
    """查看器外壳：标题栏 + 内容区。"""

    def __init__(self, viewer: Viewer, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewer = viewer
        self.path = Path(path)
        self.content_widget: QWidget | None = None
        self.setObjectName("viewerWindow")
        self.setWindowTitle(f"{self.path.name} · {viewer.name}")
        self.resize(980, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)
        self.title_label = StrongBodyLabel(self.path.name, bar)
        bar_layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(viewer.name, bar)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        self.external_button = PushButton(FluentIcon.LINK, "用系统程序打开", bar)
        self.external_button.clicked.connect(self._on_open_external)
        bar_layout.addWidget(self.external_button)
        self.reveal_button = PushButton(FluentIcon.FOLDER, "定位文件", bar)
        self.reveal_button.clicked.connect(self._on_reveal)
        bar_layout.addWidget(self.reveal_button)
        root.addWidget(bar)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        self._build_content()

    def _build_content(self) -> None:
        if self.viewer.factory is None:
            self.content_layout.addWidget(CaptionLabel("该查看器没有提供界面，请改用系统程序打开。", self.content))
            return
        try:
            widget = self.viewer.factory(self.path, self.content)
        except Exception as exc:  # 查看器异常不应影响主界面
            logger.exception("创建查看器控件失败：{}", self.viewer.id)
            self.content_layout.addWidget(CaptionLabel(f"内置查看器无法显示该文件：{exc}", self.content))
            return
        self.content_widget = widget
        self.content_layout.addWidget(widget, 1)
        caption = str(getattr(widget, "caption", "") or "")
        if caption:
            self.meta_label.setText(f"{self.viewer.name} · {caption}")

    def _on_open_external(self) -> None:
        if not shell.open_default(self.path):
            self.meta_label.setText("系统无法打开该文件，可在「打开方式」页设为自定义程序")

    def _on_reveal(self) -> None:
        shell.reveal(self.path)


def _forget(window: ViewerWindow) -> None:
    if window in _WINDOWS:
        _WINDOWS.remove(window)


def open_viewer(viewer: Viewer, path: Path, parent: QWidget | None = None) -> tuple[bool, str]:
    """在程序内打开文件，返回 (是否成功, 说明)。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    if viewer is None or viewer.factory is None:
        return False, "没有可用的内置查看器"
    try:
        window = ViewerWindow(viewer, target, parent)
    except Exception as exc:
        logger.exception("打开查看器失败：{}", target)
        return False, f"打开查看器失败：{exc}"
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    window.destroyed.connect(lambda *_: _forget(window))
    _WINDOWS.append(window)
    window.show()
    window.raise_()
    window.activateWindow()
    return True, viewer.name
