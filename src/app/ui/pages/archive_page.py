"""存档页：带筛选与分页的存档表格，以及选中存档的条目明细。

布局为两个 Tab：Tab 1 是存档列表（筛选栏 + 表格 + 分页 + 标记/删除），
Tab 2 是选中存档的条目明细与还原操作；选中存档后自动切换到 Tab 2，也可手动切回。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
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
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    TableWidget,
)

from ...core.config import config
from ...core.runtime.signals import signalBus
from ...db import database
from ...db.models import Archive
from ...services import ArchiveService, UserService, preferred_codec
from ..dialogs import TextInputDialog
from ..components.archive_restore_dialog import RestoreDialog
from ..components.archive_rebuild_worker import ArchiveRebuildWorker
from ..framework import (
    DETAIL_MARGINS,
    PANEL_MARGINS,
    Page,
    confirm,
    format_datetime,
    format_size,
    tri_state,
    type_name,
)
from ..components.data_table import (
    TableFilterBar,
    check_cell,
    fit_columns,
    match_filters,
    prepare_table,
)
from ..components.filter_panel import FilterSection
from ..components.flow_layout import FlowLayout
from ..components.pager import Pager
from ..framework import IconTextButton, IconTextPrimaryButton, accent_color

ENTRY_STATE_LABELS = {
    "same": "一致（无需还原）",
    "changed": "内容已变化",
    "removed": "已删除",
    "missing": "存档文件缺失",
    "lost": "库内文件已丢失",
}

# 条目明细的列定义：(筛选键, 显示名, 筛选控件类型[, 选项])，与表格列一一对应。
ENTRY_COLUMNS = (
    ("name", "名称", "text"),
    ("type", "类型", "choice"),
    ("size", "大小", "number", {"unit": "size"}),
    ("category", "分类", "text"),
    ("tags", "标签", "text"),
    ("user", "所属用户", "choice"),
    ("state", "状态", "choice"),
)
ENTRY_HEADERS = ("选择",) + tuple(spec[1] for spec in ENTRY_COLUMNS)

#: 条目表第一列也是勾选框（与存档列表一致），其余列整体右移一位。
ENTRY_CHECK_COLUMN = 0

#: 与当前数据不一致的状态（`same` 之外都算），高亮 / 自动勾选 / 快速跳转都以它为准。
ENTRY_INCONSISTENT_STATES = tuple(state for state in ENTRY_STATE_LABELS if state != "same")

#: 条目筛选栏只放这几列；分类与标签另用可多选的 FilterSection（见 entry_category_section）。
ENTRY_FILTER_KEYS = ("name", "type", "size", "user", "state")
ENTRY_FILTER_COLUMNS = tuple(spec for spec in ENTRY_COLUMNS if spec[0] in ENTRY_FILTER_KEYS)

# 存档表格的列定义：(筛选键, 显示名, 筛选控件类型[, 选项])；顺序即表格列顺序，
# 筛选栏里的控件顺序也依此排列（放不下时由流式布局自动折行）。
ARCHIVE_COLUMNS = (
    ("name", "名称", "text"),
    ("note", "备注", "text"),
    ("created", "创建时间", "date"),
    ("count", "条目数", "number", {"unit": "count"}),
    ("logical", "逻辑大小", "number", {"unit": "size"}),
    ("actual", "实际占用", "number", {"unit": "size"}),
    ("dedupe", "去重率", "number", {"unit": "percent"}),
    ("pinned", "标记", "choice"),
)
ARCHIVE_PIN_OPTIONS = ("已标记", "未标记")

#: 表格第一列是批量操作用的勾选框，其余列依次对应 ARCHIVE_COLUMNS（整体右移一位）。
ARCHIVE_CHECK_COLUMN = 0
ARCHIVE_HEADERS = ("选择",) + tuple(spec[1] for spec in ARCHIVE_COLUMNS)

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
def dedupe_percent(logical_size: int, actual_size: int) -> float | None:
    """去重率（百分比数值）；省不下空间时返回 None，对应表格里的「—」。"""
    logical = int(logical_size or 0)
    actual = int(actual_size or 0)
    if logical <= 0 or actual >= logical:
        return None
    return (1 - actual / logical) * 100


def dedupe_text(logical_size: int, actual_size: int) -> str:
    """去重率的显示文本。"""
    percent = dedupe_percent(logical_size, actual_size)
    return "—" if percent is None else f"{percent:.0f}%"


def timestamp_of(value) -> float | None:
    """把时间换算成时间戳；取不到时返回 None（该行不参与时间筛选）。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    try:
        return float(value.timestamp())
    except (AttributeError, ValueError, OSError):
        return None


def archive_row_texts(
    *,
    name: str,
    note: str,
    created_at,
    item_count: int,
    stored_size: int = 0,
    logical_size: int = 0,
    pinned: bool,
) -> dict[str, str]:
    """把存档字段整理成「筛选键 -> 可匹配文本」，供逐列筛选使用。

    `stored_size` 是这份存档的内容在仓库里的真实落盘字节（跨存档去重后），
    不是创建时新增的占用，因此可以和逻辑大小一起算去重率。
    """
    return {
        "name": str(name or ""),
        "note": str(note or ""),
        "created": format_datetime(created_at, "%Y-%m-%d %H:%M:%S"),
        "count": str(int(item_count or 0)),
        "logical": format_size(logical_size),
        "actual": format_size(stored_size),
        "dedupe": dedupe_text(logical_size, stored_size),
        "pinned": "已标记" if pinned else "未标记",
    }


def filter_archives(
    row_texts: Sequence[Mapping[str, str]],
    filters: Mapping[str, object],
    numbers: Sequence[Mapping[str, object]] | None = None,
    sets: Sequence[Mapping[str, object]] | None = None,
) -> list[int]:
    """返回命中筛选的原始行索引（保持原顺序），行内的数值与集合分别由 numbers / sets 提供。"""
    return [
        index
        for index, texts in enumerate(row_texts)
        if match_filters(
            texts,
            filters,
            numbers[index] if numbers else None,
            sets[index] if sets else None,
        )
    ]


def archive_row_numbers(
    *,
    created_at,
    item_count: int,
    stored_size: int = 0,
    logical_size: int = 0,
) -> dict[str, object]:
    """把存档字段整理成「筛选键 -> 数值」，与 `archive_row_texts()` 的键一一对应。"""
    return {
        "created": timestamp_of(created_at),
        "count": float(int(item_count or 0)),
        "logical": float(int(logical_size or 0)),
        "actual": float(int(stored_size or 0)),
        "dedupe": dedupe_percent(logical_size, stored_size),
    }


def entry_row_numbers(entry) -> dict[str, object]:
    """条目的数值筛选键：大小按字节比较。"""
    return {"size": float(int(entry.size or 0))}


