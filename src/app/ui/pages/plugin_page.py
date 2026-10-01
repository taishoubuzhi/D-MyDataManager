"""插件配置页：筛选、排序、查看详情、插件选项、导入 / 启停 / 编辑 / 删除。

所有插件（含内置）都在插件目录下扫描出来并显示在同一条列表里；插件类型表随清单累积，
所以类型筛选下拉里会出现插件自己声明的类型。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
    TitleLabel,
)

from ...core import shell
from ...core.plugin_kinds import plugin_kinds
from ...core.signals import signalBus
from ...services.plugin_service import PLUGIN_ORDERS, SOURCE_FILTERS, STATE_FILTERS, PluginError, plugin_service
from ..common import confirm, toast_error, toast_success, toast_warning
from ..dialogs import TextInputDialog
from ..plugin_options_dialog import PluginOptionsDialog


class PluginPage(QWidget):
    """插件管理页：列表 + 详情 + 选项 / 启停 / 编辑 / 删除。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pluginPage")
        self.service = plugin_service
        self._plugins: list = []
        self._current = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("插件", self))
        header.addStretch(1)
        zip_button = PrimaryPushButton(FluentIcon.ADD, "导入插件包", self)
        zip_button.clicked.connect(self._on_import_zip)
        header.addWidget(zip_button)
        folder_button = PushButton(FluentIcon.FOLDER, "导入插件目录", self)
        folder_button.clicked.connect(self._on_import_dir)
        header.addWidget(folder_button)
        refresh_button = PushButton(FluentIcon.SYNC, "刷新", self)
        refresh_button.clicked.connect(self._reload)
        header.addWidget(refresh_button)
        root.addLayout(header)

        root.addWidget(
            CaptionLabel(
                "所有插件（含内置）都放在插件目录下：每个插件是一个含 plugin.json 的子目录。"
                "类型（kind）写在清单里，随清单累积成类型表，两个插件声明同一个类型不会冲突；"
                "插件声明了 options 就会在「插件选项」里出现可配置项。",
                self,
            )
        )
        root.addWidget(
            CaptionLabel("启用 / 禁用会立即重建查看器注册表：禁用「打开方式」插件后，对应格式会退回系统默认程序。", self)
        )

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("搜索名称 / 说明 / 扩展名")
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(self._fill_list)
        filters.addWidget(self.search)
        self.kind_box = ComboBox(self)
        self.kind_box.setMinimumWidth(120)
        self.kind_box.currentIndexChanged.connect(self._fill_list)
        filters.addWidget(self.kind_box)
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
        root.addLayout(filters)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(8)
        sort_row.addWidget(CaptionLabel("排序", self))
        self.order_box = ComboBox(self)
        self.order_box.setMinimumWidth(130)
        for value, label in PLUGIN_ORDERS:
            self.order_box.addItem(label, userData=value)
        self.order_box.currentIndexChanged.connect(self._fill_list)
        sort_row.addWidget(self.order_box)
        self.reverse_button = PushButton(FluentIcon.UP, "正序", self)
        self.reverse_button.setCheckable(True)
        self.reverse_button.toggled.connect(self._on_reverse)
        sort_row.addWidget(self.reverse_button)
        sort_row.addStretch(1)
        self.count_label = CaptionLabel("", self)
        sort_row.addWidget(self.count_label)
        root.addLayout(sort_row)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = CardWidget(self)
        left.setFixedWidth(360)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(SubtitleLabel("插件列表", left))
        self.plugin_list = ListWidget(left)
        self.plugin_list.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.plugin_list, 1)
        body.addWidget(left)

        right = CardWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择插件", right)
        self.detail_meta = CaptionLabel("", right)
        self.detail_state = CaptionLabel("", right)
        self.detail_ext = CaptionLabel("", right)
        self.detail_protocol = CaptionLabel("", right)
        self.detail_protocol.setWordWrap(True)
        self.detail_options = CaptionLabel("", right)
        self.detail_options.setWordWrap(True)
        self.detail_path = CaptionLabel("", right)
        self.detail_path.setWordWrap(True)
        self.detail_desc = CaptionLabel("", right)
        self.detail_desc.setWordWrap(True)
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
            right_layout.addWidget(label)
        right_layout.addStretch(1)

        actions = QHBoxLayout()
        self.toggle_button = PrimaryPushButton(FluentIcon.ACCEPT, "启用", right)
        self.toggle_button.clicked.connect(self._on_toggle)
        actions.addWidget(self.toggle_button)
        self.options_button = PushButton(FluentIcon.SETTING, "插件选项", right)
        self.options_button.clicked.connect(self._on_options)
        actions.addWidget(self.options_button)
        for field, label in (("name", "重命名"), ("description", "编辑说明"), ("note", "编辑备注")):
            button = PushButton(FluentIcon.EDIT, label, right)
            button.clicked.connect(lambda _checked=False, key=field: self._on_edit(key))
            actions.addWidget(button)
            setattr(self, f"_{field}_button", button)
        self.reveal_button = PushButton(FluentIcon.FOLDER, "打开插件目录", right)
        self.reveal_button.clicked.connect(self._on_reveal)
        actions.addWidget(self.reveal_button)
        self.delete_button = PushButton(FluentIcon.DELETE, "删除", right)
        self.delete_button.clicked.connect(self._on_remove)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        right_layout.addLayout(actions)
        body.addWidget(right, 1)

        root.addLayout(body, 1)

        signalBus.pluginsChanged.connect(self._reload)
        self._reload()

    # ------------------------------------------------------------------ 筛选
    def apply_kind(self, kind: str) -> None:
        """从「打开方式」页跳转过来时自动筛成指定类型，类型未知时回到全部。"""
        index = self.kind_box.findData(kind)
        self.kind_box.setCurrentIndex(index if index >= 0 else 0)
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
            "kind": self.kind_box.currentData() or "",
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
        """类型表与创建者都随插件清单出现，刷新下拉内容并保留当前选择。"""
        kind = self.kind_box.currentData() or ""
        self.kind_box.blockSignals(True)
        self.kind_box.clear()
        self.kind_box.addItem("全部类型", userData="")
        for spec in plugin_kinds.all():
            self.kind_box.addItem(spec.name, userData=spec.id)
        index = self.kind_box.findData(kind)
        self.kind_box.setCurrentIndex(index if index >= 0 else 0)
        self.kind_box.blockSignals(False)

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

    # ------------------------------------------------------------------ 列表
    def _fill_list(self) -> None:
        self._plugins = self.service.all(**self._filters())
        total = len(self.service.all())
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        for info in self._plugins:
            mark = "✔" if info.enabled and not info.error else "✖"
            item = QListWidgetItem(f"{mark} {info.name} · {info.kind_label} · {info.source_label}")
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            item.setToolTip(f"{info.id}\n状态：{info.state_label}\n创建者：{info.author_text}")
            self.plugin_list.addItem(item)
        self.plugin_list.blockSignals(False)
        self.count_label.setText(f"{len(self._plugins)} / {total} 个插件")
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
            self.detail_title.setText("没有匹配的插件")

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
        self.detail_meta.setText(
            f"{info.id} · {info.kind_label} · {info.source_label} · 版本 {info.version_text} · 创建者 {info.author_text}"
        )
        state = f"状态：{info.state_label} · 目录：{info.path or '—'}"
        if info.note:
            state += f" · 备注：{info.note}"
        if info.error:
            state += f" · {info.error}"
        self.detail_state.setText(state)
        self.detail_ext.setText(f"扩展名（{len(info.extensions)}）：{info.extensions_text}")
        self.detail_protocol.setText(
            " · ".join(
                [
                    f"依赖插件：{info.depends_text}",
                    f"扩展接口：{info.provides_text}",
                    f"功能：{info.capabilities_text}",
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
        self.delete_button.setEnabled(not info.builtin)
        self.reveal_button.setEnabled(info.path is not None)
        self.options_button.setEnabled(info.path is not None)

    # ------------------------------------------------------------------ 操作
    def _refresh_viewers(self) -> None:
        self.service.load_viewers()
        signalBus.pluginsChanged.emit()
        signalBus.openWithChanged.emit()

    def _on_toggle(self) -> None:
        info = self._selected_info()
        if info is None:
            return
        self.service.set_enabled(info.id, not info.enabled)
        self._refresh_viewers()
        toast_success(self, "已启用插件" if not info.enabled else "已禁用插件", info.name)

    def _on_options(self) -> None:
        info = self._selected_info()
        if info is None:
            return
        PluginOptionsDialog(info, self.service, parent=self.window()).exec()

    def _on_edit(self, field: str) -> None:
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
            toast_warning(self, "名称不能为空", "插件名称不能留空")
            return
        self.service.update(info.id, **{field: value})
        self._reload()
        signalBus.pluginsChanged.emit()
        toast_success(self, "已保存插件信息", info.id)

    def _on_reveal(self) -> None:
        info = self._selected_info()
        if info is None or info.path is None:
            return
        shell.reveal(Path(info.path) / "plugin.json")

    def _on_remove(self) -> None:
        info = self._selected_info()
        if info is None:
            return
        if info.builtin:
            toast_warning(self, "内置插件不能删除", "可以禁用内置插件来停用它")
            return
        if not confirm(self, "删除插件", f"确定要删除「{info.name}」吗？插件目录会被一并删除。"):
            return
        if self.service.remove(info.id):
            self._current = ""
            self._refresh_viewers()
            toast_success(self, "已删除插件", info.name)
        else:
            toast_error(self, "删除失败", "只有外部插件可以删除")

    def _install(self, source: str, label: str) -> None:
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
                    toast_error(self, "导入失败", str(inner))
                    return
            else:
                toast_error(self, "导入失败", message)
                return
        self._refresh_viewers()
        self._reload()
        toast_success(self, "已导入插件", f"{info.name}（{label}）")

    def _on_import_zip(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择插件压缩包", "", "插件包 (*.zip);;所有文件 (*)")
        self._install(path, "压缩包")

    def _on_import_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择插件目录")
        self._install(path, "目录")
