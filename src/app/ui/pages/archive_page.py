"""存档页：内容寻址快照、历史对比与还原。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QListWidgetItem, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...services import ArchiveService, UserService
from ..common import BusyTip, confirm, format_datetime, format_size, toast_error, toast_success, toast_warning, type_name
from ..dialogs import TextInputDialog

ENTRY_STATE_LABELS = {
    "same": "一致（无需还原）",
    "changed": "内容已变化",
    "removed": "已删除",
    "missing": "存档文件缺失",
}


class ArchivePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("archivePage")
        self.session = database.new_session()
        self.service = ArchiveService(self.session)
        self.users = UserService(self.session)
        self._user_id = 0
        self._is_admin = False
        self._archives: list = []
        self._entries: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("存档", self))
        header.addStretch(1)
        create_button = PrimaryPushButton(FluentIcon.SAVE, "创建存档", self)
        create_button.clicked.connect(self._on_create)
        header.addWidget(create_button)
        clean_button = PushButton(FluentIcon.DELETE, "清理无用文件", self)
        clean_button.clicked.connect(self._on_cleanup)
        header.addWidget(clean_button)
        prune_button = PushButton(FluentIcon.HISTORY, "按策略清理", self)
        prune_button.clicked.connect(self._on_prune)
        header.addWidget(prune_button)
        root.addLayout(header)
        self.caption = CaptionLabel("", self)
        root.addWidget(self.caption)
        self.policy_label = CaptionLabel("", self)
        root.addWidget(self.policy_label)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = CardWidget(self)
        left.setFixedWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(SubtitleLabel("历史存档", left))
        self.archive_list = ListWidget(left)
        self.archive_list.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.archive_list, 1)
        body.addWidget(left)

        right = CardWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择存档", right)
        self.detail_meta = CaptionLabel("", right)
        self.diff_label = CaptionLabel("", right)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_meta)
        right_layout.addWidget(self.diff_label)

        self.table = TableWidget(right)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["名称", "类型", "大小", "分类", "标签", "所属用户", "状态"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        restore_button = PushButton(FluentIcon.SYNC, "还原选中条目", right)
        restore_button.clicked.connect(self._on_restore)
        self.restore_all_button = PushButton(FluentIcon.SYNC, "还原整个存档", right)
        self.restore_all_button.clicked.connect(self._on_restore_all)
        self.pin_button = PushButton(FluentIcon.PIN, "标记存档", right)
        self.pin_button.clicked.connect(self._on_toggle_pin)
        delete_button = PushButton(FluentIcon.DELETE, "删除该存档", right)
        delete_button.clicked.connect(self._on_delete)
        actions.addWidget(restore_button)
        actions.addWidget(self.restore_all_button)
        actions.addWidget(self.pin_button)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        right_layout.addLayout(actions)
        body.addWidget(right, 1)

        root.addLayout(body, 1)

        signalBus.itemsChanged.connect(self._reload_archives)
        signalBus.archivesChanged.connect(self._reload_archives)
        signalBus.userChanged.connect(self._on_user_changed)
        self._load_identity()
        self._reload_archives()

    # ------------------------------------------------------------------ 身份
    def _load_identity(self) -> None:
        user = self.users.current()
        self._user_id = int(user.id) if user else 0
        self._is_admin = bool(user and user.is_default)
        self.restore_all_button.setVisible(self._is_admin)
        scope = (
            "默认用户可以整档回退，或还原任意用户的单条条目。"
            if self._is_admin
            else "当前用户只能查看与还原本人的条目。"
        )
        self.caption.setText(
            "存档记录每个数据项的内容指纹，可回溯历史；只有全新内容才会额外占用空间。"
            "「标记存档」可让快照不被自动清理删除，只有取消标记或手动删除才会消失。" + scope
        )

    def _on_user_changed(self) -> None:
        self._load_identity()
        self._reload_archives()

    # ------------------------------------------------------------------ 数据
    def _refresh_policy(self) -> None:
        self.policy_label.setText(f"{self.service.policy_summary()}；创建存档时自动执行")

    def _reload_archives(self) -> None:
        self._refresh_policy()
        self._archives = self.service.history()
        current = self.archive_list.currentRow()
        self.archive_list.clear()
        for archive in self._archives:
            created = format_datetime(archive.created_at, "%Y-%m-%d %H:%M:%S")
            prefix = "【已标记】" if archive.pinned else ""
            item = QListWidgetItem(f"{prefix}{archive.name}　{created}　{archive.item_count} 项")
            self.archive_list.addItem(item)
        if self._archives:
            self.archive_list.setCurrentRow(min(max(current, 0), len(self._archives) - 1))
        else:
            self.detail_title.setText("还没有存档")
            self.detail_meta.setText("点击右上角「创建存档」即可生成第一个快照")
            self.diff_label.setText("")
            self._entries = []
            self._states = []
            self.table.setRowCount(0)
        self._update_pin_button()

    def _current_archive(self):
        row = self.archive_list.currentRow()
        if 0 <= row < len(self._archives):
            return self._archives[row]
        return None

    def _on_select(self, _row: int) -> None:
        archive = self._current_archive()
        if archive is None:
            return
        self.detail_title.setText(archive.name)
        marked = " · 【已标记】不受自动清理影响" if archive.pinned else ""
        self.detail_meta.setText(
            f"创建时间：{format_datetime(archive.created_at, '%Y-%m-%d %H:%M:%S')} · "
            f"{archive.item_count} 项 · {format_size(archive.total_size)} · "
            f"新增内容 {archive.new_blobs} 个{marked}"
        )
        diff = self.service.compare(archive, None if self._is_admin else self._user_id)
        if diff.is_empty:
            self.diff_label.setText("与当前数据一致")
        else:
            parts = []
            if diff.added:
                parts.append(f"新增 {len(diff.added)}")
            if diff.removed:
                parts.append(f"已删除 {len(diff.removed)}")
            if diff.changed:
                parts.append(f"内容变化 {len(diff.changed)}")
            if diff.missing_blobs:
                parts.append(f"文件缺失 {len(diff.missing_blobs)}")
            self.diff_label.setText("与当前数据对比：" + "，".join(parts))

        self._entries = self.service.visible_entries(archive, self._user_id, self._is_admin)
        self._states = [self.service.entry_state(entry) for entry in self._entries]
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                entry.name,
                type_name(entry.type),
                format_size(entry.size),
                entry.category or "—",
                "、".join(entry.tags or []) or "—",
                entry.user_name or "—",
                ENTRY_STATE_LABELS.get(self._states[row], self._states[row]),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, _cell(str(value)))
        self._update_pin_button()

    def _update_pin_button(self) -> None:
        """标记按钮跟随选中存档的状态：未选中时禁用，已标记时显示为取消标记。"""
        archive = self._current_archive()
        self.pin_button.setEnabled(archive is not None)
        pinned = bool(archive is not None and archive.pinned)
        self.pin_button.setText("取消标记" if pinned else "标记存档")
        self.pin_button.setIcon(FluentIcon.UNPIN if pinned else FluentIcon.PIN)
        self.pin_button.setToolTip(
            "取消标记后该存档会重新参与自动清理"
            if pinned
            else "标记后该存档不会被自动清理删除"
        )

    # ------------------------------------------------------------------ 操作
    def _on_create(self) -> None:
        dialog = TextInputDialog(
            "创建存档",
            "为这次快照写一句说明（可留空）",
            parent=self.window(),
            hint="存档会记录当前所有数据项的内容指纹",
        )
        if not dialog.exec():
            return
        busy = BusyTip(self, "正在创建存档", "正在为所有数据项计算内容指纹")
        try:
            archive = self.service.create(note=dialog.value())
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("创建存档失败")
            toast_error(self, "创建存档失败", str(exc))
            return
        self.session.commit()
        self._reload_archives()
        busy.finish(f"{archive.name}（{archive.item_count} 项）")
        toast_success(self, "存档已创建", f"{archive.name}（{archive.item_count} 项）")

    def _on_restore(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedItems()})
        if not rows:
            toast_warning(self, "未选择条目", "请先在右侧表格中选择要还原的数据项")
            return
        candidates = [
            row for row in rows if row < len(self._entries) and self._states[row] != "same"
        ]
        if not candidates:
            toast_warning(
                self, "无法还原", "所选条目与当前数据一致，只有不一致的数据才需要还原"
            )
            return
        restored = 0
        for row in candidates:
            if self.service.restore_entry(self._entries[row]) is not None:
                restored += 1
        self.session.commit()
        signalBus.itemsChanged.emit()
        if restored:
            toast_success(self, "已还原", f"{restored} 项数据已恢复")
        else:
            toast_warning(self, "无法还原", "存档内容已缺失，无法恢复")

    def _on_restore_all(self) -> None:
        archive = self._current_archive()
        if archive is None:
            toast_warning(self, "未选择存档", "请先在左侧选择要还原的存档")
            return
        if not confirm(
            self,
            "还原整个存档",
            f"确定把「{archive.name}」的全部条目还原到各自用户的分类目录吗？",
        ):
            return
        busy = BusyTip(self, "正在还原整个存档", "条目会放回其所属用户的分类目录")
        try:
            stats = self.service.restore_all(archive)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("还原失败")
            toast_error(self, "还原失败", str(exc))
            return
        self.session.commit()
        signalBus.itemsChanged.emit()
        busy.finish(f"还原 {stats['restored']} 项")
        toast_success(self, "整档还原完成", f"成功 {stats['restored']} 项，跳过 {stats['skipped']} 项")

    def _on_toggle_pin(self) -> None:
        archive = self._current_archive()
        if archive is None:
            toast_warning(self, "未选择存档", "请先在左侧选择要标记的存档")
            return
        pinned = self.service.set_pinned(archive, not archive.pinned)
        self.session.commit()
        self._reload_archives()
        if pinned:
            toast_success(self, "已标记存档", f"「{archive.name}」不会被自动清理删除")
        else:
            toast_success(self, "已取消标记", f"「{archive.name}」将重新参与自动清理")

    def _on_delete(self) -> None:
        archive = self._current_archive()
        if archive is None:
            return
        note = "该存档已标记，删除后不再受清理保护。" if archive.pinned else ""
        if not confirm(
            self,
            "删除存档",
            f"确定删除存档「{archive.name}」吗？数据本身不会被删除。{note}",
        ):
            return
        self.service.delete(archive)
        self.session.commit()
        self._reload_archives()
        toast_success(self, "存档已删除", archive.name)

    def _on_prune(self) -> None:
        busy = BusyTip(self, "正在按策略清理存档", "删除超出策略的较早快照，并释放不再被引用的内容")
        try:
            removed, freed = self.service.auto_prune()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("清理失败")
            toast_error(self, "清理失败", str(exc))
            return
        self.session.commit()
        self._reload_archives()
        if removed:
            busy.finish(f"删除 {removed} 个存档")
            toast_success(self, "已按策略清理", f"删除 {removed} 个较早的存档，释放 {format_size(freed)}")
        else:
            busy.finish("无需清理")
            toast_warning(self, "无需清理", self.service.policy_summary())

    def _on_cleanup(self) -> None:
        busy = BusyTip(self, "正在清理无用文件", "仓库中未被任何存档引用的内容会被删除")
        count, freed = self.service.cleanup_orphans()
        self.session.commit()
        busy.finish(f"移除 {count} 个无用文件")
        if count:
            toast_success(self, "已清理", f"移除 {count} 个无用文件，释放 {format_size(freed)}")
        else:
            toast_warning(self, "无需清理", "没有发现无用文件")


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


__all__ = ["ArchivePage"]
