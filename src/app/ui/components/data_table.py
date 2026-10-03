"""表格通用辅助：表头拖动、列宽自适应与 Excel 式列筛选栏。

筛选栏支持四种列类型：`text`（子串匹配）、`choice`（精确匹配）、
`number`（等于 / 大于 / 小于 / 区间四种模式）与 `date`（按天筛选：在该日 / 在该日之后 /
在该日之前 / 区间）。数值与时间条件在内部统一成闭开区间 `(下限, 含下限, 上限, 含上限)`，
交给 `match_filters()` 比较。

控件按 `FlowLayout` 流式排布（每个字段是「小标题 + 控件」的一整块），窗口变窄时自动折行，
不会再出现固定两行、长短不齐的栅格。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QDate, QDateTime, QTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QWidget,
)
from qfluentwidgets import CaptionLabel, ComboBox, DatePicker, LineEdit, TableWidget

from ..framework import release_widget
from .flow_layout import FlowLayout

FILTER_ALL = "全部"

#: 筛选栏里所有控件统一高度，避免下拉框 / 日期选择器与输入框混排时参差不齐。
FIELD_HEIGHT = 32

#: 数值列的匹配模式：(显示名, 模式键)，空键表示不筛选。
NUMBER_MODES = (
    ("不限", ""),
    ("等于", "eq"),
    ("大于", "gt"),
    ("小于", "lt"),
    ("区间", "between"),
)

#: 时间列的匹配模式：按天筛选，区间含首尾两天。
DATE_MODES = (
    ("不限", ""),
    ("在该日", "day"),
    ("在该日之后", "after"),
    ("在该日之前", "before"),
    ("区间", "range"),
)

_SIZE_FACTORS = {
    "": 1,
    "b": 1,
    "字节": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}
_SIZE_PATTERN = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(b|kb|mb|gb|tb|k|m|g|t|字节)?\s*$", re.IGNORECASE)

_SECONDS_PER_DAY = 86400.0


def parse_number(text: str, unit: str = "") -> float | None:
    """把输入框文本解析成数值；`unit == "size"` 时接受 `512KB` / `1.5MB` 这类后缀。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if unit != "size":
        try:
            return float(raw)
        except ValueError:
            return None
    match = _SIZE_PATTERN.match(raw)
    if match is None:
        return None
    return float(match.group(1)) * _SIZE_FACTORS[(match.group(2) or "").lower()]


def hit_range(value: object, spec: Sequence) -> bool:
    """判断数值是否落在筛选区间内。

    `spec` 是 `(下限, 是否含下限, 上限, 是否含上限)`，两侧都可以是 `None`（表示不限）；
    行取不到数值（比如去重率显示为「—」）时一律不命中。
    """
    low, low_inclusive, high, high_inclusive = spec
    if low is None and high is None:
        return True
    if value is None:
        return False
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if low is not None and (number < float(low) or (number == float(low) and not low_inclusive)):
        return False
    if high is not None and (number > float(high) or (number == float(high) and not high_inclusive)):
        return False
    return True


def day_start(value: QDate) -> float:
    """某个日期 00:00:00 的时间戳（秒）。"""
    return float(QDateTime(value, QTime(0, 0)).toSecsSinceEpoch())


@dataclass
class _Field:
    """一列筛选控件：按类型复用同一个结构，未用到的部件保持 None。"""

    kind: str
    widget: QWidget
    unit: str = ""
    combo: ComboBox | None = None
    line: LineEdit | None = None
    line2: LineEdit | None = None
    picker: DatePicker | None = None
    picker2: DatePicker | None = None


