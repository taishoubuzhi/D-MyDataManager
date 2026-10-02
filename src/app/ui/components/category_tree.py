"""分类树：左侧导航用的可操作分类树。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTreeWidgetItem, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu, TreeWidget

from ...services import CategoryNode, is_uncategorized

ALL_ID = None
FIXED_ROLE = Qt.ItemDataRole.UserRole + 1
FIXED_SUFFIX = "（固定）"
FIXED_TIP = "系统分类：不能重命名、删除，也不能创建子分类"


def category_label(node: CategoryNode, *, fixed: bool = False) -> str:
    """分类树里的显示文本：固定分类追加「（固定）」标记。"""
    label = f"{node.category.name} ({node.total_count})"
    return f"{label}{FIXED_SUFFIX}" if fixed else label


def menu_entries(*, all_data: bool = False, fixed: bool = False) -> list[tuple[str, str]]:
    """右键菜单项（action, 文本）：「未分类」是固定分类，不提供任何修改入口。"""
    if fixed:
        return []
    if all_data:
        return [("add", "新建子分类")]
    return [("add", "新建子分类"), ("rename", "重命名"), ("delete", "删除分类")]


MENU_ICONS = {"add": FluentIcon.ADD, "rename": FluentIcon.EDIT, "delete": FluentIcon.DELETE}


class CategoryTree(TreeWidget):
    """展示分类树；右键可新建子分类、重命名或删除。"""

    categorySelected = pyqtSignal(object)
    actionRequested = pyqtSignal(str, object)
    checkedChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemSelectionChanged.connect(self._on_selection)
        self.itemChanged.connect(self._on_item_changed)
        self._updating = False

    # ------------------------------------------------------------------ 数据
    def set_nodes(
        self,
        nodes,
        total: int = 0,
        selected: int | None = None,
        checked: set[int] | None = None,
    ) -> None:
        self.blockSignals(True)
        checked_ids = set(checked or ())
        self.clear()
        root = QTreeWidgetItem([f"全部数据 ({total})"])
        root.setData(0, Qt.ItemDataRole.UserRole, ALL_ID)
        # 根节点也能勾选：全选 / 半选 / 全不选整棵分类树。
        root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.addTopLevelItem(root)

        items: dict[int, QTreeWidgetItem] = {}
        for node in nodes:
            fixed = is_uncategorized(node.category)
            item = QTreeWidgetItem([category_label(node, fixed=fixed)])
            item.setData(0, Qt.ItemDataRole.UserRole, node.category.id)
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
            item.setExpanded(True)

        root.setExpanded(True)
        self._set_states(checked_ids)
        self.blockSignals(False)
        self.select_category(selected)

    def checked_categories(self) -> set[int]:
        """当前勾选的分类 id 集合（不含「全部数据」根节点）。"""
        return {
            int(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self._iter_items()
            if item.checkState(0) == Qt.CheckState.Checked
            and isinstance(item.data(0, Qt.ItemDataRole.UserRole), int)
        }

    def is_all_checked(self) -> bool:
        """「全部数据」根节点是否被完整勾选（此时整棵分类树都处于勾选状态）。"""
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) is ALL_ID:
                return item.checkState(0) == Qt.CheckState.Checked
        return False

    def set_checked_categories(self, category_ids: set[int] | None) -> None:
        """按 id 集合设置勾选状态（父节点自动聚合三态），不触发 checkedChanged。"""
        wanted = set(category_ids or ())
        self.blockSignals(True)
        try:
            self._set_states(wanted)
        finally:
            self.blockSignals(False)

    def _set_states(self, wanted: set[int]) -> None:
        """wanted 里的分类勾选、其余取消，然后由下而上聚合父节点状态。"""
        self._updating = True
        try:
            for item in self._iter_items():
                category_id = item.data(0, Qt.ItemDataRole.UserRole)
                if not isinstance(category_id, int):
                    continue
                state = (
                    Qt.CheckState.Checked if category_id in wanted else Qt.CheckState.Unchecked
                )
                item.setCheckState(0, state)
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
        """把状态向下级联到所有后代。"""
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
            if item.data(0, Qt.ItemDataRole.UserRole) == category_id:
                self.setCurrentItem(item)
                return
        if self.topLevelItemCount():
            self.setCurrentItem(self.topLevelItem(0))

    def current_category(self) -> int | None:
        item = self.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

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
        self.categorySelected.emit(self.current_category())

    def _show_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
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


__all__ = ["ALL_ID", "FIXED_SUFFIX", "CategoryTree", "category_label", "menu_entries"]
