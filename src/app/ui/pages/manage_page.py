"""数据管理页：分类树 + 筛选面板 + 列表/卡片视图 + 批量操作。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
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
    TitleLabel,
)

from ...core.signals import signalBus
from ...core.viewers import Viewer
from ...db import database
from ...db.models import DataType
from ...db.seed import UNCATEGORIZED_NAME
from ...repositories import (
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
)
from ...services import ExportService, ItemService, TaxonomyService, UserService, is_uncategorized
from ...services.open_with_service import MODE_ASK, open_with_service
from ..common import (
    PAGE_MARGINS,
    PAGE_SPACING,
    PANEL_MARGINS,
    clear_scroll_background,
    confirm,
    format_size,
    page_background,
    release_widget,
    toast_error,
    toast_success,
    toast_warning,
    type_name,
)
from ..viewers.open_flow import open_path, open_system, open_viewer_with
from ..dialogs import (
    CategoryConflictDialog,
    CategoryPickerDialog,
    DuplicateDialog,
    ItemEditDialog,
    TextInputDialog,
)
from ..widgets.category_tree import CategoryTree
from ..widgets.filter_panel import FilterPanel
from ..widgets.item_card import ItemCard, ItemListRow
from ..widgets.pager import DEFAULT_PAGE_SIZE, Pager, selection_summary

TOOLBAR_BUTTON_HEIGHT = 32
TOOLBAR_MAX_ROWS = 2
TOOLBAR_ROW_SPACING = 4
TOOLBAR_SCROLLBAR_HEIGHT = 16


def range_ids(order: list[int], anchor: int, target: int) -> set[int]:
    """Shift 多选：返回 anchor 到 target 之间的全部 id（按当前页顺序，含两端）。"""
    if anchor not in order or target not in order:
        return {target}
    start, end = sorted((order.index(anchor), order.index(target)))
    return set(order[start : end + 1])


def tri_state(checked: int, total: int) -> Qt.CheckState:
    """全选框的三态：空 = 全不选，横杠 = 部分选中，勾 = 全选（与筛选面板一致）。"""
    if total <= 0 or checked <= 0:
        return Qt.CheckState.Unchecked
    if checked >= total:
        return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked


#: 右键菜单条目：标识 → (单选文本, 多选文本)；多选文本里的 {count} 会替换成选中数量。
MENU_LABELS: dict[str, tuple[str, str]] = {
    "open": ("直接打开", "直接打开"),
    "open_with": ("打开方式", "打开方式"),
    "reveal": ("在文件夹中显示", "在文件夹中显示"),
    "copy": ("复制路径", "复制路径"),
    "move": ("移动到分类…", "移动到分类…（{count} 项）"),
    "edit": ("编辑信息", "编辑信息"),
    "tag": ("添加标签", "添加标签"),
    "hidden": ("隐藏 / 取消隐藏", "隐藏 / 取消隐藏"),
    "export": ("导出选中项", "导出选中项（{count}）"),
    "delete": ("移入回收站", "移入回收站（{count}）"),
    "restore": ("从回收站还原", "从回收站还原"),
    "purge": ("彻底删除", "彻底删除（{count}）"),
    "details": ("详情", "详情"),
}
MENU_ICONS: dict[str, FluentIcon] = {
    "open": FluentIcon.VIEW,
    "reveal": FluentIcon.FOLDER,
    "copy": FluentIcon.COPY,
    "move": FluentIcon.MOVE,
    "edit": FluentIcon.EDIT,
    "tag": FluentIcon.TAG,
    "hidden": FluentIcon.VIEW,
    "export": FluentIcon.SAVE,
    "delete": FluentIcon.DELETE,
    "restore": FluentIcon.SYNC,
    "purge": FluentIcon.CLOSE,
    "details": FluentIcon.INFO,
}
MENU_SEPARATORS_AFTER = frozenset({"open_with", "copy", "hidden", "purge"})
MENU_SINGLE_ONLY = frozenset({"edit", "details"})


def menu_items(count: int = 1) -> tuple[tuple[str, str], ...]:
    """右键菜单条目 (标识, 文本)：多选时带上数量提示，单项操作由页面禁用。"""
    many = count > 1
    return tuple(
        (key, more.format(count=count) if many else one)
        for key, (one, more) in MENU_LABELS.items()
    )


def open_with_items(suffix: str) -> tuple[tuple[str, str, Viewer | None], ...]:
    """「打开方式」子菜单：(标识, 文本, 查看器)，标识为 system / viewer:<id> / ask。"""
    entries: list[tuple[str, str, Viewer | None]] = [("system", "系统默认程序", None)]
    entries += [
        (f"viewer:{viewer.id}", f"{viewer.name}（{viewer.plugin_id}）", viewer)
        for viewer in open_with_service.viewers_for(suffix)
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


class ManagePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("managePage")
        page_background(self)
        self.session = database.new_session()
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
        self._unlocked = False
        self._page = 0
        self._page_size = DEFAULT_PAGE_SIZE
        self._total = 0
        self.restore_button: PushButton | None = None
        self._buttons: dict[str, PushButton] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(PAGE_SPACING)
        root.addWidget(self._build_tree_panel(), 0)
        root.addWidget(self._build_center(), 1)
        root.addWidget(self._build_filter_panel(), 0)

        signalBus.itemsChanged.connect(self.refresh)
        signalBus.categoriesChanged.connect(self.refresh)
        signalBus.tagsChanged.connect(self.refresh)
        signalBus.userChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ 构件
    def _build_tree_panel(self) -> QWidget:
        card = CardWidget(self)
        card.setFixedWidth(240)
        card.setMinimumHeight(420)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel("分类", card))

        self.tree = CategoryTree(card)
        self.tree.categorySelected.connect(self._on_category_selected)
        self.tree.actionRequested.connect(self._on_tree_action)
        layout.addWidget(self.tree, 1)

        add_button = PushButton(FluentIcon.ADD, "新建分类", card)
        add_button.clicked.connect(lambda: self._on_tree_action("add", None))
        layout.addWidget(add_button)
        return card

    def _build_center(self) -> QWidget:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("数据管理", host))
        header.addSpacing(16)
        self.view_switch = SegmentedWidget(host)
        self.view_switch.addItem("list", "列表", onClick=lambda: self._set_mode("list"))
        self.view_switch.addItem("card", "卡片", onClick=lambda: self._set_mode("card"))
        self.view_switch.setCurrentItem("list")
        header.addWidget(self.view_switch)
        header.addStretch(1)
        header.addWidget(CaptionLabel("用户", host))
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
        self.card_view, self.card_layout = _make_scroll(host, adaptive=True)
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.card_view)
        layout.addWidget(self.stack, 1)

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
            ("tag", FluentIcon.TAG, "加标签", self._on_add_tags),
            ("hidden", FluentIcon.VIEW, "隐藏/显示", self._on_toggle_hidden),
            ("delete", FluentIcon.DELETE, "删除", self._on_delete),
            ("restore", FluentIcon.SYNC, "还原", self._on_restore),
            ("purge", FluentIcon.CLOSE, "彻底删除", self._on_purge),
            ("export", FluentIcon.SAVE, "导出", self._on_export),
            ("duplicates", FluentIcon.COPY, "重复项", self._on_duplicates),
            ("refresh", FluentIcon.SYNC, "刷新", self.refresh),
        ]
        for key, icon, text, slot in buttons:
            button = PushButton(icon, text, host)
            button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
            button.clicked.connect(slot)
            flow.addWidget(button)
            self._buttons[key] = button
        self.restore_button = self._buttons["restore"]
        self.restore_button.setEnabled(False)
        scroll.setWidget(host)
        clear_scroll_background(scroll)
        self.toolbar_view = scroll
        self.toolbar_layout = flow
        scroll.setFixedHeight(TOOLBAR_BUTTON_HEIGHT + TOOLBAR_ROW_SPACING)
        return scroll

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

        self.move_button = PushButton(FluentIcon.MOVE, "移动到分类…", bar)
        self.move_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.move_button.clicked.connect(self._on_move)
        layout.addWidget(self.move_button)

        self.clear_selection_button = PushButton(FluentIcon.RETURN, "清空选择", bar)
        self.clear_selection_button.setFixedHeight(TOOLBAR_BUTTON_HEIGHT)
        self.clear_selection_button.clicked.connect(self.clear_selection)
        layout.addWidget(self.clear_selection_button)
        return bar

    def _build_filter_panel(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedWidth(288)

        host = QWidget(scroll)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 10, 6)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel("筛选", host))
        layout.addWidget(CaptionLabel("按类型、标签、关键词与分类组合过滤", host))

        self.filter_panel = FilterPanel(host)
        self.filter_panel.changed.connect(self._on_filter_changed)
        layout.addWidget(self.filter_panel)
        layout.addStretch(1)
        scroll.setWidget(host)
        page_background(scroll, "manageFilterScroll")
        page_background(host, "manageFilterHost")
        clear_scroll_background(scroll, inner=False)
        return scroll

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
                toast_success(self, "已解锁隐藏数据", "隐藏项会显示在列表中")
            else:
                self.filter_panel.hidden_box.blockSignals(True)
                self.filter_panel.hidden_box.setChecked(False)
                self.filter_panel.hidden_box.blockSignals(False)
                toast_warning(self, "口令不正确", "隐藏数据保持锁定")
                return
        self._page = 0
        self._load_items()

    def refresh(self) -> None:
        self._reload_users()
        user_id = self.user_service.current_id()
        nodes = self.taxonomy.tree(user_id=user_id)
        total = self.item_repo.count(ItemFilter(include_hidden=True, user_ids={user_id}))
        self.tree.set_nodes(nodes, total=total, selected=self._category_id)
        self.filter_panel.set_options(
            tags=self.tag_repo.names(user_id=user_id),
            categories=[(node.category.id, "　" * node.depth + node.category.name) for node in nodes],
            keywords=self.tag_repo.distinct_keywords(user_id=user_id),
            global_tags=set(self.tag_repo.global_names()),
        )
        self._refresh_duplicate_hint()
        self._load_items()

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
        category_ids = set(self.filter_panel.selected_categories())
        if self._category_id:
            category_ids.add(self._category_id)
        filters = ItemFilter(
            text_ids=self.item_repo.search_ids(text) if text else None,
            types={DataType(value) for value in self.filter_panel.selected_types()},
            tags=self.filter_panel.selected_tags(),
            keywords=self.filter_panel.selected_keywords(),
            category_ids=category_ids,
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
        for button in (getattr(self, "move_button", None), getattr(self, "clear_selection_button", None)):
            if button is not None:
                button.setEnabled(bool(count))
        pager = getattr(self, "pager", None)
        if pager is not None:
            pager.set_selection(count, visible=len(getattr(self, "_items", ())))
        # 还原只对回收站中已删除的数据有意义。
        if self.restore_button is not None:
            self.restore_button.setEnabled(
                any(item.is_deleted for item in self.selected_items())
            )

    def _on_page_changed(self, page: int) -> None:
        self._page = page
        self._load_items()

    def _on_page_size_changed(self, page_size: int) -> None:
        self._page_size = page_size
        self._page = 0
        self._load_items()

    def _render(self) -> None:
        layout = self.list_layout if self._mode == "list" else self.card_layout
        _clear_layout(layout)
        for item in self._items:
            widget = ItemListRow(item) if self._mode == "list" else ItemCard(item)
            widget.activated.connect(self._on_item_activated)
            widget.opened.connect(self._on_open)
            widget.menuRequested.connect(self._show_menu)
            widget.checkedChanged.connect(self._on_item_checked)
            widget.set_selected(item.id in self._selected)
            if isinstance(layout, AdaptiveFlowLayout):
                layout.addWidget(widget)
            else:
                layout.insertWidget(layout.count() - 1, widget)
        self._sync_select_all()

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
                toast_warning(self, "口令错误", f"无法切换到 {user.name}")
                self._reload_users()
                return
        self.user_service.set_current(user)
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已切换用户", user.name)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.stack.setCurrentIndex(0 if mode == "list" else 1)
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
            toast_error(self, "无法打开", f"文件不存在或无法打开：{item.name}")
        return path

    def _suffix(self, item) -> str:
        path = self.item_service.file_path_of(item)
        return path.suffix if path is not None else ""

    def _on_open(self, item) -> None:
        """打开数据：优先用内置查看器，没有内置方式时交给系统默认程序。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_path(path, self.window())
        if not ok:
            toast_error(self, "无法打开", message)

    def _on_open_system(self, item) -> None:
        """右键「打开方式 → 系统默认程序」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_system(path)
        if not ok:
            toast_error(self, "无法打开", message)

    def _on_open_ask(self, item) -> None:
        """右键「打开方式 → 交给系统选择」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_system(path, MODE_ASK)
        if not ok:
            toast_error(self, "无法打开", message)

    def _on_open_with(self, item, viewer) -> None:
        """右键「打开方式 → 点名某个插件」。"""
        path = self._path_of(item)
        if path is None:
            return
        ok, message = open_viewer_with(path, viewer, self.window())
        if not ok:
            toast_error(self, "无法打开", message)

    def _on_reveal(self, item) -> None:
        if not self.item_service.reveal_item(item):
            toast_warning(self, "无法定位", "文件不存在")

    def _on_copy_path(self, item) -> None:
        path = self.item_service.copy_path(item)
        QApplication.clipboard().setText(path)
        toast_success(self, "已复制路径", path)

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
        MessageBox("数据详情", "\n".join(lines), self.window()).exec()

    def _build_open_with_menu(self, item) -> RoundMenu:
        """「打开方式」子菜单：系统默认程序 / 各内置查看器 / 交给系统选择。"""
        menu = RoundMenu("打开方式", self)
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

    def _build_menu(self, item) -> RoundMenu:
        """右键菜单：打开 / 打开方式（点名插件或系统）/ 批量操作（按选中数量调整）。"""
        count = len(self._selected)
        callbacks = {
            "open": lambda: self._on_open(item),
            "reveal": lambda: self._on_reveal(item),
            "copy": lambda: self._on_copy_path(item),
            "move": self._on_move,
            "edit": self._on_edit,
            "tag": self._on_add_tags,
            "hidden": self._on_toggle_hidden,
            "export": self._on_export,
            "delete": self._on_delete,
            "restore": self._on_restore,
            "purge": self._on_purge,
            "details": lambda: self._on_details(item),
        }
        menu = RoundMenu(parent=self)
        for key, text in menu_items(count):
            if key == "open_with":
                menu.addMenu(self._build_open_with_menu(item))
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
            [(node.category.id, "　" * node.depth + node.category.name) for node in nodes],
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
            toast_warning(self, "未选择数据", "请先勾选要移动的数据")
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
        toast_success(self, "已移动", f"{count} 项 → {name}")
        return count

    def _on_edit(self) -> None:
        items = self._require_selection()
        if not items:
            return
        if len(items) > 1:
            toast_warning(self, "一次只能编辑一项", "请只选择一项后再编辑")
            return
        item = items[0]
        nodes = self.taxonomy.tree(user_id=self.user_service.current_id())
        dialog = ItemEditDialog(
            item,
            categories=[(node.category.id, "　" * node.depth + node.category.name) for node in nodes],
            known_tags=self.tag_repo.names(user_id=self.user_service.current_id()),
            global_tags=set(self.tag_repo.global_names()),
            parent=self.window(),
        )
        if not dialog.exec():
            return
        values = dialog.values()
        self.item_service.update(
            item,
            name=values["name"],
            keywords=values["keywords"],
            tags=values["tags"],
            is_hidden=values["is_hidden"],
        )
        self.item_service.move_item(item, values["category_id"])
        self.session.commit()
        signalBus.itemsChanged.emit()
        toast_success(self, "已保存", item.name)

    def _on_add_tags(self) -> None:
        items = self._require_selection()
        if not items:
            return
        dialog = TextInputDialog(
            "添加标签", "多个标签用逗号分隔", parent=self.window(), hint="新标签会自动加入标签库"
        )
        if not dialog.exec():
            return
        names = [part.strip() for part in dialog.value().replace("，", ",").split(",") if part.strip()]
        if not names:
            return
        count = self.item_service.add_tags(items, names)
        self.session.commit()
        signalBus.itemsChanged.emit()
        signalBus.tagsChanged.emit()
        toast_success(self, "已添加标签", f"{'、'.join(names)}（影响 {count} 项）")

    def _on_toggle_hidden(self) -> None:
        items = self._require_selection()
        if not items:
            return
        target = not all(item.is_hidden for item in items)
        count = self.item_service.set_hidden(items, target)
        self.session.commit()
        signalBus.itemsChanged.emit()
        toast_success(self, "已更新", f"{count} 项已{'隐藏' if target else '取消隐藏'}")

    def _on_delete(self) -> None:
        items = self._require_selection()
        if not items:
            return
        if not confirm(self, "移入回收站", f"确定把选中的 {len(items)} 项移入回收站吗？"):
            return
        count = self.item_service.delete(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        toast_success(self, "已移入回收站", f"{count} 项")

    def _on_restore(self) -> None:
        items = [item for item in self._require_selection() if item.is_deleted]
        if not items:
            toast_warning(self, "无法还原", "只有已删除（回收站中）的数据可以还原")
            return
        count = self.item_service.restore(items)
        self.session.commit()
        signalBus.itemsChanged.emit()
        toast_success(self, "已还原", f"{count} 项")

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
        toast_success(self, "已彻底删除", f"{count} 项")

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
            toast_error(self, "导出失败", str(exc))
            return
        if result.exported:
            toast_success(self, "导出完成", f"{result.summary()}，目录：{result.directory}")
        else:
            toast_warning(self, "没有可导出的文件", result.summary())

    def _on_duplicates(self) -> None:
        groups = self.item_service.duplicate_map(self._duplicate_scope())
        if not groups:
            toast_success(self, "没有重复内容", "所有数据项的内容校验和互不相同")
            return
        dialog = DuplicateDialog(groups, parent=self.window())
        if not dialog.exec():
            return
        ids = dialog.checked_ids()
        if not ids:
            toast_warning(self, "未勾选任何项", "请在列表中勾选要删除的重复项")
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
        toast_success(self, "已清理重复项", f"{count} 项")

    # ------------------------------------------------------------------ 分类
    def _on_category_selected(self, category_id) -> None:
        self._category_id = category_id
        self._page = 0
        self._load_items()

    def _on_tree_action(self, action: str, category_id) -> None:
        if category_id is not None and is_uncategorized(self.category_repo.get(category_id)):
            toast_warning(
                self,
                "固定分类",
                f"「{UNCATEGORIZED_NAME}」是固定分类，不能重命名、删除或创建子分类",
            )
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
                toast_warning(self, "无法创建", "同级已存在同名分类")
                return
            self.session.commit()
            signalBus.categoriesChanged.emit()
            toast_success(self, "已创建分类", node.name)
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
                toast_warning(self, "无法重命名", f"同级已存在分类「{name}」")
                return
            self.session.commit()
            signalBus.categoriesChanged.emit()
            toast_success(self, "已重命名", name)
        elif action == "delete":
            category = self.category_repo.get(category_id)
            if category is None:
                return
            if not confirm(self, "删除分类", f"确定删除分类「{category.name}」吗？其中的数据会变成未分类。"):
                return
            renames: dict[int, str] = {}
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
            count = self.taxonomy.delete_category(category, renames=renames)
            self.session.commit()
            signalBus.categoriesChanged.emit()
            signalBus.itemsChanged.emit()
            toast_success(self, "已删除分类", f"{count} 项数据已变为未分类")


CARD_MIN_WIDTH = 240


def _make_scroll(parent: QWidget, adaptive: bool = False):
    """列表用纵向布局（带尾哨兵），卡片用自适应流式布局（多列铺满）。"""
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    host = QWidget()
    if adaptive:
        layout = AdaptiveFlowLayout(host, needAni=False, isTight=True)
        layout.setWidgetMinimumWidth(CARD_MIN_WIDTH)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
    else:
        layout = QVBoxLayout(host)
        layout.setSpacing(6)
        layout.addStretch(1)
    layout.setContentsMargins(0, 0, 6, 0)
    scroll.setWidget(host)
    page_background(scroll, f"manageScroll{'Card' if adaptive else 'List'}")
    page_background(host, f"manageScrollHost{'Card' if adaptive else 'List'}")
    clear_scroll_background(scroll, inner=False)
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
