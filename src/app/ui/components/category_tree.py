"""分类树：左侧导航用的可操作分类树。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTreeWidgetItem, QWidget
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


def category_label(node: CategoryNode, *, fixed: bool = False) -> str:
    """分类树里的显示文本：固定分类追加「（固定）」标记。"""
    label = f"{node.category.name} ({node.total_count})"
    return f"{label}{FIXED_SUFFIX}" if fixed else label


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemChanged.connect(self._on_item_changed)
        self.itemExpanded.connect(self._on_expansion_changed)
        self.itemCollapsed.connect(self._on_expansion_changed)
        self._updating = False
        self._only_categories = True
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
        self._only_categories = bool(only_categories)
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
        item = QTreeWidgetItem([file_label(entry)])
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

    def _on_selection(self) -> None:
        item = self.currentItem()
        if item is not None and self._kind(item) == KIND_FILE:
            self.fileSelected.emit(int(item.data(0, ITEM_ROLE)))
            return
        self.categorySelected.emit(self.current_category())

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
    "FIXED_SUFFIX",
    "ITEM_ROLE",
    "KIND_FILE",
    "KIND_ROLE",
    "CategoryTree",
    "category_label",
    "file_label",
    "menu_entries",
]