def entry_row_sets(entry) -> dict[str, object]:
    """条目的集合筛选键：标签逐个比较，而不是拿拼好的字符串做子串匹配。"""
    return {"tags": set(entry.tags or [])}


def entry_row_texts(entry, state: str) -> dict[str, str]:
    """把条目与其当前状态整理成「筛选键 -> 可匹配文本」，与表格里显示的文本一致。"""
    return {
        "name": str(entry.name or ""),
        "type": type_name(entry.type),
        "size": format_size(entry.size),
        "category": entry.category or "—",
        "tags": "、".join(entry.tags or []) or "—",
        "user": entry.user_name or "—",
        "state": ENTRY_STATE_LABELS.get(state, state),
    }


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


class ArchivePage(Page):
    page_name = "archivePage"
    page_title = "存档"
    #: 重建阶段 → 进度文案（与服务层 `rebuild_storage` 的 phase 值对应）
    _REBUILD_PHASES = {"restore": "还原文件", "rebuild": "重建索引"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = database.new_session()
        self.service = ArchiveService(self.session)
        self.users = UserService(self.session)
        self._user_id = 0
        self._is_admin = False
        self._archives: list = []
        self._visible: list = []
        self._page_items: list = []
        self._archive_texts: list[dict[str, str]] = []
        self._archive_numbers: list[dict[str, object]] = []
        self._selected_id: int | None = None
        self._checked: set[int] = set()
        self._checked_entries: set[int] = set()
        self._shown_archive_id: int | None = None
        self._jump_row = -1
        self._footprints: dict[int, int] = {}
        self._entries: list = []
        self._states: list = []
        self._all_entries: list = []
        self._entry_texts: list[dict[str, str]] = []
        self._entry_numbers: list[dict[str, object]] = []
        self._entry_sets: list[dict[str, object]] = []
        self._entry_states: dict[int, str] = {}
        self._rebuild_worker = None
        self._rebuild_busy = None

        self.create_button = IconTextPrimaryButton(FluentIcon.SAVE, "创建存档", self)
        self.create_button.clicked.connect(self._on_create)
        self.header.add_action(self.create_button)
        self.clean_button = IconTextButton(FluentIcon.DELETE, "清理无用文件", self)
        self.clean_button.clicked.connect(self._on_cleanup)
        self.header.add_action(self.clean_button)
        self.prune_button = IconTextButton(FluentIcon.HISTORY, "按策略清理", self)
        self.prune_button.clicked.connect(self._on_prune)
        self.header.add_action(self.prune_button)
        self.verify_button = IconTextButton(FluentIcon.CERTIFICATE, "校验存档", self)
        self.verify_button.clicked.connect(self._on_verify)
        self.header.add_action(self.verify_button)
        self.optimize_button = IconTextButton(FluentIcon.BROOM, "优化空间", self)
        self.optimize_button.clicked.connect(self._on_optimize)
        self.optimize_button.setVisible(False)
        self.header.add_action(self.optimize_button)
        self.rebuild_button = IconTextButton(FluentIcon.UPDATE, "重新加载存档文件", self)
        self.rebuild_button.clicked.connect(self._on_rebuild)
        self.rebuild_button.setVisible(False)
        self.header.add_action(self.rebuild_button)

        self.caption = CaptionLabel("", self)
        self.caption.setWordWrap(True)
        self.add_widget(self.caption)
        self.policy_label = CaptionLabel("", self)
        self.policy_label.setWordWrap(True)
        # 文案已并进「按策略清理」按钮的提示框，这里只保留对象供 tooltip 与自检读取。
        self.policy_label.setVisible(False)
        self.add_widget(self.policy_label)

        self.tabs = SegmentedWidget(self)
        for route_key, label in ARCHIVE_TABS:
            self.tabs.addItem(route_key, label)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        self.add_row(tabs_row)

        self.stack = QStackedWidget(self)
        self.add_widget(self.stack, 1)

        # ---------------------------------------------------------- Tab 1：存档列表
        archive_card = CardWidget(self.stack)
        archive_card.setMinimumHeight(260)
        archive_layout = QVBoxLayout(archive_card)
        archive_layout.setContentsMargins(*PANEL_MARGINS)
        archive_layout.setSpacing(8)

        archive_head = QHBoxLayout()
        archive_head.addWidget(SubtitleLabel("历史存档", archive_card))
        archive_head.addStretch(1)
        self.archive_stats = CaptionLabel("", archive_card)
        archive_head.addWidget(self.archive_stats)
        self.archive_reset_button = IconTextButton(FluentIcon.SYNC, "重置筛选", archive_card)
        self.archive_reset_button.setToolTip("清空存档列表的筛选条件")
        self.archive_reset_button.clicked.connect(self._on_reset_archive_filters)
        archive_head.addWidget(self.archive_reset_button)
        archive_layout.addLayout(archive_head)
        archive_layout.addLayout(self._build_selection_bar(archive_card))

        self.filter_bar = TableFilterBar(archive_card)
        self.filter_bar.configure(ARCHIVE_COLUMNS)
        self.filter_bar.set_options("pinned", ARCHIVE_PIN_OPTIONS)
        self.filter_bar.changed.connect(self._on_filter_changed)
        archive_layout.addWidget(self.filter_bar)

        self.archive_list = _ArchiveTable(archive_card)
        self.archive_table = self.archive_list
        self.archive_list.setColumnCount(len(ARCHIVE_HEADERS))
        self.archive_list.setHorizontalHeaderLabels(list(ARCHIVE_HEADERS))
        prepare_table(self.archive_list, movable=True)
        self.archive_list.setBorderVisible(True)
        self.archive_list.setBorderRadius(8)
        self.archive_list.setMinimumHeight(140)
        self.archive_list.currentCellChanged.connect(self._on_archive_row_changed)
        self.archive_list.itemChanged.connect(self._on_item_changed)
        archive_layout.addWidget(self.archive_list, 1)

        self.pager = Pager(archive_card, page_size=config.pageSize.value)
        self.pager.pageChanged.connect(self._on_page_changed)
        self.pager.pageSizeChanged.connect(self._on_page_size_changed)
        archive_layout.addWidget(self.pager)

        archive_actions = QHBoxLayout()
        self.pin_button = IconTextButton(FluentIcon.PIN, "标记存档", archive_card)
        self.pin_button.clicked.connect(self._on_toggle_pin)
        self.delete_button = IconTextButton(FluentIcon.DELETE, "删除该存档", archive_card)
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
        detail_layout.setContentsMargins(*DETAIL_MARGINS)
        detail_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择存档", detail_card)
        self.detail_meta = CaptionLabel("", detail_card)
        self.diff_label = CaptionLabel("", detail_card)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_head = QHBoxLayout()
        detail_head.addWidget(self.diff_label, 1)
        self.entry_reset_button = IconTextButton(FluentIcon.SYNC, "重置筛选", detail_card)
        self.entry_reset_button.setToolTip("清空条目明细的筛选条件（含分类与标签勾选）")
        self.entry_reset_button.clicked.connect(self._on_reset_entry_filters)
        detail_head.addWidget(self.entry_reset_button)
        detail_layout.addLayout(detail_head)

        self.entry_filter_bar = TableFilterBar(detail_card)
        self.entry_filter_bar.configure(ENTRY_FILTER_COLUMNS)
        self.entry_filter_bar.changed.connect(self._on_entry_filter_changed)
        detail_layout.addWidget(self.entry_filter_bar)

        entry_sections = FlowLayout(spacing=8)
        self.entry_category_section = FilterSection(
            "分类", detail_card, collapsed=True, icon=FluentIcon.FOLDER
        )
        self.entry_category_section.setToolTip(
            "按「用户名 / 分类」两层展开多选；只列出当前存档里出现过的分类"
        )
        self.entry_tag_section = FilterSection(
            "标签", detail_card, collapsed=True, icon=FluentIcon.TAG
        )
        self.entry_tag_section.setToolTip("勾选要保留的标签，可全选、可搜索")
        for section in (self.entry_category_section, self.entry_tag_section):
            section.setMinimumWidth(300)
            section.changed.connect(self._on_entry_filter_changed)
            entry_sections.addWidget(section)
        detail_layout.addLayout(entry_sections)

        detail_layout.addLayout(self._build_entry_selection_bar(detail_card))

        self.table = TableWidget(detail_card)
        self.table.setColumnCount(len(ENTRY_HEADERS))
        self.table.setHorizontalHeaderLabels(list(ENTRY_HEADERS))
        prepare_table(self.table, movable=True)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.itemChanged.connect(self._on_entry_item_changed)
        self.table.cellDoubleClicked.connect(self._on_entry_double_clicked)
        detail_layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        restore_button = IconTextButton(FluentIcon.SYNC, "还原选中条目", detail_card)
        restore_button.clicked.connect(self._on_restore)
        self.restore_all_button = IconTextButton(FluentIcon.SYNC, "还原整个存档", detail_card)
        self.restore_all_button.clicked.connect(self._on_restore_all)
        actions.addWidget(restore_button)
        actions.addWidget(self.restore_all_button)
        actions.addStretch(1)
        detail_layout.addLayout(actions)
        self.entries_tab = detail_card
        self.stack.addWidget(detail_card)

        self.tabs.currentItemChanged.connect(self._on_tab_changed)
        self.tabs.setCurrentItem(TAB_ARCHIVES)

        self.auto_refresh(signalBus.itemsChanged, signalBus.archivesChanged)
        signalBus.userChanged.connect(self._on_user_changed)
        self._load_identity()
        self._reload_archives()

    # ------------------------------------------------------------------ 身份
    def _load_identity(self) -> None:
        user = self.users.current()
        self._user_id = int(user.id) if user else 0
        self._is_admin = bool(user and user.is_default)
        # 普通用户也能「还原整个存档」，只是范围限于本人（服务层按 user_id 收敛）。
        self.restore_all_button.setVisible(True)
        self.rebuild_button.setVisible(self._is_admin)
        scope = (
            "默认用户可以整份回退，也可以用「所属用户」筛选后只回退某个用户的部分。"
            if self._is_admin
            else "当前用户只能查看与还原本人的条目，「还原整个存档」也只回退属于我的部分。"
        )
        self.caption.setText(
            "存档记录每个数据项的内容指纹，可回溯历史；只有全新内容才会额外占用空间。"
            "「标记存档」可让快照不被自动清理删除，只有取消标记或手动删除才会消失。" + scope
        )
        # 说明不铺在页面上：挂到标题的悬停提示里，跟着身份一起变
        self.caption.setVisible(False)
        if self.header is not None:
            self.header.set_hint(self.caption.text())
        self._update_restore_all_text()

    def _on_user_changed(self) -> None:
        self._load_identity()
        self._reload_archives()

    def refresh(self) -> None:
        """数据或存档变化时重取数；保留筛选、分页与选中项。

        `auto_refresh()` 把 `itemsChanged` / `archivesChanged` 接到这里。页面是常驻控件，
        不重取的话在数据管理里删掉一项后，条目状态会一直停在旧快照（仍显示「与当前数据一致」）。
        """
        self._reload_archives(keep_page=True)

    # ------------------------------------------------------------------ 数据
    def _refresh_policy(self) -> None:
        text = f"{self.service.policy_summary()}；创建存档时自动执行"
        self.policy_label.setText(text)
        # 自动清理说明并入手动清理按钮的提示框；标题下不再单列这一行。
        self.prune_button.setToolTip(text)

    def _reload_archives(self, *, notify: bool = False, keep_page: bool = False) -> None:
        """重新取数；`keep_page=False` 时回到第一页，筛选条件保留。

        `notify=True` 时广播 `archivesChanged`，让数据概览等页面立刻同步存档数与占用；
        `keep_page=True` 供 `refresh()` 使用，避免后台信号把用户翻到的页码冲回第一页。
        """
        self._refresh_policy()
        self._archives = self.service.history(limit=ARCHIVE_FETCH_LIMIT)
        self._footprints = self.service.footprints(self._archives)
        self._checked &= {int(archive.id) for archive in self._archives}
        self._archive_texts = [
            archive_row_texts(
                name=archive.name,
                note=archive.note,
                created_at=archive.created_at,
                item_count=archive.item_count,
                stored_size=self._footprints.get(int(archive.id), 0),
                logical_size=archive.logical_size,
                pinned=archive.pinned,
            )
            for archive in self._archives
        ]
        self._archive_numbers = [
            archive_row_numbers(
                created_at=archive.created_at,
                item_count=archive.item_count,
                stored_size=self._footprints.get(int(archive.id), 0),
                logical_size=archive.logical_size,
            )
            for archive in self._archives
        ]
        self._rebuild_visible()
        self._render_archive_rows(reset_page=not keep_page)
        self._refresh_optimize()
        if notify:
            signalBus.archivesChanged.emit()

    def _refresh_optimize(self) -> None:
        """有内容还是旧的压缩编码时提供「优化空间」入口（§9.6）。"""
        stale = self.service.store.needs_recode()
        self.optimize_button.setVisible(bool(stale))
        if stale:
            self.optimize_button.setToolTip(
                f"{stale} 份内容还是旧的压缩编码；整理会按当前方案重新压缩并回收空间"
            )

    def _rebuild_visible(self) -> None:
        indexes = filter_archives(
            self._archive_texts, self.filter_bar.filters(), self._archive_numbers
        )
        self._visible = [self._archives[index] for index in indexes]

    def _render_archive_rows(self, *, reset_page: bool = False) -> None:
        """按当前页渲染存档表格；先更新分页状态，再填充行并恢复选中项。"""
        usage = self.service.archive_usage()
        self.archive_stats.setText(
            f"共 {len(self._archives)} 个存档，筛选后 {len(self._visible)} 个 · "
            f"总占用 {format_size(int(usage.get('stored_size', 0)))}"
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
            stored = int(self._footprints.get(int(archive.id), 0))
            values = [
                archive_name_text(archive),
                archive.note or "—",
                created,
                str(archive.item_count),
                format_size(archive.logical_size),
                format_size(stored),
                dedupe_text(archive.logical_size, stored),
                "已标记" if archive.pinned else "未标记",
            ]
            table.setItem(row, ARCHIVE_CHECK_COLUMN, check_cell(int(archive.id) in self._checked))
            for offset, value in enumerate(values):
                table.setItem(row, ARCHIVE_CHECK_COLUMN + 1 + offset, _cell(str(value)))
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
        self._sync_selection()
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
        self._all_entries = []
        self._entry_texts = []
        self._entry_numbers = []
        self._entry_sets = []
        self._entry_states = {}
        self._checked_entries.clear()
        self._shown_archive_id = None
        self._jump_row = -1
        self.table.setRowCount(0)
        self.entry_category_section.set_groups([])
        self.entry_tag_section.set_items([])
        self._sync_entry_selection()
        self._update_restore_all_text()

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

    def _on_entry_filter_changed(self) -> None:
        """条目明细的筛选条件变化：只重排表格，不重新查询。"""
        self._render_entries()

    def _on_reset_archive_filters(self) -> None:
        """清空存档列表的筛选条件（`reset()` 自己会发 changed 触发重绘）。"""
        self.filter_bar.reset()

    def _on_reset_entry_filters(self) -> None:
        """清空条目明细的筛选：筛选栏 + 分类 / 标签勾选（两个分组清空时不发信号）。"""
        self.entry_category_section.clear()
        self.entry_tag_section.clear()
        self.entry_filter_bar.reset()
        self._render_entries()

    def _on_page_changed(self, _page: int) -> None:
        self._render_archive_rows()

    def _on_page_size_changed(self, size: int) -> None:
        config.set(config.pageSize, size)
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

    def _storage_hint(self) -> str:
        """内容仓库全局状态：内容份数与实际占用（§9.2 详情卡片）。"""
        usage = self.service.store.usage()
        return (
            f"仓库 {int(usage.get('contents', 0))} 份内容 · "
            f"实际占用 {format_size(int(usage.get('total_bytes', 0)))} · "
            f"压缩后 {format_size(int(usage.get('stored_bytes', 0)))}"
        )

    def _restore_plan_text(self, archive) -> str:
        """详情标签：与回档提示同源，都按 `preview_restore()` 的变更清单描述。

        以前这里用 `compare()`，它把「当前另有、存档里没有」的项也算成新增，
        于是出现过「上面写新增 1、点回档却提示无需回档」的矛盾。
        """
        user_id = None if self._is_admin else self._user_id
        try:
            report = self.service.preview_restore(archive, "restore", user_id)
            extras = self.service.compare(archive, user_id).added
        except Exception as exc:  # noqa: BLE001
            return f"无法预览这份存档：{exc}"
        tail = f"；当前另有 {len(extras)} 项不在存档中（回档会保留）" if extras else ""
        if not report.changes:
            if not archive.item_count:
                return "这份存档没有任何条目"
            return "与当前数据一致" + tail
        if not report.actionable:
            # 清单里只有「内容缺失」的行：列出来给用户看，但确实没有能回档的东西
            return f"有 {report.missing} 项内容缺失，无法回档" + tail
        # grouped() 里已经含「内容缺失 N」，不再重复拼一遍
        parts = [f"{kind} {count}" for kind, count in report.grouped()]
        if report.skipped:
            parts.append(f"跳过 {report.skipped}")
        return "回档将：" + "，".join(parts) + tail

    def _show_archive(self, archive) -> None:
        self.detail_title.setText(archive.name)
        marked = " · 【已标记】不受自动清理影响" if archive.pinned else ""
        stored = int(self._footprints.get(int(archive.id), 0))
        self.detail_meta.setText(
            f"创建时间：{format_datetime(archive.created_at, '%Y-%m-%d %H:%M:%S')} · "
            f"{archive.item_count} 项 · 逻辑 {format_size(archive.logical_size)} · "
            f"实际占用 {format_size(stored)} · "
            f"新增内容 {archive.new_blobs} 个{marked}\n{self._storage_hint()}"
        )
        self.diff_label.setText(self._restore_plan_text(archive))

        self._all_entries = self.service.visible_entries(archive, self._user_id, self._is_admin)
        self._entry_states = self.service.states_for(self._all_entries)
        self._entry_texts = [
            entry_row_texts(entry, self._entry_states.get(entry.id, ""))
            for entry in self._all_entries
        ]
        self._entry_numbers = [entry_row_numbers(entry) for entry in self._all_entries]
        self._entry_sets = [entry_row_sets(entry) for entry in self._all_entries]
        # 只保留本存档里还存在的条目（条目 id 全局唯一，但换档后旧的勾选没有意义）
        known = {int(entry.id) for entry in self._all_entries}
        self._checked_entries &= known
        fresh = int(archive.id) != self._shown_archive_id
        self._shown_archive_id = int(archive.id)
        if fresh:
            # 打开一个新存档：自动勾选与当前数据不一致的条目
            self._auto_check_inconsistent()
        self._jump_row = -1
        self._refresh_entry_options()
        self._render_entries()

    def _auto_check_inconsistent(self) -> None:
        """把与当前数据不一致的条目加进勾选集合（`same` 的条目不动）。"""
        for entry in self._all_entries:
            if self._entry_states.get(entry.id) != "same":
                self._checked_entries.add(int(entry.id))

    def _refresh_entry_options(self) -> None:
        """选项列的候选值跟着当前存档刷新（用户已选的值由筛选栏自己保留）。"""
        self.entry_filter_bar.set_options("type", sorted({texts["type"] for texts in self._entry_texts}))
        self.entry_filter_bar.set_options("user", sorted({texts["user"] for texts in self._entry_texts}))
        present = {texts["state"] for texts in self._entry_texts}
        self.entry_filter_bar.set_options(
            "state", [label for label in ENTRY_STATE_LABELS.values() if label in present]
        )
        self._refresh_entry_sections()

    def _refresh_entry_sections(self) -> None:
        """分类按「用户名 / 分类」两层展开；标签列出当前存档里出现过的标签。"""
        groups: dict[str, list[str]] = {}
        for entry in self._all_entries:
            user = entry.user_name or "未知用户"
            category = entry.category or "—"
            names = groups.setdefault(user, [])
            if category not in names:
                names.append(category)
        self.entry_category_section.set_groups(
            [(user, [(name, name) for name in names]) for user, names in groups.items()]
        )
        tags: list[str] = []
        for entry in self._all_entries:
            for tag in entry.tags or []:
                if tag not in tags:
                    tags.append(tag)
        self.entry_tag_section.set_items([(tag, tag) for tag in sorted(tags)])

    def _entry_filters(self) -> dict[str, object]:
        """筛选栏条件 + 分类 / 标签的可多选条件，合成一份用于逐行匹配的条件。"""
        filters = dict(self.entry_filter_bar.filters())
        categories = self.entry_category_section.checked_keys()
        if categories:
            filters["category"] = set(categories)
        tags = self.entry_tag_section.checked_keys()
        if tags:
            filters["tags"] = set(tags)
        return filters

    def _entry_filter_active(self) -> bool:
        return bool(self._entry_filters())

    def _render_entries(self) -> None:
        """按筛选条件过滤后填充条目表格；没有筛选时就是全部可见条目。"""
        indexes = filter_archives(
            self._entry_texts, self._entry_filters(), self._entry_numbers, self._entry_sets
        )
        self._entries = [self._all_entries[index] for index in indexes]
        self._states = [self._entry_states.get(self._all_entries[index].id, "") for index in indexes]
        self._jump_row = -1
        state_column = ENTRY_HEADERS.index("状态")
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for row, index in enumerate(indexes):
            texts = self._entry_texts[index]
            entry = self._all_entries[index]
            state = self._states[row]
            self.table.setItem(
                row, ENTRY_CHECK_COLUMN, check_cell(int(entry.id) in self._checked_entries)
            )
            values = [texts[spec[0]] for spec in ENTRY_COLUMNS]
            for column, value in enumerate(values, start=ENTRY_CHECK_COLUMN + 1):
                cell = _cell(str(value))
                if state != "same":
                    self._mark_inconsistent(cell, emphasized=column == state_column, state=state)
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        fit_columns(self.table, min_width=72, max_width=240)
        self._sync_entry_selection()
        self._update_restore_all_text()

    @staticmethod
    def _mark_inconsistent(cell, *, emphasized: bool, state: str) -> None:
        """把「与当前数据不一致」的行标出来：整行加粗，状态列再用主题强调色。"""
        font = cell.font()
        font.setBold(True)
        cell.setFont(font)
        label = ENTRY_STATE_LABELS.get(state, state)
        cell.setToolTip(f"与当前数据不一致：{label}")
        if emphasized:
            cell.setForeground(accent_color())

    def _update_restore_all_text(self) -> None:
        """还原按钮的文字与提示跟着作用域走：有筛选时只回档筛出来的条目。"""
        if self._entry_filter_active():
            self.restore_all_button.setText(f"还原筛选结果（{len(self._entries)} 项）")
            self.restore_all_button.setToolTip("只回档下方筛选出的条目，便于按所属用户分批回退")
        else:
            self.restore_all_button.setText("还原整个存档")
            self.restore_all_button.setToolTip(self._restore_all_hint())

    def _restore_all_hint(self) -> str:
        if self._is_admin:
            return "整份回档所有用户的数据；先按「所属用户」筛选可只回退某个用户的部分"
        return "整份回档本存档中属于我的条目"

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

    # ------------------------------------------------------------------ 勾选
    def _build_selection_bar(self, parent: QWidget) -> QHBoxLayout:
        """选择条：单个三态全选框 + 批量操作，与表格勾选双向同步。"""
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.select_all_box = CheckBox("全选本页", parent)
        self.select_all_box.setTristate(True)
        self.select_all_box.setToolTip(
            "空 = 全不选，横杠 = 部分选中，勾 = 全选本页；点一下切换全选/全不选"
        )
        self.select_all_box.clicked.connect(self._on_select_all_clicked)
        bar.addWidget(self.select_all_box)

        self.selection_label = CaptionLabel("未选择存档", parent)
        bar.addWidget(self.selection_label)
        bar.addStretch(1)

        self.batch_pin_button = IconTextButton(FluentIcon.PIN, "批量标记", parent)
        self.batch_pin_button.setToolTip("标记所有勾选的存档，使其不被自动清理删除")
        self.batch_pin_button.clicked.connect(lambda: self._on_batch_pin(True))
        self.batch_unpin_button = IconTextButton(FluentIcon.UNPIN, "批量取消标记", parent)
        self.batch_unpin_button.setToolTip("取消所有勾选存档的标记，之后才能删除")
        self.batch_unpin_button.clicked.connect(lambda: self._on_batch_pin(False))
        self.batch_delete_button = IconTextButton(FluentIcon.DELETE, "批量删除", parent)
        self.batch_delete_button.setToolTip("删除所有勾选的存档；已标记的存档需先取消标记")
        self.batch_delete_button.clicked.connect(self._on_batch_delete)
        for button in (self.batch_pin_button, self.batch_unpin_button, self.batch_delete_button):
            bar.addWidget(button)
        return bar

    def _page_ids(self) -> list[int]:
        return [int(archive.id) for archive in self._page_items]

    def checked_archives(self) -> list:
        """当前勾选的存档（按列表顺序），供批量操作与自检脚本使用。"""
        return [archive for archive in self._archives if int(archive.id) in self._checked]

    def _sync_selection(self) -> None:
        """三态全选框、已选数量与批量按钮跟随勾选状态。"""
        ids = self._page_ids()
        state = tri_state(sum(1 for archive_id in ids if archive_id in self._checked), len(ids))
        self.select_all_box.blockSignals(True)
        self.select_all_box.setCheckState(state)
        self.select_all_box.blockSignals(False)
        count = len(self._checked)
        self.selection_label.setText(f"已选 {count} 个存档" if count else "未选择存档")
        for button in (self.batch_pin_button, self.batch_unpin_button, self.batch_delete_button):
            button.setEnabled(bool(count))

    def _apply_page_check_states(self) -> None:
        """把 _checked 写回本页表格的勾选框（阻断信号，避免回环）。"""
        table = self.archive_list
        table.blockSignals(True)
        for row, archive in enumerate(self._page_items):
            item = table.item(row, ARCHIVE_CHECK_COLUMN)
            if item is not None:
                checked = int(archive.id) in self._checked
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        table.blockSignals(False)
        self._sync_selection()

    def _set_page_checked(self, checked: bool) -> None:
        for archive in self._page_items:
            if checked:
                self._checked.add(int(archive.id))
            else:
                self._checked.discard(int(archive.id))
        self._apply_page_check_states()

    def select_all(self) -> None:
        """勾选本页全部存档。"""
        self._set_page_checked(True)

    def select_none(self) -> None:
        """取消本页全部勾选。"""
        self._set_page_checked(False)

    def invert_selection(self) -> None:
        """反转本页勾选状态。"""
        for archive in self._page_items:
            archive_id = int(archive.id)
            if archive_id in self._checked:
                self._checked.discard(archive_id)
            else:
                self._checked.add(archive_id)
        self._apply_page_check_states()

    def _on_select_all_clicked(self) -> None:
        """点一下：本页没有全选就全选，已全选就全不选（横杠只是部分选中的显示状态）。"""
        ids = self._page_ids()
        all_checked = bool(ids) and all(archive_id in self._checked for archive_id in ids)
        self._set_page_checked(not all_checked)

    def _on_item_changed(self, item) -> None:
        if item.column() != ARCHIVE_CHECK_COLUMN:
            return
        row = item.row()
        if not 0 <= row < len(self._page_items):
            return
        archive_id = int(self._page_items[row].id)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(archive_id)
        else:
            self._checked.discard(archive_id)
        self._sync_selection()

    # ------------------------------------------------------------ 条目勾选
    def _build_entry_selection_bar(self, parent: QWidget) -> QHBoxLayout:
        """条目侧选择条：三态全选 + 已选计数 + 只选不一致 / 清空选择 / 跳到下一个不一致。"""
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.entry_select_all_box = CheckBox("全选本页", parent)
        self.entry_select_all_box.setTristate(True)
        self.entry_select_all_box.setToolTip(
            "空 = 全不选，横杠 = 部分选中，勾 = 全选本页；点一下切换全选/全不选"
        )
        self.entry_select_all_box.clicked.connect(self._on_entry_select_all_clicked)
        bar.addWidget(self.entry_select_all_box)

        self.entry_selection_label = CaptionLabel("未选择条目", parent)
        bar.addWidget(self.entry_selection_label)
        bar.addStretch(1)

        self.entry_inconsistent_button = IconTextButton(FluentIcon.SYNC, "只选不一致", parent)
        self.entry_inconsistent_button.setToolTip(
            "只勾选与当前数据不一致的条目（已变化 / 已删除 / 存档文件缺失 / 库内文件已丢失）"
        )
        self.entry_inconsistent_button.clicked.connect(self.select_inconsistent_entries)
        self.entry_clear_button = IconTextButton(FluentIcon.CANCEL, "清空选择", parent)
        self.entry_clear_button.setToolTip("取消所有条目勾选")
        self.entry_clear_button.clicked.connect(self.select_no_entries)
        self.entry_jump_button = IconTextButton(FluentIcon.DOWN, "下一个不一致", parent)
        self.entry_jump_button.setToolTip(
            "滚动到并选中下一个与当前数据不一致的条目（到底后回到第一个）；"
            "双击不一致行可切到数据管理页定位该数据项"
        )
        self.entry_jump_button.clicked.connect(self.jump_to_next_inconsistent)
        for button in (
            self.entry_inconsistent_button,
            self.entry_clear_button,
            self.entry_jump_button,
        ):
            bar.addWidget(button)
        return bar

    def checked_entries(self) -> list:
        """当前视图里勾选的条目（按表格顺序），供还原与自检使用。"""
        return [entry for entry in self._entries if int(entry.id) in self._checked_entries]

    def _sync_entry_selection(self) -> None:
        """三态全选框、已选数量与操作按钮跟随条目勾选状态。"""
        ids = [int(entry.id) for entry in self._entries]
        checked = sum(1 for entry_id in ids if entry_id in self._checked_entries)
        self.entry_select_all_box.blockSignals(True)
        self.entry_select_all_box.setCheckState(tri_state(checked, len(ids)))
        self.entry_select_all_box.blockSignals(False)
        self.entry_selection_label.setText(f"已选 {checked} 项" if checked else "未选择条目")
        self.entry_inconsistent_button.setEnabled(bool(ids))
        self.entry_clear_button.setEnabled(bool(self._checked_entries))
        self.entry_jump_button.setEnabled(any(state != "same" for state in self._states))

    def _apply_entry_check_states(self) -> None:
        """把 _checked_entries 写回条目表的勾选框（阻断信号，避免回环）。"""
        table = self.table
        table.blockSignals(True)
        for row, entry in enumerate(self._entries):
            item = table.item(row, ENTRY_CHECK_COLUMN)
            if item is not None:
                checked = int(entry.id) in self._checked_entries
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        table.blockSignals(False)
        self._sync_entry_selection()

    def _set_entries_checked(self, checked: bool) -> None:
        for entry in self._entries:
            if checked:
                self._checked_entries.add(int(entry.id))
            else:
                self._checked_entries.discard(int(entry.id))
        self._apply_entry_check_states()

    def select_all_entries(self) -> None:
        """勾选当前视图里的全部条目。"""
        self._set_entries_checked(True)

    def select_no_entries(self) -> None:
        """取消所有条目勾选。"""
        self._checked_entries.clear()
        self._apply_entry_check_states()

    def select_inconsistent_entries(self) -> None:
        """只勾选与当前数据不一致的条目。"""
        self._checked_entries.clear()
        for entry in self._entries:
            if self._entry_states.get(entry.id) != "same":
                self._checked_entries.add(int(entry.id))
        self._apply_entry_check_states()

    def _on_entry_select_all_clicked(self) -> None:
        """点一下：当前视图没有全选就全选，已全选就全不选（横杠只是部分选中的显示状态）。"""
        ids = [int(entry.id) for entry in self._entries]
        all_checked = bool(ids) and all(entry_id in self._checked_entries for entry_id in ids)
        self._set_entries_checked(not all_checked)

    def _on_entry_item_changed(self, item) -> None:
        if item.column() != ENTRY_CHECK_COLUMN:
            return
        row = item.row()
        if not 0 <= row < len(self._entries):
            return
        entry_id = int(self._entries[row].id)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked_entries.add(entry_id)
        else:
            self._checked_entries.discard(entry_id)
        self._sync_entry_selection()

    def jump_to_next_inconsistent(self) -> None:
        """滚动到并选中下一个不一致条目（到底后回到第一个），方便逐个检查。"""
        rows = [row for row, state in enumerate(self._states) if state != "same"]
        if not rows:
            self.toast_warning("没有不一致的条目", "当前视图里没有与现有数据不一致的条目")
            return
        target = next((row for row in rows if row > self._jump_row), rows[0])
        self._jump_row = target
        self.table.setCurrentCell(target, ENTRY_HEADERS.index("名称"))
        self.table.selectRow(target)
        item = self.table.item(target, ENTRY_CHECK_COLUMN)
        if item is not None:
            self.table.scrollToItem(item)

    def _on_entry_double_clicked(self, row: int, _column: int) -> None:
        """双击不一致条目：切到「数据管理页」并定位该数据项（定位不到时给提示）。"""
        if not 0 <= row < len(self._entries) or self._states[row] == "same":
            return
        item_id = self._locatable_item_id(self._entries[row])
        if item_id is None:
            self.toast_warning("无法定位", "这条数据在当前库里已经找不到，只能在存档内查看")
            return
        signalBus.focusItem.emit(item_id)

    def _locatable_item_id(self, entry) -> int | None:
        """条目在当前库里对应的数据项 id（按 id 命中才算，仅同名不算）。"""
        if not entry.item_id:
            return None
        if self.service.items.by_ids([entry.item_id]):
            return int(entry.item_id)
        return None

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
        busy = self.busy("正在创建存档", "正在为所有数据项计算内容指纹")
        try:
            archive = self.service.create(note=dialog.value())
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("创建存档失败")
            self.toast_error("创建存档失败", str(exc))
            return
        self.session.commit()
        self._auto_cleanup()
        self._reload_archives(notify=True)
        busy.finish(f"{archive.name}（{archive.item_count} 项）")
        self.toast_success("存档已创建", f"{archive.name}（{archive.item_count} 项）")

    def _on_restore(self) -> None:
        # 优先还原勾选的条目；一个都没勾选时退回「表格里选中的行」，保持旧手感
        entries = self.checked_entries()
        if not entries:
            rows = sorted({index.row() for index in self.table.selectedItems()})
            entries = [self._entries[row] for row in rows if row < len(self._entries)]
        if not entries:
            self.toast_warning("未选择条目", "请先勾选「存档内条目」中要还原的数据项")
            return
        self._confirm_and_restore(entries, "restore")

    def _on_restore_all(self) -> None:
        archive = self._current_archive()
        if archive is None:
            self.toast_warning("未选择存档", "请先在「存档列表」中选择要还原的存档")
            return
        # 有筛选时只回档筛出来的条目，没有筛选时才整档回退。
        scope = list(self._entries) if self._entry_filter_active() else archive
        if not scope:
            self.toast_warning("没有可回档的条目", "当前筛选没有匹配的条目")
            return
        self._confirm_and_restore(scope, "restore")

    def _restore_user_id(self) -> int | None:
        """默认用户可以回档任何用户的数据，其余用户只回档自己的。"""
        return None if self._is_admin else self._user_id

    def _confirm_and_restore(self, scope, mode: str = "restore"):
        """先预览变更清单：没有变更就直接提示；有变更时弹窗确认，可先给当前数据存档。

        管理员整档回档时弹窗里可切换成覆盖式（§5.7）；切换后重新预演刷新清单。
        """
        user_id = self._restore_user_id()
        allow_mirror = self._is_admin and isinstance(scope, Archive)

        def preview_for(new_mode: str):
            try:
                return self.service.preview_restore(scope, new_mode, user_id)
            except Exception as exc:  # noqa: BLE001
                self.toast_error("无法预览回档", str(exc))
                return None

        report = preview_for(mode)
        if report is None:
            return None
        if report.is_empty:
            if report.missing:
                self.toast_warning("无法回档", f"所选 {report.missing} 项内容缺失，没有可回档的数据")
                return None
            detail = "所选内容与当前数据一致，没有需要变更的数据"
            if report.skipped:
                detail = f"所选 {report.skipped} 项都与当前数据一致，没有需要变更的数据"
            self.toast_success("无需回档", detail)
            return None
        chosen = mode
        snapshot_before = False
        if config.restorePreview.value:
            dialog = RestoreDialog(
                report,
                self.window(),
                allow_mirror=allow_mirror,
                preview=preview_for,
            )
            if not dialog.exec():
                return None
            chosen = dialog.mode
            snapshot_before = dialog.choice == RestoreDialog.SNAPSHOT
        busy = self.busy("正在回档", "按变更清单恢复数据项")
        try:
            result = self.service.restore(scope, chosen, user_id, snapshot_before=snapshot_before)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("回档失败")
            self.toast_error("回档失败", str(exc))
            return None
        self.session.commit()
        signalBus.itemsChanged.emit()
        busy.finish(result.summary())
        self.toast_success("回档完成", result.summary())
        return result

    def _on_toggle_pin(self) -> None:
        archive = self._current_archive()
        if archive is None:
            self.toast_warning("未选择存档", "请先在「存档列表」中选择要标记的存档")
            return
        pinned = self.service.set_pinned(archive, not archive.pinned)
        self.session.commit()
        self._reload_archives()
        if pinned:
            self.toast_success("已标记存档", f"「{archive.name}」不会被自动清理删除")
        else:
            self.toast_success("已取消标记", f"「{archive.name}」将重新参与自动清理")

    def _on_delete(self) -> None:
        archive = self._current_archive()
        if archive is None:
            return
        if archive.pinned:
            # 已标记的存档受保护：必须先取消标记，才能手动删除。
            self.toast_warning("无法删除", f"「{archive.name}」已标记，请先取消标记再删除")
            return
        if not confirm(
            self,
            "删除存档",
            f"确定删除存档「{archive.name}」吗？数据本身不会被删除。",
        ):
            return
        if not self.service.delete(archive):
            self.toast_warning("无法删除", f"「{archive.name}」已标记，请先取消标记再删除")
            return
        self.session.commit()
        self._checked.discard(int(archive.id))
        self._selected_id = None
        self._auto_cleanup()
        self._reload_archives(notify=True)
        self.toast_success("存档已删除", archive.name)

    def _on_batch_pin(self, pinned: bool) -> None:
        """批量标记 / 批量取消标记勾选的存档。"""
        archives = self.checked_archives()
        if not archives:
            self.toast_warning("未选择存档", "请先勾选要批量操作的存档")
            return
        for archive in archives:
            self.service.set_pinned(archive, pinned)
        self.session.commit()
        self._reload_archives()
        if pinned:
            self.toast_success("已批量标记", f"{len(archives)} 个存档不会被自动清理删除")
        else:
            self.toast_success("已批量取消标记", f"{len(archives)} 个存档将重新参与自动清理")

    def _on_batch_delete(self) -> None:
        """批量删除勾选的存档；已标记的存档会被跳过，需要先取消标记。"""
        archives = self.checked_archives()
        if not archives:
            self.toast_warning("未选择存档", "请先勾选要批量删除的存档")
            return
        pinned = [archive for archive in archives if archive.pinned]
        removable = [archive for archive in archives if not archive.pinned]
        if not removable:
            self.toast_warning(
                "无法删除", f"勾选的 {len(pinned)} 个存档都已标记，请先取消标记再删除"
            )
            return
        note = f"其中 {len(pinned)} 个已标记的存档会被跳过，需要先取消标记。" if pinned else ""
        if not confirm(
            self,
            "批量删除存档",
            f"确定删除勾选的 {len(removable)} 个存档吗？数据本身不会被删除。{note}",
        ):
            return
        for archive in removable:
            self.service.delete(archive)
        self.session.commit()
        self._checked -= {int(archive.id) for archive in removable}
        self._selected_id = None
        self._auto_cleanup()
        self._reload_archives(notify=True)
        suffix = f"，跳过 {len(pinned)} 个已标记" if pinned else ""
        self.toast_success("存档已删除", f"删除 {len(removable)} 个存档{suffix}")

    # ------------------------------------------------------------ 维护与校验
    def _require_default_user(self, action: str) -> bool:
        if self._is_admin:
            return True
        self.toast_warning(f"无法{action}", "只有默认用户可以重新加载存档文件")
        return False

    def _on_verify(self) -> None:
        """校验存档（§8）：先快速校验，有问题再问是否做深度校验。"""
        busy = self.busy("正在校验存档", "比对索引与内容仓库的物理文件")
        try:
            report = self.service.verify("quick")
        except Exception as exc:  # noqa: BLE001
            busy.finish("校验失败")
            self.toast_error("校验失败", str(exc))
            return
        busy.finish("快速校验完成")
        if report.ok:
            self.toast_success("校验通过", report.summary())
            return
        self.toast_warning("发现异常", report.summary())
        if confirm(
            self,
            "深度校验",
            f"{report.summary()}\n\n是否继续深度校验？会解码每份内容、重算校验和，耗时较长。",
        ):
            self._verify_deep()

    def _verify_deep(self) -> None:
        busy = self.busy("正在深度校验存档", "解码每份内容并重算校验和，可能需要一会儿")
        try:
            report = self.service.verify("deep")
        except Exception as exc:  # noqa: BLE001
            busy.finish("深度校验失败")
            self.toast_error("深度校验失败", str(exc))
            return
        busy.finish("深度校验完成")
        if report.ok:
            self.toast_success("深度校验通过", report.summary())
        else:
            self.toast_error("校验发现异常", report.summary())

    def _on_optimize(self) -> None:
        """手动优化：先回收无引用内容，再按当前方案重压旧编码（顺序不能反）。"""
        busy = self.busy("正在优化空间", "按当前压缩方案重压旧内容，并回收空间")
        try:
            self.service.store.cleanup()
            stats = self.service.store.recompress()
        except Exception as exc:  # noqa: BLE001
            self.toast_error("优化失败", str(exc))
            busy.finish("优化失败")
            return
        freed = int(stats.get("freed", 0))
        recoded = int(stats.get("recoded", 0))
        busy.finish(f"重压 {recoded} 份内容")
        self._refresh_optimize()
        self._reload_archives(notify=True)
        self.toast_success(
            "已优化空间",
            f"{recoded} 份内容改用 {preferred_codec()}，回收 {format_size(freed)}",
        )

    def _set_maintenance_enabled(self, enabled: bool) -> None:
        """重建期间禁用所有写存档入口，避免与服务层的互斥检查相互打架。"""
        for button in (
            self.create_button,
            self.clean_button,
            self.prune_button,
            self.delete_button,
            self.pin_button,
            self.restore_all_button,
            self.rebuild_button,
        ):
            button.setEnabled(enabled)

    def _on_rebuild(self) -> None:
        """重新加载存档文件（§5.8）：预检 → 确认 → 后台重建 + 实时进度。"""
        if not self._require_default_user("重新加载存档文件"):
            return
        if self._rebuild_worker is not None and self._rebuild_worker.isRunning():
            self.toast_warning("正在重新加载存档文件", "请等待当前任务结束")
            return
        try:
            plan = self.service.plan_rebuild()
        except Exception as exc:  # noqa: BLE001
            self.toast_error("无法重新加载存档文件", str(exc))
            return
        if not plan.entries:
            self.toast_warning("没有可重建的条目", "当前没有任何存档条目")
            return
        if not confirm(
            self,
            "重新加载存档文件",
            f"将按索引重建物理层：\n{plan.summary()}\n\n"
            "重建期间无法创建存档或回档；内容已丢失的条目会保留旧索引。继续吗？",
        ):
            return
        worker = ArchiveRebuildWorker(self)
        worker.progressed.connect(self._on_rebuild_progress)
        worker.finished_job.connect(self._on_rebuild_finished)
        self._rebuild_worker = worker
        self._rebuild_busy = self.busy("正在重新加载存档文件", "正在准备…")
        self._set_maintenance_enabled(False)
        worker.start()

    def _on_rebuild_progress(self, phase: str, index: int, total: int, detail: str) -> None:
        label = self._REBUILD_PHASES.get(phase, phase)
        text = f"{label} {index}/{total}"
        if detail:
            text += f" · {detail}"
        if self._rebuild_busy is not None:
            self._rebuild_busy.update(text)

    def _on_rebuild_finished(self, payload: dict) -> None:
        if self._rebuild_busy is not None:
            self._rebuild_busy.finish("已完成" if payload.get("ok") else "未完成")
            self._rebuild_busy = None
        self._set_maintenance_enabled(True)
        if self._rebuild_worker is not None:
            self._rebuild_worker.deleteLater()
            self._rebuild_worker = None
        self.session.expire_all()
        self._reload_archives()
        if not payload.get("ok"):
            self.toast_error("重新加载存档文件失败", str(payload.get("error") or "未知错误"))
            return
        self.toast_success("已重新加载存档文件", str(payload.get("summary") or ""))

    def auto_cleanup_startup(self) -> None:
        """启动后延迟一次自动清理（§6）：静默回收，顺手刷新策略文案。"""
        self._auto_cleanup()
        self._refresh_policy()

    def _auto_cleanup(self) -> None:
        """自动清理（§6）：启动后延迟一次，创建 / 删除存档、按策略清理之后各跑一次。

        `Auto-Cleanup` 关闭时内部直接返回；失败不应打断主流程，因此只回滚不提示。
        """
        try:
            self.service.auto_cleanup()
            self.session.commit()
        except Exception:  # noqa: BLE001 - 自动清理失败不影响主流程
            self.session.rollback()

    def _on_prune(self) -> None:
        busy = self.busy("正在按策略清理存档", "删除超出策略的较早快照，并释放不再被引用的内容")
        try:
            removed, freed = self.service.auto_prune()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("清理失败")
            self.toast_error("清理失败", str(exc))
            return
        self.session.commit()
        self._auto_cleanup()
        self._reload_archives(notify=True)
        if removed:
            busy.finish(f"删除 {removed} 个存档")
            self.toast_success("已按策略清理", f"删除 {removed} 个较早的存档，释放 {format_size(freed)}")
        else:
            busy.finish("无需清理")
            self.toast_warning("无需清理", self.service.policy_summary())

    def _on_cleanup(self) -> None:
        busy = self.busy("正在清理无用文件", "没有任何索引引用的内容文件会被删除")
        count, freed = self.service.cleanup_orphans()
        self.session.commit()
        self._reload_archives(notify=True)
        busy.finish(f"清理 {count} 项无用内容")
        if count:
            self.toast_success("已清理", f"清理 {count} 项无用内容，释放 {format_size(freed)}")
        else:
            self.toast_warning("无需清理", "没有发现无用文件")


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setToolTip(text)
    return item


__all__ = [
    "ARCHIVE_CHECK_COLUMN",
    "ARCHIVE_COLUMNS",
    "ARCHIVE_HEADERS",
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
