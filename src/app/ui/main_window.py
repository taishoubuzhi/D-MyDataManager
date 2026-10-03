"""主窗口：导航与页面装配。"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
)

from ..core import paths
from ..core.app_ui import APP_UI_EXTENSION, PageSpec
from ..core.config import config
from ..core.extensions import extension_registry
from ..core.signals import signalBus
from ..db.database import new_session
from .pages.archive_page import ArchivePage
from .pages.home_page import HomePage
from .pages.import_page import ImportPage
from .pages.manage_page import ManagePage
from .pages.plugin_page import PluginPage
from .pages.settings_page import SettingsPage
from .pages.tag_page import TagPage
from .pages.user_page import UserPage
from .pages.workbench_page import NavEntry, WorkbenchPage
from .components.cover_loader import shutdown_cover_loader
from .components.library_watcher import LibraryWatcher


#: 内置页面：属性名、侧栏标题、图标、是否放在侧栏底部。
BUILTIN_PAGES: tuple[tuple[str, str, FluentIcon, bool], ...] = (
    ("home_page", "首页", FluentIcon.HOME, False),
    ("import_page", "导入", FluentIcon.CLOUD, False),
    ("manage_page", "数据管理", FluentIcon.FOLDER, False),
    ("tag_page", "标签", FluentIcon.TAG, False),
    ("user_page", "用户", FluentIcon.PEOPLE, False),
    ("archive_page", "存档", FluentIcon.HISTORY, False),
    ("plugin_page", "插件", FluentIcon.TILES, False),
    ("workbench_page", "页面管理", FluentIcon.LAYOUT, False),
    ("settings_page", "设置", FluentIcon.SETTING, True),
)

#: 侧栏最多追加的插件页面图标数：默认窗口高度下不会把侧栏撑出滚动条，
#: 超出的插件页面只出现在「页面管理」页里（内置页面永远全部显示）。
PLUGIN_SIDEBAR_LIMIT = 7

class MainWindow(FluentWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plugin_pages: dict[str, QWidget] = {}
        self._plugin_specs: dict[str, PageSpec] = {}
        #: 已经追加到侧栏的插件页面路由（顺序即侧栏里的顺序）。
        self._plugin_nav_routes: list[str] = []
        self.home_page = HomePage(self)
        self.import_page = ImportPage(self)
        self.manage_page = ManagePage(self)
        self.tag_page = TagPage(self)
        self.user_page = UserPage(self)
        self.archive_page = ArchivePage(self)
        self.plugin_page = PluginPage(self)
        self.workbench_page = WorkbenchPage(self)
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
        """把页面挂进堆叠区：内置页按内置顺序装配侧栏，插件页面随后追加。"""
        for attr, title, icon, bottom in BUILTIN_PAGES:
            position = NavigationItemPosition.BOTTOM if bottom else NavigationItemPosition.TOP
            self.addSubInterface(getattr(self, attr), icon, title, position)
        self._sync_plugin_pages()
        # 「页面管理」列出全部页面：启动时自己填一次，没有插件页面也不能是空页。
        self.workbench_page.refresh()

    # ------------------------------------------------------ 插件界面
    def _app_ui(self):
        """程序本体提供的界面扩展接口（`app.ui`），未注册时返回 None。"""
        return extension_registry.provider(APP_UI_EXTENSION)

    def plugin_pages(self) -> tuple[str, ...]:
        """当前已装配的插件页面 key。"""
        return tuple(self._plugin_pages)

    def _sync_plugin_pages(self) -> None:
        """按 `app.ui` 里登记的页面增删堆叠区页面：插件启停或重载后保持同步。"""
        api = self._app_ui()
        specs: dict[str, PageSpec] = {}
        if api is not None and hasattr(api, "pages"):
            for spec in api.pages():
                specs[spec.key] = spec
        for key in [key for key in self._plugin_pages if key not in specs]:
            widget = self._plugin_pages.pop(key)
            self._plugin_specs.pop(key, None)
            self.removeInterface(widget, True)
        for key, spec in specs.items():
            if key in self._plugin_pages:
                continue
            widget = self._build_plugin_page(spec)
            if widget is None:
                continue
            widget.setObjectName(spec.route)
            self._plugin_pages[key] = widget
            self._plugin_specs[key] = spec
            self.stackedWidget.addWidget(widget)
        self._sync_plugin_nav()

    def _build_plugin_page(self, spec: PageSpec) -> QWidget | None:
        """调用插件提供的工厂创建页面控件；插件出错时退化成提示页。"""
        try:
            widget = spec.factory()
        except Exception as exc:  # 插件页面出错不应该影响主界面
            logger.exception("创建插件页面失败：{}", spec.key)
            return CaptionLabel(f"插件页面无法显示：{exc}", self)
        if not isinstance(widget, QWidget):
            logger.warning("插件页面工厂没有返回控件：{}", spec.key)
            return CaptionLabel(f"插件页面无法显示：{spec.title}", self)
        widget.setParent(self)
        return widget

    def _plugin_icon(self, name: str):
        """把清单里的图标名解析成 FluentIcon，未知名字回退成通用图标。"""
        icon = getattr(FluentIcon, str(name or "").upper(), None)
        return icon if icon is not None else FluentIcon.APPLICATION

    # ------------------------------------------------------ 侧栏与页面清单
    def sidebar_routes(self) -> tuple[str, ...]:
        """当前侧栏里显示的页面路由：内置页按内置顺序（设置恒在最后），插件页追加在后。"""
        routes = [
            getattr(self, attr).objectName()
            for attr, _title, _icon, bottom in BUILTIN_PAGES
            if not bottom
        ]
        routes.extend(self._plugin_nav_routes)
        routes.extend(
            getattr(self, attr).objectName()
            for attr, _title, _icon, bottom in BUILTIN_PAGES
            if bottom
        )
        return tuple(routes)

    def page_entries(self) -> list[NavEntry]:
        """全部页面（内置 + 插件）的清单，供「页面管理」页列出与打开。"""
        shown = set(self.sidebar_routes())
        entries: list[NavEntry] = []
        for attr, title, icon, bottom in BUILTIN_PAGES:
            widget = getattr(self, attr)
            entries.append(
                NavEntry(
                    widget=widget,
                    icon=icon,
                    title=title,
                    bottom=bottom,
                    in_sidebar=widget.objectName() in shown,
                )
            )
        for key, widget in self._plugin_pages.items():
            spec = self._plugin_specs.get(key)
            if spec is None:
                continue
            entries.append(
                NavEntry(
                    widget=widget,
                    icon=self._plugin_icon(spec.icon),
                    title=spec.title,
                    builtin=False,
                    plugin_id=spec.plugin_id,
                    bottom=spec.bottom,
                    in_sidebar=widget.objectName() in shown,
                )
            )
        return entries

    def _sync_plugin_nav(self) -> None:
        """把插件页面按载入顺序追加到侧栏；超出名额的只在「页面管理」页里出现。"""
        live = {widget.objectName() for widget in self._plugin_pages.values()}
        shown = [route for route in self._plugin_nav_routes if route in live]
        for route in self._plugin_nav_routes:
            if route not in shown:
                self.navigationInterface.removeWidget(route)
        self._plugin_nav_routes = shown
        for key, widget in self._plugin_pages.items():
            route = widget.objectName()
            if route in self._plugin_nav_routes:
                continue
            spec = self._plugin_specs.get(key)
            if spec is None or len(self._plugin_nav_routes) >= PLUGIN_SIDEBAR_LIMIT:
                continue
            # 插件页面一律追加在内置页面之后、设置之前：设置永远在最下面，谁也不能挤到它下面。
            self.addSubInterface(widget, self._plugin_icon(spec.icon), spec.title, NavigationItemPosition.TOP)
            self._plugin_nav_routes.append(route)
        current = self.stackedWidget.currentWidget()
        if current is not None:
            self.navigationInterface.setCurrentItem(current.objectName())

    def open_navigation(self, route: str) -> None:
        """打开某个页面：侧栏里没显示的页面（插件页面过多时）也照样切过去。"""
        for entry in self.page_entries():
            if entry.route == route and self.stackedWidget.indexOf(entry.widget) >= 0:
                self.switchTo(entry.widget)
                return

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
        # 启动后延迟一次自动清理（§6）：先让界面画出来，避免卡在开屏。
        QTimer.singleShot(1500, self.archive_page.auto_cleanup_startup)
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
        signalBus.pluginsChanged.connect(self._sync_plugin_pages)
        signalBus.focusItem.connect(self._on_focus_item)
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)

    def _on_focus_item(self, item_id: int) -> None:
        """从概览页的「最近导入」跳到数据管理页并选中对应的数据项。"""
        self.switchTo(self.manage_page)
        self.manage_page.focus_item(item_id)

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
            self.plugin_page,
            self.workbench_page,
            self.settings_page,
            *self._plugin_pages.values(),
        ):
            session = getattr(page, "session", None)
            if session is not None:
                session.close()
        self._watch_session.close()
        shutdown_cover_loader()
        super().closeEvent(event)


__all__ = ["MainWindow"]
