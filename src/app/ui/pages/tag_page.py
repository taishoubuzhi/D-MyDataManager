"""标签管理页：全局标签与个人标签的创建、归属切换与清理。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    TitleLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...db.models import Tag
from ...services import TaxonomyService, UserService
from ..common import confirm, toast_error, toast_success, toast_warning
from ..dialogs import TextInputDialog


class TagPage(QWidget):
    """标签管理：区分全局标签与个人标签，只有创建者能改归属、改名与删除。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tagPage")
        self.session = database.new_session()
        self.taxonomy = TaxonomyService(self.session)
        self.users = UserService(self.session)
        self.tag_repo = self.taxonomy.tags
        self._tags: list[Tag] = []
        self._user_id: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("标签", self))
        header.addStretch(1)
        self.global_box = CheckBox("新建时设为全局标签", self)
        header.addWidget(self.global_box)
        create_button = PrimaryPushButton(FluentIcon.ADD, "新建标签", self)
        create_button.clicked.connect(self._on_create)
        header.addWidget(create_button)
        root.addLayout(header)
        root.addWidget(
            CaptionLabel(
                "全局标签对所有用户可见，个人标签只属于创建者，其他用户看不到。默认用户可以看到全部标签；"
                "只有创建者能改名、删除或切换归属；个人标签不能与已有全局标签重名。",
                self,
            )
        )

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for text, icon, slot in (
            ("转为全局", FluentIcon.GLOBE, self._on_to_global),
            ("转为个人", FluentIcon.PEOPLE, self._on_to_personal),
            ("重命名", FluentIcon.EDIT, self._on_rename),
            ("删除", FluentIcon.DELETE, self._on_delete),
            ("清理未使用标签", FluentIcon.BROOM, self._on_cleanup),
        ):
            button = PushButton(icon, text, self)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        self.table = TableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["名称", "归属", "创建者", "数据项数"])
        self.table.verticalHeader().setVisible(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        signalBus.tagsChanged.connect(self.refresh)
        signalBus.userChanged.connect(self.refresh)
        self.refresh()

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        """按当前用户刷新标签列表：全局标签 + 自己的个人标签。"""
        self._user_id = self.users.current_id()
        owners = {info.user.id: info.name for info in self.users.list_users()}
        usage = self.taxonomy.usage(user_id=self._user_id)
        # 默认用户（管理员）可以看到全部标签，其他用户只看全局标签与自己创建的标签。
        self._tags = self.tag_repo.all() if self.users.is_admin() else self.tag_repo.all(
            user_id=self._user_id
        )
        self.table.setRowCount(len(self._tags))
        for row, tag in enumerate(self._tags):
            creator = self.tag_repo.is_creator(tag, self._user_id)
            if tag.is_global:
                scope = "全局"
            else:
                scope = "个人（我）" if creator else "个人"
            owner_id = tag.created_by or tag.user_id
            cells = [tag.name, scope, owners.get(owner_id, "—"), str(usage.get(tag.name, 0))]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, tag.id)
                if column == 3:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, cell)
        self.table.clearSelection()

    # ---------------------------------------------------------------- 校验
    def _selected_tag(self) -> Tag | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._tags):
            return None
        return self._tags[row]

    def _require_selection(self) -> Tag | None:
        tag = self._selected_tag()
        if tag is None:
            toast_warning(self, "请先选择一个标签")
        return tag

    def _require_creator(self, tag: Tag) -> bool:
        if self.tag_repo.is_creator(tag, self._user_id):
            return True
        toast_warning(self, "只有创建者可以修改该标签", f"「{tag.name}」不是你创建的标签")
        return False

    def _done(self, title: str, content: str = "") -> None:
        self.session.commit()
        signalBus.tagsChanged.emit()
        toast_success(self, title, content)

    # ---------------------------------------------------------------- 操作
    def _on_create(self) -> None:
        dialog = TextInputDialog("新建标签", "标签名称", parent=self.window())
        if not dialog.exec():
            return
        name = dialog.value()
        if not name:
            toast_warning(self, "标签名称不能为空")
            return
        is_global = self.global_box.isChecked()
        tag = self.taxonomy.create_tag(name, user_id=self._user_id, is_global=is_global)
        if tag is None:
            toast_warning(
                self, "标签已存在", f"已有可见标签「{name}」，个人标签不能与全局标签重名"
            )
            return
        self._done("标签已创建", f"{'全局' if is_global else '个人'}标签「{tag.name}」")

    def _switch_global(self, is_global: bool) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_creator(tag):
            return
        label = "全局" if is_global else "个人"
        if bool(tag.is_global) == is_global:
            toast_warning(self, "标签归属未变化", f"「{tag.name}」已经是{label}标签")
            return
        if not self.taxonomy.set_tag_global(tag, is_global, user_id=self._user_id):
            toast_error(self, "切换失败", f"已存在同名全局标签「{tag.name}」")
            return
        self._done("标签归属已更新", f"「{tag.name}」现在是{label}标签")

    def _on_to_global(self) -> None:
        self._switch_global(True)

    def _on_to_personal(self) -> None:
        self._switch_global(False)

    def _on_rename(self) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_creator(tag):
            return
        dialog = TextInputDialog("重命名标签", "新的标签名称", text=tag.name, parent=self.window())
        if not dialog.exec():
            return
        new_name = dialog.value()
        if not new_name or new_name == tag.name:
            toast_warning(self, "标签名称未变化")
            return
        if not self.taxonomy.rename_tag(tag, new_name, user_id=self._user_id):
            toast_error(self, "重命名失败", f"已存在可见标签「{new_name}」")
            return
        self._done("标签已重命名", f"新名称「{new_name}」")

    def _on_delete(self) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_creator(tag):
            return
        count = self.tag_repo.item_count(tag)
        if not confirm(
            self, "删除标签", f"确定删除标签「{tag.name}」吗？它会被从 {count} 个数据项上移除。"
        ):
            return
        self.taxonomy.delete_tag(tag)
        self._done("标签已删除", f"「{tag.name}」已从 {count} 个数据项上移除")

    def _on_cleanup(self) -> None:
        removed = self.taxonomy.cleanup_unused(user_id=self._user_id)
        self.session.commit()
        signalBus.tagsChanged.emit()
        toast_success(self, "清理完成", f"已删除 {removed} 个未使用的标签")


__all__ = ["TagPage"]
