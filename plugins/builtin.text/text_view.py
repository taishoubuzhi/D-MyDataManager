"""文本与代码查看器：猜测编码、可切换编码、自动换行、复制全文。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, CheckBox, ComboBox, FluentIcon, PlainTextEdit, PushButton

from app.sdk.data import ENCODINGS, read_text
from app.sdk.ui import COMPACT_MARGINS
from app.sdk.ui import IconTextButton

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

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("编码", self))
        self._combo = ComboBox(self)
        self._combo.addItem(AUTO_ENCODING)
        self._combo.addItems(list(ENCODINGS))
        self._combo.setFixedWidth(140)
        self._combo.currentTextChanged.connect(self._on_encoding)
        bar.addWidget(self._combo)
        self._wrap = CheckBox("自动换行", self)
        self._wrap.setChecked(True)
        self._wrap.stateChanged.connect(self._on_wrap)
        bar.addWidget(self._wrap)
        copy_button = IconTextButton(FluentIcon.COPY, "复制全文", self)
        copy_button.clicked.connect(self._on_copy)
        bar.addWidget(copy_button)
        bar.addStretch(1)
        self.status_label = CaptionLabel("", self)
        bar.addWidget(self.status_label)
        root.addLayout(bar)

        self._edit = PlainTextEdit(self)
        self._edit.setReadOnly(True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self._edit.setFont(font)
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

    def _on_wrap(self) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if self._wrap.isChecked() else QPlainTextEdit.LineWrapMode.NoWrap
        self._edit.setLineWrapMode(mode)

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self._edit.toPlainText())
        self.status_label.setText("已复制到剪贴板")
