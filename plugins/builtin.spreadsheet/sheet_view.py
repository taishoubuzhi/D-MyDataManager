"""表格查看器：xlsx / xlsm / csv / tsv 的工作表切换与网格预览。

网格用 builtin.lib.ui 的只读表格工厂，工作表选择用它的下拉工厂。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QTableWidgetItem, QVBoxLayout, QWidget

from app.sdk.data import SheetData, csv_rows, human_size, xlsx_sheets
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    caption,
    combo_box,
    read_only_table,
    status_label,
    toolbar,
)

XLSX_SUFFIXES = ("xlsx", "xlsm")
MAX_ROWS = 300
MAX_COLS = 60


def column_label(index: int) -> str:
    """0 → A、25 → Z、26 → AA。"""
    label = ""
    value = index + 1
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


class SheetViewer(QWidget):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        try:
            self._sheets: list[SheetData] = self._load_sheets()
            self._error = ""
        except Exception as exc:  # 损坏的工作簿不应让查看器崩溃
            self._sheets = []
            self._error = str(exc)
        size = 0
        try:
            size = self._path.stat().st_size
        except OSError:
            pass
        self.caption = f"{self._path.name} · {human_size(size)} · {len(self._sheets)} 个工作表"
        self._build_ui()
        self._show(0)

    def _load_sheets(self) -> list[SheetData]:
        suffix = self._path.suffix.lower().lstrip(".")
        if suffix in XLSX_SUFFIXES:
            return xlsx_sheets(self._path, max_rows=MAX_ROWS, max_cols=MAX_COLS)
        return csv_rows(self._path, max_rows=MAX_ROWS, max_cols=MAX_COLS)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar, row = toolbar(self)
        row.addWidget(caption(bar, "工作表"))
        self._combo = combo_box(
            bar,
            items=tuple(sheet.name for sheet in self._sheets) or ("无可用工作表",),
            width=220,
            on_change=self._show_index,
        )
        self._combo.setEnabled(bool(self._sheets))
        row.addWidget(self._combo)
        row.addStretch(1)
        self.status_label = status_label(bar, self.caption)
        row.addWidget(self.status_label)
        root.addWidget(bar)

        self._table = read_only_table(self)
        root.addWidget(self._table, 1)

        if self._error:
            self.status_label.setText(f"无法读取表格：{self._error}")

    def _show_index(self, index: int) -> None:
        self._show(int(index) if index is not None else -1)

    def _show(self, index: int) -> None:
        if not self._sheets or index < 0 or index >= len(self._sheets):
            self._table.clear()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            return
        sheet = self._sheets[index]
        rows = sheet.rows
        columns = max((len(row) for row in rows), default=0)
        self._table.clear()
        self._table.setColumnCount(columns)
        self._table.setRowCount(len(rows))
        self._table.setHorizontalHeaderLabels([column_label(i) for i in range(columns)])
        self._table.setVerticalHeaderLabels([str(i + 1) for i in range(len(rows))])
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self._table.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        note = "（已截断）" if sheet.truncated else ""
        self.status_label.setText(f"{sheet.name} · {len(rows)} 行 × {columns} 列{note} · {human_size(self._path.stat().st_size)}")
