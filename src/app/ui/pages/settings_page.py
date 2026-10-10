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
    SettingCard,
    SettingCardGroup,
    StrongBodyLabel,
    SwitchSettingCard,
    Theme,
    setTheme,
)

from ...core.runtime import logging_setup, paths
from ...core.config import (
    DOUBLE_CLICK_EDITOR,
    DOUBLE_CLICK_VIEWER,
    ResourceRootExists,
    config,
    export_dir,
    import_resource_root,
    resources_root,
    set_resource_root,
)
from ...core.runtime.signals import signalBus
from ...core.runtime.version import APP_VERSION
from ...db import database
from ...sdk import Events, ExtensionPoint
from ...sdk.data import human_size
from ...services import (
    DatabaseBundleError,
    DatabaseBundleService,
    LibraryService,
    UserService,
    import_replace,
    inspect_package,
    timestamp_name,
)
from ...services import cover_service
from ...services.maintenance import compact_database, reset_to_defaults
from ...services.plugin_service import plugin_service
from ...services.privacy_service import privacy
from ..components.pager import PAGE_SIZES, normalize_page_size
from ..framework import (
    ActionCard,
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
            self._backup_group(),
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
        group.addSettingCard(
            ComboSettingCard(
                FluentIcon.EDIT,
                "左键双击",
                "数据管理页左键双击条目时的动作（右键菜单里仍可随时选用另一个）",
                [("打开查看器", DOUBLE_CLICK_VIEWER), ("打开编辑器", DOUBLE_CLICK_EDITOR)],
                config.doubleClickAction.value,
                lambda value: config.set(config.doubleClickAction, value),
                group,
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
        self._compact_card = ActionCard(
            "整理数据库",
            FluentIcon.ZIP_FOLDER,
            "整理数据库",
            "合并数据库里的空洞并刷新查询统计；删过大量数据后运行可以减小体积",
            group,
        )
        self._compact_card.clicked.connect(self._compact_database)
        group.addSettingCard(self._compact_card)

        self._cover_card = ActionCard(
            "重置封面",
            FluentIcon.PHOTO,
            "重置封面数据",
            "清空现有封面文件，再按当前规范重新生成一遍：图片直接用自己、视频取第一帧",
            group,
        )
        self._cover_card.clicked.connect(self._reset_covers)
        group.addSettingCard(self._cover_card)

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
            "整理数据库、重置封面与恢复初始化都属于全库操作",
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
        for card in (
            self._path_card,
            self._import_card,
            self._scan_card,
            self._rebuild_card,
            self._compact_card,
            self._reset_card,
            self._db_export_card,
            self._db_merge_card,
            self._db_replace_card,
        ):
            card.setEnabled(self._is_admin)
        for hint in (
            self._library_permission_card,
            self._maintenance_permission_card,
            self._backup_permission_card,
        ):
            hint.setVisible(not self._is_admin)

    def _require_admin(self, action: str) -> bool:
        """系统级设置只有默认用户（管理员）可以改动。"""
        if self._is_admin:
            return True
        self.toast_warning("无权操作", f"只有默认用户可以{action}")
        return False

    def _compact_database(self) -> None:
        """整理数据库：`VACUUM` 要独占整库，所以先断引擎、整理完重建会话。"""
        if not self._require_admin("整理数据库"):
            return
        if not confirm(
            self,
            "整理数据库",
            "将合并数据库里的空洞并刷新查询统计，运行期间界面会短暂无响应。\n确定继续？",
        ):
            return
        tip = self.busy("正在整理数据库", "合并空洞并刷新统计…")
        try:
            result = compact_database()
        except Exception as exc:  # noqa: BLE001
            tip.finish("整理失败")
            self.toast_warning("整理数据库失败", str(exc))
            return
        self._reload_session()
        tip.finish("整理完成")
        self.toast_success("整理数据库完成", f"释放 {human_size(result.get('freed', 0))}")

    def _reset_covers(self) -> None:
        """重置封面：清空 `全局/covers/` 与所有 `cover_path`，再按规范重新生成。"""
        if not self._require_admin("重置封面"):
            return
        if not confirm(
            self,
            "重置封面",
            "将清空现有封面文件并按规范重新生成：图片直接用自己、视频重新抽取第一帧、"
            "其它类型不再持有封面。\n数据文件本身不受影响，确定继续？",
        ):
            return
        tip = self.busy("正在重置封面", "清空旧封面并按规范重新生成…")
        try:
            result = cover_service.reset_covers(self.session)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            tip.finish("重置失败")
            self.toast_warning("重置封面失败", str(exc))
            return
        signalBus.itemsChanged.emit()
        tip.finish("重置完成")
        message = f"生成 {result['covers']} 个视频封面、清理旧封面 {result['cleared']} 个"
        if result["failed"]:
            self.toast_warning(
                "重置封面完成",
                f"{message}；{result['failed']} 个视频没能抽到第一帧（编码不支持或文件损坏）",
            )
        else:
            self.toast_success("重置封面完成", message)

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

    # ------------------------------------------------------------------ 整库包
    def _database_target(self) -> Path:
        name = f"数据库包-{timestamp_name()}.zip"
        try:
            return export_dir() / name
        except OSError:
            return Path(name)

    def _read_package(self, action: str):
        """选一个整库包并读它的清单；读不了就提示并返回 None。"""
        chosen, _filter = QFileDialog.getOpenFileName(
            self, action, str(export_dir()), "整库包 (*.zip);;所有文件 (*)"
        )
        if not chosen:
            return None
        try:
            return Path(chosen), inspect_package(chosen)
        except (DatabaseBundleError, OSError) as exc:
            self.toast_warning("读不了这个整库包", str(exc))
            return None

    def _after_database_import(self) -> None:
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

    def _toast_import(self, report) -> None:
        if report.missing:
            self.toast_warning("导入完成，但有内容缺失", report.summary())
        else:
            self.toast_success("导入完成", report.summary())

    def _on_export_database(self) -> None:
        if not self._require_admin("导出整库"):
            return
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "导出整库包",
            str(self._database_target()),
            "整库包 (*.zip);;所有文件 (*)",
        )
        if not chosen:
            return
        tip = self.busy("正在导出整库", "打包数据库与库内文件…")
        try:
            result = DatabaseBundleService(self.session).export(chosen)
        except (DatabaseBundleError, OSError) as exc:
            tip.finish("导出失败")
            self.toast_warning("导出整库失败", str(exc))
            return
        tip.finish("导出完成")
        self.toast_success("已导出整库包", result.summary())

    def _on_import_database_merge(self) -> None:
        if not self._require_admin("导入整库"):
            return
        picked = self._read_package("导入整库（新增式）")
        if picked is None:
            return
        path, info = picked
        if not confirm(
            self,
            "导入整库（新增式）",
            f"将从 {path.name} 导入：{info.summary()}\n\n"
            "现有数据不会被删除；同名用户、分类与标签会合并，重名文件在同目录加 _1 后缀，"
            "重名存档加「（导入）」后缀。\n确定继续？",
        ):
            return
        tip = self.busy("正在导入整库", "合并用户、分类、标签与数据项…")
        try:
            report = DatabaseBundleService(self.session).import_merge(path)
        except (DatabaseBundleError, OSError) as exc:
            try:
                self.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            tip.finish("导入失败")
            self.toast_warning("导入整库失败", str(exc))
            return
        self._after_database_import()
        tip.finish("导入完成")
        self._toast_import(report)

    def _on_import_database_replace(self) -> None:
        if not self._require_admin("导入整库"):
            return
        picked = self._read_package("导入整库（覆盖式）")
        if picked is None:
            return
        path, info = picked
        if not confirm(
            self,
            "导入整库（覆盖式）",
            f"将用 {path.name} 里的数据库与库文件夹整体替换现在的数据：{info.summary()}\n\n"
            "现在的数据库会另存一份 data.db.bak-<时间戳>，库文件夹改名成 library.bak-<时间戳>，"
            "但界面上的数据会立刻变成包里的。\n确定继续？",
        ):
            return
        if not confirm(
            self,
            "再确认一次",
            "覆盖式导入会清空当前数据库与库文件夹（只留上面提到的备份）。\n确定要覆盖？",
        ):
            return
        tip = self.busy("正在导入整库", "替换数据库与库文件夹…")
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            report = import_replace(path)
        except (DatabaseBundleError, OSError) as exc:
            self._reload_session()
            tip.finish("导入失败")
            self.toast_warning("导入整库失败", str(exc))
            return
        self._reload_session()
        self._after_database_import()
        tip.finish("导入完成")
        self.toast_success("覆盖式导入完成", f"{report.summary()}，应用即将重启")
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

        self._import_card = ActionCard(
            "导入资源文件夹",
            FluentIcon.DOWNLOAD,
            "导入资源文件夹",
            "把一份现成的资源文件夹直接作为当前资源文件夹使用（不搬动、不删除任何一份）",
            group,
        )
        self._import_card.clicked.connect(self._import_resource_root)
        group.addSettingCard(self._import_card)

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

    # ------------------------------------------------------------------ 备份与迁移
    def _backup_group(self) -> SettingCardGroup:
        group = SettingCardGroup("备份与迁移", self)

        self._db_export_card = ActionCard(
            "导出整库",
            FluentIcon.SAVE,
            "导出整库包",
            "把数据库与库文件夹里的文件打成一个压缩包，可以在别的机器上导入",
            group,
        )
        self._db_export_card.clicked.connect(self._on_export_database)
        group.addSettingCard(self._db_export_card)

        self._db_merge_card = ActionCard(
            "导入整库",
            FluentIcon.DOWNLOAD,
            "导入整库（新增式）",
            "把包里的用户、分类、标签、数据项与存档并进现有数据；同名分类标签合并，重名文件加 _1 后缀，不删任何东西",
            group,
        )
        self._db_merge_card.clicked.connect(self._on_import_database_merge)
        group.addSettingCard(self._db_merge_card)

        self._db_replace_card = ActionCard(
            "导入整库",
            FluentIcon.SYNC,
            "导入整库（覆盖式）",
            "用包里的数据库与库文件夹整体替换现在的内容；原数据会改名备份，界面上的数据随即变成包里的",
            group,
        )
        self._db_replace_card.clicked.connect(self._on_import_database_replace)
        group.addSettingCard(self._db_replace_card)

        self._backup_permission_card = SettingCard(
            FluentIcon.INFO,
            "仅默认用户可用",
            "整库导入导出会读写数据库与整个库文件夹，属于全库操作",
            group,
        )
        group.addSettingCard(self._backup_permission_card)
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
        # 页面自己的会话占着 data.db，Windows 会因此拒绝改名/删除，先放掉连接
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            moved = set_resource_root(directory)
        except ResourceRootExists as exc:
            # 目标位置里已经有一个资源文件夹（多半是上次搬到一半留下的），问一句再决定
            if not confirm(
                self,
                "目标位置里已经有资源文件夹",
                f"{exc.target}\n\n"
                f"这里已经有一个 {paths.RESOURCE_ROOT_NAME}（可能是上一次搬迁留下的，数据可能不完整）。\n\n"
                "删掉它、把当前资源文件夹重新搬过去吗？\n"
                "（只删目标位置那一份；当前资源文件夹里的数据不受影响。）",
            ):
                self._reload_session()
                return
            try:
                moved = set_resource_root(directory, replace=True)
            except Exception as retry_exc:  # noqa: BLE001
                self._reload_session()
                self.toast_warning("无法更改位置", str(retry_exc))
                return
        except Exception as exc:  # noqa: BLE001
            self._reload_session()
            self.toast_warning("无法更改位置", str(exc))
            return
        # 引擎已指向新位置，各页面持有的会话随之失效：重启程序最稳妥。
        privacy.invalidate()
        self.toast_success("资源文件夹已迁移", f"{moved}\n程序即将重启")
        restart_application()

    def _import_resource_root(self) -> None:
        """把一份现成的资源文件夹直接采纳为当前资源文件夹（不搬、不删，用户 m03406 第 2 条）。

        与「更改位置」（迁移）的区别：迁移会把当前资源文件夹搬过去、并删掉留下的那一份；
        导入只改配置指向，两份都原样留在盘上，所以随时可以再导入回来，也不会破坏别的一份。
        """
        if not self._require_admin("导入资源文件夹"):
            return
        directory = QFileDialog.getExistingDirectory(
            self, "选择要导入的资源文件夹", str(resources_root().parent)
        )
        if not directory:
            return
        target = paths.resource_root(directory)
        if target.resolve() == resources_root().resolve():
            self.toast_info("无需导入", "这已经是当前使用的资源文件夹")
            return
        if not confirm(
            self,
            "导入资源文件夹",
            f"将直接把\n{target}\n作为当前资源文件夹使用。\n\n"
            f"它与当前使用的资源文件夹都会原样保留，不会删除任何一边；\n"
            f"想换回来的时候再导入一次即可。继续吗？",
        ):
            return
        # 页面自己的会话占着 data.db，切换库根前先放掉连接
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            imported = import_resource_root(directory)
        except Exception as exc:  # noqa: BLE001
            self._reload_session()
            self.toast_warning("无法导入资源文件夹", str(exc))
            return
        # 引擎已指向新位置，各页面持有的会话随之失效：重启程序最稳妥。
        privacy.invalidate()
        self.toast_success("资源文件夹已导入", f"{imported}\n程序即将重启")
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
