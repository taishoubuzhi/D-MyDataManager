"""设置页：外观、导入、存储、资源文件夹、隐私、日志与维护。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PushButton,
    PushSettingCard,
    SettingCard,
    SettingCardGroup,
    StrongBodyLabel,
    SwitchSettingCard,
    Theme,
    setTheme,
)

from ...core import logging_setup, paths
from ...core.config import config, export_dir, resources_root, set_resource_root
from ...core.signals import signalBus
from ...core.version import APP_VERSION
from ...db import database
from ...sdk import Events, ExtensionPoint
from ...services import LibraryService, UserService
from ...services.maintenance import reset_to_defaults
from ...services.plugin_service import plugin_service
from ...services.privacy_service import privacy
from ..components.pager import PAGE_SIZES, normalize_page_size
from ..framework import (
    NumberSettingCard,
    ScrollPage,
    confirm,
    open_path,
    release_widget,
    restart_application,
)
from ..framework.contributions import items, resolve, value_of
from ..framework import IconTextButton

TOAST_DELAY_MS = 400


class ComboSettingCard(SettingCard):
    """下拉选择设置卡（不依赖 OptionsConfigItem）。"""

    changed = pyqtSignal(object)

    def __init__(self, icon, title: str, content: str, options, current, callback, parent=None) -> None:
        super().__init__(icon, title, content, parent)
        self._callback = callback
        self.combo = ComboBox(self)
        for label, value in options:
            self.combo.addItem(label, userData=value)
        for index in range(self.combo.count()):
            if self.combo.itemData(index) == current:
                self.combo.setCurrentIndex(index)
                break
        self.combo.currentIndexChanged.connect(self._on_index_changed)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_index_changed(self, _index: int) -> None:
        """选项变化：先落配置，再让页面据此弹提示。"""
        self._callback(self.combo.currentData())
        self.changed.emit(self.combo.currentText())


class ActionCard(PushSettingCard):
    """设置卡上的操作按钮：换成 Fluent PushButton，避免全站混入原生 QPushButton。"""

    def __init__(self, text, icon, title: str, content: str, parent=None) -> None:
        super().__init__(text, icon, title, content, parent)
        index = self.hBoxLayout.indexOf(self.button)
        release_widget(self.button)
        self.button = PushButton(text, self)
        self.hBoxLayout.insertWidget(index, self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.button.clicked.connect(self.clicked)


class SettingsPage(ScrollPage):
    page_name = "settingsPage"
    page_title = "设置"
    page_subtitle = "外观、导入、存储与隐私选项，改动会立即生效"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = database.new_session()
        self.users = UserService(self.session)
        self._is_admin = self.users.is_admin()

        groups = [
            self._appearance_group(),
            self._import_group(),
            self._storage_group(),
            self._library_group(),
            self._privacy_group(),
            self._library_card(),
            self._log_group(),
            self._maintenance_group(),
            self._about_group(),
        ]
        plugin_group = self._plugin_group()
        if plugin_group is not None:
            groups.append(plugin_group)
        for group in groups:
            self.add_widget(group)
        self.add_stretch()

        self._pending_toast: tuple | None = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(TOAST_DELAY_MS)
        self._toast_timer.timeout.connect(self._flush_setting_toast)
        self._connect_setting_toasts()

        signalBus.userChanged.connect(self._sync_admin)
        self._apply_permissions()

    def _connect_setting_toasts(self) -> None:
        """每张配置卡改动后都在右上角提示一次：切换配置一定有反馈。"""
        # 两个保护开关有自己的详细提示（是否支持、放行了几个目录），不再重复接一遍
        own = (config.resourceProtected, config.hiddenProtected)
        for card in self.findChildren(SwitchSettingCard):
            if getattr(card, "configItem", None) in own:
                continue
            card.checkedChanged.connect(
                lambda checked, item=card: self._queue_setting_toast(item, "已开启" if checked else "已关闭")
            )
        for card in self.findChildren(NumberSettingCard):
            card.valueChanged.connect(
                lambda value, item=card: self._queue_setting_toast(item, f"已设为 {value}")
            )
        for card in self.findChildren(ComboSettingCard):
            card.changed.connect(
                lambda text, item=card: self._queue_setting_toast(item, f"已切换为 {text}")
            )

    def _queue_setting_toast(self, card, detail: str) -> None:
        """滑块拖动会连发信号，等用户停下来再弹，避免刷屏。"""
        self._pending_toast = (card, detail)
        self._toast_timer.start()

    def _flush_setting_toast(self) -> None:
        pending = self._pending_toast
        if pending is None:
            return
        self._pending_toast = None
        card, detail = pending
        self.toast_success(card.titleLabel.text(), detail)

    def _plugin_group(self) -> SettingCardGroup | None:
        """插件贡献的设置卡片（扩展点 app.ui.settings.card）；没有贡献时整组不出现。"""
        contributions = items(ExtensionPoint.SETTINGS_CARD)
        if not contributions:
            return None
        group = SettingCardGroup("插件", self)
        added = 0
        for item in contributions:
            card = resolve(value_of(item).get("factory"), group)
            if isinstance(card, QWidget):
                group.addSettingCard(card)
                added += 1
        return group if added else None

    # ------------------------------------------------------------------ 分组
    def _appearance_group(self) -> SettingCardGroup:
        group = SettingCardGroup("外观", self)
        self._theme_card = ComboSettingCard(
            FluentIcon.BRUSH,
            "主题",
            "切换浅色、深色或跟随系统",
            [("浅色", "light"), ("深色", "dark"), ("跟随系统", "auto")],
            config.theme.value,
            self._on_theme_changed,
            group,
        )
        group.addSettingCard(self._theme_card)

        mica_card = SwitchSettingCard(
            FluentIcon.TRANSPARENT,
            "云母效果",
            "使用 Windows 11 的云母材质背景",
            configItem=config.micaEnabled,
            parent=group,
        )
        mica_card.checkedChanged.connect(lambda checked: signalBus.micaEnableChanged.emit(checked))
        group.addSettingCard(mica_card)

        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.ZOOM,
                "界面缩放",
                "高分辨率屏幕下的缩放比例，重启后生效",
                [("自动", "Auto"), ("100%", "100%"), ("125%", "125%"), ("150%", "150%"), ("200%", "200%")],
                config.dpiScale.value,
                lambda value: config.set(config.dpiScale, value),
                group,
            )
        )
        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.FONT,
                "简化显示",
                "不简化 / 默认（只简化不会混淆的图标）/ 完全简化，所有页面与弹窗都生效",
                [("不简化", "none"), ("默认", "default"), ("完全简化", "full")],
                config.simpleDisplay.value,
                lambda value: config.set(config.simpleDisplay, value),
                group,
            )
        )
        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.SCROLL,
                "每页条数",
                "数据管理页与存档页每页显示多少条",
                [(f"{size} 条", size) for size in PAGE_SIZES],
                normalize_page_size(config.pageSize.value),
                lambda value: config.set(config.pageSize, value),
                group,
            )
        )
        group.addSettingCard(
            NumberSettingCard(
                config.tooltipDelay,
                FluentIcon.INFO,
                "悬停提示延迟（毫秒）",
                "鼠标停在按钮或标题上多久后弹出说明，0 表示立刻弹出",
                group,
            )
        )
        group.addSettingCard(
            SwitchSettingCard(
                FluentIcon.MENU,
                "分类栏默认展开",
                "数据管理页左侧分类栏启动时展开全部分类（默认全部收起）",
                configItem=config.expandCategories,
                parent=group,
            )
        )
        return group

    def _import_group(self) -> SettingCardGroup:
        group = SettingCardGroup("导入", self)

        group.addSettingCard(
            SwitchSettingCard(
                FluentIcon.DATE_TIME,
                "按时间命名",
                "导入文件时使用导入时间作为名称",
                configItem=config.nameByTime,
                parent=group,
            )
        )
        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.SYNC,
                "重复内容处理",
                "导入时遇到内容完全相同的文件",
                [("重命名保留", "rename"), ("跳过", "skip"), ("覆盖已有项", "overwrite")],
                config.duplicatePolicy.value,
                lambda value: config.set(config.duplicatePolicy, value),
                group,
            )
        )
        group.addSettingCard(
            SwitchSettingCard(
                FluentIcon.HISTORY,
                "导入后创建存档",
                "每次导入完成后自动记录一次快照",
                configItem=config.archiveOnImport,
                parent=group,
            )
        )
        return group

    def _storage_group(self) -> SettingCardGroup:
        group = SettingCardGroup("存储", self)

        pick_export = ActionCard("选择目录", FluentIcon.SAVE, "默认导出目录", str(export_dir()), group)
        pick_export.clicked.connect(self._choose_export_dir)
        self._export_card = pick_export
        group.addSettingCard(pick_export)

        open_logs = ActionCard("打开目录", FluentIcon.DOCUMENT, "日志目录", str(paths.LOG_DIR), group)
        open_logs.clicked.connect(lambda: self._open_path(paths.LOG_DIR))
        group.addSettingCard(open_logs)

        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.HISTORY,
                "存档自动清理",
                "创建存档后按该策略删除较早的快照，始终保留最新一个",
                [("按数量", "count"), ("按容量", "size"), ("按时间", "age"), ("不自动清理", "none")],
                config.pruneMode.value,
                self._on_prune_mode_changed,
                group,
            )
        )
        self._keep_versions_card = NumberSettingCard(
            config.keepVersions,
            FluentIcon.HISTORY,
            "存档保留数量",
            "超过该数量时自动删除最早的存档",
            group,
        )
        self._keep_size_card = NumberSettingCard(
            config.keepSize,
            FluentIcon.SAVE,
            "仓库容量上限（MB）",
            "超出后从最早的存档开始删除；数据项自身的内容不会被删除",
            group,
        )
        self._keep_days_card = NumberSettingCard(
            config.keepDays,
            FluentIcon.DATE_TIME,
            "存档保留天数",
            "超过该天数的存档会被自动删除",
            group,
        )
        for card in (self._keep_versions_card, self._keep_size_card, self._keep_days_card):
            group.addSettingCard(card)
        self._sync_prune_cards()

        self._auto_cleanup_switch = SwitchSettingCard(
            FluentIcon.BROOM,
            "自动清理无用内容",
            "启动后、创建或删除存档时回收没有索引引用的内容文件，并按当前方案重写旧内容",
            configItem=config.autoCleanup,
            parent=group,
        )
        group.addSettingCard(self._auto_cleanup_switch)

        rebuild_index = ActionCard("重建索引", FluentIcon.SYNC, "全文检索索引", "检索结果异常时重建索引", group)
        rebuild_index.clicked.connect(self._rebuild_search_index)
        group.addSettingCard(rebuild_index)
        return group

    def _rebuild_search_index(self) -> None:
        busy = self.busy("正在重建索引", "数据较多时需要一点时间")
        database.rebuild_fts()
        self.session.expire_all()
        busy.finish("全文检索已可正常使用")
        self.toast_success("索引已重建", "全文检索已可正常使用")

    def _log_group(self) -> SettingCardGroup:
        group = SettingCardGroup("日志", self)

        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.SAVE,
                "日志文件模式",
                "切换后需重启应用生效",
                [
                    ("单文件追加", logging_setup.MODE_SINGLE),
                    ("每次启动一个文件", logging_setup.MODE_SESSION),
                    ("每天一个文件", logging_setup.MODE_DAILY),
                    ("按大小切分", logging_setup.MODE_SIZE),
                ],
                config.logMode.value,
                self._on_log_mode_changed,
                group,
            )
        )
        self._log_keep_files_card = NumberSettingCard(
            config.logKeepFiles,
            FluentIcon.DOCUMENT,
            "最多保留日志文件数",
            "超出后从最早的日志文件开始删除",
            group,
        )
        self._log_max_file_card = NumberSettingCard(
            config.logMaxFileSizeMB,
            FluentIcon.ZIP_FOLDER,
            "单个日志文件大小上限（MB）",
            "达到上限时切分出新文件",
            group,
        )
        self._log_keep_days_card = NumberSettingCard(
            config.logKeepDays,
            FluentIcon.DATE_TIME,
            "日志保留天数",
            "超过该天数的日志文件会被删除",
            group,
        )
        self._log_total_card = NumberSettingCard(
            config.logMaxTotalSizeMB,
            FluentIcon.SAVE,
            "日志总大小上限（MB）",
            "日志目录总体积超限时从最早的文件开始删除",
            group,
        )
        for card in (
            self._log_keep_files_card,
            self._log_max_file_card,
            self._log_keep_days_card,
            self._log_total_card,
        ):
            group.addSettingCard(card)
        self._sync_log_cards()

        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.INFO,
                "日志级别",
                "重启后生效",
                [("调试", "DEBUG"), ("常规", "INFO"), ("警告", "WARNING"), ("错误", "ERROR")],
                config.logLevel.value,
                lambda value: config.set(config.logLevel, value),
                group,
            )
        )
        group.addSettingCard(
            SwitchSettingCard(
                FluentIcon.DOCUMENT,
                "输出到控制台",
                "在控制台同时打印日志",
                configItem=config.logToConsole,
                parent=group,
            )
        )
        group.addSettingCard(
            SwitchSettingCard(
                FluentIcon.SYNC,
                "异步写入",
                "部分受限环境下不可用，程序会自动退化为同步写入",
                configItem=config.logEnqueue,
                parent=group,
            )
        )
        return group

    def _maintenance_group(self) -> SettingCardGroup:
        group = SettingCardGroup("维护", self)
        self._reset_card = ActionCard(
            "恢复初始化",
            FluentIcon.DELETE,
            "恢复初始化",
            "清空全部数据与设置，回到首次运行的状态",
            group,
        )
        self._reset_card.clicked.connect(self._reset_to_defaults)
        group.addSettingCard(self._reset_card)

        self._maintenance_permission_card = SettingCard(
            FluentIcon.INFO,
            "仅默认用户可用",
            "恢复初始化会清空所有用户的数据与设置",
            group,
        )
        group.addSettingCard(self._maintenance_permission_card)
        return group

    def _about_group(self) -> SettingCardGroup:
        group = SettingCardGroup("关于", self)
        group.addSettingCard(
            SettingCard(FluentIcon.INFO, "版本", f"v{APP_VERSION} · 数据目录 {paths.ROOT}", group)
        )
        return group

    # ------------------------------------------------------------------ 行为
    def _on_theme_changed(self, value: str) -> None:
        mapping = {"light": Theme.LIGHT, "dark": Theme.DARK, "auto": Theme.AUTO}
        setTheme(mapping.get(value, Theme.AUTO))
        config.set(config.theme, value)
        plugin_service.publish(Events.THEME_CHANGED, theme=value)

    def _open_path(self, path) -> None:
        if not open_path(path):
            self.toast_warning("无法打开目录", str(path))

    def _reload_session(self) -> None:
        """恢复初始化会销毁数据库引擎，旧会话随之失效。"""
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        self.session = database.new_session()
        self.users = UserService(self.session)

    # ------------------------------------------------------------------ 权限
    def _sync_admin(self) -> None:
        """切换用户后重新判定权限：系统级设置只有默认用户可改。"""
        self._is_admin = UserService(self.session).is_admin()
        self._apply_permissions()

    def _apply_permissions(self) -> None:
        for card in (self._path_card, self._scan_card, self._rebuild_card, self._reset_card):
            card.setEnabled(self._is_admin)
        for hint in (self._library_permission_card, self._maintenance_permission_card):
            hint.setVisible(not self._is_admin)

    def _require_admin(self, action: str) -> bool:
        """系统级设置只有默认用户（管理员）可以改动。"""
        if self._is_admin:
            return True
        self.toast_warning("无权操作", f"只有默认用户可以{action}")
        return False

    def _reset_to_defaults(self) -> None:
        if not self._require_admin("恢复初始化"):
            return
        if not confirm(
            self,
            "恢复初始化",
            "将清空全部数据项、库文件夹、内容仓库、封面与存档，并把所有设置重置为默认值。\n"
            "此操作不可撤销，确定继续？",
        ):
            return
        tip = self.busy("正在恢复初始化", "清空数据并写入默认用户与分类…")
        try:
            reset_to_defaults()
        except Exception as exc:  # noqa: BLE001
            tip.finish("恢复失败")
            self.toast_warning("恢复初始化失败", str(exc))
            return
        self._reload_session()
        self._refresh_libraries()
        for signal in (
            signalBus.itemsChanged,
            signalBus.categoriesChanged,
            signalBus.tagsChanged,
            signalBus.userChanged,
            signalBus.archivesChanged,
            signalBus.librariesChanged,
        ):
            signal.emit()
        tip.finish("已恢复初始化")
        self.toast_success("已恢复初始化", "设置已重置，应用即将重启")
        restart_application()

    def _choose_export_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择默认导出目录", str(export_dir()))
        if not directory:
            return
        config.set(config.exportPath, directory)
        self._export_card.setContent(directory)
        self.toast_success("已更新导出目录", directory)

    def _on_prune_mode_changed(self, value: str) -> None:
        config.set(config.pruneMode, value)
        self._sync_prune_cards()
        signalBus.archivesChanged.emit()

    def _sync_prune_cards(self) -> None:
        mode = str(config.pruneMode.value)
        self._keep_versions_card.setEnabled(mode == "count")
        self._keep_size_card.setEnabled(mode == "size")
        self._keep_days_card.setEnabled(mode == "age")

    def _on_log_mode_changed(self, value: str) -> None:
        config.set(config.logMode, value)
        self._sync_log_cards()

    def _sync_log_cards(self) -> None:
        """按日志模式启用对应的细节设置卡。"""
        mode = str(config.logMode.value)
        self._log_max_file_card.setEnabled(bool(logging_setup.MODE_USES_FILE_SIZE.get(mode, True)))
        self._log_keep_days_card.setEnabled(bool(logging_setup.MODE_USES_KEEP_DAYS.get(mode, False)))

    # ------------------------------------------------------------------ 资源文件夹
    def _library_group(self) -> SettingCardGroup:
        group = SettingCardGroup("资源文件夹", self)

        self._path_card = ActionCard(
            "更改位置",
            FluentIcon.FOLDER,
            "资源文件夹位置",
            str(resources_root()),
            group,
        )
        self._path_card.clicked.connect(self._change_resource_root)
        group.addSettingCard(self._path_card)

        self._scan_card = ActionCard(
            "扫描并登记",
            FluentIcon.SYNC,
            "扫描库文件夹",
            "把各用户名文件夹中已有的文件登记为数据项",
            group,
        )
        self._scan_card.clicked.connect(self._scan_library)
        group.addSettingCard(self._scan_card)

        self._rebuild_card = ActionCard(
            "重建目录结构",
            FluentIcon.FOLDER_ADD,
            "重建目录结构",
            "补齐“全局”文件夹与每个用户的用户名文件夹",
            group,
        )
        self._rebuild_card.clicked.connect(self._rebuild_layout)
        group.addSettingCard(self._rebuild_card)

        open_card = ActionCard(
            "打开文件夹",
            FluentIcon.LINK,
            "打开资源文件夹",
            "在文件管理器中查看库内容（开启保护后同样可以直接打开）",
            group,
        )
        open_card.clicked.connect(self._open_resource_dir)
        group.addSettingCard(open_card)

        self._library_permission_card = SettingCard(
            FluentIcon.INFO,
            "仅默认用户可用",
            "更改位置、扫描并登记、重建目录结构属于全库操作",
            group,
        )
        group.addSettingCard(self._library_permission_card)
        return group

    def _privacy_group(self) -> SettingCardGroup:
        group = SettingCardGroup("隐私保护", self)

        self._resource_switch = SwitchSettingCard(
            FluentIcon.FOLDER,
            "保护资源文件夹",
            "程序退出后对 Everyone 关闭继承并拒绝读/遍历，资源管理器显示「拒绝访问」；运行期间保持可访问",
            configItem=config.resourceProtected,
            parent=group,
        )
        self._resource_switch.checkedChanged.connect(lambda _checked: self._on_protection_changed())
        group.addSettingCard(self._resource_switch)

        self._hidden_switch = SwitchSettingCard(
            FluentIcon.HIDE,
            "保护隐藏文件",
            "程序退出后锁定各分类下的 .hiddens 隐藏目录；隐藏数据在磁盘上同样不可直接查看",
            configItem=config.hiddenProtected,
            parent=group,
        )
        self._hidden_switch.checkedChanged.connect(lambda _checked: self._on_protection_changed())
        group.addSettingCard(self._hidden_switch)

        self._refresh_privacy()
        return group

    def _refresh_privacy(self) -> None:
        if not hasattr(self, "_hidden_switch"):
            return
        # 资源文件夹整体锁定后，隐藏目录已被覆盖
        self._hidden_switch.setEnabled(not config.resourceProtected.value)

    def _normalize_privacy(self) -> None:
        """资源文件夹开启保护后，隐藏文件开关自动收起（置灰不可用）。"""
        if not hasattr(self, "_hidden_switch"):
            return
        if not (config.resourceProtected.value and config.hiddenProtected.value):
            return
        self._suppress_privacy = True
        try:
            config.set(config.hiddenProtected, False)
            self._hidden_switch.setChecked(False)
        finally:
            self._suppress_privacy = False
        privacy.invalidate()

    def _on_protection_changed(self) -> None:
        """开关变化：开启只是记下设置（程序退出后生效），关闭立刻放行。"""
        if getattr(self, "_suppress_privacy", False):
            return
        self._normalize_privacy()
        if not privacy.supported():
            self.toast_warning("当前系统不支持", "只有 Windows 支持 ACL 锁定")
            self._refresh_privacy()
            return
        if config.resourceProtected.value or config.hiddenProtected.value:
            self.toast_success("已开启保护", "程序退出后会锁定保护目录；运行期间保持可访问")
        else:
            count, message = privacy.unlock()
            if count:
                self.toast_success("已关闭保护", f"已放行 {count} 个目录")
            else:
                self.toast_warning("放行失败", message)
        self._refresh_privacy()

    def _library_card(self) -> CardWidget:
        card, layout = self.add_section("库内容", "各用户的数据文件夹与全局资源目录")
        self._library_layout = QVBoxLayout()
        self._library_layout.setContentsMargins(0, 0, 0, 0)
        self._library_layout.setSpacing(6)
        layout.addLayout(self._library_layout)
        self._refresh_libraries()
        return card

    def _refresh_libraries(self) -> None:
        while self._library_layout.count():
            widget = self._library_layout.takeAt(0).widget()
            if widget is not None:
                release_widget(widget)
        service = LibraryService(self.session)
        library = service.ensure_default()
        self._path_card.setContent(str(resources_root()))
        self._library_layout.addWidget(
            self._library_folder_row(
                paths.GLOBAL_DIR_NAME,
                service.global_dir(library),
                "全局配置、内容仓库、封面与备份",
            )
        )
        for info in UserService(self.session).list_users():
            self._library_layout.addWidget(
                self._library_folder_row(
                    info.name,
                    service.user_dir(library, info.user),
                    f"{info.item_count} 项数据 · {info.category_count} 个分类",
                )
            )

    def _library_folder_row(self, name: str, folder: Path, detail: str) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)
        info.addWidget(StrongBodyLabel(f"{name} · {detail}", row))
        info.addWidget(CaptionLabel(str(folder), row))
        layout.addLayout(info, 1)

        open_button = IconTextButton(FluentIcon.FOLDER, "打开", row)
        open_button.clicked.connect(lambda _=False, path=folder: self._open_path(path))
        layout.addWidget(open_button)
        return row

    def _open_resource_dir(self) -> None:
        self._open_path(resources_root())

    def _change_resource_root(self) -> None:
        if not self._require_admin("更改资源文件夹位置"):
            return
        current = resources_root()
        directory = QFileDialog.getExistingDirectory(self, "选择新的资源文件夹位置", str(current))
        if not directory or Path(directory).resolve() == current.resolve():
            return
        target = paths.resource_root(directory)
        if not confirm(
            self,
            "更改资源文件夹位置",
            f"将把资源文件夹（库数据与数据库）\n{current}\n移动到\n{target}\n继续吗？",
        ):
            return
        try:
            moved = set_resource_root(directory)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self.toast_warning("无法更改位置", str(exc))
            return
        # 引擎已指向新位置，各页面持有的会话随之失效：重启程序最稳妥。
        privacy.invalidate()
        self.toast_success("资源文件夹已迁移", f"{moved}\n程序即将重启")
        restart_application()

    def _scan_library(self) -> None:
        if not self._require_admin("扫描并登记库文件夹"):
            return
        busy = self.busy("正在扫描库文件夹", "扫描完成后文件才会登记为数据项")
        try:
            service = LibraryService(self.session)
            result = service.scan(service.ensure_default())
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("扫描失败")
            self.toast_warning("扫描失败", str(exc))
            return
        for signal in (signalBus.itemsChanged, signalBus.categoriesChanged, signalBus.librariesChanged):
            signal.emit()
        self._refresh_libraries()
        busy.finish(f"扫描完成：{result.summary()}")
        self.toast_success("扫描完成", result.summary())

    def _rebuild_layout(self) -> None:
        if not self._require_admin("重建目录结构"):
            return
        try:
            created = LibraryService(self.session).rebuild_layout()
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            self.toast_warning("无法重建目录结构", str(exc))
            return
        signalBus.librariesChanged.emit()
        self._refresh_libraries()
        self.toast_success("已重建目录结构", "、".join(created))


__all__ = ["SettingsPage"]
