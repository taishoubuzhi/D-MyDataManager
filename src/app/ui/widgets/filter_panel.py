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
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    TransparentToolButton,
)

from ...db.models import DATA_TYPE_NAMES, DataType

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

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boxes: dict = {}
        self._syncing = False
        self._collapsed = False

        self.toggle_button = TransparentToolButton(FluentIcon.CHEVRON_DOWN_MED, self)
        self.toggle_button.setFixedSize(22, 22)
        self.toggle_button.setToolTip("展开 / 折叠")
        self.toggle_button.clicked.connect(self._toggle_body)
        self.title_label = StrongBodyLabel(title, self)
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
        self.scroll.setFixedHeight(SECTION_BODY_HEIGHT)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._empty = CaptionLabel("没有匹配的选项", self.body)
        self._empty.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        root.addLayout(header)
        root.addWidget(self.scroll)

    # ------------------------------------------------------------------ 选项
    @property
    def boxes(self) -> dict:
        return self._boxes

    def set_items(self, items: list[tuple[object, str]]) -> None:
        """按 (键, 显示名) 更新选项，保留已有的勾选状态。"""
        wanted = list(items)
        keys = {key for key, _label in wanted}
        for key in [key for key in self._boxes if key not in keys]:
            box = self._boxes.pop(key)
            box.setParent(None)
            box.deleteLater()
        for key, label in wanted:
            box = self._boxes.get(key)
            if box is None:
                box = CheckBox(label, self.body)
                box.stateChanged.connect(self._on_box_state)
                self._boxes[key] = box
            elif box.text() != label:
                box.setText(label)
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
        shown: list[CheckBox] = []
        for box in self._boxes.values():
            visible = not query or query in box.text().lower()
            box.setVisible(visible)
            if visible:
                shown.append(box)
        for index, box in enumerate(shown):
            self.grid.addWidget(box, index // 2, index % 2)
        if shown or not self._boxes:
            self._empty.hide()
        else:
            self._empty.setVisible(True)
            self.grid.addWidget(self._empty, 0, 0, 1, 2)
        self._sync_all()

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

    def _toggle_body(self) -> None:
        """折叠 / 展开分组内容（状态独立于页面是否可见）。"""
        self._collapsed = not self._collapsed
        self.scroll.setVisible(not self._collapsed)
        self.toggle_button.setIcon(
            FluentIcon.CHEVRON_RIGHT_MED if self._collapsed else FluentIcon.CHEVRON_DOWN_MED
        )


class FilterPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.type_section = FilterSection("类型", self)
        self.tag_section = FilterSection("标签", self)
        self.keyword_section = FilterSection("关键词", self)
        self.category_section = FilterSection("分类", self)
        for section in self.sections():
            section.changed.connect(self.changed.emit)

        self._type_boxes = self.type_section.boxes
        self._tag_boxes = self.tag_section.boxes
        self._keyword_boxes = self.keyword_section.boxes
        self._category_boxes = self.category_section.boxes

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

        self.reset_button = PushButton("重置筛选", self)
        self.reset_button.setToolTip("清空全部筛选条件")
        self.reset_button.clicked.connect(self.reset)

        options_card = CardWidget(self)
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(10, 8, 10, 10)
        options_layout.setSpacing(6)
        options_layout.addWidget(StrongBodyLabel("范围与排序", options_card))

        sort_row = QWidget(options_card)
        sort_layout = QHBoxLayout(sort_row)
        sort_layout.setContentsMargins(0, 0, 0, 0)
        sort_layout.setSpacing(6)
        sort_layout.addWidget(CaptionLabel("排序", sort_row))
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
            self.category_section,
        )

    def set_options(
        self,
        tags: list[str] | None = None,
        categories: list[tuple[int, str]] | None = None,
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
        self.category_section.set_items(
            [(int(category_id), label) for category_id, label in (categories or [])]
        )

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

    def selected_categories(self) -> set[int]:
        return self.category_section.checked_keys()

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
