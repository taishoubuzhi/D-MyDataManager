"""插件配置页：筛选、排序、查看详情、插件选项、导入 / 启停 / 编辑 / 删除。

所有插件（含内置）都在插件目录下扫描出来并显示在同一条列表里；插件按扩展点（贡献）归类，
筛选下拉来自 SDK 的扩展点常量，插件在 setup() 里登记自己的贡献。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
)

from ...core import shell
from ...core.signals import signalBus
from ...db import database
from ...services import UserService
from ...services.plugin_service import (
    CONTRIBUTION_FILTERS,
    PLUGIN_ORDERS,
    SOURCE_FILTERS,
    STATE_FILTERS,
    PluginError,
    PluginInfo,
    plugin_service,
)
from ..components import BADGES_ROLE, SUBTITLE_ROLE, TITLE_ROLE, FlowArea, PluginItemDelegate
from ..dialogs import TextInputDialog
from ..framework import (
    icon_label,
    icon_text_label,
    DETAIL_MARGINS,
    PANEL_MARGINS,
    Page,
    StatusBadge,
    badge_row,
    confirm,
    tri_state,
)
from ..plugin_options_dialog import PluginOptionsDialog
from ..framework import IconTextButton, IconTextPrimaryButton


class PluginPage(Page):
    """插件管理页：列表 + 详情 + 选项 / 启停 / 编辑 / 删除。"""

    page_name = "pluginPage"
    page_title = "插件"
    page_subtitle = (
        "所有插件（含内置）都放在插件目录下：每个插件是一个含 plugin.json 的子目录。"
        "插件在 setup() 里按扩展点登记贡献（查看器、页面……），列表按贡献归类；"
        "插件声明了 options 就会在「插件选项」里出现可配置项。"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = plugin_service
        self.session = database.new_session()
        self.users = UserService(self.session)
        self._is_admin = self.users.is_admin()
        self._plugins: list = []
        self._checked: set[str] = set()
        self._current = ""

        self._zip_button = IconTextPrimaryButton(FluentIcon.ADD, "导入插件包", self)
        self._zip_button.clicked.connect(self._on_import_zip)
        self.header.add_action(self._zip_button)
        self._folder_button = IconTextButton(FluentIcon.FOLDER, "导入插件目录", self)
        self._folder_button.clicked.connect(self._on_import_dir)
        self.header.add_action(self._folder_button)
        refresh_button = IconTextButton(FluentIcon.SYNC, "刷新", self)
        refresh_button.clicked.connect(self._reload)
        self.header.add_action(refresh_button)

        # 整段说明改挂在页面标题上：鼠标停住才弹出来，不再铺在页面上
        if self.header is not None:
            self.header.add_hint(
                "启用 / 禁用会立即重建查看器注册表：禁用「查看器」插件后，对应格式会退回系统默认程序。"
            )
        self.permission_hint = CaptionLabel(
            "只有默认用户可以导入、启用、编辑或删除插件；其他用户可以查看、筛选与打开插件目录。", self
        )
        self.add_widget(self.permission_hint)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索名称 / 说明 / 扩展名")
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(self._fill_list)
        filters.addWidget(self.search)
        self.point_box = ComboBox(self)
        self.point_box.setMinimumWidth(120)
        for value, label in CONTRIBUTION_FILTERS:
            self.point_box.addItem(label, userData=value)
        self.point_box.currentIndexChanged.connect(self._fill_list)
        filters.addWidget(self.point_box)
        self.source_box = ComboBox(self)
        for value, label in SOURCE_FILTERS:
            self.source_box.addItem(label, userData=value)
        self.source_box.currentIndexChanged.connect(self._fill_list)
        filters.addWidget(self.source_box)
        self.author_box = ComboBox(self)
        self.author_box.setMinimumWidth(130)
        self.author_box.currentIndexChanged.connect(self._fill_list)
        filters.addWidget(self.author_box)
        self.state_box = ComboBox(self)
        for value, label in STATE_FILTERS:
            self.state_box.addItem(label, userData=value)
        self.state_box.currentIndexChanged.connect(self._fill_list)
        filters.addWidget(self.state_box)
        filters.addStretch(1)
        self.add_row(filters)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(8)
        sort_row.addWidget(icon_label(FluentIcon.MENU, "排序", self))
        self.order_box = ComboBox(self)
        self.order_box.setMinimumWidth(130)
        for value, label in PLUGIN_ORDERS:
            self.order_box.addItem(label, userData=value)
        self.order_box.currentIndexChanged.connect(self._fill_list)
        sort_row.addWidget(self.order_box)
        self.reverse_button = IconTextButton(FluentIcon.UP, "正序", self)
        self.reverse_button.setCheckable(True)
        self.reverse_button.toggled.connect(self._on_reverse)
        sort_row.addWidget(self.reverse_button)
        sort_row.addStretch(1)
        # 从「查看器」页跳过来会带上贡献筛选，列表可能只列出一部分插件：
        # 计数文案写清「已筛选」，并给一个一键回到全部插件的按钮。
        self.reset_button = IconTextButton(FluentIcon.SYNC, "清除筛选", self)
        self.reset_button.setToolTip("清空关键词与四个筛选下拉，回到「全部插件」")
        self.reset_button.clicked.connect(self._clear_filters)
        self.reset_button.setVisible(False)
        sort_row.addWidget(self.reset_button)
        self.count_label = CaptionLabel("", self)
        sort_row.addWidget(self.count_label)
        self.add_row(sort_row)

        self.add_row(self._build_selection_bar(self))

        body = QHBoxLayout()
        body.setSpacing(12)

        left = CardWidget(self)
        left.setFixedWidth(400)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(*PANEL_MARGINS)
        left_layout.setSpacing(8)
        left_layout.addWidget(SubtitleLabel("插件列表", left))
        self.plugin_list = ListWidget(left)
        # 右侧的状态徽章由自绘代理画，勾选框与文字仍走基类，勾选语义不受影响；
        # qfluentwidgets 的 ListWidget 会把横向滚动条钉成 AlwaysOff，列表项宽度就跟着视口走：
        # 长插件名由代理用省略号收尾（完整文字在悬停提示与右侧详情里），徽章不会被挤出可视区。
        self.plugin_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plugin_list.setItemDelegate(PluginItemDelegate(self.plugin_list))
        self.plugin_list.currentRowChanged.connect(self._on_select)
        self.plugin_list.itemChanged.connect(self._on_item_changed)
        left_layout.addWidget(self.plugin_list, 1)
        body.addWidget(left)

        right = CardWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(*DETAIL_MARGINS)
        right_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择插件", right)
        # 类型 / 来源 / 状态三个徽章：短标签一眼扫过去，不必读整段文字
        self.kind_badge = StatusBadge("", "plain", right)
        self.source_badge = StatusBadge("", "plain", right)
        self.state_badge = StatusBadge("", "plain", right)
        self.detail_badges = badge_row(self.kind_badge, self.source_badge, self.state_badge)
        self.detail_meta = CaptionLabel("", right)
        self.detail_state = CaptionLabel("", right)
        self.detail_state.setWordWrap(True)
        self.detail_desc = CaptionLabel("", right)
        self.detail_desc.setWordWrap(True)
        self.detail_ext = CaptionLabel("", right)
        self.detail_ext.setWordWrap(True)
        self.detail_protocol = CaptionLabel("", right)
        self.detail_protocol.setWordWrap(True)
        self.detail_options = CaptionLabel("", right)
        self.detail_options.setWordWrap(True)
        self.detail_path = CaptionLabel("", right)
        self.detail_path.setWordWrap(True)

        right_layout.addWidget(self.detail_title)
        right_layout.addLayout(self.detail_badges)
        right_layout.addWidget(self.detail_meta)
        right_layout.addWidget(self.detail_desc)
        right_layout.addWidget(self.detail_state)

        # 协议信息分两栏，长句不再从窗口左边一直铺到右边
        protocol_column = QVBoxLayout()
        protocol_column.setSpacing(6)
        protocol_column.addWidget(icon_text_label(FluentIcon.CODE, "协议与接口", right))
        protocol_column.addWidget(self.detail_protocol)
        protocol_column.addStretch(1)
        content_column = QVBoxLayout()
        content_column.setSpacing(6)
        content_column.addWidget(icon_text_label(FluentIcon.DOCUMENT, "清单与选项", right))
        content_column.addWidget(self.detail_ext)
        content_column.addWidget(self.detail_options)
        content_column.addWidget(self.detail_path)
        content_column.addStretch(1)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(protocol_column, 1)
        columns.addLayout(content_column, 1)
        right_layout.addLayout(columns)
        right_layout.addStretch(1)

        # 功能按钮数量多：按各按钮自己的文字宽度换行，标签才不会被压窄截断。
        self.actions_host = FlowArea(right, horizontal_spacing=8, vertical_spacing=8)
        self.toggle_button = IconTextPrimaryButton(FluentIcon.ACCEPT, "启用", self.actions_host)
        self.toggle_button.clicked.connect(self._on_toggle)
        self.actions_host.add_widget(self.toggle_button)
        self.options_button = IconTextButton(FluentIcon.SETTING, "插件选项", self.actions_host)
        self.options_button.clicked.connect(self._on_options)
        self.actions_host.add_widget(self.options_button)
        for field, label in (("name", "重命名"), ("description", "编辑说明"), ("note", "编辑备注")):
            button = IconTextButton(FluentIcon.EDIT, label, self.actions_host)
            button.clicked.connect(lambda _checked=False, key=field: self._on_edit(key))
            self.actions_host.add_widget(button)
            setattr(self, f"_{field}_button", button)
        self.reveal_button = IconTextButton(FluentIcon.FOLDER, "打开插件目录", self.actions_host)
        self.reveal_button.clicked.connect(self._on_reveal)
        self.actions_host.add_widget(self.reveal_button)
        self.delete_button = IconTextButton(FluentIcon.DELETE, "删除", self.actions_host)
        self.delete_button.clicked.connect(self._on_remove)
        self.actions_host.add_widget(self.delete_button)
        right_layout.addWidget(self.actions_host)
        body.addWidget(right, 1)

        self.body.addLayout(body, 1)

        self.auto_refresh(signalBus.pluginsChanged)
        signalBus.userChanged.connect(self._sync_admin)
        self._reload()
        self._apply_permissions()

    # ------------------------------------------------------------------ 筛选
    def apply_contribution(self, point: str) -> None:
        """从「查看器」页跳转过来时自动筛成指定扩展点，未知扩展点时回到全部。"""
        index = self.point_box.findData(point)
        self.point_box.setCurrentIndex(index if index >= 0 else 0)
        self._reload()

    def _select_plugin(self, plugin_id: str) -> bool:
        """按插件 id 选中列表项（自检与内部跳转用）。"""
        for row in range(self.plugin_list.count()):
            if self.plugin_list.item(row).data(Qt.ItemDataRole.UserRole) == plugin_id:
                self.plugin_list.setCurrentRow(row)
                return True
        return False

    def _filters(self) -> dict:
        return {
            "contribution": self.point_box.currentData() or "",
            "state": self.state_box.currentData() or "",
            "source": self.source_box.currentData() or "",
            "author": self.author_box.currentData() or "",
            "query": self.search.text().strip(),
            "order": self.order_box.currentData() or "default",
            "reverse": self.reverse_button.isChecked(),
        }

    def _on_reverse(self, checked: bool) -> None:
        self.reverse_button.setText("逆序" if checked else "正序")
        self.reverse_button.setIcon(FluentIcon.DOWN if checked else FluentIcon.UP)
        self._fill_list()

    def _reload_filters(self) -> None:
        """创建者随插件清单出现，刷新下拉内容并保留当前选择。"""
        author = self.author_box.currentData() or ""
        self.author_box.blockSignals(True)
        self.author_box.clear()
        self.author_box.addItem("全部创建者", userData="")
        for name in self.service.authors():
            self.author_box.addItem(name, userData=name)
        index = self.author_box.findData(author)
        self.author_box.setCurrentIndex(index if index >= 0 else 0)
        self.author_box.blockSignals(False)

    def _reload(self) -> None:
        self._reload_filters()
        self._fill_list()

    def _clear_filters(self) -> None:
        """一键回到「全部插件」：清空关键词与四个筛选下拉。"""
        for box in (self.point_box, self.state_box, self.source_box, self.author_box):
            box.blockSignals(True)
            box.setCurrentIndex(0)
            box.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._reload()

    def refresh(self) -> None:
        self._reload()

    # ------------------------------------------------------------------ 权限
    def _sync_admin(self) -> None:
        """切换用户后重新判定权限：插件属于系统级资源，只有默认用户可以改动。"""
        self._is_admin = UserService(self.session).is_admin()
        self._apply_permissions()

    def _apply_permissions(self) -> None:
        for button in (
            self._zip_button,
            self._folder_button,
            self._name_button,
            self._description_button,
            self._note_button,
        ):
            button.setEnabled(self._is_admin)
        self.permission_hint.setVisible(not self._is_admin)
        row = self.plugin_list.currentRow()
        if row >= 0:
            self._on_select(row)
        else:
            # 列表为空（或还没选中）时详情按钮没有作用对象：统一禁用，
            # 否则非管理员会看到可点的「启用 / 停用插件」「插件更多选项」「删除插件」。
            self._current = ""
            for button in (self.toggle_button, self.delete_button, self.options_button, self.reveal_button):
                button.setEnabled(False)
        self._sync_selection()

    def _require_admin(self, action: str) -> bool:
        if self._is_admin:
            return True
        self.toast_warning("无权操作", f"只有默认用户可以{action}")
        return False

    # ------------------------------------------------------------------ 列表
    def _fill_list(self) -> None:
        filters = self._filters()
        self._plugins = self.service.all(**filters)
        total = len(self.service.all())
        # 勾选跟随插件 id 保留，被筛选隐藏或已删除的插件自动丢弃。
        self._checked &= {info.id for info in self.service.all()}
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        for info in self._plugins:
            item = QListWidgetItem(
                f"{info.name} · {info.kind_label} · {info.contributions_text} · {info.source_label}"
            )
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            item.setData(BADGES_ROLE, [(info.state_label, info.state_tone)])
            # 两行显示：第一行插件名，第二行类型 · 来源 · 贡献；列表项文本仍保留全部信息供检索与断言
            item.setData(TITLE_ROLE, info.name)
            item.setData(
                SUBTITLE_ROLE,
                f"{info.kind_label} · {info.source_label} · {info.contributions_text}",
            )
            item.setToolTip(
                f"{info.id}\n类型：{info.kind_label}\n状态：{info.state_label}\n创建者：{info.author_text}"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if info.id in self._checked else Qt.CheckState.Unchecked
            )
            self.plugin_list.addItem(item)
        self.plugin_list.blockSignals(False)
        active = [
            self.point_box.currentText() if filters["contribution"] else "",
            self.state_box.currentText() if filters["state"] else "",
            self.source_box.currentText() if filters["source"] else "",
            self.author_box.currentText() if filters["author"] else "",
        ]
        active = [label for label in active if label]
        if filters["query"]:
            active.append(f"关键词「{filters['query']}」")
        if active:
            self.count_label.setText(f"已筛选：{len(self._plugins)} / 共 {total} 个插件")
            self.count_label.setToolTip("当前筛选：" + "、".join(active) + "\n点右侧「清除筛选」回到全部插件")
        else:
            self.count_label.setText(f"共 {total} 个插件")
            self.count_label.setToolTip("")
        self.reset_button.setVisible(bool(active))
        if self._plugins:
            self.plugin_list.setCurrentRow(0)
        else:
            self._current = ""
            for label in (
                self.detail_title,
                self.detail_meta,
                self.detail_state,
                self.detail_ext,
                self.detail_protocol,
                self.detail_options,
                self.detail_path,
                self.detail_desc,
            ):
                label.setText("")
            self._set_detail_badges(None)
            self.detail_title.setText("没有匹配的插件")
        self._sync_selection()

    def _set_detail_badges(self, info) -> None:
        """详情标题下的三个徽章；传 None 表示清空（列表为空时）。"""
        if info is None:
            for badge in (self.kind_badge, self.source_badge, self.state_badge):
                badge.set_badge("")
            return
        self.kind_badge.set_badge(info.kind_label, "plain")
        self.source_badge.set_badge(info.source_label, "plain")
        self.state_badge.set_badge(info.state_label, info.state_tone)

    def _selected_info(self):
        return self.service.get(self._current)

    # ------------------------------------------------------------------ 详情
    def _on_select(self, row: int) -> None:
        item = self.plugin_list.item(row)
        if item is None:
            return
        info = self.service.get(item.data(Qt.ItemDataRole.UserRole))
        if info is None:
            return
        self._current = info.id
        self.detail_title.setText(info.name)
        self._set_detail_badges(info)
        self.detail_meta.setText(
            f"{info.id} · {info.contributions_text} · {info.source_label} · 版本 {info.version_text} · 创建者 {info.author_text}"
        )
        state = f"状态：{info.state_label} · 目录：{info.path or '—'}"
        if info.conflict_with:
            state += f" · 与插件冲突：{info.conflict_with_text}（不能同时启用）"
        if info.note:
            state += f" · 备注：{info.note}"
        if info.error:
            state += f" · {info.error}"
        self.detail_state.setText(state)
        self.detail_ext.setText(f"清单数据：{info.data_text}")
        self.detail_protocol.setText(
            " · ".join(
                [
                    f"贡献：{info.contributions_text}",
                    f"依赖插件：{info.depends_text}",
                    f"冲突声明：{info.conflicts_text}",
                    f"扩展接口：{info.provides_text}",
                    f"提供库：{info.libraries_text}",
                    f"适配 SDK：{info.api_text}",
                    f"适用管理器版本：{info.manager_text}",
                    f"入口文件：{info.entry or '—'}",
                ]
            )
        )
        if info.has_options:
            self.detail_options.setText(f"插件选项（{len(info.options)}）：{info.settings_text}")
        else:
            self.detail_options.setText("插件选项：清单里没有声明 options")
        self.detail_path.setText(f"清单：{(Path(info.path) / 'plugin.json') if info.path else '—'}")
        self.detail_desc.setText(info.description or "（没有填写说明）")
        self.toggle_button.setText("禁用" if info.enabled else "启用")
        self.toggle_button.setEnabled(self._is_admin)
        self.delete_button.setEnabled(self._is_admin and not info.builtin)
        self.reveal_button.setEnabled(info.path is not None)
        self.options_button.setEnabled(self._is_admin and info.path is not None)

    # ------------------------------------------------------------------ 勾选
    def _build_selection_bar(self, parent: QWidget) -> QHBoxLayout:
        """选择条：单个三态全选框 + 批量操作，与列表勾选双向同步。"""
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.select_all_box = CheckBox("全选本页", parent)
        self.select_all_box.setTristate(True)
        self.select_all_box.setToolTip(
            "空 = 全不选，横杠 = 部分选中，勾 = 全选当前列出的插件；点一下切换全选/全不选"
        )
        self.select_all_box.clicked.connect(self._on_select_all_clicked)
        bar.addWidget(self.select_all_box)

        self.selection_label = CaptionLabel("未选择插件", parent)
        bar.addWidget(self.selection_label)
        bar.addStretch(1)

        self.batch_enable_button = IconTextButton(FluentIcon.ACCEPT, "批量启用", parent)
        self.batch_enable_button.setToolTip("启用所有勾选的插件，之后立即重建查看器注册表")
        self.batch_enable_button.clicked.connect(lambda: self._on_batch_toggle(True))
        self.batch_disable_button = IconTextButton(FluentIcon.CLOSE, "批量禁用", parent)
        self.batch_disable_button.setToolTip("禁用所有勾选的插件，之后立即重建查看器注册表")
        self.batch_disable_button.clicked.connect(lambda: self._on_batch_toggle(False))
        self.batch_remove_button = IconTextButton(FluentIcon.DELETE, "批量删除", parent)
        self.batch_remove_button.setToolTip("删除勾选的外部插件；内置插件需改为批量禁用")
        self.batch_remove_button.clicked.connect(self._on_batch_remove)
        for button in (
            self.batch_enable_button,
            self.batch_disable_button,
            self.batch_remove_button,
        ):
            bar.addWidget(button)
        return bar

    def checked_infos(self) -> list:
        """当前勾选的插件（按列表顺序），供批量操作与自检脚本使用。"""
        return [info for info in self._plugins if info.id in self._checked]

    def _sync_selection(self) -> None:
        """三态全选框、已选数量与批量按钮跟随勾选状态。"""
        ids = [info.id for info in self._plugins]
        state = tri_state(sum(1 for plugin_id in ids if plugin_id in self._checked), len(ids))
        self.select_all_box.blockSignals(True)
        self.select_all_box.setCheckState(state)
        self.select_all_box.blockSignals(False)
        count = len(self._checked)
        self.selection_label.setText(f"已选 {count} 个插件" if count else "未选择插件")
        for button in (
            self.batch_enable_button,
            self.batch_disable_button,
            self.batch_remove_button,
        ):
            button.setEnabled(bool(count) and self._is_admin)

    def _apply_check_states(self) -> None:
        """把 _checked 写回列表勾选框（阻断信号，避免回环）。"""
        self.plugin_list.blockSignals(True)
        for row, info in enumerate(self._plugins):
            item = self.plugin_list.item(row)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if info.id in self._checked else Qt.CheckState.Unchecked
                )
        self.plugin_list.blockSignals(False)
        self._sync_selection()

    def _set_visible_checked(self, checked: bool) -> None:
        for info in self._plugins:
            if checked:
                self._checked.add(info.id)
            else:
                self._checked.discard(info.id)
        self._apply_check_states()

    def select_all(self) -> None:
        """勾选当前列出的全部插件。"""
        self._set_visible_checked(True)

    def select_none(self) -> None:
        """取消全部勾选。"""
        self._set_visible_checked(False)

    def invert_selection(self) -> None:
        """反转当前列出插件的勾选状态。"""
        for info in self._plugins:
            if info.id in self._checked:
                self._checked.discard(info.id)
            else:
                self._checked.add(info.id)
        self._apply_check_states()

    def _on_select_all_clicked(self) -> None:
        """点一下：没有全选就全选，已全选就全不选（横杠只是部分选中的显示状态）。"""
        ids = [info.id for info in self._plugins]
        all_checked = bool(ids) and all(plugin_id in self._checked for plugin_id in ids)
        self._set_visible_checked(not all_checked)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        plugin_id = item.data(Qt.ItemDataRole.UserRole)
        if not plugin_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(plugin_id)
        else:
            self._checked.discard(plugin_id)
        self._sync_selection()

    # ------------------------------------------------------------------ 操作
    def _refresh_viewers(self, changed=()) -> None:
        """按变更的插件增量重载；没给 id（重载 / 删除等整体性操作）时退回全量。"""
        self.service.apply_changes(changed)
        signalBus.pluginsChanged.emit()
        signalBus.openWithChanged.emit()

    def _refusal_text(self, info: PluginInfo) -> str:
        """启用被拒时给一句能照做的说明：冲突优先，其次清单 / 依赖错误。"""
        conflict = self.service.conflict_text(info.id)
        if conflict:
            return conflict
        return info.error_text or "原因见控制台日志"

    def _on_toggle(self) -> None:
        if not self._require_admin("启用或禁用插件"):
            return
        info = self._selected_info()
        if info is None:
            return
        target = not info.enabled
        if not self.service.set_enabled(info.id, target):
            self.toast_error("无法启用插件" if target else "无法禁用插件", self._refusal_text(info))
            return
        self._refresh_viewers((info.id,))
        self.toast_success("已启用插件" if target else "已禁用插件", info.name)

    def _on_batch_toggle(self, enabled: bool) -> None:
        """批量启用 / 批量禁用勾选的插件。"""
        if not self._require_admin("批量启用或禁用插件"):
            return
        infos = self.checked_infos()
        if not infos:
            self.toast_warning("未选择插件", "请先勾选要批量操作的插件")
            return
        changed = [info for info in infos if bool(info.enabled) != enabled]
        refused: list[str] = []
        accepted: list[str] = []
        done = 0
        for info in changed:
            if self.service.set_enabled(info.id, enabled):
                accepted.append(info.id)
                done += 1
            else:
                refused.append(f"{info.name}：{self._refusal_text(info)}")
        if done:
            self._refresh_viewers(accepted)
        if refused:
            head = "；".join(refused[:3])
            more = f"；另有 {len(refused) - 3} 个同类问题" if len(refused) > 3 else ""
            self.toast_error(
                f"{len(refused)} 个插件没能{'启用' if enabled else '禁用'}",
                head + more,
            )
        if done:
            self.toast_success(
                "已批量启用插件" if enabled else "已批量禁用插件",
                f"{done} 个插件已{'启用' if enabled else '禁用'}",
            )
        elif not refused:
            self.toast_warning(
                "状态未变化",
                f"勾选的 {len(infos)} 个插件都已经是{'启用' if enabled else '禁用'}状态",
            )

    def _on_batch_remove(self) -> None:
        """批量删除勾选的外部插件；内置插件会被跳过。"""
        if not self._require_admin("批量删除插件"):
            return
        infos = self.checked_infos()
        if not infos:
            self.toast_warning("未选择插件", "请先勾选要批量删除的插件")
            return
        builtin = [info for info in infos if info.builtin]
        removable = [info for info in infos if not info.builtin]
        if not removable:
            self.toast_warning(
                "无法删除", f"勾选的 {len(builtin)} 个插件都是内置插件，可以改为批量禁用"
            )
            return
        note = f"其中 {len(builtin)} 个内置插件会被跳过。" if builtin else ""
        if not confirm(
            self,
            "批量删除插件",
            f"确定要删除勾选的 {len(removable)} 个外部插件吗？插件目录会被一并删除。{note}",
        ):
            return
        for info in removable:
            self.service.remove(info.id)
            self._checked.discard(info.id)
        self._current = ""
        self._refresh_viewers()
        self.toast_success("已删除插件", f"删除 {len(removable)} 个外部插件")

    def _on_options(self) -> None:
        info = self._selected_info()
        if info is None:
            return
        PluginOptionsDialog(info, self.service, parent=self.window()).exec()

    def _on_edit(self, field: str) -> None:
        if not self._require_admin("编辑插件信息"):
            return
        info = self._selected_info()
        if info is None:
            return
        current = {"name": info.name, "description": info.description, "note": info.note}[field]
        titles = {"name": "重命名插件", "description": "编辑插件说明", "note": "编辑插件备注"}
        dialog = TextInputDialog(titles[field], "留空以清除", current, parent=self.window(), multiline=field == "description")
        if not dialog.exec():
            return
        value = dialog.value()
        if field == "name" and not value:
            self.toast_warning("名称不能为空", "插件名称不能留空")
            return
        self.service.update(info.id, **{field: value})
        self._reload()
        signalBus.pluginsChanged.emit()
        self.toast_success("已保存插件信息", info.id)

    def _on_reveal(self) -> None:
        info = self._selected_info()
        if info is None or info.path is None:
            return
        shell.reveal(Path(info.path) / "plugin.json")

    def _on_remove(self) -> None:
        if not self._require_admin("删除插件"):
            return
        info = self._selected_info()
        if info is None:
            return
        if info.builtin:
            self.toast_warning("内置插件不能删除", "可以禁用内置插件来停用它")
            return
        if not confirm(self, "删除插件", f"确定要删除「{info.name}」吗？插件目录会被一并删除。"):
            return
        if self.service.remove(info.id):
            self._current = ""
            self._refresh_viewers()
            self.toast_success("已删除插件", info.name)
        else:
            self.toast_error("删除失败", "只有外部插件可以删除")

    def _install(self, source: str, label: str) -> None:
        if not self._require_admin("导入插件"):
            return
        if not source:
            return
        try:
            info = self.service.import_plugin(source)
        except PluginError as exc:
            message = str(exc)
            if "已存在" in message and confirm(self, "插件已存在", f"{message}\n\n要覆盖安装吗？"):
                try:
                    info = self.service.import_plugin(source, overwrite=True)
                except PluginError as inner:
                    self.toast_error("导入失败", str(inner))
                    return
            else:
                self.toast_error("导入失败", message)
                return
        self._refresh_viewers()
        self._reload()
        self.toast_success("已导入插件", f"{info.name}（{label}）")

    def _on_import_zip(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择插件压缩包", "", "插件包 (*.zip);;所有文件 (*)")
        self._install(path, "压缩包")

    def _on_import_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择插件目录")
        self._install(path, "目录")
