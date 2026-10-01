"""存档页：带筛选与分页的存档表格，以及选中存档的条目明细。

布局为两个 Tab：Tab 1 是存档列表（筛选栏 + 表格 + 分页 + 标记/删除），
Tab 2 是选中存档的条目明细与还原操作；选中存档后自动切换到 Tab 2，也可手动切回。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    TableWidget,
    TitleLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...services import ArchiveService, UserService
from ..common import (
    BusyTip,
    confirm,
    format_datetime,
    format_size,
    toast_error,
    toast_success,
    toast_warning,
    type_name,
)
from ..dialogs import TextInputDialog
from ..widgets.data_table import TableFilterBar, fit_columns, prepare_table
from ..widgets.pager import Pager

ENTRY_STATE_LABELS = {
    "same": "一致（无需还原）",
    "changed": "内容已变化",
    "removed": "已删除",
    "missing": "存档文件缺失",
}

# 存档表格的列定义：(筛选键, 显示名, 筛选控件类型)
ARCHIVE_COLUMNS = (
    ("name", "名称", "text"),
    ("note", "备注", "text"),
    ("created", "创建时间", "text"),
    ("count", "条目数", "text"),
    ("size", "大小", "text"),
    ("pinned", "标记", "choice"),
)
ARCHIVE_PIN_OPTIONS = ("已标记", "未标记")

ARCHIVE_PAGE_SIZE = 50
ARCHIVE_FETCH_LIMIT = 1000

# 两个 Tab：(路由键, 标题)；顺序与 QStackedWidget 页序一致。
TAB_ARCHIVES = "archives"
TAB_ENTRIES = "entries"
ARCHIVE_TABS = ((TAB_ARCHIVES, "存档列表"), (TAB_ENTRIES, "存档内条目"))
TAB_INDEX = {route_key: index for index, (route_key, _) in enumerate(ARCHIVE_TABS)}


def tab_index(route_key: str) -> int:
    """路由键对应的堆栈页号；未知键回退到第一个 Tab。"""
    return TAB_INDEX.get(str(route_key), 0)


# ---------------------------------------------------------------------- 纯逻辑
def match_filters(row_texts: Mapping[str, str], filters: Mapping[str, str]) -> bool:
    """逐列判断一行是否命中筛选：所有非空条件都必须满足（不区分大小写）。"""
    for key, value in filters.items():
        if not value:
            continue
        if str(value).lower() not in str(row_texts.get(key, "")).lower():
            return False
    return True


def archive_row_texts(
    *,
    name: str,
    note: str,
    created_at,
    item_count: int,
    total_size: int,
    pinned: bool,
) -> dict[str, str]:
    """把存档字段整理成「筛选键 -> 可匹配文本」，供逐列筛选使用。"""
    return {
        "name": str(name or ""),
        "note": str(note or ""),
        "created": format_datetime(created_at, "%Y-%m-%d %H:%M:%S"),
        "count": str(int(item_count or 0)),
        "size": format_size(total_size),
        "pinned": "已标记" if pinned else "未标记",
    }


def filter_archives(
    row_texts: Sequence[Mapping[str, str]], filters: Mapping[str, str]
) -> list[int]:
    """返回命中筛选的原始行索引（保持原顺序）。"""
    return [index for index, texts in enumerate(row_texts) if match_filters(texts, filters)]


def page_bounds(total: int, page: int, page_size: int) -> tuple[int, int]:
    """当前页对应的切片 [start, stop)，页码越界时自动裁剪。"""
    size = max(1, int(page_size))
    total = max(0, int(total))
    pages = max(1, -(-total // size))
    current = max(0, min(int(page), pages - 1))
    start = current * size
    return start, min(total, start + size)


def archive_name_text(archive) -> str:
    """存档名称单元格文本：已标记的存档带前缀，便于一眼识别。"""
    return ("【已标记】" if archive.pinned else "") + archive.name


class _ArchiveTable(TableWidget):
    """存档表格：保留旧列表控件的行访问接口，便于既有校验脚本沿用 `archive_list`。"""

    def setCurrentRow(self, row: int) -> None:  # noqa: N802 - Qt 命名
        self.selectRow(int(row))

    def item(self, row: int, column: int = 0):  # type: ignore[override]
        return super().item(row, column)


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
        self._visible: list = []
        self._page_items: list = []
        self._archive_texts: list[dict[str, str]] = []
        self._selected_id: int | None = None
        self._entries: list = []
        self._states: list = []

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

        body = QVBoxLayout()
        body.setSpacing(12)

        tabs_row = QHBoxLayout()
        self.tabs = SegmentedWidget(self)
        for route_key, label in ARCHIVE_TABS:
            self.tabs.addItem(route_key, label)
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        body.addLayout(tabs_row)

        self.stack = QStackedWidget(self)
        body.addWidget(self.stack, 1)

        # ---------------------------------------------------------- Tab 1：存档列表
        archive_card = CardWidget(self.stack)
        archive_card.setMinimumHeight(260)
        archive_layout = QVBoxLayout(archive_card)
        archive_layout.setContentsMargins(12, 12, 12, 12)
        archive_layout.setSpacing(8)

        archive_head = QHBoxLayout()
        archive_head.addWidget(SubtitleLabel("历史存档", archive_card))
        archive_head.addStretch(1)
        self.archive_stats = CaptionLabel("", archive_card)
        archive_head.addWidget(self.archive_stats)
        archive_layout.addLayout(archive_head)

        self.filter_bar = TableFilterBar(archive_card)
        self.filter_bar.configure(ARCHIVE_COLUMNS)
        self.filter_bar.set_options("pinned", ARCHIVE_PIN_OPTIONS)
        self.filter_bar.changed.connect(self._on_filter_changed)
        archive_layout.addWidget(self.filter_bar)

        self.archive_list = _ArchiveTable(archive_card)
        self.archive_table = self.archive_list
        self.archive_list.setColumnCount(len(ARCHIVE_COLUMNS))
        self.archive_list.setHorizontalHeaderLabels([label for _, label, _ in ARCHIVE_COLUMNS])
        prepare_table(self.archive_list, movable=True)
        self.archive_list.setBorderVisible(True)
        self.archive_list.setBorderRadius(8)
        self.archive_list.setMinimumHeight(140)
        self.archive_list.currentCellChanged.connect(self._on_archive_row_changed)
        archive_layout.addWidget(self.archive_list, 1)

        self.pager = Pager(archive_card, page_size=ARCHIVE_PAGE_SIZE)
        self.pager.pageChanged.connect(self._on_page_changed)
        self.pager.pageSizeChanged.connect(self._on_page_size_changed)
        archive_layout.addWidget(self.pager)

        archive_actions = QHBoxLayout()
        self.pin_button = PushButton(FluentIcon.PIN, "标记存档", archive_card)
        self.pin_button.clicked.connect(self._on_toggle_pin)
        self.delete_button = PushButton(FluentIcon.DELETE, "删除该存档", archive_card)
        self.delete_button.clicked.connect(self._on_delete)
        archive_actions.addWidget(self.pin_button)
        archive_actions.addWidget(self.delete_button)
        archive_actions.addStretch(1)
        archive_layout.addLayout(archive_actions)
        self.archive_tab = archive_card
        self.stack.addWidget(archive_card)

        # ---------------------------------------------------------- Tab 2：条目明细
        detail_card = CardWidget(self.stack)
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择存档", detail_card)
        self.detail_meta = CaptionLabel("", detail_card)
        self.diff_label = CaptionLabel("", detail_card)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addWidget(self.diff_label)

        self.table = TableWidget(detail_card)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["名称", "类型", "大小", "分类", "标签", "所属用户", "状态"]
        )
        prepare_table(self.table, movable=True)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        detail_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        restore_button = PushButton(FluentIcon.SYNC, "还原选中条目", detail_card)
        restore_button.clicked.connect(self._on_restore)
        self.restore_all_button = PushButton(FluentIcon.SYNC, "还原整个存档", detail_card)
        self.restore_all_button.clicked.connect(self._on_restore_all)
        actions.addWidget(restore_button)
        actions.addWidget(self.restore_all_button)
        actions.addStretch(1)
        detail_layout.addLayout(actions)
        self.entries_tab = detail_card
        self.stack.addWidget(detail_card)

        self.tabs.currentItemChanged.connect(self._on_tab_changed)
        self.tabs.setCurrentItem(TAB_ARCHIVES)

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
        """重新取数并重置到第一页；筛选条件保留。"""
        self._refresh_policy()
        self._archives = self.service.history(limit=ARCHIVE_FETCH_LIMIT)
        self._archive_texts = [
            archive_row_texts(
                name=archive.name,
                note=archive.note,
                created_at=archive.created_at,
                item_count=archive.item_count,
                total_size=archive.total_size,
                pinned=archive.pinned,
            )
            for archive in self._archives
        ]
        self._rebuild_visible()
        self._render_archive_rows(reset_page=True)

    def _rebuild_visible(self) -> None:
        indexes = filter_archives(self._archive_texts, self.filter_bar.filters())
        self._visible = [self._archives[index] for index in indexes]

    def _render_archive_rows(self, *, reset_page: bool = False) -> None:
        """按当前页渲染存档表格；先更新分页状态，再填充行并恢复选中项。"""
        self.archive_stats.setText(
            f"共 {len(self._archives)} 个存档，筛选后 {len(self._visible)} 个"
        )
        self.pager.set_state(
            len(self._visible), 0 if reset_page else self.pager.page, self.pager.page_size
        )
        start, stop = page_bounds(len(self._visible), self.pager.page, self.pager.page_size)
        self._page_items = self._visible[start:stop]

        table = self.archive_list
        table.blockSignals(True)
        table.setRowCount(len(self._page_items))
        for row, archive in enumerate(self._page_items):
            created = format_datetime(archive.created_at, "%Y-%m-%d %H:%M:%S")
            values = [
                archive_name_text(archive),
                archive.note or "—",
                created,
                str(archive.item_count),
                format_size(archive.total_size),
                "已标记" if archive.pinned else "未标记",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, _cell(str(value)))
        table.blockSignals(False)
        fit_columns(table, min_width=72, max_width=240)

        target = next(
            (row for row, archive in enumerate(self._page_items) if archive.id == self._selected_id),
            -1,
        )
        if target < 0 and self._page_items:
            target = 0
        table.blockSignals(True)
        if target >= 0:
            table.setCurrentCell(target, 0)
        else:
            table.clearSelection()
            table.setCurrentCell(-1, -1)
        table.blockSignals(False)
        if target >= 0:
            self._selected_id = int(self._page_items[target].id)
            self._show_archive(self._page_items[target])
        else:
            self._clear_detail()
        self._update_pin_button()

    def _clear_detail(self) -> None:
        if not self._archives:
            self.detail_title.setText("还没有存档")
            self.detail_meta.setText("点击右上角「创建存档」即可生成第一个快照")
        elif not self._visible:
            self.detail_title.setText("没有匹配的存档")
            self.detail_meta.setText("调整筛选条件后再试")
        else:
            self.detail_title.setText("未选择存档")
            self.detail_meta.setText("请在「存档列表」中选择一个存档")
        self.diff_label.setText("")
        self._entries = []
        self._states = []
        self.table.setRowCount(0)

    def _current_archive(self):
        row = self.archive_list.currentRow()
        if 0 <= row < len(self._page_items):
            return self._page_items[row]
        return None

    # ------------------------------------------------------------------ Tab
    def tab_keys(self) -> list[str]:
        """两个 Tab 的路由键（与页序一致）。"""
        return [route_key for route_key, _ in ARCHIVE_TABS]

    def current_tab(self) -> str:
        """当前 Tab 的路由键。"""
        return self.tabs.currentRouteKey() or TAB_ARCHIVES

    def switch_tab(self, route_key: str) -> None:
        """切换到指定 Tab；未知路由键不做任何事。"""
        if route_key in TAB_INDEX:
            self.tabs.setCurrentItem(route_key)

    def _on_tab_changed(self, route_key: str) -> None:
        self.stack.setCurrentIndex(tab_index(route_key))

    def _on_filter_changed(self) -> None:
        self._rebuild_visible()
        self._render_archive_rows(reset_page=True)

    def _on_page_changed(self, _page: int) -> None:
        self._render_archive_rows()

    def _on_page_size_changed(self, _size: int) -> None:
        self._render_archive_rows(reset_page=True)

    def _on_archive_row_changed(
        self, current_row: int, _current_col: int, _previous_row: int, _previous_col: int
    ) -> None:
        self._on_select(current_row)
        if current_row >= 0:
            # 用户手动选中存档：自动跳到条目明细；程序化刷新不会走到这里（信号被阻断）。
            self.switch_tab(TAB_ENTRIES)

    def _on_select(self, _row: int = -1) -> None:
        archive = self._current_archive()
        if archive is None:
            self._clear_detail()
            self._update_pin_button()
            return
        self._selected_id = int(archive.id)
        self._show_archive(archive)
        self._update_pin_button()

    def _show_archive(self, archive) -> None:
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
        fit_columns(self.table, min_width=72, max_width=240)

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
            toast_warning(self, "未选择条目", "请先在「存档内条目」中选择要还原的数据项")
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
            toast_warning(self, "未选择存档", "请先在「存档列表」中选择要还原的存档")
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
            toast_warning(self, "未选择存档", "请先在「存档列表」中选择要标记的存档")
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
        self._selected_id = None
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
    item.setToolTip(text)
    return item


__all__ = [
    "ARCHIVE_COLUMNS",
    "ARCHIVE_PAGE_SIZE",
    "ARCHIVE_TABS",
    "ArchivePage",
    "TAB_ARCHIVES",
    "TAB_ENTRIES",
    "archive_name_text",
    "archive_row_texts",
    "filter_archives",
    "match_filters",
    "page_bounds",
    "tab_index",
]
