"""数据管理页：分类树 + 筛选面板 + 列表/卡片视图 + 批量操作。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    AdaptiveFlowLayout,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    FlowLayout,
    MessageBox,
    PushButton,
    RoundMenu,
    SegmentedWidget,
    StrongBodyLabel,
    TransparentToolButton,
)

from ...core.runtime.signals import signalBus
from ...services.editor_service import (
    EditorInfo,
    edit_path as edit_path_with,
    edit_with,
    editors_for,
    open_system as edit_open_system,
)
from ...services.viewer_service import ViewerInfo, open_path, open_system, open_viewer_with, viewers_for
from ...db import database
from ...db.models import DataItem, DataType
from ...db.seed import UNCATEGORIZED_NAME
from ...repositories import (
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
)
from ...sdk import ExtensionPoint
from ...sdk.items import SelectionContext
from ...services import (
    ExportService,
    ItemService,
    TaxonomyService,
    UserService,
    cover_service,
    is_uncategorized,
    reconcile_categories,
)
from ...services.item_api import to_ref
from ..framework import (
    icon_label,
    PAGE_SPACING,
    PANEL_MARGINS,
    Page,
    SCROLL_GUTTER,
    clear_scroll_background,
    confirm,
    format_size,
    open_path as open_folder,
    release_widget,
    tri_state,
    type_name,
)
from ..framework.contributions import icon_of, items, resolve, resolve_with, value_of
from ..dialogs import (
    BatchRenameDialog,
    CategoryConflictDialog,
    CategoryDeleteDialog,
    CategoryMoveDialog,
    CategoryPickerDialog,
    CategorySelectDialog,
    DuplicateDialog,
    ItemEditDialog,
    KeywordManagerDialog,
    TagManagerDialog,
    TextInputDialog,
)
from ...core.config import DOUBLE_CLICK_EDITOR, config, export_dir
from ..components.export_dialog import ExportDialog
from ..components.category_tree import SORT_MODES as CATEGORY_SORT_MODES
from ..components.category_tree import CategoryTree
from ..components.cover_loader import cover_loader
from ..components.filter_panel import FilterPanel
from ..components.item_card import (
    LIST_COLUMNS,
    VIEW_SIZE_KEYS,
    VIEW_SIZE_LABELS,
    ItemCard,
    ItemListRow,
    ListHeader,
    view_size_key,
    view_size_preset,
)
from ..components.pager import Pager, normalize_page_size, selection_summary
from ..framework import IconTextButton

TOOLBAR_BUTTON_HEIGHT = 32
TOOLBAR_MAX_ROWS = 2
TOOLBAR_ROW_SPACING = 4
TOOLBAR_SCROLLBAR_HEIGHT = 16

#: 分类栏里「复选框」与「批量操作」是两套互不相干的机制（用户 m00117 第 6 条、m01932 第 2 条）：
#: 复选框只决定中间列表显示哪些数据（只有直接点复选框才会变）；批量重命名 / 移动 / 删除
#: 一律在按钮弹出的弹窗里现挑分类。
CATEGORY_TREE_TIP = (
    "复选框决定中间列表显示哪些数据（只有直接点复选框才会变）；"
    "批量重命名 / 移动 / 删除请在按钮弹出的弹窗里选分类"
)


def range_ids(order: list[int], anchor: int, target: int) -> set[int]:
    """Shift 多选：返回 anchor 到 target 之间的全部 id（按当前页顺序，含两端）。"""
    if anchor not in order or target not in order:
        return {target}
    start, end = sorted((order.index(anchor), order.index(target)))
    return set(order[start : end + 1])


#: 右键菜单条目：标识 → (单选文本, 多选文本)；多选文本里的 {count} 会替换成选中数量。
MENU_LABELS: dict[str, tuple[str, str]] = {
    "open": ("直接打开", "直接打开"),
    "open_with": ("查看器", "查看器"),
    "editor": ("编辑器", "编辑器"),
    "reveal": ("在文件夹中显示", "在文件夹中显示"),
    "copy": ("复制路径", "复制路径"),
    "move": ("移动到分类…", "移动到分类…（{count} 项）"),
    "edit": ("编辑信息", "编辑信息"),
    "tag": ("标签管理", "标签管理（{count} 项）"),
    "keyword": ("关键词管理", "关键词管理（{count} 项）"),
    "rename": ("批量重命名…", "批量重命名…（{count} 项）"),
    "hidden": ("隐藏 / 取消隐藏", "隐藏 / 取消隐藏"),
    "export": ("导出选中项", "导出选中项（{count}）"),
    "export_zip": ("导出为压缩包…", "导出为压缩包…（{count} 项）"),
    "delete": ("移入回收站", "移入回收站（{count}）"),
    "restore": ("从回收站还原", "从回收站还原"),
    "purge": ("彻底删除", "彻底删除（{count}）"),
    "details": ("详情", "详情"),
}
MENU_ICONS: dict[str, FluentIcon] = {
    "open": FluentIcon.VIEW,
    "editor": FluentIcon.EDIT,
    "reveal": FluentIcon.FOLDER,
    "copy": FluentIcon.COPY,
    "move": FluentIcon.MOVE,
    "edit": FluentIcon.EDIT,
    "tag": FluentIcon.TAG,
    "keyword": FluentIcon.DICTIONARY,
    "rename": FluentIcon.FONT,
    "hidden": FluentIcon.VIEW,
    "export": FluentIcon.SAVE,
    "export_zip": FluentIcon.ZIP_FOLDER,
    "delete": FluentIcon.DELETE,
    "restore": FluentIcon.SYNC,
    "purge": FluentIcon.CLOSE,
    "details": FluentIcon.INFO,
}
MENU_SEPARATORS_AFTER = frozenset({"open_with", "editor", "copy", "rename", "hidden", "purge"})
MENU_SINGLE_ONLY = frozenset({"edit", "details"})


def menu_items(count: int = 1) -> tuple[tuple[str, str], ...]:
    """右键菜单条目 (标识, 文本)：多选时带上数量提示，单项操作由页面禁用。"""
    many = count > 1
    return tuple(
        (key, more.format(count=count) if many else one)
        for key, (one, more) in MENU_LABELS.items()
    )


def open_with_items(suffix: str) -> tuple[tuple[str, str, ViewerInfo | None], ...]:
    """「查看器」子菜单：(标识, 文本, 查看器)，标识为 system / viewer:<id> / ask。"""
    entries: list[tuple[str, str, ViewerInfo | None]] = [("system", "系统默认程序", None)]
    entries += [
        (f"viewer:{viewer.id}", f"{viewer.name}（{viewer.plugin_id}）", viewer)
        for viewer in viewers_for(suffix)
    ]
    entries.append(("ask", "交给系统选择…", None))
    return tuple(entries)


def editor_menu_items(suffix: str) -> tuple[tuple[str, str, EditorInfo | None], ...]:
    """「编辑器」子菜单：(标识, 文本, 编辑器)，标识为 system / editor:<id> / ask。

    没启用编辑器插件时只剩「系统默认程序」与「交给系统选择…」——菜单本身照旧在
    （与查看器子菜单一致），因为它是程序本体内置的，不依赖插件贡献。
    """
    entries: list[tuple[str, str, EditorInfo | None]] = [("system", "系统默认程序", None)]
    entries += [
        (f"editor:{editor.id}", f"{editor.name}（{editor.plugin_id}）", editor)
        for editor in editors_for(suffix)
    ]
    entries.append(("ask", "交给系统选择…", None))
    return tuple(entries)


class _ToolbarView(QScrollArea):
    """工具栏容器：宽度变化时通知页面重新计算高度。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_resized = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        callback = self.on_resized
        if callback is not None:
            callback()


