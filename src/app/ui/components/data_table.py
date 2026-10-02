"""表格通用辅助：表头拖动、列宽自适应与 Excel 式列筛选栏。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QGridLayout, QHeaderView, QTableWidgetItem, QWidget
from qfluentwidgets import ComboBox, LineEdit, TableWidget

from ..framework import release_widget

FILTER_ALL = "全部"


class TableFilterBar(QWidget):
    """贴在表格上方的筛选栏：每个列一个控件，文本列模糊匹配、选项列精确匹配。"""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(4)
        self._editors: dict[str, tuple[str, QWidget]] = {}
        self._columns = 0

    # ------------------------------------------------------------------ 构建
    def configure(self, columns: Sequence[tuple[str, str, str]]) -> None:
        """按 (键, 显示名, 类型) 重建筛选栏，类型取 `text` 或 `choice`。"""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                release_widget(widget)
        self._editors.clear()
        self._columns = len(columns)
        for index, (key, label, kind) in enumerate(columns):
            if kind == "choice":
                editor = ComboBox(self)
                editor.addItem(FILTER_ALL, userData="")
                editor.currentIndexChanged.connect(lambda _=0: self.changed.emit())
            else:
                editor = LineEdit(self)
                editor.setPlaceholderText(f"筛选{label}")
                editor.setClearButtonEnabled(True)
                editor.textChanged.connect(lambda _= "": self.changed.emit())
            self._layout.addWidget(editor, 0, index)
            self._layout.setColumnStretch(index, 1)
            self._editors[key] = (kind, editor)

    def set_options(self, key: str, values: Iterable[str]) -> None:
        """设置选项列的候选项（保留当前选择）。"""
        entry = self._editors.get(key)
        if entry is None or entry[0] != "choice":
            return
        editor: ComboBox = entry[1]  # type: ignore[assignment]
        current = editor.currentData()
        editor.blockSignals(True)
        editor.clear()
        editor.addItem(FILTER_ALL, userData="")
        for value in values:
            editor.addItem(str(value), userData=str(value))
        for index in range(editor.count()):
            if editor.itemData(index) == current:
                editor.setCurrentIndex(index)
                break
        editor.blockSignals(False)

    # ------------------------------------------------------------------ 读取
    def filters(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, (kind, editor) in self._editors.items():
            if kind == "choice":
                text = str(editor.currentData() or "")
            else:
                text = editor.text().strip()
            if text:
                values[key] = text
        return values

    def set_filter(self, key: str, value: str) -> None:
        """以编程方式设置某个筛选值（会同步触发 changed 信号）。"""
        entry = self._editors.get(key)
        if entry is None:
            return
        kind, editor = entry
        if kind == "choice":
            wanted = str(value or "")
            for index in range(editor.count()):
                if str(editor.itemData(index) or "") == wanted:
                    editor.setCurrentIndex(index)
                    return
            editor.setCurrentIndex(0)
        else:
            editor.setText(str(value or ""))

    def reset(self) -> None:
        """清空所有筛选条件；有变化时触发 changed，让调用方重新过滤。"""
        changed = False
        for kind, editor in self._editors.values():
            editor.blockSignals(True)
            if kind == "choice":
                if editor.currentIndex() != 0:
                    changed = True
                editor.setCurrentIndex(0)
            else:
                if editor.text():
                    changed = True
                editor.clear()
            editor.blockSignals(False)
        if changed:
            self.changed.emit()


def check_cell(checked: bool = False) -> QTableWidgetItem:
    """可勾选的单元格：带复选框、仍可整行选中，供批量操作使用。"""
    cell = QTableWidgetItem("")
    cell.setFlags(
        Qt.ItemFlag.ItemIsEnabled
        | Qt.ItemFlag.ItemIsSelectable
        | Qt.ItemFlag.ItemIsUserCheckable
    )
    cell.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return cell


def prepare_table(
    table: TableWidget,
    *,
    movable: bool = True,
    stretch: Mapping[int, int] | None = None,
) -> None:
    """统一表格行为：隐藏行号、单选整行、表头可拖动、可编辑关闭。"""
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setAlternatingRowColors(False)
    head = table.horizontalHeader()
    head.setSectionsMovable(movable)
    head.setStretchLastSection(False)
    head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for column, width in (stretch or {}).items():
        table.setColumnWidth(column, width)


def fit_columns(
    table: TableWidget,
    *,
    min_width: int = 72,
    max_width: int = 260,
    weights: Mapping[int, float] | None = None,
) -> None:
    """按内容自适应列宽：先量内容，再夹到 [min, max]，权重列吃剩余宽度。"""
    table.resizeColumnsToContents()
    for column in range(table.columnCount()):
        width = max(min_width, min(max_width, table.columnWidth(column) + 24))
        table.setColumnWidth(column, width)
    weights = weights or {}
    if weights:
        available = max(0, table.viewport().width() - sum(
            table.columnWidth(column) for column in range(table.columnCount())
        ))
        for column, weight in weights.items():
            table.setColumnWidth(column, table.columnWidth(column) + int(available * weight))


def match_filters(values: Mapping[str, str], filters: Mapping[str, str]) -> bool:
    """判断一行是否命中全部筛选条件：忽略大小写的子串匹配，空条件表示不筛选。"""
    for key, wanted in filters.items():
        text = str(wanted or "").strip().lower()
        if not text:
            continue
        if text not in str(values.get(key, "")).strip().lower():
            return False
    return True


def column_values(table: TableWidget, column: int) -> list[str]:
    """收集某列去重后的文本（筛选候选项用）。"""
    seen: list[str] = []
    for row in range(table.rowCount()):
        item = table.item(row, column)
        text = item.text() if item is not None else ""
        if text and text not in seen:
            seen.append(text)
    return seen


__all__ = [
    "FILTER_ALL",
    "TableFilterBar",
    "check_cell",
    "prepare_table",
    "fit_columns",
    "match_filters",
    "column_values",
]
