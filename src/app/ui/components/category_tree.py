"""分类树：左侧导航用的可操作分类树。"""

from __future__ import annotations

import unicodedata

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QAbstractItemView, QTreeWidgetItem, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu, TreeWidget

from ...core.config import config
from ...services import CategoryNode, is_uncategorized

ALL_ID = None
FIXED_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2
ITEM_ROLE = Qt.ItemDataRole.UserRole + 3
KIND_ALL = "all"
KIND_CATEGORY = "category"
KIND_FILE = "file"
FIXED_SUFFIX = "（固定）"
FIXED_TIP = "系统分类：不能重命名、删除，也不能创建子分类"

#: 分类排序方式 → 显示名（顺序就是下拉框里的顺序）
SORT_MODES: tuple[tuple[str, str], ...] = (
    ("default", "默认顺序"),
    ("name", "按名称"),
    ("count", "按数据量"),
    ("latest", "按最新导入"),
)
SORT_KEYS = tuple(key for key, _label in SORT_MODES)
SORT_LABELS = dict(SORT_MODES)

#: 排序配置在配置里的键名（声明在 `app.core.config.Config`）：`Layout/Category-Sort-Mode` 与
#: `Layout/Category-Sort-Reverse`，读写走 `config.categorySortMode` / `config.categorySortReverse`
CATEGORY_SORT_MODE = "Layout/Category-Sort-Mode"
CATEGORY_SORT_REVERSE = "Layout/Category-Sort-Reverse"


def _sort_key(mode: str, reverse: bool, *, label: str, count: int, latest: str):
    """某个分类排序方式对应的排序键（一律是升序键，逆序由调用方 `reverse=True` 实现）。"""
    if mode == "count":
        return (int(count), label.lower())
    if mode == "latest":
        return (latest, label.lower())
    return (label.lower(), int(count))


def _latest_key(value) -> str:
    """最新导入时间的排序键：时间越晚字符串越大，直接用倒序比较。"""
    if value is None:
        return ""
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%Y%m%d%H%M%S%f")
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


#: 分类名 / 文件名每行最多显示多少个「显示宽度单位」（中日韩全角字算 2，其余算 1）。
#: 分类栏宽度固定 240 px，扣掉缩进与复选框后大约能放下这么多；配合 `setWordWrap(True)`
#: 一起用——有些 Qt 平台 / 样式下 `QTreeView` 不理会 `setWordWrap`，自己折行能保证长名字
#: 一定看得全，而不是被省略号截断（用户 m00003 第 1 条）。
LABEL_WRAP_UNITS = 22


def _wrap_label(text: str, limit: int = LABEL_WRAP_UNITS) -> str:
    """按显示宽度折行（全角算 2、半角算 1），太长的一行拆成多行而不是截断。"""
    if not text:
        return text
    lines: list[str] = []
    current = ""
    width = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if current and width + char_width > limit:
            lines.append(current)
            current = ""
            width = 0
        current += char
        width += char_width
    lines.append(current)
    return "\n".join(lines)


def category_label(node: CategoryNode, *, fixed: bool = False) -> str:
    """分类树里的显示文本：固定分类追加「（固定）」标记，名字太长时折成多行。"""
    label = f"{node.category.name} ({node.total_count})"
    return _wrap_label(f"{label}{FIXED_SUFFIX}" if fixed else label)


def file_label(entry) -> str:
    """文件行的显示文本：数据项名称，没有名称时退回文件路径里的文件名。"""
    name = str(getattr(entry, "name", "") or "")
    if name:
        return name
    path = str(getattr(entry, "file_path", "") or "").replace("\\", "/")
    return path.rsplit("/", 1)[-1] or "（未命名）"


def menu_entries(*, all_data: bool = False, fixed: bool = False) -> list[tuple[str, str]]:
    """右键菜单项（action, 文本）：「未分类」是固定分类，不提供任何修改入口。"""
    if fixed:
        return []
    if all_data:
        return [("add", "新建子分类")]
    return [("add", "新建子分类"), ("rename", "重命名"), ("delete", "删除分类")]


MENU_ICONS = {"add": FluentIcon.ADD, "rename": FluentIcon.EDIT, "delete": FluentIcon.DELETE}


