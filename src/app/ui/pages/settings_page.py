"""设置页：外观、导入、存储、日志与维护。"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PushButton,
    PushSettingCard,
    RangeSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    StrongBodyLabel,
    SwitchSettingCard,
    Theme,
    setTheme,
)

from ...core import logging_setup, paths
from ...core.config import config, export_dir, library_root
from ...core.signals import signalBus
from ...db import database
from ...services import LibraryService, UserService
from ...services.maintenance import reset_to_defaults
from ..common import (
    DETAIL_MARGINS,
    BusyTip,
    confirm,
    clear_scroll_background,
    page_background,
    page_header,
    page_layout,
    panel_card,
    release_widget,
    restart_application,
    toast_success,
    toast_warning,
)

APP_VERSION = "0.1.0"


class ComboSettingCard(SettingCard):
    """下拉选择设置卡（不依赖 OptionsConfigItem）。"""

    def __init__(self, icon, title: str, content: str, options, current, callback, parent=None) -> None:
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        for label, value in options:
            self.combo.addItem(label, userData=value)
        for index in range(self.combo.count()):
            if self.combo.itemData(index) == current:
                self.combo.setCurrentIndex(index)
                break
        self.combo.currentIndexChanged.connect(lambda _index: callback(self.combo.currentData()))
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


class ActionCard(PushSettingCard):
    """设置卡上的操作按钮：换成 Fluent PushButton，避免全站混入原生 QPushButton。"""

    def __init__(self, text, icon, title: str, content: str, parent=None) -> None:
        super().__init__(text, icon, title, content, parent)
        index = self.hBoxLayout.indexOf(self.button)
        release_widget(self.button)
        self.button = PushButton(text, self)
        self.hBoxLayout.insertWidget(index, self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.button.clicked.connect(self.clicked)


class SettingsPage(ScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.session = database.new_session()

        host = QWidget(self)
        page_background(host, "settingsHost")
        layout = page_layout(host)
        page_header(layout, host, "设置")

        layout.addWidget(self._appearance_group(host))
        layout.addWidget(self._import_group(host))
        layout.addWidget(self._storage_group(host))
        layout.addWidget(self._library_group(host))
        layout.addWidget(self._library_card(host))
        layout.addWidget(self._log_group(host))
        layout.addWidget(self._maintenance_group(host))
        layout.addWidget(self._about_group(host))
        layout.addStretch(1)

        self.setWidget(host)
        clear_scroll_background(self, inner=False)
        self.setWidgetResizable(True)

    # ------------------------------------------------------------------ 分组
    def _appearance_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("外观", parent)

        theme_card = ComboSettingCard(
            FluentIcon.BRUSH,
            "主题",
            "切换浅色、深色或跟随系统",
            [("浅色", "light"), ("深色", "dark"), ("跟随系统", "auto")],
            config.theme.value,
            self._on_theme_changed,
            group,
        )
        group.addSettingCard(theme_card)

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
        return group

    def _import_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("导入", parent)

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

    def _storage_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("存储", parent)

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
        self._keep_versions_card = RangeSettingCard(
            config.keepVersions,
            FluentIcon.HISTORY,
            "存档保留数量",
            "超过该数量时自动删除最早的存档",
            group,
        )
        self._keep_size_card = RangeSettingCard(
            config.keepSize,
            FluentIcon.SAVE,
            "仓库容量上限（MB）",
            "超出后从最早的存档开始删除；数据项自身的内容不会被删除",
            group,
        )
        self._keep_days_card = RangeSettingCard(
            config.keepDays,
            FluentIcon.DATE_TIME,
            "存档保留天数",
            "超过该天数的存档会被自动删除",
            group,
        )
        for card in (self._keep_versions_card, self._keep_size_card, self._keep_days_card):
            group.addSettingCard(card)
        self._sync_prune_cards()

        rebuild_index = ActionCard("重建索引", FluentIcon.SYNC, "全文检索索引", "检索结果异常时重建索引", group)
        rebuild_index.clicked.connect(self._rebuild_search_index)
        group.addSettingCard(rebuild_index)
        return group

    def _rebuild_search_index(self) -> None:
        busy = BusyTip(self, "正在重建索引", "数据较多时需要一点时间")
        database.rebuild_fts()
        self.session.expire_all()
        busy.finish("全文检索已可正常使用")
        toast_success(self, "索引已重建", "全文检索已可正常使用")

    def _log_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("日志", parent)

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
        self._log_keep_files_card = RangeSettingCard(
            config.logKeepFiles,
            FluentIcon.DOCUMENT,
            "最多保留日志文件数",
            "超出后从最早的日志文件开始删除",
            group,
        )
        self._log_max_file_card = RangeSettingCard(
            config.logMaxFileSizeMB,
            FluentIcon.ZIP_FOLDER,
            "单个日志文件大小上限（MB）",
            "达到上限时切分出新文件",
            group,
        )
        self._log_keep_days_card = RangeSettingCard(
            config.logKeepDays,
            FluentIcon.DATE_TIME,
            "日志保留天数",
            "超过该天数的日志文件会被删除",
            group,
        )
        self._log_total_card = RangeSettingCard(
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

    def _maintenance_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("维护", parent)
        reset_card = ActionCard(
            "恢复初始化",
            FluentIcon.DELETE,
            "恢复初始化",
            "清空全部数据与设置，回到首次运行的状态",
            group,
        )
        reset_card.clicked.connect(self._reset_to_defaults)
        group.addSettingCard(reset_card)
        return group

    def _about_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("关于", parent)
        group.addSettingCard(
            SettingCard(FluentIcon.INFO, "版本", f"v{APP_VERSION} · 数据目录 {paths.ROOT}", group)
        )
        return group

    # ------------------------------------------------------------------ 行为
    def _on_theme_changed(self, value: str) -> None:
        mapping = {"light": Theme.LIGHT, "dark": Theme.DARK, "auto": Theme.AUTO}
        setTheme(mapping.get(value, Theme.AUTO))
        config.set(config.theme, value)
        toast_success(self, "主题已切换", {"light": "浅色", "dark": "深色"}.get(value, "跟随系统"))

    def _open_path(self, path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            toast_warning(self, "无法打开目录", str(exc))

    def _reload_session(self) -> None:
        """恢复初始化会销毁数据库引擎，旧会话随之失效。"""
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        self.session = database.new_session()

    def _reset_to_defaults(self) -> None:
        if not confirm(
            self,
            "恢复初始化",
            "将清空全部数据项、库文件夹、内容仓库、封面与存档，并把所有设置重置为默认值。\n"
            "此操作不可撤销，确定继续？",
        ):
            return
        tip = BusyTip(self, "正在恢复初始化", "清空数据并写入默认用户与分类…")
        try:
            reset_to_defaults()
        except Exception as exc:  # noqa: BLE001
            tip.finish("恢复失败")
            toast_warning(self, "恢复初始化失败", str(exc))
            return
        self._reload_session()
        self._refresh_libraries()
        signalBus.itemsChanged.emit()
        signalBus.categoriesChanged.emit()
        signalBus.tagsChanged.emit()
        signalBus.userChanged.emit()
        signalBus.archivesChanged.emit()
        signalBus.librariesChanged.emit()
        tip.finish("已恢复初始化")
        toast_success(self, "已恢复初始化", "设置已重置，应用即将重启")
        restart_application()

    def _choose_export_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择默认导出目录", str(export_dir()))
        if not directory:
            return
        config.set(config.exportPath, directory)
        self._export_card.setContent(directory)
        toast_success(self, "已更新导出目录", directory)

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
        label = logging_setup.MODE_LABELS.get(value, value)
        toast_success(self, "已切换日志文件模式", f"{label} · 重启应用后生效")

    def _sync_log_cards(self) -> None:
        """按日志模式启用对应的细节设置卡。"""
        mode = str(config.logMode.value)
        self._log_max_file_card.setEnabled(bool(logging_setup.MODE_USES_FILE_SIZE.get(mode, True)))
        self._log_keep_days_card.setEnabled(bool(logging_setup.MODE_USES_KEEP_DAYS.get(mode, False)))

    # ------------------------------------------------------------------ 库
    def _library_group(self, parent: QWidget) -> SettingCardGroup:
        group = SettingCardGroup("库文件夹", parent)

        self._path_card = ActionCard(
            "更改位置",
            FluentIcon.FOLDER,
            "库文件夹位置",
            str(library_root()),
            group,
        )
        self._path_card.clicked.connect(self._change_library_path)
        group.addSettingCard(self._path_card)

        scan_card = ActionCard(
            "扫描并登记",
            FluentIcon.SYNC,
            "扫描库文件夹",
            "把各用户名文件夹中已有的文件登记为数据项",
            group,
        )
        scan_card.clicked.connect(self._scan_library)
        group.addSettingCard(scan_card)

        rebuild_card = ActionCard(
            "重建目录结构",
            FluentIcon.FOLDER_ADD,
            "重建目录结构",
            "补齐“全局”文件夹与每个用户的用户名文件夹",
            group,
        )
        rebuild_card.clicked.connect(self._rebuild_layout)
        group.addSettingCard(rebuild_card)

        open_card = ActionCard(
            "打开文件夹",
            FluentIcon.LINK,
            "打开库文件夹",
            "在文件管理器中查看库内容",
            group,
        )
        open_card.clicked.connect(self._open_library_dir)
        group.addSettingCard(open_card)
        return group

    def _library_card(self, parent: QWidget) -> CardWidget:
        card, layout = panel_card(parent, DETAIL_MARGINS)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel("库内容", card))
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
        self._path_card.setContent(str(library.path))
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

        open_button = PushButton(FluentIcon.FOLDER, "打开", row)
        open_button.clicked.connect(lambda _=False, path=folder: self._open_path(path))
        layout.addWidget(open_button)
        return row

    def _open_library_dir(self) -> None:
        self._open_path(Path(LibraryService(self.session).ensure_default().path))

    def _change_library_path(self) -> None:
        current = LibraryService(self.session).ensure_default().path
        directory = QFileDialog.getExistingDirectory(self, "选择新的库文件夹位置", current)
        if not directory or directory == current:
            return
        if not confirm(self, "更改库文件夹位置", f"将把库内容从\n{current}\n移动到\n{directory}\n继续吗？"):
            return
        service = LibraryService(self.session)
        try:
            service.set_path(directory)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            toast_warning(self, "无法更改位置", str(exc))
            return
        for signal in (signalBus.librariesChanged, signalBus.itemsChanged, signalBus.categoriesChanged):
            signal.emit()
        self._refresh_libraries()
        toast_success(self, "库文件夹已迁移", directory)

    def _scan_library(self) -> None:
        busy = BusyTip(self, "正在扫描库文件夹", "扫描完成后文件才会登记为数据项")
        try:
            service = LibraryService(self.session)
            result = service.scan(service.ensure_default())
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            busy.finish("扫描失败")
            toast_warning(self, "扫描失败", str(exc))
            return
        for signal in (signalBus.itemsChanged, signalBus.categoriesChanged, signalBus.librariesChanged):
            signal.emit()
        self._refresh_libraries()
        busy.finish(f"扫描完成：{result.summary()}")
        toast_success(self, "扫描完成", result.summary())

    def _rebuild_layout(self) -> None:
        try:
            created = LibraryService(self.session).rebuild_layout()
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            toast_warning(self, "无法重建目录结构", str(exc))
            return
        signalBus.librariesChanged.emit()
        self._refresh_libraries()
        toast_success(self, "已重建目录结构", "、".join(created))


__all__ = ["SettingsPage"]
