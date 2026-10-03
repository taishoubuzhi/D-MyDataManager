"""文本与代码查看器：猜测编码、可切换编码、自动换行、复制全文。

页面结构由 builtin.lib.ui 的工具条与控件工厂给出，这里只接行为回调。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.data import ENCODINGS, read_text
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    caption,
    check_box,
    combo_box,
    icon_button,
    status_label,
    text_area,
    toolbar,
)

AUTO_ENCODING = "自动检测"


class TextViewer(QWidget):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._encoding = ""
        self.caption = self._path.name
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar, row = toolbar(self)
        row.addWidget(caption(bar, "编码"))
        self._combo = combo_box(bar, items=(AUTO_ENCODING, *ENCODINGS), width=140, on_change=self._on_encoding)
        row.addWidget(self._combo)
        self._wrap = check_box(bar, text="自动换行", checked=True, on_change=self._on_wrap)
        row.addWidget(self._wrap)
        row.addWidget(icon_button(bar, FluentIcon.COPY, "复制全文", self._on_copy))
        row.addStretch(1)
        self.status_label = status_label(bar, "")
        row.addWidget(self.status_label)
        root.addWidget(bar)

        self._edit = text_area(self, read_only=True, monospace=True)
        root.addWidget(self._edit, 1)

    # ------------------------------------------------------------------ 行为
    def _load(self) -> None:
        try:
            text, used, truncated = read_text(self._path, encoding=self._encoding)
        except OSError as exc:
            self._edit.setPlainText("")
            self.status_label.setText(f"无法读取文件：{exc}")
            return
        self._edit.setPlainText(text)
        cursor = self._edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._edit.setTextCursor(cursor)
        self._combo.blockSignals(True)
        self._combo.setCurrentText(used if used in ENCODINGS else AUTO_ENCODING)
        self._combo.blockSignals(False)
        pieces = [self._path.name, f"编码 {used}"]
        if truncated:
            pieces.append("已截断（仅显示前 512 KiB）")
        self.caption = " · ".join(pieces)
        self.status_label.setText(self.caption)

    def _on_encoding(self, text: str) -> None:
        self._encoding = "" if text == AUTO_ENCODING else text
        self._load()

    def _on_wrap(self, checked: bool = True) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if checked else QPlainTextEdit.LineWrapMode.NoWrap
        self._edit.setLineWrapMode(mode)

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self._edit.toPlainText())
        self.status_label.setText("已复制到剪贴板")
