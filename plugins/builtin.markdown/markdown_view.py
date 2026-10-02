"""Markdown 查看器：渲染视图与源码视图可自由切换。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PlainTextEdit, PushButton

from app.sdk.data import read_text
from app.sdk.ui import COMPACT_MARGINS

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

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._toggle_button = PushButton(FluentIcon.CODE, "查看源码", self)
        self._toggle_button.clicked.connect(self._toggle)
        bar.addWidget(self._toggle_button)
        bar.addStretch(1)
        self.status_label = CaptionLabel(self.caption, self)
        bar.addWidget(self.status_label)
        root.addLayout(bar)

        self._stack = QStackedWidget(self)
        self._browser = QTextBrowser(self._stack)
        self._browser.setOpenExternalLinks(True)
        self._browser.setMarkdown(text)
        self._stack.addWidget(self._browser)

        self._source = PlainTextEdit(self._stack)
        self._source.setReadOnly(True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self._source.setFont(font)
        self._source.setPlainText(text)
        self._stack.addWidget(self._source)
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
