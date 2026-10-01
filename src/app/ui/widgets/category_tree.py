"""分类树：左侧导航用的可操作分类树。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QTreeWidgetItem, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu, TreeWidget

from ...services import CategoryNode

ALL_ID = None


class CategoryTree(TreeWidget):
    """展示分类树；右键可新建子分类、重命名或删除。"""

    categorySelected = pyqtSignal(object)
    actionRequested = pyqtSignal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemSelectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------------ 数据
    def set_nodes(self, nodes: list[CategoryNode], total: int = 0, selected: int | None = None) -> None:
        self.blockSignals(True)
        self.clear()
        root = QTreeWidgetItem([f"全部数据 ({total})"])
        root.setData(0, Qt.ItemDataRole.UserRole, ALL_ID)
        self.addTopLevelItem(root)

        items: dict[int, QTreeWidgetItem] = {}
        for node in nodes:
            label = f"{node.category.name} ({node.total_count})"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, node.category.id)
            parent = items.get(node.category.parent_id) if node.category.parent_id else root
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
            items[node.category.id] = item
            item.setExpanded(True)

        root.setExpanded(True)
        self.blockSignals(False)
        self.select_category(selected)

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

    def _on_selection(self) -> None:
        self.categorySelected.emit(self.current_category())

    def _show_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action(FluentIcon.ADD, "新建子分类", triggered=lambda: self.actionRequested.emit("add", category_id))
        )
        if category_id is not None:
            menu.addAction(
                Action(FluentIcon.EDIT, "重命名", triggered=lambda: self.actionRequested.emit("rename", category_id))
            )
            menu.addAction(
                Action(FluentIcon.DELETE, "删除分类", triggered=lambda: self.actionRequested.emit("delete", category_id))
            )
        menu.exec(self.viewport().mapToGlobal(pos))


__all__ = ["CategoryTree"]
