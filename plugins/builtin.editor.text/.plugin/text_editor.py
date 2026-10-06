"""内置文本编辑器：为纯文本 / 代码 / markdown / csv / json 提供可编辑并保存回库内文件的控件。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.data import ENCODINGS, read_text
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    check_box,
    combo_box,
    confirm,
    icon_button,
    status_label,
    text_area,
    toolbar,
)

AUTO_ENCODING = "自动检测"


class TextEditor(QWidget):
    """多行文本编辑器：编码探测 / 切换、自动换行、保存回原文件。

    外壳（`EditorWindow`）会读取这里的 `caption` / `is_dirty()` / `save()`。
    """

    def __init__(self, path, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._encoding = ""
        self._used = ""
        self._write = ""
        self._original = ""
        self._truncated = False
        self.caption = ""
        self._build_ui()
        self._load()

    # ------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(6)

        bar, bar_layout = toolbar(self)
        self.encoding_box = combo_box(
            bar,
            items=(AUTO_ENCODING, *ENCODINGS),
            width=150,
            on_change=self._on_encoding,
        )
        bar_layout.addWidget(self.encoding_box)
        self.wrap_box = check_box(bar, text="自动换行", checked=True, on_change=self._on_wrap)
        bar_layout.addWidget(self.wrap_box)
        bar_layout.addStretch(1)
        self.copy_button = icon_button(bar, FluentIcon.COPY, "复制全文", self._on_copy)
        bar_layout.addWidget(self.copy_button)
        root.addWidget(bar)

        self.area = text_area(self, read_only=False, monospace=True, on_change=self._on_changed)
        root.addWidget(self.area, 1)
        self.status = status_label(self, "")
        root.addWidget(self.status)

    # ------------------------------------------------------------ 读写
    def _load(self) -> None:
        try:
            text, used, truncated = read_text(self._path, encoding=self._encoding)
        except Exception as exc:  # noqa: BLE001 - 读不了就退化成只读提示
            self._original = ""
            self._truncated = True
            self._write = ""
            self.area.setReadOnly(True)
            self.caption = "无法读取"
            self.status.setText(f"无法读取：{exc}")
            return
        self._original = text
        self._used = used or "utf-8"
        self._write = self._write_encoding(self._used)
        self._truncated = bool(truncated)
        self.area.blockSignals(True)
        self.area.setPlainText(text)
        self.area.setReadOnly(self._truncated)
        self.area.blockSignals(False)
        self.encoding_box.blockSignals(True)
        position = self.encoding_box.findText(self._used)
        self.encoding_box.setCurrentIndex(position if position >= 0 else 0)
        self.encoding_box.blockSignals(False)
        self.caption = "大文件截断 · 只读" if self._truncated else f"编码 {self._used}"
        self._update_status()

    def _write_encoding(self, used: str) -> str:
        """utf-8-sig 只在文件本来带 BOM 时保留，否则按 utf-8 写，避免保存时凭空加 BOM。"""
        if used != "utf-8-sig":
            return used
        try:
            with self._path.open("rb") as handle:
                return "utf-8-sig" if handle.read(3) == b"\xef\xbb\xbf" else "utf-8"
        except OSError:
            return "utf-8"

    def is_dirty(self) -> bool:
        return (not self._truncated) and self.area.toPlainText() != self._original

    def save(self) -> tuple[bool, str]:
        if self._truncated:
            return False, "文件过大，已按只读打开"
        text = self.area.toPlainText()
        try:
            self._path.write_text(text, encoding=self._write or "utf-8")
        except (OSError, UnicodeEncodeError) as exc:
            return False, str(exc)
        self._original = text
        self._update_status()
        return True, "已保存"

    # ------------------------------------------------------------ 回调
    def _on_encoding(self, value) -> None:
        if self.is_dirty() and not confirm(self, "切换编码将丢弃修改？", "当前有未保存的修改，切换编码会重新读取文件。"):
            position = self.encoding_box.findText(self._used)
            self.encoding_box.blockSignals(True)
            self.encoding_box.setCurrentIndex(position if position >= 0 else 0)
            self.encoding_box.blockSignals(False)
            return
        self._encoding = "" if str(value) == AUTO_ENCODING else str(value)
        self._load()

    def _on_wrap(self, checked: bool) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if checked else QPlainTextEdit.LineWrapMode.NoWrap
        self.area.setLineWrapMode(mode)

    def _on_changed(self, _text: str) -> None:
        self._update_status()

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self.area.toPlainText())

    def _update_status(self) -> None:
        text = self.area.toPlainText()
        marker = " · 未保存" if self.is_dirty() else ""
        self.status.setText(f"{len(text)} 个字符 · {self.caption}{marker}")

    def closeEvent(self, event):  # noqa: N802 - Qt 命名
        # 外壳负责询问未保存改动，这里不重复拦截
        super().closeEvent(event)


__all__ = ["AUTO_ENCODING", "TextEditor"]
