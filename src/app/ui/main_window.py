"""主窗口：导航与页面装配。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
)

from ..core import paths
from ..core.config import config
from ..core.signals import signalBus
from ..db.database import new_session
from .pages.archive_page import ArchivePage
from .pages.home_page import HomePage
from .pages.import_page import ImportPage
from .pages.manage_page import ManagePage
from .pages.open_with_page import OpenWithPage
from .pages.plugin_page import PluginPage
from .pages.settings_page import SettingsPage
from .pages.tag_page import TagPage
from .pages.user_page import UserPage
from .widgets.cover_loader import shutdown_cover_loader
from .widgets.library_watcher import LibraryWatcher


class MainWindow(FluentWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.home_page = HomePage(self)
        self.import_page = ImportPage(self)
        self.manage_page = ManagePage(self)
        self.tag_page = TagPage(self)
        self.user_page = UserPage(self)
        self.archive_page = ArchivePage(self)
        self.open_with_page = OpenWithPage(self)
        self.plugin_page = PluginPage(self)
        self.settings_page = SettingsPage(self)

        self._init_navigation()
        self._init_window()
        self._connect_signals()

        self._watch_session = new_session()
        self.library_watcher = LibraryWatcher(self._watch_session, self)
        self.library_watcher.changed.connect(self._on_library_changed)
        signalBus.librariesChanged.connect(self._on_libraries_changed)
        signalBus.itemsChanged.connect(self.library_watcher.pause)

    def _init_navigation(self) -> None:
        self.addSubInterface(self.home_page, FluentIcon.HOME, "首页")
        self.addSubInterface(self.import_page, FluentIcon.CLOUD, "导入")
        self.addSubInterface(self.manage_page, FluentIcon.FOLDER, "数据管理")
        self.addSubInterface(self.tag_page, FluentIcon.TAG, "标签")
        self.addSubInterface(self.user_page, FluentIcon.PEOPLE, "用户")
        self.addSubInterface(self.archive_page, FluentIcon.HISTORY, "存档")
        self.addSubInterface(self.open_with_page, FluentIcon.APPLICATION, "打开方式")
        self.addSubInterface(self.plugin_page, FluentIcon.TILES, "插件")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, "设置", NavigationItemPosition.BOTTOM)

    def _init_window(self) -> None:
        self._centered = False
        self.setWindowTitle("个人数据管理器")
        self.resize(1200, 780)
        self.setMinimumSize(960, 640)

        logo = paths.IMAGE_DIR / "logo.png"
        if logo.exists():
            self.setWindowIcon(QIcon(str(logo)))

        self.setMicaEffectEnabled(bool(config.micaEnabled.value))

    def showEvent(self, event) -> None:  # noqa: N802
        """首次显示时把窗口摆到当前屏幕中央。"""
        super().showEvent(event)
        if self._centered:
            return
        self._centered = True
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = self.frameGeometry()
        geometry.moveCenter(screen.availableGeometry().center())
        self.move(geometry.topLeft())

    def _connect_signals(self) -> None:
        signalBus.requestImport.connect(lambda: self.switchTo(self.import_page))
        signalBus.requestManage.connect(lambda: self.switchTo(self.manage_page))
        signalBus.requestArchive.connect(lambda: self.switchTo(self.archive_page))
        signalBus.requestPlugins.connect(self._on_request_plugins)
        signalBus.focusItem.connect(self._on_focus_item)
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)

    def _on_focus_item(self, item_id: int) -> None:
        """从概览页的「最近导入」跳到数据管理页并选中对应的数据项。"""
        self.switchTo(self.manage_page)
        self.manage_page.focus_item(item_id)

    def _on_request_plugins(self, kind: str = "") -> None:
        """从「打开方式」页跳到插件页并自动筛选成对应类型。"""
        self.switchTo(self.plugin_page)
        self.plugin_page.apply_kind(kind)

    def _on_libraries_changed(self) -> None:
        """库增删或扫描登记后重新绑定监听，并忽略本次内部写入。"""
        self.library_watcher.pause()
        self.library_watcher.refresh()

    def _on_library_changed(self) -> None:
        InfoBar.warning(
            title="库文件夹发生变化",
            content="库中有外部新增或删除的文件，可在「设置 → 库文件夹」中执行「扫描并登记」。",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=8000,
            parent=self,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        for page in (
            self.home_page,
            self.import_page,
            self.manage_page,
            self.tag_page,
            self.user_page,
            self.archive_page,
            self.open_with_page,
            self.plugin_page,
            self.settings_page,
        ):
            session = getattr(page, "session", None)
            if session is not None:
                session.close()
        self._watch_session.close()
        shutdown_cover_loader()
        super().closeEvent(event)


__all__ = ["MainWindow"]
