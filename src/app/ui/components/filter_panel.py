"""筛选面板：搜索、类型、标签、关键词、分类、排序与回收站。

每个筛选分组（类型 / 标签 / 关键词 / 分类）都是一张卡片：标题栏带折叠按钮、选项计数、
搜索框（只显示匹配的选项）与三态全选框（空 = 全不选，横杠 = 部分选中，勾 = 全选），
三态框与分组内的勾选状态双向同步。分组内的选项区独立滚动、高度受控，整栏可整体滚动。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    SearchLineEdit,
    TransparentToolButton,
)

from ...core.config import config
from ...db.models import DATA_TYPE_NAMES, DataType
from ..framework import (
    align_check_box,
    COMPACT_MARGINS,
    IconTextButton,
    IconTextLabel,
    clear_scroll_background,
    icon_label,
    release_widget,
)

SORT_OPTIONS: list[tuple[str, str, bool]] = [
    ("最新导入", "created_at", True),
    ("最早导入", "created_at", False),
    ("最近修改", "updated_at", True),
    ("名称", "name", False),
    ("大小", "size", True),
]

SECTION_BODY_HEIGHT = 116
SECTION_SEARCH_WIDTH = 92


class FilterSection(CardWidget):
    """单个筛选分组卡片：可折叠、可滑动、可搜索，标题栏右侧是三态全选框。"""

    changed = pyqtSignal()
    collapsedChanged = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None, collapsed: bool = False, icon=None) -> None:
        super().__init__(parent)
        self._boxes: dict = {}
        self._groups: list[tuple[str, list]] | None = None
        self._group_labels: dict[str, CaptionLabel] = {}
        self._syncing = False
        self._collapsed = bool(collapsed)

        self.toggle_button = TransparentToolButton(FluentIcon.CHEVRON_DOWN_MED, self)
        self.toggle_button.setFixedSize(22, 22)
        self.toggle_button.setToolTip("展开 / 折叠")
        self.toggle_button.clicked.connect(self._toggle_body)
        self.title_label = IconTextLabel(icon, title, self, strong=True)
        self.count_label = CaptionLabel("", self)
        self.count_label.setToolTip("已选 / 全部")

        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索")
        self.search.setFixedWidth(SECTION_SEARCH_WIDTH)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._rebuild())

        self.all_box = CheckBox(self)
        self.all_box.setTristate(True)
        self.all_box.setToolTip("全选 / 全不选")
        self.all_box.stateChanged.connect(self._on_all_state)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(self.toggle_button)
        header.addWidget(self.title_label)
        header.addWidget(self.count_label)
        header.addStretch(1)
        header.addWidget(self.search)
        header.addWidget(self.all_box)

        self.body = QWidget(self)
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.body)
        clear_scroll_background(self.scroll)
        self.scroll.setFixedHeight(SECTION_BODY_HEIGHT)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._empty = CaptionLabel("没有匹配的选项", self.body)
        self._empty.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(6)
        root.addLayout(header)
        root.addWidget(self.scroll)
        self._apply_collapsed()

    # ------------------------------------------------------------------ 选项
    @property
    def boxes(self) -> dict:
        return self._boxes

    def set_items(self, items: list[tuple[object, str]]) -> None:
        """按 (键, 显示名) 更新选项，保留已有的勾选状态。"""
        self.set_groups([("", list(items))])

    def set_groups(self, groups: list[tuple[str, list[tuple[object, str]]]]) -> None:
        """按「分组标题 + 选项」更新选项：标题非空时在选项上方单独占一行。

        用来表达层级（比如分类按「用户名 / 分类」两层展开）；只传一个空标题的分组时
        与 `set_items()` 等价，勾选状态同样按 (键) 保留。
        """
        wanted = [(key, label) for _title, items in groups for key, label in items]
        keys = {key for key, _label in wanted}
        for key in [key for key in self._boxes if key not in keys]:
            box = self._boxes.pop(key)
            release_widget(box)
        for key, label in wanted:
            box = self._boxes.get(key)
            if box is None:
                box = CheckBox(label, self.body)
                box.stateChanged.connect(self._on_box_state)
                self._boxes[key] = box
            elif box.text() != label:
                box.setText(label)
        self._groups = [
            (str(title), [key for key, _label in items]) for title, items in groups
        ]
        self._rebuild()

    def checked_keys(self) -> set:
        return {
            key
            for key, box in self._boxes.items()
            if box.checkState() == Qt.CheckState.Checked
        }

    def clear(self) -> None:
        """清空搜索与全部勾选（不发信号）。"""
        self.search.clear()
        self._syncing = True
        for box in self._boxes.values():
            box.setChecked(False)
        self._syncing = False
        self._sync_all()

    # ------------------------------------------------------------------ 内部
    def _rebuild(self) -> None:
        query = self.search.text().strip().lower()
        while self.grid.count():
            self.grid.takeAt(0)
        groups = self._groups or [("", list(self._boxes))]
        shown = 0
        row = 0
        used: set[str] = set()
        for title, keys in groups:
            boxes = [self._boxes[key] for key in keys if key in self._boxes]
            visible = [box for box in boxes if not query or query in box.text().lower()]
            for box in boxes:
                box.setVisible(box in visible)
            if not visible:
                continue
            if title:
                label = self._group_label(title)
                used.add(title)
                label.setVisible(True)
                self.grid.addWidget(label, row, 0, 1, 2)
                row += 1
            for index, box in enumerate(visible):
                self.grid.addWidget(box, row + index // 2, index % 2)
            row += (len(visible) + 1) // 2
            shown += len(visible)
        for title, label in self._group_labels.items():
            if title not in used:
                label.hide()
        if shown or not self._boxes:
            self._empty.hide()
        else:
            self._empty.setVisible(True)
            self.grid.addWidget(self._empty, 0, 0, 1, 2)
        self._sync_all()

    def _group_label(self, title: str) -> CaptionLabel:
        """分组标题（复用一个控件，避免反复重建）。"""
        label = self._group_labels.get(title)
        if label is None:
            label = CaptionLabel(title, self.body)
            self._group_labels[title] = label
        return label

    def _sync_all(self) -> None:
        """让三态框反映分组内的勾选情况：全不选 / 部分选中 / 全选。"""
        total = len(self._boxes)
        checked = len(self.checked_keys())
        self.count_label.setText(f"{checked}/{total}")
        if total == 0 or checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        if self.all_box.checkState() == state:
            return
        self._syncing = True
        self.all_box.setCheckState(state)
        self._syncing = False

    def _on_box_state(self, _state: int) -> None:
        if self._syncing:
            return
        self._sync_all()
        self.changed.emit()

    def _on_all_state(self, state: int) -> None:
        if self._syncing:
            return
        self._set_all(Qt.CheckState(state) != Qt.CheckState.Unchecked)

    def _set_all(self, checked: bool) -> None:
        self._syncing = True
        for box in self._boxes.values():
            box.setChecked(checked)
        self._syncing = False
        self._sync_all()
        self.changed.emit()

    @property
    def collapsed(self) -> bool:
        """内容区是否处于折叠状态。"""
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """设置折叠状态：用户点击与配置回填共用同一条路径。"""
        if self._collapsed == bool(collapsed):
            return
        self._collapsed = bool(collapsed)
        self._apply_collapsed()
        self.collapsedChanged.emit()

    def _toggle_body(self) -> None:
        """折叠 / 展开分组内容（状态独立于页面是否可见）。"""
        self.set_collapsed(not self._collapsed)

    def _apply_collapsed(self) -> None:
        self.scroll.setVisible(not self._collapsed)
        self.toggle_button.setIcon(
            FluentIcon.CHEVRON_RIGHT_MED if self._collapsed else FluentIcon.CHEVRON_DOWN_MED
        )
        self.toggle_button.setToolTip("展开筛选" if self._collapsed else "折叠筛选")


class FilterPanel(QWidget):
    changed = pyqtSignal()
    collapsedChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, expanded: Iterable[str] | None = None) -> None:
        super().__init__(parent)
        opened = self._initial_expanded() if expanded is None else {str(key) for key in expanded}
        self.type_section = FilterSection("类型", self, collapsed="type" not in opened, icon=FluentIcon.TILES)
        self.tag_section = FilterSection("标签", self, collapsed="tag" not in opened, icon=FluentIcon.TAG)
        self.keyword_section = FilterSection("关键词", self, collapsed="keyword" not in opened, icon=FluentIcon.FONT)
        for section in self.sections():
            section.changed.connect(self.changed.emit)
            section.collapsedChanged.connect(self.collapsedChanged.emit)

        self._type_boxes = self.type_section.boxes
        self._tag_boxes = self.tag_section.boxes
        self._keyword_boxes = self.keyword_section.boxes

        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("全文检索：名称、内容、关键词（空格分隔多个词）")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.changed.emit())

        self.sort_box = ComboBox(self)
        for label, _key, _desc in SORT_OPTIONS:
            self.sort_box.addItem(label)
        self.sort_box.setCurrentIndex(0)
        self.sort_box.setMinimumWidth(120)
        self.sort_box.currentIndexChanged.connect(lambda _index: self.changed.emit())

        self.hidden_box = CheckBox("显示隐藏项", self)
        self.trash_box = CheckBox("只看回收站", self)
        self.hidden_box.stateChanged.connect(lambda _state: self.changed.emit())
        self.trash_box.stateChanged.connect(lambda _state: self.changed.emit())
        # 指示器与同一张卡片里 IconTextLabel 的图标列对齐（否则两行文字差 5 px）
        align_check_box(self.hidden_box)
        align_check_box(self.trash_box)

        self.reset_button = IconTextButton(FluentIcon.SYNC, "重置筛选", self)
        self.reset_button.setToolTip("清空全部筛选条件")
        self.reset_button.clicked.connect(self.reset)

        options_card = CardWidget(self)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(10, 8, 10, 10)
        options_layout.setSpacing(6)
        options_layout.addWidget(IconTextLabel(FluentIcon.LAYOUT, "范围与排序", options_card, strong=True))

        sort_row = QWidget(options_card)
        sort_layout = QHBoxLayout(sort_row)
        sort_layout.setContentsMargins(0, 0, 0, 0)
        sort_layout.setSpacing(6)
        sort_layout.addWidget(icon_label(FluentIcon.MENU, "排序", sort_row))
        sort_layout.addStretch(1)
        sort_layout.addWidget(self.sort_box)
        options_layout.addWidget(sort_row)
        options_layout.addWidget(self.hidden_box)
        options_layout.addWidget(self.trash_box)
        options_layout.addWidget(self.reset_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.search)
        for section in self.sections():
            layout.addWidget(section)
        layout.addWidget(options_card)
        layout.addStretch(1)

        self.set_options()

    # ------------------------------------------------------------------ 选项
    def sections(self) -> tuple[FilterSection, ...]:
        return (
            self.type_section,
            self.tag_section,
            self.keyword_section,
        )

    def section_keys(self) -> dict[str, FilterSection]:
        """分组键 → 分组卡片：配置里记的就是这些键。"""
        return {"type": self.type_section, "tag": self.tag_section, "keyword": self.keyword_section}

    def expanded_keys(self) -> list[str]:
        """当前展开着的分组键（写回配置用）。"""
        return [key for key, section in self.section_keys().items() if not section.collapsed]

    @staticmethod
    def _initial_expanded() -> set[str]:
        """筛选分组默认全部折叠；只有配置里记着展开过，启动时才展开。"""
        value = config.expandedFilters.value
        if isinstance(value, (list, tuple, set)):
            return {str(key) for key in value}
        return set()

    def set_options(
        self,
        tags: list[str] | None = None,
        keywords: list[str] | None = None,
        global_tags: set[str] | None = None,
    ) -> None:
        self._fill_types()
        global_names = set(global_tags or ())
        self.tag_section.set_items(
            [
                (name, f"{name}（全局）" if name in global_names else str(name))
                for name in sorted(tags or [])
            ]
        )
        self.keyword_section.set_items([(name, str(name)) for name in sorted(keywords or [])])

    def _fill_types(self) -> None:
        if self._type_boxes:
            return
        self.type_section.set_items(
            [(data_type.value, DATA_TYPE_NAMES[data_type]) for data_type in DataType]
        )

    # ------------------------------------------------------------------ 取值
    def text(self) -> str:
        return self.search.text().strip()

    def selected_types(self) -> set[str]:
        return self.type_section.checked_keys()

    def selected_tags(self) -> set[str]:
        return self.tag_section.checked_keys()

    def selected_keywords(self) -> set[str]:
        return self.keyword_section.checked_keys()


    def show_hidden(self) -> bool:
        return self.hidden_box.isChecked()

    def only_trash(self) -> bool:
        return self.trash_box.isChecked()

    def sort_option(self) -> tuple[str, bool]:
        index = max(0, self.sort_box.currentIndex())
        _label, key, descending = SORT_OPTIONS[index]
        return key, descending

    def reset(self) -> None:
        self.search.clear()
        for section in self.sections():
            section.clear()
        self.hidden_box.setChecked(False)
        self.trash_box.setChecked(False)
        self.sort_box.setCurrentIndex(0)
        self.changed.emit()


__all__ = ["FilterPanel", "FilterSection", "SECTION_BODY_HEIGHT", "SORT_OPTIONS"]