class TableFilterBar(QWidget):
    """贴在表格上方的筛选栏：每列一个控件，文本模糊匹配、选项精确匹配、数值可比较。"""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = FlowLayout(self, spacing=8)
        self._fields: dict[str, _Field] = {}
        self._columns = 0

    # ------------------------------------------------------------------ 构建
    def configure(self, columns: Sequence[Sequence]) -> None:
        """按 `(键, 显示名, 类型[, 选项])` 重建筛选栏。

        类型取 `text` / `choice` / `number` / `date`；选项字典目前只用到 `unit`
        （number 的取值单位，`size` 表示接受 KB/MB 后缀、`percent` 表示百分比）。
        控件宽度固定，放不下时由 `FlowLayout` 自动折行。
        """
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                release_widget(widget)
        self._fields.clear()
        self._columns = len(columns)
        for spec in columns:
            key, label, kind = str(spec[0]), str(spec[1]), str(spec[2])
            options = dict(spec[3]) if len(spec) > 3 and spec[3] else {}
            field = self._build_field(label, kind, options)
            self._layout.addWidget(field.widget)
            self._fields[key] = field

    def _box(self, label: str, editors: Sequence[QWidget]) -> QWidget:
        """把一个字段包成「小标题 + 控件」的整体，作为流式布局里的一个单元。

        控件统一高度：下拉框与日期选择器默认比输入框矮，混排会出现参差不齐的边。
        """
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(CaptionLabel(label, holder))
        for editor in editors:
            editor.setFixedHeight(FIELD_HEIGHT)
            layout.addWidget(editor)
        return holder

    def _build_field(self, label: str, kind: str, options: Mapping) -> _Field:
        if kind == "choice":
            combo = ComboBox(self)
            combo.addItem(FILTER_ALL, userData="")
            combo.setFixedWidth(120)
            combo.currentIndexChanged.connect(lambda _index=0: self.changed.emit())
            return _Field(kind, self._box(label, [combo]), combo=combo)
        if kind == "number":
            return self._build_number(label, str(options.get("unit", "")))
        if kind == "date":
            return self._build_date(label)
        line = LineEdit(self)
        line.setFixedWidth(150)
        line.setPlaceholderText("包含…")
        line.setClearButtonEnabled(True)
        line.setToolTip(f"按「{label}」模糊筛选")
        line.textChanged.connect(lambda _text="": self.changed.emit())
        return _Field(kind, self._box(label, [line]), line=line)

    def _build_number(self, label: str, unit: str) -> _Field:
        combo = ComboBox(self)
        for text, mode in NUMBER_MODES:
            combo.addItem(text, userData=mode)
        combo.setFixedWidth(72)
        combo.setToolTip(f"「{label}」的匹配方式：大于 / 小于是单侧条件，区间要两个数都填")
        first = LineEdit(self)
        second = LineEdit(self)
        if unit == "size":
            hint, placeholder = "支持 512KB / 1.5MB，纯数字按字节", "如 1.5MB"
        elif unit == "percent":
            hint, placeholder = "按百分比数字筛选，如 30 表示 30%", "如 30"
        else:
            hint, placeholder = "只填数字", "数值"
        for line, place in ((first, placeholder), (second, "上限")):
            line.setFixedWidth(96)
            line.setPlaceholderText(place)
            line.setClearButtonEnabled(True)
            line.setToolTip(f"「{label}」{hint}")
            line.textChanged.connect(lambda _text="": self.changed.emit())
        second.hide()
        combo.currentIndexChanged.connect(lambda _index=0: self._sync_number(combo, second))
        holder = self._box(label, [combo, first, second])
        return _Field("number", holder, unit=unit, combo=combo, line=first, line2=second)

    def _build_date(self, label: str) -> _Field:
        combo = ComboBox(self)
        for text, mode in DATE_MODES:
            combo.addItem(text, userData=mode)
        combo.setFixedWidth(96)
        combo.setToolTip(f"「{label}」按天筛选：在该日之后是所选日期次日往后，区间含首尾两天")
        first = DatePicker(self)
        second = DatePicker(self)
        first.setFixedWidth(120)
        second.setFixedWidth(120)
        second.hide()
        for picker in (first, second):
            picker.dateChanged.connect(lambda *_args: self.changed.emit())
        combo.currentIndexChanged.connect(lambda _index=0: self._sync_date(combo, second))
        holder = self._box(label, [combo, first, second])
        return _Field("date", holder, combo=combo, picker=first, picker2=second)

    def _sync_number(self, combo: ComboBox, second: LineEdit) -> None:
        second.setVisible(str(combo.currentData() or "") == "between")
        self.changed.emit()

    def _sync_date(self, combo: ComboBox, second: DatePicker) -> None:
        second.setVisible(str(combo.currentData() or "") == "range")
        self.changed.emit()

    def set_options(self, key: str, values: Iterable[str]) -> None:
        """设置选项列的候选项（保留当前选择）。"""
        entry = self._fields.get(key)
        if entry is None or entry.kind != "choice":
            return
        editor: ComboBox = entry.combo  # type: ignore[assignment]
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
    def filters(self) -> dict[str, object]:
        """当前生效的筛选条件；文本是字符串、选项是字符串、数值与时间是比较区间。"""
        values: dict[str, object] = {}
        for key, field in self._fields.items():
            value = self._field_value(field)
            if value is not None:
                values[key] = value
        return values

    def has_filters(self) -> bool:
        """是否有任意条件生效（调用方常用它决定按钮文字与作用域）。"""
        return bool(self.filters())

    def _field_value(self, field: _Field) -> object | None:
        if field.kind == "choice":
            return str(field.combo.currentData() or "") or None
        if field.kind == "number":
            mode = str(field.combo.currentData() or "")
            if not mode:
                return None
            first = parse_number(field.line.text(), field.unit)
            if mode == "eq":
                return (first, True, first, True) if first is not None else None
            if mode == "gt":
                return (first, False, None, False) if first is not None else None
            if mode == "lt":
                return (None, False, first, False) if first is not None else None
            second = parse_number(field.line2.text(), field.unit)
            if first is None and second is None:
                return None
            return (first, False, second, False)
        if field.kind == "date":
            mode = str(field.combo.currentData() or "")
            if not mode:
                return None
            start = day_start(field.picker.getDate())
            if mode == "day":
                return (start, True, start + _SECONDS_PER_DAY, False)
            if mode == "after":
                return (start + _SECONDS_PER_DAY, False, None, False)
            if mode == "before":
                return (None, False, start, False)
            other = day_start(field.picker2.getDate())
            return (min(start, other), True, max(start, other) + _SECONDS_PER_DAY, False)
        return field.line.text().strip() or None

    def set_filter(self, key: str, value: object) -> None:
        """以编程方式设置某个筛选值（会同步触发 changed 信号）。"""
        field = self._fields.get(key)
        if field is None:
            return
        if field.kind == "choice":
            wanted = str(value or "")
            for index in range(field.combo.count()):
                if str(field.combo.itemData(index) or "") == wanted:
                    field.combo.setCurrentIndex(index)
                    return
            field.combo.setCurrentIndex(0)
        elif field.kind == "number":
            text = str(value or "").strip()
            field.combo.setCurrentIndex(1 if text else 0)  # 1 = 等于
            field.line.setText(text)
        elif field.kind == "date":
            text = str(value or "").strip()
            if not text:
                field.combo.setCurrentIndex(0)
                return
            field.picker.setDate(QDate.fromString(text[:10], "yyyy-MM-dd"))
            field.combo.setCurrentIndex(1)  # 1 = 在该日
        else:
            field.line.setText(str(value or ""))

    def reset(self) -> None:
        """清空所有筛选条件；有变化时触发 changed，让调用方重新过滤。"""
        changed = False
        for field in self._fields.values():
            if field.combo is not None:
                field.combo.blockSignals(True)
                if field.combo.currentIndex() != 0:
                    changed = True
                field.combo.setCurrentIndex(0)
                field.combo.blockSignals(False)
            for line in (field.line, field.line2):
                if line is None:
                    continue
                line.blockSignals(True)
                if line.text():
                    changed = True
                line.clear()
                line.blockSignals(False)
            if field.line2 is not None and not field.line2.isHidden():
                field.line2.hide()
            if field.picker2 is not None and not field.picker2.isHidden():
                field.picker2.hide()
                changed = True
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


def match_filters(
    values: Mapping[str, str],
    filters: Mapping[str, object],
    numbers: Mapping[str, object] | None = None,
    sets: Mapping[str, Iterable[str]] | None = None,
) -> bool:
    """判断一行是否命中全部筛选条件。

    文本条件忽略大小写做子串匹配；数值 / 时间条件是比较区间
    （`(下限, 含下限, 上限, 含上限)`，见 `hit_range()`）；集合条件要求该行的集合与
    条件有交集（行本身不是集合时按单值是否在集合里判断）。
    """
    for key, wanted in filters.items():
        if isinstance(wanted, tuple):
            if not hit_range(numbers.get(key) if numbers else None, wanted):
                return False
            continue
        if isinstance(wanted, (set, frozenset)):
            row = sets.get(key) if sets else None
            if row is None:
                if str(values.get(key, "")) not in wanted:
                    return False
            elif not (set(row) & set(wanted)):
                return False
            continue
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
    "NUMBER_MODES",
    "DATE_MODES",
    "TableFilterBar",
    "check_cell",
    "prepare_table",
    "fit_columns",
    "match_filters",
    "hit_range",
    "parse_number",
    "day_start",
    "column_values",
]
