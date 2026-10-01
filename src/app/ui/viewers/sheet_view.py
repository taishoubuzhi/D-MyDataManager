"""表格查看器：xlsx / xlsm / csv / tsv 的工作表切换与网格预览。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ComboBox, TableWidget

from ...core.viewer_data import SheetData, csv_rows, human_size, xlsx_sheets

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
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("工作表", self))
        self._combo = ComboBox(self)
        self._combo.addItems([sheet.name for sheet in self._sheets] or ["无可用工作表"])
        self._combo.setFixedWidth(220)
        self._combo.setEnabled(bool(self._sheets))
        self._combo.currentIndexChanged.connect(self._show)
        bar.addWidget(self._combo)
        bar.addStretch(1)
        self.status_label = CaptionLabel(self.caption, self)
        bar.addWidget(self.status_label)
        root.addLayout(bar)

        self._table = TableWidget(self)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setWordWrap(False)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        root.addWidget(self._table, 1)

        if self._error:
            self.status_label.setText(f"无法读取表格：{self._error}")

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
