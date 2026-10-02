"""标签管理页：全局标签与个人标签的创建、归属切换、清理与逐列筛选。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QTableWidgetItem, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    TableWidget,
)

from ...core.signals import signalBus
from ...db import database
from ...db.models import Tag
from ...services import TaxonomyService, UserService
from ..dialogs import TextInputDialog
from ..framework import Page, confirm, tri_state
from ..components.data_table import (
    TableFilterBar,
    check_cell,
    column_values,
    fit_columns,
    match_filters,
    prepare_table,
)

#: 表格第一列是批量操作勾选框，其余列依次对应 名称 / 归属 / 创建者 / 数据项数。
TAG_CHECK_COLUMN = 0
TAG_HEADERS = ("选择", "名称", "归属", "创建者", "数据项数")

_FILTER_COLUMNS = (
    ("name", "名称", "text"),
    ("scope", "归属", "choice"),
    ("owner", "创建者", "choice"),
    ("usage", "数据项数", "text"),
)


class TagPage(Page):
    """标签管理：区分全局标签与个人标签；默认用户（管理员）可管理任意标签，其他用户只能管理自己创建的标签。"""

    page_name = "tagPage"
    page_title = "标签"
    page_subtitle = (
        "全局标签对所有用户可见，个人标签只属于创建者。基础标签（重要 / 待整理 / 收藏）默认即全局标签，"
        "开箱即用；默认用户（管理员）可管理任意标签，其他用户只能改名、删除或切换自己创建的标签的归属，"
        "个人标签不能与已有全局标签重名。"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = database.new_session()
        self.taxonomy = TaxonomyService(self.session)
        self.users = UserService(self.session)
        self.tag_repo = self.taxonomy.tags
        self._tags: list[Tag] = []
        self._row_values: list[dict[str, str]] = []
        self._user_id: int | None = None
        self._is_admin = False
        self._checked: set[int] = set()

        self.global_box = CheckBox("新建时设为全局标签", self)
        self.header.add_action(self.global_box)
        self.create_button = PrimaryPushButton(FluentIcon.ADD, "新建标签", self)
        self.create_button.clicked.connect(self._on_create)
        self.header.add_action(self.create_button)

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
        self.add_row(actions)

        self.add_row(self._build_selection_bar(self))

        self.filter_bar = TableFilterBar(self)
        self.filter_bar.configure(_FILTER_COLUMNS)
        self.filter_bar.changed.connect(self._apply_filters)
        self.add_widget(self.filter_bar)

        info_row = QHBoxLayout()
        self.filter_caption = CaptionLabel("", self)
        info_row.addWidget(self.filter_caption)
        info_row.addStretch(1)
        reset_button = PushButton(FluentIcon.CLEAR_SELECTION, "重置筛选", self)
        reset_button.clicked.connect(self._on_reset_filters)
        info_row.addWidget(reset_button)
        self.add_row(info_row)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(TAG_HEADERS))
        self.table.setHorizontalHeaderLabels(list(TAG_HEADERS))
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        prepare_table(self.table, movable=True)
        self.table.itemChanged.connect(self._on_item_changed)
        self.add_widget(self.table, 1)

        self.auto_refresh(signalBus.tagsChanged, signalBus.userChanged)
        self.refresh()

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        """按当前用户刷新标签列表：全局标签 + 自己的个人标签。"""
        self._user_id = self.users.current_id()
        self._is_admin = self.users.is_admin()
        owners = {info.user.id: info.name for info in self.users.list_users()}
        usage = self.taxonomy.usage(user_id=self._user_id)
        # 默认用户（管理员）可以看到全部标签，其他用户只看全局标签与自己创建的标签。
        self._tags = (
            self.tag_repo.all()
            if self._is_admin
            else self.tag_repo.all(user_id=self._user_id)
        )
        self.table.setRowCount(len(self._tags))
        self._checked &= {int(tag.id) for tag in self._tags}
        self._row_values = []
        for row, tag in enumerate(self._tags):
            creator = self.tag_repo.is_creator(tag, self._user_id)
            if tag.is_global:
                scope = "全局"
            else:
                scope = "个人（我）" if creator else "个人"
            owner_id = tag.created_by or tag.user_id
            owner = owners.get(owner_id, "—")
            count = str(usage.get(tag.name, 0))
            self.table.setItem(
                row, TAG_CHECK_COLUMN, check_cell(int(tag.id) in self._checked)
            )
            cells = [tag.name, scope, owner, count]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, tag.id)
                if column == len(cells) - 1:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, TAG_CHECK_COLUMN + 1 + column, cell)
            self._row_values.append(
                {"name": tag.name, "scope": scope, "owner": owner, "usage": count}
            )
        self.filter_bar.set_options("scope", column_values(self.table, TAG_CHECK_COLUMN + 2))
        self.filter_bar.set_options("owner", column_values(self.table, TAG_CHECK_COLUMN + 3))
        self._apply_filters()
        self._sync_selection()

    # ---------------------------------------------------------------- 筛选
    def _apply_filters(self) -> None:
        filters = self.filter_bar.filters()
        visible = 0
        for row, values in enumerate(self._row_values):
            hit = match_filters(values, filters)
            self.table.setRowHidden(row, not hit)
            visible += int(hit)
        self.table.clearSelection()
        self.filter_caption.setText(f"显示 {visible} / {len(self._row_values)} 个标签")
        fit_columns(self.table, min_width=64, max_width=220)
        self._sync_selection()

    def _on_reset_filters(self) -> None:
        self.filter_bar.reset()
        self._apply_filters()

    # ---------------------------------------------------------------- 勾选
    def _build_selection_bar(self, parent: QWidget) -> QHBoxLayout:
        """选择条：三态全选框 + 全选/全不选/反选 + 批量操作，与表格勾选双向同步。"""
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.select_all_box = CheckBox("全选本页", parent)
        self.select_all_box.setTristate(True)
        self.select_all_box.setToolTip("空 = 全不选，横杠 = 部分选中，勾 = 全选当前显示的标签")
        self.select_all_box.stateChanged.connect(self._on_select_all_toggled)
        bar.addWidget(self.select_all_box)

        self.selection_label = CaptionLabel("未选择标签", parent)
        bar.addWidget(self.selection_label)

        self.select_all_button = PushButton(FluentIcon.ACCEPT, "全选", parent)
        self.select_all_button.setToolTip("勾选当前显示的全部标签")
        self.select_all_button.clicked.connect(self.select_all)
        self.select_none_button = PushButton(FluentIcon.CLOSE, "全不选", parent)
        self.select_none_button.setToolTip("取消全部勾选")
        self.select_none_button.clicked.connect(self.select_none)
        self.invert_button = PushButton(FluentIcon.SYNC, "反选", parent)
        self.invert_button.setToolTip("反转当前显示标签的勾选状态")
        self.invert_button.clicked.connect(self.invert_selection)
        for button in (self.select_all_button, self.select_none_button, self.invert_button):
            bar.addWidget(button)
        bar.addStretch(1)

        self.batch_global_button = PushButton(FluentIcon.GLOBE, "批量转为全局", parent)
        self.batch_global_button.setToolTip("把勾选的标签转为全局标签；只有创建者能改归属")
        self.batch_global_button.clicked.connect(lambda: self._on_batch_switch_global(True))
        self.batch_personal_button = PushButton(FluentIcon.PEOPLE, "批量转为个人", parent)
        self.batch_personal_button.setToolTip("把勾选的标签转为个人标签；只有创建者能改归属")
        self.batch_personal_button.clicked.connect(lambda: self._on_batch_switch_global(False))
        self.batch_delete_button = PushButton(FluentIcon.DELETE, "批量删除", parent)
        self.batch_delete_button.setToolTip("删除勾选的标签；只有创建者能删除")
        self.batch_delete_button.clicked.connect(self._on_batch_delete)
        for button in (self.batch_global_button, self.batch_personal_button, self.batch_delete_button):
            bar.addWidget(button)
        return bar

    def _visible_rows(self) -> list[tuple[int, Tag]]:
        """当前未被筛选隐藏的行与对应标签。"""
        return [
            (row, tag)
            for row, tag in enumerate(self._tags)
            if not self.table.isRowHidden(row)
        ]

    def checked_tags(self) -> list[Tag]:
        """当前勾选的标签（按列表顺序），供批量操作与自检脚本使用。"""
        return [tag for tag in self._tags if int(tag.id) in self._checked]

    def _sync_selection(self) -> None:
        """三态全选框、已选数量与批量按钮跟随勾选状态。"""
        rows = self._visible_rows()
        checked = sum(1 for _row, tag in rows if int(tag.id) in self._checked)
        self.select_all_box.blockSignals(True)
        self.select_all_box.setCheckState(tri_state(checked, len(rows)))
        self.select_all_box.blockSignals(False)
        count = len(self._checked)
        self.selection_label.setText(f"已选 {count} 个标签" if count else "未选择标签")
        for button in (
            self.batch_global_button,
            self.batch_personal_button,
            self.batch_delete_button,
        ):
            button.setEnabled(bool(count))

    def _apply_check_states(self) -> None:
        """把 _checked 写回表格勾选框（阻断信号，避免回环）。"""
        self.table.blockSignals(True)
        for row, tag in enumerate(self._tags):
            item = self.table.item(row, TAG_CHECK_COLUMN)
            if item is not None:
                checked = int(tag.id) in self._checked
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._sync_selection()

    def _set_visible_checked(self, checked: bool) -> None:
        for _row, tag in self._visible_rows():
            if checked:
                self._checked.add(int(tag.id))
            else:
                self._checked.discard(int(tag.id))
        self._apply_check_states()

    def select_all(self) -> None:
        """勾选当前显示的全部标签。"""
        self._set_visible_checked(True)

    def select_none(self) -> None:
        """取消全部勾选。"""
        self._set_visible_checked(False)

    def invert_selection(self) -> None:
        """反转当前显示标签的勾选状态。"""
        for _row, tag in self._visible_rows():
            tag_id = int(tag.id)
            if tag_id in self._checked:
                self._checked.discard(tag_id)
            else:
                self._checked.add(tag_id)
        self._apply_check_states()

    def _on_select_all_toggled(self, state: int) -> None:
        self._set_visible_checked(Qt.CheckState(state) == Qt.CheckState.Checked)

    def _on_item_changed(self, item) -> None:
        if item.column() != TAG_CHECK_COLUMN:
            return
        row = item.row()
        if not 0 <= row < len(self._tags):
            return
        tag_id = int(self._tags[row].id)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(tag_id)
        else:
            self._checked.discard(tag_id)
        self._sync_selection()

    # ---------------------------------------------------------------- 校验
    def _selected_tag(self) -> Tag | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._tags) or self.table.isRowHidden(row):
            return None
        return self._tags[row]

    def _require_selection(self) -> Tag | None:
        tag = self._selected_tag()
        if tag is None:
            self.toast_warning("请先选择一个标签")
        return tag

    def _require_manage(self, tag: Tag) -> bool:
        """默认用户（管理员）可管理任意标签，其他用户只能管理自己创建的标签。"""
        if self.tag_repo.can_manage(tag, self._user_id, self._is_admin):
            return True
        self.toast_warning(
            "无权修改该标签",
            f"「{tag.name}」不是你创建的标签，只有默认用户或创建者可以修改",
        )
        return False

    def _done(self, title: str, content: str = "") -> None:
        self.session.commit()
        signalBus.tagsChanged.emit()
        self.toast_success(title, content)

    # ---------------------------------------------------------------- 操作
    def _on_create(self) -> None:
        dialog = TextInputDialog("新建标签", "标签名称", parent=self.window())
        if not dialog.exec():
            return
        name = dialog.value()
        if not name:
            self.toast_warning("标签名称不能为空")
            return
        is_global = self.global_box.isChecked()
        tag = self.taxonomy.create_tag(name, user_id=self._user_id, is_global=is_global)
        if tag is None:
            self.toast_warning(
                "标签已存在", f"已有可见标签「{name}」，个人标签不能与全局标签重名"
            )
            return
        self._done("标签已创建", f"{'全局' if is_global else '个人'}标签「{tag.name}」")

    def _switch_global(self, is_global: bool) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_manage(tag):
            return
        label = "全局" if is_global else "个人"
        if bool(tag.is_global) == is_global:
            self.toast_warning("标签归属未变化", f"「{tag.name}」已经是{label}标签")
            return
        if not self.taxonomy.set_tag_global(
            tag, is_global, user_id=self._user_id, is_admin=self._is_admin
        ):
            self.toast_error("切换失败", f"已存在同名全局标签「{tag.name}」")
            return
        self._done("标签归属已更新", f"「{tag.name}」现在是{label}标签")

    def _on_to_global(self) -> None:
        self._switch_global(True)

    def _on_to_personal(self) -> None:
        self._switch_global(False)

    def _on_rename(self) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_manage(tag):
            return
        dialog = TextInputDialog("重命名标签", "新的标签名称", text=tag.name, parent=self.window())
        if not dialog.exec():
            return
        new_name = dialog.value()
        if not new_name or new_name == tag.name:
            self.toast_warning("标签名称未变化")
            return
        if not self.taxonomy.rename_tag(
            tag, new_name, user_id=self._user_id, is_admin=self._is_admin
        ):
            self.toast_error("重命名失败", f"已存在可见标签「{new_name}」")
            return
        self._done("标签已重命名", f"新名称「{new_name}」")

    def _on_delete(self) -> None:
        tag = self._require_selection()
        if tag is None or not self._require_manage(tag):
            return
        count = self.tag_repo.item_count(tag)
        if not confirm(
            self, "删除标签", f"确定删除标签「{tag.name}」吗？它会被从 {count} 个数据项上移除。"
        ):
            return
        self.taxonomy.delete_tag(tag, user_id=self._user_id, is_admin=self._is_admin)
        self._done("标签已删除", f"「{tag.name}」已从 {count} 个数据项上移除")

    def _owned_or_skipped(self, tags: list[Tag], action: str) -> tuple[list[Tag], list[Tag]]:
        """按管理权限拆分勾选的标签，返回（可操作, 被跳过的）。"""
        owned = [
            tag
            for tag in tags
            if self.tag_repo.can_manage(tag, self._user_id, self._is_admin)
        ]
        skipped = [tag for tag in tags if tag not in owned]
        if skipped and not owned:
            self.toast_warning(
                f"无法{action}",
                f"勾选的 {len(skipped)} 个标签都不是你创建的，只有默认用户或创建者可以{action}",
            )
        return owned, skipped

    def _on_batch_switch_global(self, is_global: bool) -> None:
        """批量切换勾选标签的归属（全局 / 个人）。"""
        tags = self.checked_tags()
        if not tags:
            self.toast_warning("未选择标签", "请先勾选要批量操作的标签")
            return
        label = "全局" if is_global else "个人"
        owned, skipped = self._owned_or_skipped(tags, f"转为{label}标签")
        if not owned:
            return
        changed = 0
        failed = 0
        for tag in owned:
            if bool(tag.is_global) == is_global:
                continue
            if self.taxonomy.set_tag_global(
                tag, is_global, user_id=self._user_id, is_admin=self._is_admin
            ):
                changed += 1
            else:
                failed += 1
        self.session.commit()
        signalBus.tagsChanged.emit()
        detail = [f"{changed} 个标签现在是{label}标签"]
        if failed:
            detail.append(f"{failed} 个因存在同名全局标签而跳过")
        if skipped:
            detail.append(f"{len(skipped)} 个不是由你创建的已跳过")
        self.toast_success("标签归属已更新", "；".join(detail))

    def _on_batch_delete(self) -> None:
        """批量删除勾选的标签；只有默认用户或创建者能删除，其余跳过。"""
        tags = self.checked_tags()
        if not tags:
            self.toast_warning("未选择标签", "请先勾选要批量删除的标签")
            return
        owned, skipped = self._owned_or_skipped(tags, "删除")
        if not owned:
            return
        affected = sum(self.tag_repo.item_count(tag) for tag in owned)
        note = f"其中 {len(skipped)} 个不是由你创建的会被跳过。" if skipped else ""
        if not confirm(
            self,
            "批量删除标签",
            f"确定删除勾选的 {len(owned)} 个标签吗？它们会被从 {affected} 个数据项上移除。{note}",
        ):
            return
        for tag in owned:
            self.taxonomy.delete_tag(tag, user_id=self._user_id, is_admin=self._is_admin)
            self._checked.discard(int(tag.id))
        self.session.commit()
        signalBus.tagsChanged.emit()
        self.toast_success(
            "标签已删除",
            f"{len(owned)} 个标签已从 {affected} 个数据项上移除",
        )

    def _on_cleanup(self) -> None:
        removed = self.taxonomy.cleanup_unused(
            user_id=self._user_id, is_admin=self._is_admin
        )
        self.session.commit()
        signalBus.tagsChanged.emit()
        self.toast_success("清理完成", f"已删除 {removed} 个未使用的标签")


__all__ = ["TAG_CHECK_COLUMN", "TAG_HEADERS", "TagPage"]