class CategoryTree(TreeWidget):
    """展示分类树；右键可新建子分类、重命名或删除。

    关掉「仅显示分类」后，每个分类下面还会列出该分类文件夹里的文件（文件行只带
    两态复选框），「未分类」不再是分类节点，它的文件直接挂在「全部数据」下面。
    """

    categorySelected = pyqtSignal(object)
    fileSelected = pyqtSignal(int)
    actionRequested = pyqtSignal(str, object)
    checkedChanged = pyqtSignal()
    sortChanged = pyqtSignal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemChanged.connect(self._on_item_changed)
        self.itemExpanded.connect(self._on_expansion_changed)
        self.itemCollapsed.connect(self._on_expansion_changed)
        # 行高自适应：分类名太长时由 `_wrap_label()` 折行显示，而不是被截断
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setUniformRowHeights(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._updating = False
        self._only_categories = True
        self._sync_guard = False
        self._syncing_selection = False
        self._cat_children: dict = {}
        self._cat_meta: dict = {}
        self._sort_mode = "default"
        self._sort_reverse = False
        self._sort_dirty = False
        self._sort_timer = QTimer(self)
        self._sort_timer.setSingleShot(True)
        self._sort_timer.setInterval(0)
        self._sort_timer.timeout.connect(self._apply_sort_now)
        # 用户手动展开 / 收起过的分类 id；None = 还没动过，按配置的默认值来
        self._user_expanded: set | None = None

    # ------------------------------------------------------------------ 数据
    def set_nodes(
        self,
        nodes,
        total: int = 0,
        selected: int | None = None,
        checked: set[int] | None = None,
        checked_items: set[int] | None = None,
        files=None,
        expand: bool | None = None,
        only_categories: bool = True,
    ) -> None:
        """重建整棵树；`only_categories` 为假时按 `files` 在每个分类下列出文件。"""
        self.blockSignals(True)
        self._sort_timer.stop()
        self._only_categories = bool(only_categories)
        self._cat_children = {}
        self._cat_meta = {}
        checked_ids = set(checked or ())
        checked_files = set(checked_items or ())
        self.clear()
        root = QTreeWidgetItem([f"全部数据 ({total})"])
        root.setData(0, Qt.ItemDataRole.UserRole, ALL_ID)
        root.setData(0, KIND_ROLE, KIND_ALL)
        # 根节点也能勾选：全选 / 半选 / 全不选整棵分类树。
        root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.addTopLevelItem(root)

        items: dict[int, QTreeWidgetItem] = {}
        merged_ids: set[int] = set()
        for node in nodes:
            fixed = is_uncategorized(node.category)
            if fixed and not self._only_categories:
                # 「未分类」的文件就在用户名文件夹下：不再单列节点，直接挂到根节点
                merged_ids.add(node.category.id)
                continue
            item = QTreeWidgetItem([category_label(node, fixed=fixed)])
            item.setData(0, Qt.ItemDataRole.UserRole, node.category.id)
            item.setData(0, KIND_ROLE, KIND_CATEGORY)
            item.setData(0, FIXED_ROLE, fixed)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if fixed:
                item.setToolTip(0, FIXED_TIP)
            parent = items.get(node.category.parent_id) if node.category.parent_id else root
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
            items[node.category.id] = item
            self._remember_sort_meta(item, node)
            item.setExpanded(self._wants_expanded(node.category.id, expand))

        if not self._only_categories:
            # 文件行统一在分类节点之后追加：目录在前、文件在后
            for category_id, entries in self._group_files(files):
                target = items.get(category_id)
                if target is None and category_id in merged_ids:
                    target = root
                if target is None:
                    continue
                for entry in entries:
                    target.addChild(self._file_item(entry))

        root.setExpanded(self._wants_expanded(ALL_ID, expand))
        self._set_states(checked_ids, checked_files)
        self.blockSignals(False)
        # 重建之后按当前排序方式重排一次（默认「全部数据」在最前、未分类在最后）
        self._sort_dirty = True
        self._schedule_sort()
        self.select_category(selected)

    def _group_files(self, files) -> list[tuple[int, list]]:
        """按分类分组文件行，组内按名称排序。"""
        groups: dict[int, list] = {}
        for entry in files or ():
            category_id = getattr(entry, "category_id", None)
            if category_id is None:
                continue
            groups.setdefault(int(category_id), []).append(entry)
        return [(key, sorted(value, key=file_label)) for key, value in groups.items()]

    def _file_item(self, entry) -> QTreeWidgetItem:
        # 显示时折行；`file_label()` 仍返回未折行的原名，排序按原名走
        item = QTreeWidgetItem([_wrap_label(file_label(entry))])
        item.setData(0, Qt.ItemDataRole.UserRole, ALL_ID)
        item.setData(0, KIND_ROLE, KIND_FILE)
        item.setData(0, ITEM_ROLE, int(getattr(entry, "id", 0) or 0))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        path = str(getattr(entry, "file_path", "") or "")
        if path:
            item.setToolTip(0, path)
        return item

    def _kind(self, item: QTreeWidgetItem) -> str:
        return item.data(0, KIND_ROLE) or KIND_CATEGORY

    def _key(self, item: QTreeWidgetItem):
        if self._kind(item) == KIND_FILE:
            return ("file", item.data(0, ITEM_ROLE))
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _wants_expanded(self, key, expand: bool | None = None) -> bool:
        """分类栏默认全部收起；用户动过展开状态之后，刷新时保持用户的选择。"""
        if self._user_expanded is not None:
            return key in self._user_expanded
        if expand is None:
            return bool(config.expandCategories.value)
        return bool(expand)

    def _on_expansion_changed(self, item: QTreeWidgetItem) -> None:
        if self._updating:
            return
        if self._user_expanded is None:
            self._user_expanded = {
                self._key(current) for current in self._iter_items() if current.isExpanded()
            }
        key = self._key(item)
        if item.isExpanded():
            self._user_expanded.add(key)
        else:
            self._user_expanded.discard(key)

    def checked_categories(self) -> set[int]:
        """当前勾选的分类 id 集合（不含「全部数据」根节点与文件行）。"""
        return {
            int(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self._iter_items()
            if self._kind(item) == KIND_CATEGORY
            and item.checkState(0) == Qt.CheckState.Checked
        }

    def checked_items(self) -> set[int]:
        """当前勾选的文件（数据项）id 集合。"""
        return {
            int(item.data(0, ITEM_ROLE))
            for item in self._iter_items()
            if self._kind(item) == KIND_FILE
            and isinstance(item.data(0, ITEM_ROLE), int)
            and item.checkState(0) == Qt.CheckState.Checked
        }

    def is_all_checked(self) -> bool:
        """「全部数据」根节点是否被完整勾选（此时整棵分类树都处于勾选状态）。"""
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) is ALL_ID:
                return item.checkState(0) == Qt.CheckState.Checked
        return False

    def set_checked_categories(
        self, category_ids: set[int] | None, item_ids: set[int] | None = None
    ) -> None:
        """按 id 集合设置勾选状态（父节点自动聚合三态），不触发 checkedChanged。"""
        wanted = set(category_ids or ())
        wanted_items = set(item_ids or ())
        self.blockSignals(True)
        try:
            self._set_states(wanted, wanted_items)
        finally:
            self.blockSignals(False)

    def _set_states(self, wanted: set[int], wanted_items: set[int] = frozenset()) -> None:
        """wanted 里的分类 / 文件勾选、其余取消，然后由下而上聚合父节点状态。"""
        self._updating = True
        try:
            for item in self._iter_items():
                kind = self._kind(item)
                if kind == KIND_CATEGORY:
                    key = item.data(0, Qt.ItemDataRole.UserRole)
                    wanted_here = key in wanted
                elif kind == KIND_FILE:
                    wanted_here = item.data(0, ITEM_ROLE) in wanted_items
                else:
                    continue
                item.setCheckState(
                    0, Qt.CheckState.Checked if wanted_here else Qt.CheckState.Unchecked
                )
            self._aggregate_all()
        finally:
            self._updating = False

    def _aggregate_all(self) -> None:
        """自底向上刷新父节点：全选 / 半选 / 全不选。"""
        for item in reversed(list(self._iter_items())):
            if item.childCount():
                item.setCheckState(0, self._aggregate_state(item))

    def _aggregate_state(self, item: QTreeWidgetItem) -> Qt.CheckState:
        states = [item.child(index).checkState(0) for index in range(item.childCount())]
        if all(state == Qt.CheckState.Checked for state in states):
            return Qt.CheckState.Checked
        if all(state == Qt.CheckState.Unchecked for state in states):
            return Qt.CheckState.Unchecked
        return Qt.CheckState.PartiallyChecked

    def _apply_state(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        """把状态向下级联到所有后代（分类与文件行一起）。"""
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._apply_state(item.child(index), state)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        state = item.checkState(0)
        if state == Qt.CheckState.PartiallyChecked:
            state = Qt.CheckState.Checked  # 半选被点中时按全选处理
        self._updating = True
        try:
            self._apply_state(item, state)
            self._aggregate_all()
        finally:
            self._updating = False
        self.checkedChanged.emit()

    def select_category(self, category_id: int | None) -> None:
        for item in self._iter_items():
            if self._kind(item) == KIND_FILE:
                continue
            if item.data(0, Qt.ItemDataRole.UserRole) == category_id:
                self.setCurrentItem(item)
                return
        if self.topLevelItemCount():
            self.setCurrentItem(self.topLevelItem(0))

    def current_category(self) -> int | None:
        """当前行所属的分类 id：文件行算它所在的那个分类。"""
        item = self.currentItem()
        while item is not None:
            if self._kind(item) != KIND_FILE:
                return item.data(0, Qt.ItemDataRole.UserRole)
            item = item.parent()
        return None

    # ------------------------------------------------------------------ 排序
    def sort_mode(self) -> str:
        return self._sort_mode

    def sort_reverse(self) -> bool:
        return self._sort_reverse

    def set_sort(self, mode: str, reverse: bool = False, *, notify: bool = False) -> None:
        """设置分类排序方式与正序 / 逆序；notify 为真时发出 `sortChanged`。"""
        mode = mode if mode in SORT_KEYS else "default"
        if (mode, bool(reverse)) == (self._sort_mode, self._sort_reverse):
            return
        self._sort_mode = mode
        self._sort_reverse = bool(reverse)
        self._sort_dirty = True
        self._schedule_sort()
        if notify:
            self.sortChanged.emit(self._sort_mode, self._sort_reverse)

    def apply_sort(self) -> None:
        """立刻按当前排序方式重排（不等待事件循环）。"""
        self._sort_timer.stop()
        self._apply_sort_now()

    def _schedule_sort(self) -> None:
        """排序推迟到事件循环空闲时执行：避免了在插入过程中反复重排整棵树。"""
        self._sort_timer.start()

    def _cat_key(self, item: QTreeWidgetItem):
        """排序元数据的键：分类 id。

        `QTreeWidgetItem` 在 PyQt6 里不可哈希（没有 `__hash__`），不能直接当字典键，
        所以用行上挂的分类 id 作为键。
        """
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _remember_sort_meta(self, item: QTreeWidgetItem, node: CategoryNode) -> None:
        parent_id = node.category.parent_id
        self._cat_children.setdefault(parent_id, []).append(item)
        # 「按最新导入」用的是分类下最近一条数据的导入时间（由 taxonomy 一并算好）
        latest = getattr(node, "latest_at", None)
        self._cat_meta[self._cat_key(item)] = (
            node.category.name,
            int(node.total_count),
            _latest_key(latest),
        )

    def _fixed_sort_key(self, item: QTreeWidgetItem):
        """固定分类（「未分类」）永远排在最后。"""
        return 1 if item.data(0, FIXED_ROLE) else 0

    def _reordered(self, items: list[QTreeWidgetItem]) -> list[QTreeWidgetItem]:
        """排好序的分类行：普通分类在前，「未分类」始终最后。

        「默认顺序」保持分类自己的顺序（`sort_order`，即 taxonomy 给出的次序）；
        正序 = 升序（名称 A→Z、数据量少→多、时间旧→新），逆序 = 降序；切换只改顺序不丢节点。
        """
        mode = self._sort_mode
        keyed = []
        for index, item in enumerate(items):
            name, count, latest = self._cat_meta.get(self._cat_key(item), ("", 0, ""))
            keyed.append(
                (
                    self._fixed_sort_key(item),
                    _sort_key(mode, self._sort_reverse, label=name, count=count, latest=latest),
                    index,
                    item,
                )
            )
        normal = [row for row in keyed if row[0] == 0]
        fixed = [row for row in keyed if row[0] == 1]
        if mode == "default":
            # 保持 taxonomy 给的次序（sort_order），只把固定分类挪到最后
            return [row[3] for row in normal] + [row[3] for row in fixed]
        normal.sort(key=lambda row: row[1], reverse=bool(self._sort_reverse))
        fixed.sort(key=lambda row: row[2])
        return [row[3] for row in normal] + [row[3] for row in fixed]

    def _apply_sort_now(self) -> None:
        """按当前排序方式重排分类节点（文件行保持「目录在前、文件在后」）。

        Qt 没有「稳定重排」，只能把分类行摘下来再插回去；这里按「先整体取出、再按序插回」
        来做，避免边取边插导致取错行（`takeChild(0)` 在插入之后取的已经不是目标行），
        同时保证挂在同一个分类下的文件行不会被误当成分类行摘走。

        `takeChild` 会把「当前项」清掉，而重排是推迟到零间隔定时器里跑的（可能在
        `select_category()` 之后），所以这里先把当前分类记下来、重排完再放回去，
        否则「跳到某条数据」之后分类树会失去聚焦（自检 `recent_focus`）。
        """
        if not self._sort_dirty or self._updating:
            return
        self._sort_dirty = False
        current_id = self.current_category()
        self.blockSignals(True)
        try:
            for parent_id, items in list(self._cat_children.items()):
                if len(items) < 2:
                    continue
                ordered = self._reordered(items)
                self._cat_children[parent_id] = ordered
                # 顶层分类挂在「全部数据」根节点下面（不是 QTreeWidget 的顶层项）
                parent = self.topLevelItem(0) if parent_id is None else self._parent_item(parent_id)
                if parent is None:
                    continue
                for item in ordered:
                    index = parent.indexOfChild(item)
                    if index >= 0:
                        parent.takeChild(index)
                for position, item in enumerate(ordered):
                    parent.insertChild(position, item)
        finally:
            # 重排把当前项摘走过，按分类 id 放回（信号已屏蔽，不会重复通知页面）
            if self.current_category() != current_id:
                self.select_category(current_id)
            self.blockSignals(False)

    def _parent_item(self, parent_id) -> QTreeWidgetItem | None:
        for item in self._iter_items():
            if self._kind(item) != KIND_CATEGORY:
                continue
            if item.data(0, Qt.ItemDataRole.UserRole) == parent_id:
                return item
        return None

    # ------------------------------------------------------------------ 事件
    def _iter_items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def fixed_ids(self) -> set[int]:
        """固定分类（「未分类」）对应的分类 id 集合。"""
        return {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in self._iter_items()
            if item.data(0, FIXED_ROLE)
        }

    def selected_categories(self) -> list[int]:
        """当前选中的分类 id 列表（文件行折算成它所属的分类，并按出现顺序去重）。"""
        ids: list[int] = []
        for item in self.selectedItems():
            node = item
            while node is not None and self._kind(node) == KIND_FILE:
                node = node.parent()
            if node is None:
                continue
            category_id = node.data(0, Qt.ItemDataRole.UserRole)
            if category_id is None or category_id == ALL_ID:
                # 根节点（全部数据）不参与批量分类操作
                continue
            if category_id not in ids:
                ids.append(category_id)
        return ids

    def _on_selection(self) -> None:
        if self._syncing_selection:
            return
        item = self.currentItem()
        if item is not None and self._kind(item) == KIND_FILE:
            self.fileSelected.emit(int(item.data(0, ITEM_ROLE)))
            return
        # Ctrl / Shift 多选：把选中的分类同步勾上，批量操作即按勾选集合执行。
        # 单选保持原样（不勾选），由页面把它作为单选过滤处理。
        selected = self.selected_categories()
        if len(selected) > 1:
            self._syncing_selection = True
            try:
                targets = []
                for category_id in selected:
                    target = self._find_category_item(category_id)
                    if target is not None and target.checkState(0) != Qt.CheckState.Checked:
                        targets.append(target)
                if targets:
                    # 用 `_updating` 压住 itemChanged 的级联回环，级联与汇总在这里做一次
                    self._updating = True
                    try:
                        for target in targets:
                            self._apply_state(target, Qt.CheckState.Checked)
                        self._aggregate_all()
                    finally:
                        self._updating = False
            finally:
                self._syncing_selection = False
        self.categorySelected.emit(self.current_category())

    def _find_category_item(self, category_id: int) -> QTreeWidgetItem | None:
        for item in self._iter_items():
            if self._kind(item) != KIND_CATEGORY:
                continue
            if item.data(0, Qt.ItemDataRole.UserRole) == category_id:
                return item
        return None

    def _show_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None or self._kind(item) == KIND_FILE:
            return
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        entries = menu_entries(
            all_data=category_id is None, fixed=bool(item.data(0, FIXED_ROLE))
        )
        if not entries:
            return
        menu = RoundMenu(parent=self)
        for action, text in entries:
            menu.addAction(
                Action(
                    MENU_ICONS[action],
                    text,
                    triggered=lambda _=False, name=action, target=category_id: (
                        self.actionRequested.emit(name, target)
                    ),
                )
            )
        menu.exec(self.viewport().mapToGlobal(pos))


__all__ = [
    "ALL_ID",
    "CATEGORY_SORT_MODE",
    "CATEGORY_SORT_REVERSE",
    "FIXED_SUFFIX",
    "ITEM_ROLE",
    "KIND_FILE",
    "KIND_ROLE",
    "SORT_KEYS",
    "SORT_LABELS",
    "SORT_MODES",
    "CategoryTree",
    "category_label",
    "file_label",
    "menu_entries",
]
