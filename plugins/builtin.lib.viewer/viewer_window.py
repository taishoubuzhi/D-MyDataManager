"""查看器内容页外壳：文件名 + 查看器名 + 系统打开 / 定位文件 + 内容区。

窗口装饰（标题栏、关闭按钮）由弹窗工具库（builtin.lib.dialog）负责；本模块只做「页面本身」，
内容由调用方通过 `build(container)` 造出来，所以外壳与具体查看器无关。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel

from app.sdk import ui
from app.sdk.ui import IconTextButton

#: 默认的弹窗扩展接口名（弹窗工具库 builtin.lib.dialog 提供）
DEFAULT_HOST = "dialog"


class ViewerWindow(QWidget):
    """查看器内容页：工具栏 + 查看器控件；窗口装饰由弹窗插件负责。"""

    def __init__(self, path, build, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.build = build
        self.viewer_name = str(name or "")
        self.content_widget: QWidget | None = None
        self.setObjectName("viewerWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)
        self.title_label = StrongBodyLabel(self.path.name, bar)
        bar_layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(self.viewer_name, bar)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        self.external_button = IconTextButton(FluentIcon.LINK, "用系统程序打开", bar)
        self.external_button.clicked.connect(self._on_open_external)
        bar_layout.addWidget(self.external_button)
        self.reveal_button = IconTextButton(FluentIcon.FOLDER, "定位文件", bar)
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
        if self.build is None:
            self.content_layout.addWidget(CaptionLabel("该查看器没有提供界面，请改用系统程序打开。", self.content))
            return
        try:
            widget = self.build(self.content)
        except Exception as exc:  # 查看器异常不应影响主界面
            logger.exception("创建查看器控件失败：{}", self.viewer_name)
            self.content_layout.addWidget(CaptionLabel(f"内置查看器无法显示该文件：{exc}", self.content))
            return
        self.content_widget = widget
        self.content_layout.addWidget(widget, 1)
        caption = str(getattr(widget, "caption", "") or "")
        if caption:
            self.meta_label.setText(f"{self.viewer_name} · {caption}" if self.viewer_name else caption)

    def _on_open_external(self) -> None:
        if not ui.open_default(self.path):
            self.meta_label.setText("系统无法打开该文件，可在「打开方式」页设为自定义程序")

    def _on_reveal(self) -> None:
        ui.reveal(self.path)


__all__ = ["DEFAULT_HOST", "ViewerWindow"]
