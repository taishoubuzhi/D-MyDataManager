"""Markdown 查看器：渲染视图与源码视图可自由切换。

两个视图与切换按钮都由 builtin.lib.ui 提供，这里只接渲染 / 源码数据与切换行为。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.data import read_text
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    icon_button,
    status_label,
    text_area,
    text_browser,
    toolbar,
    view_stack,
)

RENDER_INDEX = 0
SOURCE_INDEX = 1


class MarkdownViewer(QWidget):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._truncated = False
        text = ""
        try:
            text, _used, self._truncated = read_text(self._path)
        except OSError as exc:
            text = f"无法读取文件：{exc}"
        self.caption = self._path.name
        if self._truncated:
            self.caption = f"{self._path.name} · 已截断（仅显示前 512 KiB）"
        self._build_ui(text)

    def _build_ui(self, text: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar, row = toolbar(self)
        self._toggle_button = icon_button(bar, FluentIcon.CODE, "查看源码", self._toggle)
        row.addWidget(self._toggle_button)
        row.addStretch(1)
        self.status_label = status_label(bar, self.caption)
        row.addWidget(self.status_label)
        root.addWidget(bar)

        self._browser = text_browser(self, text=text, markdown=True)
        self._browser.setOpenExternalLinks(True)
        self._source = text_area(self, text=text, read_only=True, monospace=True)
        self._stack = view_stack(self, self._browser, self._source)
        root.addWidget(self._stack, 1)

    def _toggle(self) -> None:
        if self._stack.currentIndex() == RENDER_INDEX:
            self._stack.setCurrentIndex(SOURCE_INDEX)
            self._toggle_button.setText("查看渲染")
            self._toggle_button.setIcon(FluentIcon.VIEW)
        else:
            self._stack.setCurrentIndex(RENDER_INDEX)
            self._toggle_button.setText("查看源码")
            self._toggle_button.setIcon(FluentIcon.CODE)