class ManagePage(Page):
    page_name = "managePage"
    page_title = "数据管理"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 导出目录的入口放在标题区：导出的压缩包、整库备份都落在那儿
        self.export_dir_button = IconTextButton(FluentIcon.FOLDER, "打开导出文件夹", self)
        self.export_dir_button.setToolTip("打开导出目录——导出的压缩包与整库备份都在这里")
        self.export_dir_button.clicked.connect(self._on_open_export_dir)
        self.header.add_action(self.export_dir_button)
        self.session = database.new_session()
        self._checked_categories: set[int] = set()
        self._checked_items: set[int] = set()
        self._syncing_tree = False
        self.item_service = ItemService(self.session)
        self.taxonomy = TaxonomyService(self.session)
        self.item_repo = ItemRepository(self.session)
        self.tag_repo = TagRepository(self.session)
        self.category_repo = CategoryRepository(self.session)
        self.user_service = UserService(self.session)

        self._items = []
        self._selected: set[int] = set()
        self._anchor: int | None = None
        self._syncing = False
        self._category_id: int | None = None
        self._mode = "list"
        #: 显示大小档位（用户 m04164 第 1 条）：控件构造时尺寸就定死了，换档要整批重建
        self._view_size = view_size_key(config.viewSize.value)
        #: 两种视图各自的控件池：刷新时按位复用，避免整页销毁重建。
        self._rows: dict[str, list[QWidget]] = {"list": [], "card": []}
        #: 卡片高度要等布局跑完才量得准，延后一轮再量（见 `_fit_cards`）
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_cards)
        self._unlocked = False
        self._page = 0
        self._page_size = normalize_page_size(config.pageSize.value)
        self._total = 0
        self.restore_button: PushButton | None = None
        self._buttons: dict[str, PushButton] = {}
        self._plugin_buttons: list[PushButton] = []

        columns = QHBoxLayout()
        columns.setSpacing(PAGE_SPACING)
        self.tree_card = self._build_tree_panel()
        self.filter_card = self._build_filter_panel()
        columns.addWidget(self.tree_card, 0)
        columns.addWidget(self._build_center(), 1)
        columns.addWidget(self.filter_card, 0)
        self.body.addLayout(columns, 1)

        self.auto_refresh(
            signalBus.itemsChanged,
            signalBus.categoriesChanged,
            signalBus.tagsChanged,
            signalBus.userChanged,
        )
        signalBus.pluginsChanged.connect(self._sync_plugin_buttons)
        self.refresh()

    # ------------------------------------------------------------------ 构件
    def _build_tree_panel(self) -> QWidget:
        card = CardWidget(self)
        card.setFixedWidth(240)
        card.setMinimumHeight(420)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(StrongBodyLabel("分类", card))
        title_row.addStretch(1)
        layout.addLayout(title_row)

        self.tree = CategoryTree(card)
        # 分类树的说明改挂在树上：说明文字不显示，鼠标停住才弹出
        self.tree.setToolTip(CATEGORY_TREE_TIP)
        self.tree.categorySelected.connect(self._on_category_selected)
        self.tree.fileSelected.connect(self.focus_item)
        self.tree.checkedChanged.connect(self._on_category_checked)
        self.tree.actionRequested.connect(self._on_tree_action)
        self.tree.sortChanged.connect(self._on_category_sort_changed)
        layout.addWidget(self.tree, 1)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(6)
        self.category_sort_box = ComboBox(card)
        for mode, label in CATEGORY_SORT_MODES:
            self.category_sort_box.addItem(label, userData=mode)
        self.category_sort_box.setToolTip("分类排序方式：按名称、按数据量、按最新导入数据时间")
        self.category_sort_box.currentIndexChanged.connect(self._on_category_sort_mode_changed)
        self.category_sort_reverse_button = TransparentToolButton(
            FluentIcon.UP, card
        )
        self.category_sort_reverse_button.setCheckable(True)
        self.category_sort_reverse_button.setToolTip("正序 / 逆序切换")
        self.category_sort_reverse_button.toggled.connect(self._on_category_sort_reverse_toggled)
        sort_row.addWidget(self.category_sort_box, 1)
        sort_row.addWidget(self.category_sort_reverse_button)
        layout.addLayout(sort_row)
        self._load_category_sort_config()

        self.only_categories_box = CheckBox("仅显示分类", card)
        self.only_categories_box.setToolTip(
            "勾选时分类栏只显示分类；取消勾选后每个分类下面列出该分类文件夹里的文件，"
            "没有分类的文件列在「全部数据」下面"
        )
        self.only_categories_box.setChecked(bool(config.onlyShowCategories.value))
        self.only_categories_box.toggled.connect(self._on_only_categories_toggled)
        layout.addWidget(self.only_categories_box)
        self.category_hint = CaptionLabel(CATEGORY_TREE_TIP, card)
        self.category_hint.setVisible(False)
        layout.addWidget(self.category_hint)
        self.category_rename_button = IconTextButton(FluentIcon.EDIT, "批量重命名", card)
        self.category_rename_button.setToolTip("在弹窗里选分类，按规则批量重命名；「未分类」不能重命名")
        self.category_rename_button.clicked.connect(self._on_category_batch_rename)
        self.category_move_button = IconTextButton(FluentIcon.MOVE, "批量移动", card)
        self.category_move_button.setToolTip(
            "点一个加一个，把要移动的分类攒进「分类移动表」，再选一个目标分类；"
            "根分类、存在上级 / 下级关系的分类不能一起移动"
        )
        self.category_move_button.clicked.connect(self._on_category_batch_move)
        self.category_delete_button = IconTextButton(FluentIcon.DELETE, "批量删除", card)
        self.category_delete_button.setToolTip(
            "在弹窗里选分类后删除，其中的数据会变成未分类；根分类与「未分类」不能删除"
        )
        self.category_delete_button.clicked.connect(self._on_category_batch_delete)
        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)
        batch_row.addWidget(self.category_rename_button)
        batch_row.addWidget(self.category_move_button)
        batch_row.addWidget(self.category_delete_button)
        batch_row.addStretch(1)
        layout.addLayout(batch_row)

        add_button = IconTextButton(FluentIcon.ADD, "新建分类", card)
        add_button.clicked.connect(lambda: self._on_tree_action("add", None))
        layout.addWidget(add_button)
        sync_button = IconTextButton(FluentIcon.SYNC, "按目录同步", card)
        sync_button.setToolTip(
            "分类就是库文件夹里的目录：这里按磁盘上的真实目录刷新分类树"
            "（新目录变成分类、被删掉的目录连同空分类一起收掉、找不到位置的文件重新对齐）"
        )
        sync_button.clicked.connect(self._on_category_sync)
        layout.addWidget(sync_button)
        return card

    def _build_center(self) -> QWidget:
        # 与左侧「分类」卡片保持同一套外观：卡片 + 统一内边距。
        card = CardWidget(self)
        card.setMinimumHeight(420)
        host = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.view_switch = SegmentedWidget(host)
        self.view_switch.addItem("list", "列表", onClick=lambda: self._set_mode("list"))
        self.view_switch.addItem("card", "卡片", onClick=lambda: self._set_mode("card"))
        self.view_switch.setCurrentItem("list")
        header.addWidget(self.view_switch)
        header.addSpacing(12)
        header.addWidget(icon_label(FluentIcon.FIT_PAGE, "显示大小", host))
        self.size_box = ComboBox(host)
        self.size_box.setMinimumWidth(90)
        self.size_box.setToolTip("封面与条目的显示大小（列表与卡片都跟着变）")
        for key in VIEW_SIZE_KEYS:
            self.size_box.addItem(VIEW_SIZE_LABELS[key], userData=key)
        self.size_box.setCurrentIndex(max(0, VIEW_SIZE_KEYS.index(self._view_size)))
        self.size_box.currentIndexChanged.connect(self._on_view_size_changed)
        header.addWidget(self.size_box)
        header.addSpacing(12)
        self.tree_toggle_button = IconTextButton(FluentIcon.MENU, "分类栏", host)
        self.tree_toggle_button.setCheckable(True)
        self.tree_toggle_button.setToolTip("显示/隐藏左侧分类栏")
        self.tree_toggle_button.toggled.connect(self.tree_card.setVisible)
        self.tree_toggle_button.toggled.connect(self._save_panel_visibility)
        self.filter_toggle_button = IconTextButton(FluentIcon.FILTER, "筛选栏", host)
        self.filter_toggle_button.setCheckable(True)
        self.filter_toggle_button.setToolTip("显示/隐藏右侧筛选栏")
        self.filter_toggle_button.toggled.connect(self.filter_card.setVisible)
        self.filter_toggle_button.toggled.connect(self._save_panel_visibility)
        # 两栏显隐跟着配置走：上次关掉的，这次打开还是关着的
        self.tree_toggle_button.setChecked(bool(config.showCategoryPanel.value))
        self.filter_toggle_button.setChecked(bool(config.showFilterPanel.value))
        self.tree_card.setVisible(self.tree_toggle_button.isChecked())
        self.filter_card.setVisible(self.filter_toggle_button.isChecked())
        header.addWidget(self.tree_toggle_button)
        header.addWidget(self.filter_toggle_button)
        header.addStretch(1)
        header.addWidget(icon_label(FluentIcon.PEOPLE, "用户", host))
        self.user_box = ComboBox(host)
        self.user_box.setMinimumWidth(140)
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        header.addWidget(self.user_box)
        self.count_label = CaptionLabel("", host)
        header.addWidget(self.count_label)
        layout.addLayout(header)
        layout.addWidget(self._build_toolbar(host))
        layout.addWidget(self._build_selection_bar(host))

        self.stack = QStackedWidget(host)
        self.list_view, self.list_layout = _make_scroll(host)
        self.card_view, self.card_layout = _make_scroll(
            host, adaptive=True, card_min_width=view_size_preset(self._view_size).card_min_width
        )
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.card_view)
        # 表头放在滚动区之外（纵向滚动不会把它带走），横向滚动靠 set_offset 同步
        self.list_header = ListHeader(host, self._view_size)
        layout.addWidget(self.list_header)
        layout.addWidget(self.stack, 1)
        self.list_view.horizontalScrollBar().valueChanged.connect(self.list_header.set_offset)

        self.pager = Pager(host, self._page_size)
        self.pager.pageChanged.connect(self._on_page_changed)
        self.pager.pageSizeChanged.connect(self._on_page_size_changed)
        layout.addWidget(self.pager)
        return host

    def _build_toolbar(self, parent: QWidget) -> QWidget:
        """工具栏：流式排列，宽度不足时换行，仍然放不下则纵向滚动。"""
        scroll = _ToolbarView(parent)
        scroll.on_resized = self._fit_toolbar
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        host = QWidget(scroll)
        flow = FlowLayout(host, needAni=False, isTight=True)
        flow.setHorizontalSpacing(6)
        flow.setVerticalSpacing(TOOLBAR_ROW_SPACING)
        flow.setContentsMargins(0, 0, 0, 0)
        buttons = [
            ("move", FluentIcon.MOVE, "移动到分类", self._on_move),
            ("edit", FluentIcon.EDIT, "编辑", self._on_edit),
            ("tag", FluentIcon.TAG, "标签管理", self._on_manage_tags),
            ("keyword", FluentIcon.DICTIONARY, "关键词管理", self._on_manage_keywords),
            ("rename", FluentIcon.FONT, "批量重命名", self._on_batch_rename),
            ("hidden", FluentIcon.VIEW, "隐藏/显示", self._on_toggle_hidden),
            ("delete", FluentIcon.DELETE, "删除", self._on_delete),
            ("restore", FluentIcon.SYNC, "还原", self._on_restore),
            ("purge", FluentIcon.CLOSE, "彻底删除", self._on_purge),
            ("export", FluentIcon.SAVE, "导出", self._on_export),
            ("export_zip", FluentIcon.ZIP_FOLDER, "导出为 ZIP", self._on_export_zip),
            ("duplicates", FluentIcon.COPY, "重复项", self._on_duplicates),
            ("refresh", FluentIcon.SYNC, "刷新", self.refresh),
        ]
        for key, icon, text, slot in buttons:
            button = IconTextButton(icon, text, host)
            button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
            button.clicked.connect(slot)
            flow.addWidget(button)
            self._buttons[key] = button
        self.restore_button = self._buttons["restore"]
        self.restore_button.setEnabled(False)
        # 已在回收站的数据不能再移入回收站，没选中可删项时按钮保持置灰。
        self._buttons["delete"].setEnabled(False)
        self._plugin_buttons = self._plugin_button_list(flow, host)
        scroll.setWidget(host)
        clear_scroll_background(scroll)
        self.toolbar_view = scroll
        self.toolbar_layout = flow
        scroll.setFixedHeight(TOOLBAR_BUTTON_HEIGHT + TOOLBAR_ROW_SPACING)
        return scroll

    def _plugin_button_list(self, flow: FlowLayout, host: QWidget) -> list[PushButton]:
        """按扩展点贡献建工具栏按钮（app.ui.manage.toolbar）。

        回调按签名调用：写 1 个参数就收到 `SelectionContext`（当前勾选的条目），
        写 0 个参数按旧行为调用，插件不必为了兼容而改写。
        """
        buttons: list[PushButton] = []
        for item in items(ExtensionPoint.MANAGE_TOOLBAR):
            data = value_of(item)
            button = IconTextButton(icon_of(data.get("icon")), str(data.get("text") or item.name), host)
            button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
            tip = str(data.get("tip") or item.description or "")
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(
                lambda _checked=False, cb=data.get("callback"): resolve_with(cb, self.selection_context())
            )
            flow.addWidget(button)
            buttons.append(button)
        return buttons

    def selection_context(self) -> SelectionContext:
        """给插件按钮一份「当前选中了什么」的只读上下文。"""
        selected = self.selected_items()
        return SelectionContext(
            items=tuple(to_ref(item) for item in selected),
            user_id=self.user_service.current_id(),
            refresh=self.refresh,
        )

    def _sync_plugin_buttons(self) -> None:
        """插件载入或卸载后重建工具栏上的插件按钮。"""
        if self.toolbar_layout is None or self.toolbar_view is None:
            return
        for button in self._plugin_buttons:
            self.toolbar_layout.removeWidget(button)
            button.deleteLater()
        self._plugin_buttons = self._plugin_button_list(self.toolbar_layout, self.toolbar_view.widget())
        self._fit_toolbar()

    def _build_selection_bar(self, parent: QWidget) -> QWidget:
        """选择条：三态全选框 + 已选数量 + 批量操作，与列表里的勾选状态双向同步。"""
        bar = QWidget(parent)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.select_all_box = CheckBox("全选本页", bar)
        self.select_all_box.setTristate(True)
        self.select_all_box.setToolTip("空 = 全不选，横杠 = 部分选中，勾 = 全选本页")
        self.select_all_box.stateChanged.connect(self._on_select_all)
        layout.addWidget(self.select_all_box)

        self.selection_label = CaptionLabel("未选择数据", bar)
        layout.addWidget(self.selection_label)
        layout.addStretch(1)

        self.move_button = IconTextButton(FluentIcon.MOVE, "移动到分类…", bar)
        self.move_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.move_button.clicked.connect(self._on_move)
        layout.addWidget(self.move_button)

        self.tag_button = IconTextButton(FluentIcon.TAG, "标签管理", bar)
        self.tag_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.tag_button.setToolTip("用三态复选框批量增删所选数据的标签")
        self.tag_button.clicked.connect(self._on_manage_tags)
        layout.addWidget(self.tag_button)

        self.keyword_button = IconTextButton(FluentIcon.DICTIONARY, "关键词管理", bar)
        self.keyword_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.keyword_button.setToolTip("汇总所选数据的关键词，用三态复选框批量增删")
        self.keyword_button.clicked.connect(self._on_manage_keywords)
        layout.addWidget(self.keyword_button)

        self.rename_button = IconTextButton(FluentIcon.FONT, "批量重命名…", bar)
        self.rename_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.rename_button.setToolTip("替换 / 覆盖 / 添加 / 删改，改之前先看变更清单")
        self.rename_button.clicked.connect(self._on_batch_rename)
        layout.addWidget(self.rename_button)

        self.clear_selection_button = IconTextButton(FluentIcon.RETURN, "清空选择", bar)
        self.clear_selection_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.clear_selection_button.clicked.connect(self.clear_selection)
        layout.addWidget(self.clear_selection_button)
        #: 没选中数据时统一置灰的批量按钮（清空选择也在内）。
        self._batch_buttons = (
            self.move_button,
            self.tag_button,
            self.keyword_button,
            self.rename_button,
            self.clear_selection_button,
        )
        return bar

    def _build_filter_panel(self) -> QWidget:
        # 与左侧「分类」卡片保持同一套外观：卡片 + 统一内边距，标题同级别。
        card = CardWidget(self)
        card.setFixedWidth(288)
        card.setMinimumHeight(420)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.setSpacing(8)
        filter_title_row = QHBoxLayout()
        filter_title_row.setContentsMargins(0, 0, 0, 0)
        filter_title_row.setSpacing(6)
        filter_title_row.addWidget(StrongBodyLabel("筛选", card))
        filter_title_row.addStretch(1)
        layout.addLayout(filter_title_row)
        card.setToolTip("按类型、标签、关键词与分类组合过滤")

        scroll = QScrollArea(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(scroll)
        inner = QVBoxLayout(host)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(10)
        self.filter_panel = FilterPanel(host)
        self.filter_panel.changed.connect(self._on_filter_changed)
        self.filter_panel.collapsedChanged.connect(self._on_filter_collapsed)
        inner.addWidget(self.filter_panel)
        inner.addStretch(1)
        scroll.setWidget(host)
        clear_scroll_background(scroll)
        layout.addWidget(scroll, 1)
        return card

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_toolbar()

    def _fit_toolbar(self) -> None:
        """工具栏按内容自适应高度，最多两行，再多就纵向滚动。"""
        view = getattr(self, "toolbar_view", None)
        flow = getattr(self, "toolbar_layout", None)
        if view is None or flow is None:
            return
        single = TOOLBAR_BUTTON_HEIGHT + TOOLBAR_ROW_SPACING
        limit = (
            (TOOLBAR_BUTTON_HEIGHT + TOOLBAR_ROW_SPACING) * TOOLBAR_MAX_ROWS
            + TOOLBAR_SCROLLBAR_HEIGHT
        )
        needed = flow.heightForWidth(max(1, view.viewport().width()))
        height = single if needed <= 0 else min(max(needed, single), limit)
        if view.height() != height:
            view.setFixedHeight(height)

    # ------------------------------------------------------------------ 数据
    def _on_filter_changed(self) -> None:
        """显示隐藏项前先通过口令校验（当前用户设置了口令时）。"""
        user = self.user_service.current()
        if (
            self.filter_panel.show_hidden()
            and not self._unlocked
            and user is not None
            and bool(user.password_hash)
        ):
            dialog = TextInputDialog(
                "解锁隐藏数据", f"请输入 {user.name} 的口令", parent=self.window(), hint="留空以取消"
            )
            if dialog.exec() and self.user_service.verify(user, dialog.value()):
                self._unlocked = True
                self.toast_success("已解锁隐藏数据", "隐藏项会显示在列表中")
            else:
                self.filter_panel.hidden_box.blockSignals(True)
                self.filter_panel.hidden_box.setChecked(False)
                self.filter_panel.hidden_box.blockSignals(False)
                self.toast_warning("口令不正确", "隐藏数据保持锁定")
                return
        self._page = 0
        if self._files_in_tree():
            # 分类栏里也列着文件：隐藏项 / 回收站的显隐同样要重建分类栏
            self.refresh()
        else:
            self._load_items()

    def refresh(self) -> None:
        self._reload_users()
        user_id = self.user_service.current_id()
        nodes = self.taxonomy.tree(user_id=user_id)
        total = self.item_repo.count(ItemFilter(include_hidden=True, user_ids={user_id}))
        self._syncing_tree = True
        self.tree.set_nodes(
            nodes,
            total=total,
            selected=self._category_id,
            checked=self._checked_categories,
            checked_items=self._checked_items,
            files=self._tree_files(user_id),
            only_categories=not self._files_in_tree(),
        )
        self._syncing_tree = False
        self.filter_panel.set_options(
            tags=self.tag_repo.names(user_id=user_id),
            keywords=self.tag_repo.distinct_keywords(user_id=user_id),
            global_tags=set(self.tag_repo.global_names()),
        )
        self._refresh_duplicate_hint()
        self._load_items()

    def _files_in_tree(self) -> bool:
        """分类栏是否要列出文件：只有关掉「仅显示分类」时才列。"""
        box = getattr(self, "only_categories_box", None)
        return box is not None and not box.isChecked()

    def _tree_files(self, user_id: int) -> list:
        """分类栏里要列出的数据项；可见性与中间列表保持一致（隐藏项、回收站）。"""
        if not self._files_in_tree():
            return []
        return self.item_repo.query(
            ItemFilter(
                user_ids={user_id},
                include_hidden=self.filter_panel.show_hidden(),
                only_deleted=self.filter_panel.only_trash(),
            )
        )

    def _on_only_categories_toggled(self, checked: bool) -> None:
        """「仅显示分类」开关：记进配置并立刻重建分类栏。"""
        config.set(config.onlyShowCategories, bool(checked))
        self.refresh()

    def _duplicate_scope(self) -> int | None:
        """默认用户（管理员）可以查重全部数据，其他用户只查自己的数据。"""
        return None if self.user_service.is_admin() else self.user_service.current_id()

    def _refresh_duplicate_hint(self) -> None:
        """在「重复项」按钮上提示当前范围内的重复组数量。"""
        count = int(self.item_repo.stats(self._duplicate_scope()).get("duplicate_groups") or 0)
        button = self._buttons.get("duplicates")
        if button is not None:
            button.setToolTip(f"当前范围有 {count} 组重复内容" if count else "当前范围没有重复内容")

    def _load_items(self) -> None:
        sort_by, descending = self.filter_panel.sort_option()
        text = self.filter_panel.text()
        category_ids = set(self._checked_categories)
        if not category_ids and self._category_id is not None:
            category_ids.add(self._category_id)
        if category_ids:
            # 勾选分类时连同它下面的所有子分类一起显示
            category_ids |= self._descendant_category_ids(category_ids)
        filters = ItemFilter(
            text_ids=self.item_repo.search_ids(text) if text else None,
            types={DataType(value) for value in self.filter_panel.selected_types()},
            tags=self.filter_panel.selected_tags(),
            keywords=self.filter_panel.selected_keywords(),
            category_ids=category_ids,
            item_ids=set(self._checked_items),
            user_ids={self.user_service.current_id()},
            include_hidden=self.filter_panel.show_hidden(),
            only_deleted=self.filter_panel.only_trash(),
            sort_by=sort_by,
            descending=descending,
        )
        self._total = self.item_repo.count(filters)
        self._page = max(0, min(self._page, self.pager_page_count() - 1))
        self._items = self.item_repo.query(
            filters, limit=self._page_size, offset=self._page * self._page_size
        )
        self.pager.set_state(self._total, self._page, self._page_size)
        self._update_count_label()
        self._render()

    def pager_page_count(self) -> int:
        return max(1, -(-self._total // self._page_size))

    def _update_count_label(self) -> None:
        self.count_label.setText(selection_summary(self._total, len(self._selected)))
        # 选择条与翻页区的计数、批量按钮可用状态都与选择集合保持同步。
        count = len(self._selected)
        label = getattr(self, "selection_label", None)
        if label is not None:
            label.setText(f"已选 {count} 项" if count else "未选择数据")
        for button in getattr(self, "_batch_buttons", ()):
            button.setEnabled(bool(count))
        pager = getattr(self, "pager", None)
        if pager is not None:
            pager.set_selection(count, visible=len(getattr(self, "_items", ())))
        # 还原只对回收站中已删除的数据有意义，删除只对尚未删除的数据有意义。
        selected = self.selected_items()
        if self.restore_button is not None:
            self.restore_button.setEnabled(any(item.is_deleted for item in selected))
        delete_button = self._buttons.get("delete")
        if delete_button is not None:
            delete_button.setEnabled(any(not item.is_deleted for item in selected))

    def _on_page_changed(self, page: int) -> None:
        self._page = page
        self._load_items()

    def _on_page_size_changed(self, page_size: int) -> None:
        self._page_size = page_size
        self._page = 0
        config.set(config.pageSize, page_size)
        self._load_items()

    def _save_panel_visibility(self) -> None:
        """把左右两栏的显隐记进配置，下次打开界面保持一致。"""
        config.set(config.showCategoryPanel, self.tree_toggle_button.isChecked())
        config.set(config.showFilterPanel, self.filter_toggle_button.isChecked())

    def _on_filter_collapsed(self) -> None:
        """记下展开着的筛选分组，下次打开界面保持一致。"""
        config.set(config.expandedFilters, self.filter_panel.expanded_keys())

    def _render(self) -> None:
        layout = self.list_layout if self._mode == "list" else self.card_layout
        pool = self._rows[self._mode]
        if not pool:
            _clear_layout(layout)
        while len(pool) > len(self._items):
            widget = pool.pop()
            layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        for index, item in enumerate(self._items):
            if index < len(pool):
                widget = pool[index]
                widget.set_item(item)
            else:
                widget = (
                    ItemListRow(item, view_size=self._view_size)
                    if self._mode == "list"
                    else ItemCard(item, view_size=self._view_size)
                )
                widget.activated.connect(lambda target, page=self: page._on_item_activated(target))
                widget.opened.connect(lambda target, page=self: page._on_open(target))
                widget.menuRequested.connect(lambda target, pos, page=self: page._show_menu(target, pos))
                widget.checkedChanged.connect(
                    lambda target, checked, page=self: page._on_item_checked(target, checked)
                )
                pool.append(widget)
                if isinstance(layout, AdaptiveFlowLayout):
                    layout.addWidget(widget)
                else:
                    layout.insertWidget(layout.count() - 1, widget)
            widget.set_selected(item.id in self._selected)
        if self._mode == "list":
            self._measure_list_columns()
        else:
            self._fit_cards()
            self._schedule_card_fit()
        self._sync_select_all()

    def _measure_list_columns(self) -> None:
        """按本页内容量出各列宽度（用户 m04164 第 2 条）。

        列宽取「所有行内容的最大值」，一次量完同时写给表头与每一行：两边的列结构完全一样，
        因此表头文字正好落在对应列上。内容很长的列会把滚动区的画布撑宽，看不全就左右滑动。
        """
        widths = {key: minimum for key, _title, minimum in LIST_COLUMNS}
        for widget in self._rows["list"]:
            for key, width in widget.column_widths().items():
                widths[key] = max(widths[key], width)
        self.list_header.apply_widths(widths)
        for widget in self._rows["list"]:
            widget.apply_column_widths(widths)

    def _schedule_card_fit(self) -> None:
        """延后一轮再量卡片高度：此刻宽度与标签换行都还没算稳。"""
        self._fit_timer.start(0)

    def _fit_cards(self) -> None:
        """让卡片按内容给出准确高度（用户 m04164 第 3 条）。

        卡片里的标签行是流式容器，换行后的高度取决于卡片宽度；而卡片视图的自适应流式布局
        又是按控件 `sizeHint()` 摆行的。所以先把每张卡片的内部高度锁定、再让流式布局重排一遍，
        多跑两轮是因为「卡片宽度 → 标签换行 → 卡片高度」这一圈要收敛。
        """
        if self._mode != "card":
            return
        cards = [widget for widget in self._rows["card"] if getattr(widget, "item", None) is not None]
        for _ in range(3):
            self.card_layout.invalidate()
            self.card_layout.activate()  # 先把宽度分给卡片，卡片才知道标签该在哪换行
            for widget in cards:
                widget.sync_height()
            self.card_layout.activate()  # 卡片高度变了，再摆一次行位置

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._mode == "card":
            self._schedule_card_fit()

    def _on_view_size_changed(self, index: int) -> None:
        """切换显示大小档位（用户 m04164 第 1 条）：控件尺寸在构造时就定死了，得整批重建。"""
        key = self.size_box.itemData(index)
        if not key or key == self._view_size:
            return
        self._view_size = view_size_key(key)
        config.set(config.viewSize, self._view_size)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """丢掉两种视图的控件池，按新的显示大小重建（列宽、封面、卡片布局都跟着变）。"""
        for layout in (self.list_layout, self.card_layout):
            _clear_layout(layout)
        self._rows = {"list": [], "card": []}
        self.card_layout.setWidgetMinimumWidth(view_size_preset(self._view_size).card_min_width)
        self.list_header.set_view_size(self._view_size)
        self.list_header.set_offset(0)
        self._render()

    # ------------------------------------------------------------------ 用户
    def _reload_users(self) -> None:
        current = self.user_service.current_id()
        self.user_box.blockSignals(True)
        self.user_box.clear()
        for info in self.user_service.list_users():
            self.user_box.addItem(info.name, userData=info.user.id)
        index = self.user_box.findData(current)
        self.user_box.setCurrentIndex(index if index >= 0 else 0)
        self.user_box.blockSignals(False)

    def _on_user_changed(self, index: int) -> None:
        user_id = self.user_box.itemData(index)
        if not user_id or user_id == self.user_service.current_id():
            return
        user = self.user_service.by_id(user_id)
        if user is None:
            return
        if user.password_hash:
            dialog = TextInputDialog(
                "切换用户", f"请输入 {user.name} 的口令", parent=self.window(), hint="留空以取消"
            )
            if not dialog.exec() or not self.user_service.verify(user, dialog.value()):
                self.toast_warning("口令错误", f"无法切换到 {user.name}")
                self._reload_users()
                return
        self.user_service.set_current(user)
        self.session.commit()
        signalBus.userChanged.emit()
        self.toast_success("已切换用户", user.name)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.stack.setCurrentIndex(0 if mode == "list" else 1)
        # 表头只在列表视图出现（卡片视图没有列）
        self.list_header.setVisible(mode == "list")
        self._render()

    def _sync_selection(self) -> None:
        layout = self.list_layout if self._mode == "list" else self.card_layout
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if hasattr(widget, "set_selected") and hasattr(widget, "item"):
                widget.set_selected(widget.item.id in self._selected)
        self._sync_select_all()
        self._update_count_label()

    def _visible_ids(self) -> list[int]:
        return [item.id for item in self._items]

    def _sync_select_all(self) -> None:
        """三态全选框反映本页勾选情况（空 / 横杠 / 勾）。"""
        box = getattr(self, "select_all_box", None)
        if box is None:
            return
        ids = self._visible_ids()
        state = tri_state(sum(1 for item_id in ids if item_id in self._selected), len(ids))
        if box.checkState() == state:
            return
        self._syncing = True
        box.setCheckState(state)
        self._syncing = False

    def _on_select_all(self, state: int) -> None:
        if self._syncing:
            return
        ids = self._visible_ids()
        if Qt.CheckState(state) == Qt.CheckState.Unchecked:
            self._selected.difference_update(ids)
        else:
            self._selected.update(ids)
        self._sync_selection()

    def clear_selection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self._anchor = None
        self._sync_selection()

    # ------------------------------------------------------------------ 选择
    def _on_item_activated(self, item, modifiers=None) -> None:
        """单击只改选择：Ctrl 切换单项，Shift 从锚点到点击项连选（Windows 规则）。"""
        keys = QApplication.keyboardModifiers() if modifiers is None else modifiers
        order = self._visible_ids()
        shift = bool(keys & Qt.KeyboardModifier.ShiftModifier)
        control = bool(keys & Qt.KeyboardModifier.ControlModifier)
        if shift:
            anchor = self._anchor if self._anchor in order else item.id
            selected = range_ids(order, anchor, item.id)
            self._selected = (self._selected | selected) if control else selected
            self._anchor = anchor
        elif control:
            if item.id in self._selected:
                self._selected.discard(item.id)
            else:
                self._selected.add(item.id)
            self._anchor = item.id
        else:
            self._selected = {item.id}
            self._anchor = item.id
        self._sync_selection()

    def _on_item_checked(self, item, checked: bool) -> None:
        """勾选框：只加减这一项，不影响其它已勾选的数据。"""
        if checked:
            self._selected.add(item.id)
        else:
            self._selected.discard(item.id)
        self._anchor = item.id
        self._sync_selection()

    def selected_items(self) -> list:
        """选中的项按 id 从库中取回，跨页选择同样有效。"""
        if not self._selected:
            return []
        items = self.item_repo.by_ids(sorted(self._selected))
        self._selected &= {item.id for item in items}
        return items

    def _require_selection(self) -> list:
        items = self.selected_items()
        if not items:
            toast_warning(
                self, "未选择数据", "请先勾选数据左侧的复选框，或按住 Ctrl / Shift 点击选择多项"
            )
        return items

    def focus_item(self, item_id: int) -> None:
        """跳到指定数据项：清掉筛选、展开它的分类并选中它。"""
        item = self.item_repo.get(item_id)
        if item is None:
            return
        self.filter_panel.reset()
        self.filter_panel.trash_box.setChecked(bool(item.is_deleted))
        if item.is_hidden:
            self.filter_panel.hidden_box.setChecked(True)
        self._category_id = item.category_id
        self._checked_categories.clear()
        self._checked_items.clear()
        self.tree.set_checked_categories(set())
        self._selected = {item.id}
        self._anchor = item.id
        self._page = 0
        self.refresh()
        self._locate_item(item.id)
        self._sync_selection()

    def _locate_item(self, item_id: int) -> None:
        """必要时翻页并滚动，直到目标项出现在视野里。"""
        if not any(item.id == item_id for item in self._items):
            for page in range(self.pager_page_count()):
                self._page = page
                self._load_items()
                if any(item.id == item_id for item in self._items):
                    break
        layout = self.list_layout if self._mode == "list" else self.card_layout
        scroll = self.list_view if self._mode == "list" else self.card_view
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if getattr(widget, "item", None) is not None and widget.item.id == item_id:
                scroll.ensureWidgetVisible(widget)
                break

    # ------------------------------------------------------------------ 操作
    def _path_of(self, item):
        path = self.item_service.file_path_of(item)
        if path is None:
            self.toast_error("无法打开", f"文件不存在或无法打开：{item.name}")
        return path

    def _suffix(self, item) -> str:
        path = self.item_service.file_path_of(item)
        return path.suffix if path is not None else ""

    def _on_open(self, item) -> None:
        """左键双击：按「设置 → 外观 → 左键双击」打开查看器（默认）或交给编辑器插件。"""
        if config.doubleClickAction.value == DOUBLE_CLICK_EDITOR:
            self._open_in_editor(item)
            return
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_path(path, self.window())
        if not ok:
            self.toast_error("无法打开", message)

    def _open_in_editor(self, item) -> None:
        """双击选到「打开编辑器」时走编辑器门面（没装编辑器插件时由它退回系统默认程序）。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = edit_path_with(path, self.window())
        if not ok:
            self.toast_error("无法编辑", message)

    def _on_open_system(self, item) -> None:
        """右键「查看器 → 系统默认程序」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_system(path)
        if not ok:
            self.toast_error("无法打开", message)

    def _on_open_ask(self, item) -> None:
        """右键「查看器 → 交给系统选择」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_system(path, ask=True)
        if not ok:
            self.toast_error("无法打开", message)

    def _on_open_with(self, item, viewer) -> None:
        """右键「查看器 → 点名某个插件」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_viewer_with(path, viewer, self.window())
        if not ok:
            self.toast_error("无法打开", message)

    def _on_edit_system(self, item) -> None:
        """右键「编辑器 → 系统默认程序」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = edit_open_system(path)
        if not ok:
            self.toast_error("无法编辑", message)

    def _on_edit_ask(self, item) -> None:
        """右键「编辑器 → 交给系统选择」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = edit_open_system(path, ask=True)
        if not ok:
            self.toast_error("无法编辑", message)

    def _on_edit_with(self, item, editor) -> None:
        """右键「编辑器 → 点名某个插件」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = edit_with(path, editor, self.window())
        if not ok:
            self.toast_error("无法编辑", message)

    def _on_reveal(self, item) -> None:
        if not self.item_service.reveal_item(item):
            self.toast_warning("无法定位", "文件不存在")

    def _on_copy_path(self, item) -> None:
        path = self.item_service.copy_path(item)
        QApplication.clipboard().setText(path)
        self.toast_success("已复制路径", path)

    def _on_details(self, item) -> None:
        lines = [
            f"名称：{item.name}",
            f"类型：{type_name(item.type)}",
            f"分类：{self.taxonomy.path_of(item.category) if item.category else '未分类'}",
            f"大小：{format_size(item.size)}",
            f"标签：{'、'.join(item.tag_names) or '无'}",
            f"关键词：{'、'.join(item.keywords or []) or '无'}",
            f"校验和：{item.checksum}",
            f"来源：{item.source_path or '（本机创建）'}",
            f"导入时间：{item.created_at:%Y-%m-%d %H:%M:%S}" if item.created_at else "导入时间：未知",
            f"是否隐藏：{'是' if item.is_hidden else '否'}",
            f"是否在回收站：{'是' if item.is_deleted else '否'}",
        ]
        lines.extend(self._plugin_detail_lines(item))
        MessageBox("数据详情", "\n".join(lines), self.window()).exec()

    def _plugin_detail_lines(self, item) -> list[str]:
        """插件贡献的详情行（扩展点 app.ui.detail.panel）。"""
        lines: list[str] = []
        for contribution in items(ExtensionPoint.DETAIL_PANEL):
            data = value_of(contribution)
            rows = resolve(data.get("lines"), item)
            if rows is None:
                continue
            values = [str(row) for row in rows] if isinstance(rows, (list, tuple)) else [str(rows)]
            title = str(data.get("title") or contribution.name)
            lines.append(f"{title}：{'；'.join(values)}" if values else title)
        return lines

    def _build_open_with_menu(self, item) -> RoundMenu:
        """「查看器」子菜单：系统默认程序 / 各内置查看器 / 交给系统选择。"""
        menu = RoundMenu("查看器", self)
        for key, text, viewer in open_with_items(self._suffix(item)):
            if key == "system":
                menu.addAction(
                    Action(FluentIcon.VIEW, text, triggered=lambda: self._on_open_system(item))
                )
            elif key == "ask":
                menu.addSeparator()
                menu.addAction(
                    Action(FluentIcon.FOLDER, text, triggered=lambda: self._on_open_ask(item))
                )
            else:
                menu.addAction(
                    Action(
                        FluentIcon.CHECKBOX,
                        text,
                        triggered=lambda _checked=False, chosen=viewer: self._on_open_with(item, chosen),
                    )
                )
        return menu

    def _build_editor_menu(self, item) -> RoundMenu:
        """「编辑器」子菜单：系统默认程序 / 各内置编辑器 / 交给系统选择。"""
        menu = RoundMenu("编辑器", self)
        for key, text, editor in editor_menu_items(self._suffix(item)):
            if key == "system":
                menu.addAction(
                    Action(FluentIcon.VIEW, text, triggered=lambda: self._on_edit_system(item))
                )
            elif key == "ask":
                menu.addSeparator()
                menu.addAction(
                    Action(FluentIcon.FOLDER, text, triggered=lambda: self._on_edit_ask(item))
                )
            else:
                menu.addAction(
                    Action(
                        FluentIcon.EDIT,
                        text,
                        triggered=lambda _checked=False, chosen=editor: self._on_edit_with(
                            item, chosen
                        ),
                    )
                )
        return menu

    def _build_menu(self, item) -> RoundMenu:
        """右键菜单：打开 / 查看器、编辑器（点名插件或系统）/ 批量操作（按选中数量调整）。"""
        count = len(self._selected)
        callbacks = {
            "open": lambda: self._on_open(item),
            "reveal": lambda: self._on_reveal(item),
            "copy": lambda: self._on_copy_path(item),
            "move": self._on_move,
            "edit": self._on_edit,
            "tag": self._on_manage_tags,
            "keyword": self._on_manage_keywords,
            "rename": self._on_batch_rename,
            "hidden": self._on_toggle_hidden,
            "export": self._on_export,
            "export_zip": self._on_export_zip,
            "delete": self._on_delete,
            "restore": self._on_restore,
            "purge": self._on_purge,
            "details": lambda: self._on_details(item),
        }
        menu = RoundMenu(parent=self)
        plugin_items = items(ExtensionPoint.MANAGE_ITEM_MENU)
        for key, text in menu_items(count):
            if key == "open_with":
                menu.addMenu(self._build_open_with_menu(item))
            elif key == "editor":
                menu.addMenu(self._build_editor_menu(item))
                # 插件贡献的菜单项跟在「查看器」「编辑器」子菜单之后。
                for contribution in plugin_items:
                    data = value_of(contribution)
                    menu.addAction(
                        Action(
                            icon_of(data.get("icon")),
                            str(data.get("text") or contribution.name),
                            triggered=lambda _checked=False, cb=data.get("callback"): resolve(cb, item),
                        )
                    )
            else:
                action = Action(MENU_ICONS[key], text, triggered=callbacks[key])
                if key in MENU_SINGLE_ONLY and count != 1:
                    action.setEnabled(False)
                menu.addAction(action)
            if key in MENU_SEPARATORS_AFTER:
                menu.addSeparator()
        return menu

    def _show_menu(self, item, pos) -> None:
        """右键：先保证该项在选中集合里（不破坏已有的多选），再弹出菜单。"""
        if item.id not in self._selected:
            self._selected = {item.id}
            self._anchor = item.id
            self._sync_selection()
        self._build_menu(item).exec(pos)

    # ------------------------------------------------------------------ 批量
    def _on_move(self) -> None:
        """把选中的数据（单个或批量）移动到另一个分类。"""
        items = self._require_selection()
        if not items:
            return
        nodes = self.taxonomy.tree(user_id=self.user_service.current_id())
        dialog = CategoryPickerDialog(
            nodes,
            parent=self.window(),
            count=len(items),
        )
        if not dialog.exec():
            return
        self.move_selected(dialog.category_id())

    def move_selected(self, category_id: int | None) -> int:
        """把当前选中的项批量移动到指定分类，返回移动条数。"""
        items = self.selected_items()
        if not items:
            self.toast_warning("未选择数据", "请先勾选要移动的数据")
            return 0
        user_id = items[0].user_id
        category = self.category_repo.get(category_id) if category_id else None
        if category is None:
            # 没有分类就归到「未分类」，文件同样落进 <用户>/未分类/ 目录。
            category = self.taxonomy.uncategorized_category(user_id)
            category_id = category.id if category is not None else None
        count = self.item_service.set_category(items, category_id)
        self.session.commit()
        signalBus.itemsChanged.emit()
        name = self.taxonomy.path_of(category) if category is not None else UNCATEGORIZED_NAME
        self.toast_success("已移动", f"{count} 项 → {name}")
        return count

    def _on_edit(self) -> None:
        items = self._require_selection()
        if not items:
            return
        if len(items) > 1:
            self.toast_warning("一次只能编辑一项", "请只选择一项后再编辑")
            return
        item = items[0]
        nodes = self.taxonomy.tree(user_id=self.user_service.current_id())
        dialog = ItemEditDialog(
            item,
            categories=[(node.category.id, "　" * node.depth + node.category.name) for node in nodes],
            known_tags=self.tag_repo.names(user_id=self.user_service.current_id()),
            global_tags=set(self.tag_repo.global_names()),
            info=self._item_info(item),
            parent=self.window(),
            nodes=nodes,
        )
        if not dialog.exec():
            return
        values = dialog.values()
        cover = values.get("cover")
        if cover is not None:
            # 用户动过封面才处理：换封面会把没人引用的旧封面文件删掉减少冗余，
            # 存档条目引用着的会保留（存档要能还原封面变更，用户 m02499 第 2 条）
            cover_service.set_cover(self.session, item, cover)
        self.item_service.update(
            item,
            name=values["name"],
            keywords=values["keywords"],
            tags=values["tags"],
            is_hidden=values["is_hidden"],
        )
        self.item_service.move_item(item, values["category_id"])
        self.session.commit()
        if cover is not None:
            # 卡片封面按路径缓存，换了封面要让它重新取图
            cover_loader().clear()
        signalBus.itemsChanged.emit()
        self.toast_success("已保存", item.name)

    def _item_info(self, item: DataItem) -> list[tuple[str, str]]:
        """编辑弹窗里的只读信息区：这条数据现在是什么、放在哪儿。"""
        owner = self.user_service.by_id(item.user_id)
        library = item.library if item.library_id else None
        if library is None:
            library = self.item_service.libraries.default()
        path = self.item_service.libraries.abs_path(item)
        mime = f"（{item.mime}）" if item.mime else ""
        return [
            ("类型", f"{type_name(item.type)}{mime}"),
            ("大小", format_size(item.size or 0)),
            ("归属用户", owner.name if owner is not None else "未归属"),
            ("所在库", f"{library.name}（{library.path}）" if library is not None else "—"),
            ("库内路径", item.file_path or "—"),
            ("磁盘位置", str(path) if path is not None else "（文件不在库里）"),
            ("创建时间", item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "—"),
            ("内容指纹", (item.checksum or "—")[:16]),
        ]

    # ------------------------------------------------------------- 标签 / 关键词
    def _tag_state(self, items: list, name: str) -> Qt.CheckState:
        """三态：所选数据全都带这个标签 = 勾，部分带 = 横杠，都没带 = 空。"""
        owned = sum(1 for item in items if name in item.tag_names)
        return tri_state(owned, len(items))

    def _on_manage_tags(self) -> None:
        """标签快捷管理：现有用户标签 + 全局标签用三态复选框批量增减，也能直接新建。"""
        items = self._require_selection()
        if not items:
            return
        global_names = sorted(self.tag_repo.global_names())
        globals_set = set(global_names)
        user_names = [
            name
            for name in self.tag_repo.names(user_id=self.user_service.current_id())
            if name not in globals_set
        ]
        entries = [
            (name, self._tag_state(items, name)) for name in user_names + global_names
        ]
        dialog = TagManagerDialog(
            entries,
            parent=self.window(),
            count=len(items),
            suffixes={name: "（全局）" for name in global_names},
        )
        if not dialog.exec():
            return
        additions = dialog.additions()
        removals = dialog.removals()
        if not additions and not removals:
            self.toast_info("没有改动", "标签的勾选状态没有变化")
            return
        if additions:
            self.item_service.add_tags(items, additions)
        if removals:
            self.item_service.remove_tags(items, removals)
        self.session.commit()
        signalBus.itemsChanged.emit()
        signalBus.tagsChanged.emit()
        self.toast_success(
            "标签已更新", f"新增 {len(additions)} 个、移除 {len(removals)} 个（影响 {len(items)} 项）"
        )

    def _keyword_names(self, items: list) -> list[str]:
        """所选数据的关键词汇总，保持首次出现的顺序。"""
        names: list[str] = []
        for item in items:
            for word in item.keywords or []:
                word = str(word)
                if word not in names:
                    names.append(word)
        return names

    def _on_manage_keywords(self) -> None:
        """关键词快捷管理：所选数据的关键词汇总成三态复选框，批量加减或新建。"""
        items = self._require_selection()
        if not items:
            return
        entries = [
            (
                name,
                tri_state(
                    sum(1 for item in items if name in (item.keywords or [])), len(items)
                ),
            )
            for name in self._keyword_names(items)
        ]
        dialog = KeywordManagerDialog(entries, parent=self.window(), count=len(items))
        if not dialog.exec():
            return
        additions = dialog.additions()
        removals = dialog.removals()
        if not additions and not removals:
            self.toast_info("没有改动", "关键词的勾选状态没有变化")
            return
        if additions:
            self.item_service.add_keywords(items, additions)
        if removals:
            self.item_service.remove_keywords(items, removals)
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success(
            "关键词已更新", f"新增 {len(additions)} 个、移除 {len(removals)} 个（影响 {len(items)} 项）"
        )

    # --------------------------------------------------------------- 批量改名
    def _item_display_name(self, item: DataItem) -> str:
        """改名清单里显示的名称：补上文件后缀，让用户看到的和磁盘上一致。"""
        suffix = Path(item.file_path or "").suffix
        if suffix and not item.name.lower().endswith(suffix.lower()):
            return f"{item.name}{suffix}"
        return item.name

    def _on_batch_rename(self) -> None:
        """批量重命名：先看变更清单、勾选要改的行，再一次性应用。"""
        items = self._require_selection()
        if not items:
            return
        selected_ids = {item.id for item in items}
        dialog = BatchRenameDialog(
            [(item.id, self._item_display_name(item)) for item in items],
            parent=self.window(),
            reserved={
                self._item_display_name(item)
                for item in self._items
                if item.id not in selected_ids
            },
        )
        if not dialog.exec():
            return
        renames = dialog.renames()
        by_id = {item.id: item for item in items}
        changed = 0
        for item_id, new_name in renames:
            item = by_id.get(item_id)
            if item is None or new_name == item.name:
                continue
            self.item_service.update(item, name=new_name)
            changed += 1
        if not changed:
            self.toast_info("没有改动", "没有需要改名的文件")
            return
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success("已重命名", f"{changed} 项")

    def _on_toggle_hidden(self) -> None:
        items = self._require_selection()
        if not items:
            return
        target = not all(item.is_hidden for item in items)
        count = self.item_service.set_hidden(items, target)
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success("已更新", f"{count} 项已{'隐藏' if target else '取消隐藏'}")

    def _on_delete(self) -> None:
        selected = self._require_selection()
        if not selected:
            return
        # 已在回收站里的项跳过，避免「重复删除」这种看起来没生效的操作。
        items = [item for item in selected if not item.is_deleted]
        if not items:
            self.toast_warning("无需操作", "选中的数据都已在回收站里")
            return
        if not confirm(self, "移入回收站", f"确定把选中的 {len(items)} 项移入回收站吗？"):
            return
        count = self.item_service.delete(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        if count:
            self.toast_success("已移入回收站", f"{count} 项")
        else:
            self.toast_warning("未移入回收站", "选中的数据都已在回收站里")

    def _on_restore(self) -> None:
        items = [item for item in self._require_selection() if item.is_deleted]
        if not items:
            self.toast_warning("无法还原", "只有已删除（回收站中）的数据可以还原")
            return
        count = self.item_service.restore(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success("已还原", f"{count} 项")

    def _on_purge(self) -> None:
        items = self._require_selection()
        if not items:
            return
        if not confirm(
            self,
            "彻底删除",
            f"将永久删除选中的 {len(items)} 项及其文件，且无法恢复。确定继续吗？",
        ):
            return
        count = self.item_service.purge(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success("已彻底删除", f"{count} 项")

    def _on_export(self) -> None:
        items = self._require_selection()
        if not items:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not directory:
            return
        try:
            result = ExportService(self.session).export_items(items, directory)
        except Exception as exc:  # noqa: BLE001
            self.toast_error("导出失败", str(exc))
            return
        if result.exported:
            self.toast_success("导出完成", f"{result.summary()}，目录：{result.directory}")
        else:
            self.toast_warning("没有可导出的文件", result.summary())

    def _on_export_zip(self) -> None:
        """把选中的数据打包导出：先弹导出对话框定分包方式与命名模板，再真写盘。"""
        items = self._require_selection()
        if not items:
            return
        service = ExportService(self.session)
        user = self.user_service.current()
        dialog = ExportDialog(
            self,
            items=service.planned_items(items),
            summary=f"选中的 {len(items)} 项将被打包导出。",
            directory=str(export_dir()),
            user=getattr(user, "name", "") or "",
        )
        if not dialog.exec():
            return
        tip = self.busy("正在导出", f"共 {len(items)} 项")
        try:
            result = service.export_packages(
                items,
                dialog.directory() or str(export_dir()),
                mode=dialog.mode(),
                template=dialog.template(),
            )
        except Exception as exc:  # noqa: BLE001 - 没写权限 / 磁盘满都要如实说出来
            tip.finish("导出失败")
            self.toast_error("导出失败", str(exc))
            return
        tip.finish("导出完成")
        if result.exported:
            self.toast_success("导出完成", result.summary())
        else:
            self.toast_warning("没有可导出的文件", result.summary())
        if result.warnings:
            self.toast_warning("模板里有认不出的变量", "；".join(result.warnings))

    def _on_open_export_dir(self) -> None:
        """打开导出目录：导出的压缩包与整库备份都写在这里。"""
        folder = export_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_folder(folder):
            self.toast_warning("打开导出文件夹失败", str(folder))

    def _on_duplicates(self) -> None:
        groups = self.item_service.duplicate_map(self._duplicate_scope())
        if not groups:
            self.toast_success("没有重复内容", "所有数据项的内容校验和互不相同")
            return
        dialog = DuplicateDialog(groups, parent=self.window())
        if not dialog.exec():
            return
        ids = dialog.checked_ids()
        if not ids:
            self.toast_warning("未勾选任何项", "请在列表中勾选要删除的重复项")
            return
        items = [item for item in (self.item_repo.get(item_id) for item_id in ids) if item is not None]
        if not items:
            return
        if not confirm(
            self,
            "删除重复项",
            f"将永久删除选中的 {len(items)} 项及其文件，且无法恢复。确定继续吗？",
        ):
            return
        count = self.item_service.purge(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        self.toast_success("已清理重复项", f"{count} 项")

    # ------------------------------------------------------------------ 分类
    def _on_category_checked(self) -> None:
        """左侧分类树的复选框：只管中间列表显示哪些数据，与批量操作完全无关（用户 m01932 第 2 条）。"""
        self._checked_categories = self.tree.checked_categories()
        self._checked_items = self.tree.checked_items()
        self._page = 0
        self._load_items()

    def _category_name(self, category_id: int) -> str:
        category = self.category_repo.get(category_id)
        return category.name if category is not None else str(category_id)

    def _eligible_category_ids(self, category_ids: list[int]) -> list[int]:
        """可批量移动/删除的分类：根分类与固定分类受保护。"""
        eligible: list[int] = []
        for category_id in category_ids:
            category = self.category_repo.get(category_id)
            if category is None or is_uncategorized(category) or category.parent_id is None:
                continue
            eligible.append(category_id)
        return eligible

    def _renameable_category_ids(self, category_ids: list[int]) -> list[int]:
        """可批量重命名的分类：只有固定的「未分类」受保护（根分类可以改名）。"""
        eligible: list[int] = []
        for category_id in category_ids:
            category = self.category_repo.get(category_id)
            if category is None or is_uncategorized(category):
                continue
            eligible.append(category_id)
        return eligible

    def _pick_category_ids(
        self,
        *,
        title: str,
        tip: str,
        list_title: str,
        accept_text: str,
        allow_nested: bool,
    ) -> list[int] | None:
        """弹出分类选择弹窗让用户现挑要处理的分类；取消时返回 None。

        批量删除 / 批量重命名都改成这样挑分类（用户 m01929、m01932 第 1 条），交互与
        批量移动的第一步完全一致：不再依赖分类栏的复选框，也不再依赖「选中状态」。
        """
        dialog = CategorySelectDialog(
            self.taxonomy.tree(user_id=self.user_service.current_id()),
            parent=self.window(),
            title=title,
            tip=tip,
            list_title=list_title,
            accept_text=accept_text,
            allow_nested=allow_nested,
        )
        if not dialog.exec():
            return None
        return dialog.category_ids()

    def _descendant_category_ids(self, category_ids: set[int]) -> set[int]:
        """给定分类的全部子孙分类 id，用于拒绝非法的移动目标。"""
        children: dict[int | None, list[int]] = {}
        for node in self.taxonomy.tree(user_id=self.user_service.current_id()):
            children.setdefault(node.category.parent_id, []).append(node.category.id)
        found: set[int] = set()
        stack = list(category_ids)
        while stack:
            for child_id in children.get(stack.pop(), ()):
                if child_id not in found:
                    found.add(child_id)
                    stack.append(child_id)
        return found

    def _on_category_batch_move(self) -> None:
        """批量移动分类：先攒一张「分类移动表」，再挑一个目标分类统一移过去。"""
        nodes = self.taxonomy.tree(user_id=self.user_service.current_id())
        table = CategoryMoveDialog(nodes, parent=self.window())
        if not table.exec():
            return
        eligible: list[int] = []
        for category_id in table.category_ids():
            category = self.category_repo.get(category_id)
            if category is None or is_uncategorized(category) or category.parent_id is None:
                self.toast_warning(
                    "已跳过分类", f"「{self._category_name(category_id)}」是根分类或固定分类，不能移动"
                )
                continue
            eligible.append(category_id)
        if not eligible:
            self.toast_warning("无法移动", "根分类与「未分类」不能移动")
            return
        # 目标里不能出现要移动的分类本身或它的子孙，否则会把分类移动到自己的子树里
        blocked = set(eligible) | self._descendant_category_ids(set(eligible))
        candidates = [node for node in nodes if node.category.id not in blocked]
        picker = CategoryPickerDialog(
            candidates,
            parent=self.window(),
            title="选择目标分类",
            tip=f"把移动表里的 {len(eligible)} 个分类移动到：",
            allow_root=True,
        )
        if not picker.exec():
            return
        target_id = picker.category_id()
        target = self.category_repo.get(target_id) if target_id is not None else None
        target_name = self.taxonomy.path_of(target) if target is not None else "顶层"
        if all(self.category_repo.get(category_id).parent_id == target_id for category_id in eligible):
            self.toast_warning("无需移动", f"要移动的分类已经在「{target_name}」下")
            return
        names = "、".join(self._category_name(category_id) for category_id in eligible)
        if not confirm(
            self,
            "批量移动分类",
            f"将 {len(eligible)} 个分类（{names}）移动到「{target_name}」下吗？",
        ):
            return
        moved = 0
        failed = 0
        for category_id in eligible:
            category = self.category_repo.get(category_id)
            if category is None:
                continue
            if self.taxonomy.move_category(category, target_id):
                moved += 1
            else:
                failed += 1
        self.session.commit()
        signalBus.categoriesChanged.emit()
        if failed:
            self.toast_warning("已移动分类", f"{moved} 个分类已移动；{failed} 个与目标下的分类重名")
        else:
            self.toast_success("已移动分类", f"{moved} 个分类已移动到「{target_name}」")

    def _move_single_category(self, category_id: int) -> None:
        """右键「移动到另一个分类下」：只动点中的这一个分类，与勾选、多选无关。"""
        category = self.category_repo.get(category_id)
        if category is None or is_uncategorized(category):
            self.toast_warning("无法移动", "「未分类」不能移动")
            return
        nodes = self.taxonomy.tree(user_id=self.user_service.current_id())
        blocked = {category.id} | self._descendant_category_ids({category.id})
        candidates = [node for node in nodes if node.category.id not in blocked]
        dialog = CategoryPickerDialog(
            candidates,
            parent=self.window(),
            title="移动到另一个分类下",
            tip=f"把「{category.name}」移动到：",
            allow_root=True,
        )
        if not dialog.exec():
            return
        target_id = dialog.category_id()
        if target_id == category.parent_id:
            self.toast_warning("无需移动", f"「{category.name}」已经在该分类下")
            return
        target = self.category_repo.get(target_id) if target_id is not None else None
        target_name = self.taxonomy.path_of(target) if target is not None else "顶层"
        if not self.taxonomy.move_category(category, target_id):
            self.toast_warning("移动失败", f"「{category.name}」与「{target_name}」下的分类重名")
            return
        self.session.commit()
        signalBus.categoriesChanged.emit()
        self.toast_success("已移动分类", f"「{category.name}」 → {target_name}")
        self.refresh()

    def _on_category_batch_delete(self) -> None:
        """批量删除分类：先在弹窗里现挑分类，再问下级分类保留还是一起删。

        分类在弹窗里点一个加一个地挑（用户 m01929、m01932 第 1 条），不再看分类栏的
        复选框。挑完若下面还有分类，用和右键单个删除一样的二选一弹窗问「下级分类是保留
        还是一起删」（用户 m01604 第 3 条）；没有下级就还是一句确认。两种选择都只删分类，
        分类里的数据一律变成未分类 —— 也就是「只保留分类里的文件」。
        """
        picked = self._pick_category_ids(
            title="批量删除分类",
            tip="点左侧分类把它加入右边的待删除列表，确认前可以随时新增或移出；"
            "确认后再选择下级分类怎么处理。",
            list_title="待删除分类",
            accept_text="删除",
            allow_nested=True,
        )
        if not picked:
            return
        ids = self._eligible_category_ids(picked)
        if not ids:
            self.toast_warning("无法删除", "根分类与「未分类」不能删除，请重新选择要删除的分类")
            return
        names = "、".join(self._category_name(category_id) for category_id in ids)
        # 只数「选中项之外」的下级分类：选中项自己不算「被一并删除的下级」
        closure = self._descendant_category_ids(set(ids))
        descendants = closure - set(ids)
        # 递归删除时父级已经连同下级一起删掉了：同时被选中的下级不必再处理一遍
        covered = set(ids) & closure
        recursive = False
        if descendants:
            subject = (
                f"选中的 {len(ids)} 个分类（{names}）"
                if len(ids) <= 3
                else f"选中的 {len(ids)} 个分类"
            )
            dialog = CategoryDeleteDialog(subject, len(descendants), self.window())
            if not dialog.exec():
                return
            recursive = dialog.recursive()
        elif not confirm(
            self,
            "批量删除分类",
            f"确定删除选中的 {len(ids)} 个分类（{names}）吗？其中的数据会变成未分类。",
        ):
            return
        removed = 0
        skipped = 0
        affected = 0
        for category_id in ids:
            if recursive and category_id in covered:
                continue
            category = self.category_repo.get(category_id)
            if category is None:
                continue
            # 下级一起删时不必再挡「下级重名」：那些下级本来就要被删掉
            if not recursive and self.taxonomy.promotion_conflicts(category):
                skipped += 1
                continue
            affected += self.taxonomy.delete_category(category, recursive=recursive)
            removed += 1
        self.session.commit()
        signalBus.categoriesChanged.emit()
        signalBus.itemsChanged.emit()
        message = f"已删除 {removed} 个分类，{affected} 项数据已变为未分类"
        if recursive:
            message += f"，{len(descendants)} 个下级分类已一并删除"
        if skipped:
            message += f"；{skipped} 个分类有重名子分类，请单独删除"
            self.toast_warning("已删除分类", message)
        else:
            self.toast_success("已删除分类", message)
        self._checked_categories.clear()
        self._checked_items.clear()
        self.refresh()

    def _on_category_batch_rename(self) -> None:
        """批量重命名分类：先在弹窗里现挑分类，再走「改名前先看清单」的流程。

        挑分类的方式与批量删除 / 批量移动一致（用户 m01929、m01932 第 1 条），只看弹窗里
        挑中的分类，不看分类栏的复选框，也不再把「下级全选」的上级牵连进来。
        """
        picked = self._pick_category_ids(
            title="批量重命名分类",
            tip="点左侧分类把它加入右边的待重命名列表，确认前可以随时新增或移出；"
            "确认后逐项填写新名称。",
            list_title="待重命名分类",
            accept_text="下一步",
            allow_nested=True,
        )
        if not picked:
            return
        ids = self._renameable_category_ids(picked)
        if not ids:
            self.toast_warning("无法重命名", "「未分类」不能重命名，请重新选择要改名的分类")
            return
        selected = set(ids)
        entries: list[tuple[int, str]] = []
        reserved: set[str] = set()
        for category in self.category_repo.roots():
            if category.id not in selected:
                reserved.add(category.name)
        for category_id in ids:
            category = self.category_repo.get(category_id)
            if category is None:
                continue
            entries.append((category.id, category.name))
            for sibling in self.category_repo.children_of(category.parent_id):
                if sibling.id not in selected:
                    reserved.add(sibling.name)
        if not entries:
            return
        dialog = BatchRenameDialog(entries, parent=self.window(), reserved=reserved)
        if not dialog.exec():
            return
        renames = dialog.renames()
        changed = 0
        failed = 0
        for category_id, new_name in renames:
            category = self.category_repo.get(category_id)
            if category is None or new_name == category.name:
                continue
            if self.taxonomy.rename_category(category, new_name):
                changed += 1
            else:
                failed += 1
        if not changed and not failed:
            self.toast_info("没有改动", "没有需要改名的分类")
            return
        self.session.commit()
        signalBus.categoriesChanged.emit()
        signalBus.itemsChanged.emit()
        message = f"已重命名 {changed} 个分类"
        if failed:
            message += f"；{failed} 个与同级分类重名，已跳过"
            self.toast_warning("已重命名分类", message)
        else:
            self.toast_success("已重命名分类", message)

    # ------------------------------------------------------------- 分类排序
    def _load_category_sort_config(self) -> None:
        """读取持久化的排序方式并套用到分类树与控件（不回写配置、不发信号）。"""
        mode = str(config.categorySortMode.value or "default")
        reverse = bool(config.categorySortReverse.value)
        if mode not in {key for key, _ in CATEGORY_SORT_MODES}:
            mode = "default"
        self.tree.set_sort(mode, reverse)
        self.category_sort_box.blockSignals(True)
        index = self.category_sort_box.findData(mode)
        self.category_sort_box.setCurrentIndex(index if index >= 0 else 0)
        self.category_sort_box.blockSignals(False)
        self.category_sort_reverse_button.blockSignals(True)
        self.category_sort_reverse_button.setChecked(reverse)
        self.category_sort_reverse_button.blockSignals(False)
        self._update_sort_button(reverse)

    def _save_category_sort_config(self, mode: str, reverse: bool) -> None:
        """写回配置；`config.set` 自己会保存到配置文件。"""
        config.set(config.categorySortMode, mode)
        config.set(config.categorySortReverse, bool(reverse))

    def _update_sort_button(self, reverse: bool) -> None:
        """同步「正序 / 逆序」按钮的图标与提示：两种顺序要一眼能看出区别。"""
        self.category_sort_reverse_button.setIcon(
            FluentIcon.DOWN if reverse else FluentIcon.UP
        )
        self.category_sort_reverse_button.setToolTip(
            "当前：逆序（点击切回正序）" if reverse else "当前：正序（点击切为逆序）"
        )

    def _on_category_sort_changed(self, mode: str, reverse: bool) -> None:
        """分类树自己改了排序（例如右键菜单）：同步控件并持久化。"""
        self._save_category_sort_config(mode, reverse)
        self.category_sort_box.blockSignals(True)
        index = self.category_sort_box.findData(mode)
        self.category_sort_box.setCurrentIndex(index if index >= 0 else 0)
        self.category_sort_box.blockSignals(False)
        self.category_sort_reverse_button.blockSignals(True)
        self.category_sort_reverse_button.setChecked(bool(reverse))
        self.category_sort_reverse_button.blockSignals(False)
        self._update_sort_button(bool(reverse))

    def _on_category_sort_mode_changed(self, index: int) -> None:
        mode = self.category_sort_box.itemData(index) or "default"
        self.tree.set_sort(str(mode), self.category_sort_reverse_button.isChecked(), notify=True)

    def _on_category_sort_reverse_toggled(self, checked: bool) -> None:
        self._update_sort_button(bool(checked))
        self.tree.set_sort(self.tree.sort_mode(), bool(checked), notify=True)

    def _on_category_sync(self) -> None:
        """按库文件夹里的真实目录刷新分类树（分类即目录，见 category_sync.py）。"""
        stats = reconcile_categories(self.session)
        self.session.commit()
        signalBus.categoriesChanged.emit()
        signalBus.itemsChanged.emit()
        self.toast_success(
            "已按目录同步分类",
            f"新建分类 {stats['created']}、调整归属 {stats['recategorized']}、"
            f"重新对齐文件 {stats['relinked']}、收掉空分类 {stats['pruned']}",
        )

    def _on_category_selected(self, category_id) -> None:
        """分类栏里点中的分类变了：只决定中间列表显示哪个分类的数据，与批量操作无关。"""
        if self._syncing_tree:
            return
        self._category_id = category_id
        self._page = 0
        # 勾选集为空时中间列表跟着选中的分类走；勾了复选框则优先按勾选显示
        self._load_items()

    def _on_tree_action(self, action: str, category_id) -> None:
        if category_id is not None and is_uncategorized(self.category_repo.get(category_id)):
            toast_warning(
                self,
                "固定分类",
                f"「{UNCATEGORIZED_NAME}」是固定分类，不能重命名、删除或创建子分类",
            )
            return
        if action == "move":
            self._move_single_category(int(category_id))
            return
        if action == "add":
            dialog = TextInputDialog("新建分类", "分类名称", parent=self.window(), hint="留空以取消")
            if not dialog.exec():
                return
            name = dialog.value()
            if not name:
                return
            node = self.taxonomy.create_category(
                name, parent_id=category_id, user_id=self.user_service.current_id()
            )
            if node is None:
                self.toast_warning("无法创建", "同级已存在同名分类")
                return
            self.session.commit()
            signalBus.categoriesChanged.emit()
            self.toast_success("已创建分类", node.name)
            hint = self.taxonomy.category_dir_hint(node, self.user_service.current_id())
            if hint:
                self.toast_warning("分类目录太深", hint)
        elif action == "rename":
            category = self.category_repo.get(category_id)
            if category is None:
                return
            dialog = TextInputDialog("重命名分类", "新名称", text=category.name, parent=self.window())
            if not dialog.exec():
                return
            name = dialog.value()
            if not name or name == category.name:
                return
            if not self.taxonomy.rename_category(category, name):
                self.toast_warning("无法重命名", f"同级已存在分类「{name}」")
                return
            self.session.commit()
            signalBus.categoriesChanged.emit()
            self.toast_success("已重命名", name)
        elif action == "delete":
            category = self.category_repo.get(category_id)
            if category is None:
                return
            descendants = self._descendant_category_ids({category.id})
            recursive = False
            if descendants:
                # 有下级分类时才需要问：下级是保留（自动上移一层）还是跟着一起删
                dialog = CategoryDeleteDialog(category.name, len(descendants), self.window())
                if not dialog.exec():
                    return
                recursive = dialog.recursive()
            elif not confirm(
                self, "删除分类", f"确定删除分类「{category.name}」吗？其中的数据会变成未分类。"
            ):
                return
            renames: dict[int, str] = {}
            if not recursive:
                # 下级分类要上移：与父级下已有分类重名的先处理掉（一起删除时不需要）
                conflicts = self.taxonomy.promotion_conflicts(category)
                if conflicts:
                    repo = self.taxonomy.categories
                    rows = [
                        (child.name, repo.unique_sibling_name(child.name, category.parent_id, child.user_id))
                        for child in conflicts
                    ]
                    dialog = CategoryConflictDialog(rows, self.window())
                    if not dialog.exec():
                        return
                    if not dialog.auto():
                        renames = {
                            child.id: name
                            for child, name in zip(conflicts, dialog.renames())
                            if name
                        }
            count = self.taxonomy.delete_category(
                category, renames=renames, recursive=recursive
            )
            self.session.commit()
            signalBus.categoriesChanged.emit()
            signalBus.itemsChanged.emit()
            message = f"{count} 项数据已变为未分类"
            if recursive:
                message += f"，{len(descendants)} 个下级分类已一并删除"
            self.toast_success("已删除分类", message)


CARD_MIN_WIDTH = 240


class _WheelScrollArea(QScrollArea):
    """管理页内容滚动区：滚轮上下滑，按住 Shift 时滚轮左右滑（用户要求）。

    列表内容比可视区宽时只有底部那条横向滚动条，用鼠标去拖很别扭；
    Qt 默认也不把 Shift+滚轮 当横向滚动，所以这里自己接管。
    """

    def wheelEvent(self, event) -> None:
        bar = self.horizontalScrollBar()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and bar.maximum() > bar.minimum():
            # 竖滚轮取 y、能左右倾的滚轮取 x；触控板优先用像素增量。
            delta = event.angleDelta().y() or event.angleDelta().x()
            pixel = event.pixelDelta().y() or event.pixelDelta().x()
            bar.setValue(bar.value() - (pixel or delta))
            event.accept()
            return
        super().wheelEvent(event)


def _make_scroll(parent: QWidget, adaptive: bool = False, card_min_width: int = CARD_MIN_WIDTH):
    """列表用纵向布局（带尾哨兵），卡片用自适应流式布局（多列铺满）。"""
    scroll = _WheelScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    host = QWidget()
    if adaptive:
        layout = AdaptiveFlowLayout(host, needAni=False, isTight=True)
        layout.setWidgetMinimumWidth(card_min_width)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
    else:
        layout = QVBoxLayout(host)
        layout.setSpacing(6)
        layout.addStretch(1)
    layout.setContentsMargins(0, 0, SCROLL_GUTTER, 0)
    scroll.setWidget(host)
    # 内容区现在是卡片，滚动区保持透明露出卡片背景，避免多层底色叠加。
    clear_scroll_background(scroll)
    return scroll, layout


def _clear_layout(layout) -> None:
    """清空列表布局（保留尾哨兵）或流式布局（无哨兵）；FlowLayout.takeAt 直接返回控件。"""
    adaptive = isinstance(layout, AdaptiveFlowLayout)
    while layout.count() > (0 if adaptive else 1):
        entry = layout.takeAt(0)
        widget = entry if adaptive else entry.widget()
        if widget is not None:
            release_widget(widget)


__all__ = [
    "ManagePage",
    "menu_items",
    "open_with_items",
    "range_ids",
    "tri_state",
]
