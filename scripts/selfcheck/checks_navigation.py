"""L2 页面检查：导航只读（页面管理）。

对应 TODO 任务 2.7 的修正版：侧栏顺序固定 —— 内置页面按内置顺序装配（设置恒在最下面），
插件页面按载入顺序追加，追加不下的插件页面只出现在「页面管理」页里；「页面管理」本身是只读页，
不做任何布局改动。
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from .harness import Case, build_window, check, dispose_window, ensure_app

HOME_ROUTE = "homePage"
IMPORT_ROUTE = "importPage"
MANAGE_ROUTE = "managePage"
TAG_ROUTE = "tagPage"
WORKBENCH_ROUTE = "workbenchPage"
SETTINGS_ROUTE = "settingsPage"
#: 自检插件贡献的页面路由 / 页面 key 与插件 id。
PLUGIN_PAGE_ROUTE = "plugin.navpage"
PLUGIN_PAGE_KEY = "navpage"
PLUGIN_ID = "selfcheck.navpage"
#: 直接经 `AppUiApi` 登记的插件页面 key（最后一个用来验证「挤不进侧栏」）。
PAGE_KEYS = ("page1", "page2", "page3", "page4", "page5", "page6", "page7", "page8")


def _expect(problems: list[str], ok: bool, message: str) -> None:
    """记录一条不满足的判据（不抛异常，便于一次收集全部问题）。"""
    if not ok:
        problems.append(message)


def _click_row(row) -> None:
    """点页面管理里的一行：整行可点，行内还有「打开」按钮。"""
    row.resize(240, 44)
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)


def _layout_routes(layout, panel) -> list[str]:
    """布局里按显示顺序排出的导航项路由名。

    移除侧栏项时旧控件是 `deleteLater()` 回收的，在没有事件循环的检查里还会挂在
    布局上；只认 `panel.items` 里当代表的那一个控件，据此滤掉待销毁的旧项。
    """
    routes: list[str] = []
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        route = widget.property("routeKey") if widget is not None else None
        if not route:
            continue
        item = panel.items.get(str(route))
        if item is not None and item.widget is widget:
            routes.append(str(route))
    return routes


def _write_page_plugin(folder: Path) -> Path:
    """写一个「贡献一个页面」的插件：验证插件页面随插件禁用消失。"""
    target = Path(folder) / PLUGIN_ID
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.json").write_text(
        json.dumps(
            {
                "id": PLUGIN_ID,
                "name": "自检导航插件",
                "version": "1.0.0",
                "api_version": ">=1.0 <2.0",
                "description": "自检用：往界面里加一个页面。",
                "author": "selfcheck",
                "entry": "plugin.py",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (target / "plugin.py").write_text(
        "\n".join(
            [
                '"""自检用插件：贡献一个页面。"""',
                "",
                "from __future__ import annotations",
                "",
                "from PyQt6.QtWidgets import QLabel",
                "",
                "from app.sdk import Plugin, PluginContext",
                "",
                "",
                "class NavPagePlugin(Plugin):",
                "    def setup(self, ctx: PluginContext) -> None:",
                "        self._ctx = ctx",
                '        ctx.add_page("navpage", "自检页面", self._create_page, icon="APPLICATION")',
                "",
                "    def _create_page(self):",
                '        return QLabel("自检页面", None)',
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return target


@check("navigation_workbench", "pages")
def navigation_workbench(case: Case) -> None:
    """侧栏只读：内置固定顺序、设置恒底部、插件页追加、溢出的只在页面管理里打开。"""
    from PyQt6.QtWidgets import QWidget

    from app.core.runtime import paths
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service
    from app.ui.framework.sections import PageHeader
    from app.ui.main_window import BUILTIN_PAGES, PLUGIN_SIDEBAR_LIMIT

    api = AppUiApi()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    # 先换上一个干净的 app.ui：内置插件贡献的页面（如查看器配置页）不该干扰导航判据
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    _fixture, window = build_window(case)
    page = window.workbench_page
    problems: list[str] = []
    top_builtin = [getattr(window, attr).objectName() for attr, _t, _i, bottom in BUILTIN_PAGES if not bottom]
    bottom_builtin = [getattr(window, attr).objectName() for attr, _t, _i, bottom in BUILTIN_PAGES if bottom]
    plugin_routes = [f"plugin.{key}" for key in PAGE_KEYS]
    try:
        # ① 默认侧栏：内置页按内置顺序，设置恒在底部，插件页面还没有
        _expect(
            problems,
            list(window.sidebar_routes()) == [*top_builtin, *bottom_builtin],
            f"侧栏应为「内置顺序 + 设置」，实际 {list(window.sidebar_routes())}",
        )
        panel = window.navigationInterface.panel
        _expect(
            problems,
            _layout_routes(panel.topLayout, panel) == top_builtin,
            f"侧栏上部应为内置顺序 {top_builtin}，实际 {_layout_routes(panel.topLayout, panel)}",
        )
        _expect(
            problems,
            _layout_routes(panel.bottomLayout, panel) == bottom_builtin,
            f"设置应固定在侧栏底部，实际 {_layout_routes(panel.bottomLayout, panel)}",
        )
        _expect(
            problems,
            _layout_routes(panel.scrollLayout, panel) == [],
            f"侧栏不应再使用可滚动区，实际 {_layout_routes(panel.scrollLayout, panel)}",
        )
        _expect(
            problems,
            top_builtin and top_builtin[-1] == WORKBENCH_ROUTE,
            f"「页面管理」应排在设置页前面，实际 {top_builtin}",
        )
        entries = {entry.route: entry for entry in window.page_entries()}
        _expect(
            problems,
            list(entries) == [*top_builtin, *bottom_builtin],
            f"页面清单应等于内置页，实际 {list(entries)}",
        )
        _expect(
            problems,
            entries[SETTINGS_ROUTE].bottom and entries[SETTINGS_ROUTE].in_sidebar,
            "设置页应固定在侧栏底部且不可动",
        )

        # ② 「页面管理」是只读页：每页一行、只有「打开」，没有固定 / 排序控件
        _expect(
            problems,
            len(page.findChildren(PageHeader)) == 1,
            f"标题区只应有一个（ScrollPage 已建好，别再 add_header），实际 {len(page.findChildren(PageHeader))}",
        )
        _expect(problems, len(page.rows) == len(entries), f"每个页面应有一行，实际 {len(page.rows)} 行")
        for route in (HOME_ROUTE, TAG_ROUTE, SETTINGS_ROUTE):
            row = page.row_for(route)
            _expect(problems, row is not None, f"页面管理页缺少 {route} 的行")
            if row is None:
                continue
            _expect(
                problems,
                not hasattr(row, "pin_box") and not hasattr(row, "up_button") and not hasattr(row, "down_button"),
                f"{route} 行不该再有固定 / 排序控件",
            )
            _expect(problems, getattr(row, "open_button", None) is not None, f"{route} 行应有「打开」按钮")
        _expect(
            problems,
            not hasattr(page, "reset_button"),
            "页面管理页不该再有「恢复默认」（侧栏不可改）",
        )
        import_row = page.row_for(IMPORT_ROUTE)
        if import_row is not None:
            _click_row(import_row)
        _expect(
            problems,
            window.stackedWidget.currentWidget() is window.import_page,
            "点「导入」这一行应切到该页面",
        )

        # ②b 整行可点：行内没有按钮，点行的空白处也要切页
        home_row = page.row_for(HOME_ROUTE)
        if home_row is not None:
            _click_row(home_row)
        _expect(
            problems,
            window.stackedWidget.currentWidget() is window.home_page,
            "点行的空白处也应切到该页面（整行可点）",
        )

        # ③ 插件页面按名额显示；超出名额的只出现在页面管理里
        plugin_service.bootstrap(APP_UI_EXTENSION, api)
        for key in PAGE_KEYS:
            api.add_page(key, f"自检页面 {key}", lambda: QWidget(window), icon="APPLICATION", plugin_id="selfcheck.plugin")
        window._sync_plugin_pages()
        page.refresh()
        shown = list(window.sidebar_routes())
        _expect(
            problems,
            shown == [*top_builtin, *plugin_routes[:PLUGIN_SIDEBAR_LIMIT], *bottom_builtin],
            f"插件页面应按载入顺序追加在设置页之前，实际 {shown}",
        )
        _expect(
            problems,
            _layout_routes(panel.bottomLayout, panel) == bottom_builtin,
            "插件页面不该挤掉底部的设置页",
        )
        entries = {entry.route: entry for entry in window.page_entries()}
        _expect(
            problems,
            [route for route in plugin_routes if route in entries] == plugin_routes,
            f"插件页面都应出现在页面清单里，实际 {list(entries)}",
        )
        _expect(
            problems,
            entries[plugin_routes[0]].in_sidebar and not entries[plugin_routes[-1]].in_sidebar,
            "超出名额的插件页面应标为「只在页面管理里打开」",
        )
        overflow_row = page.row_for(plugin_routes[-1])
        _expect(problems, overflow_row is not None, "溢出的插件页面也应在页面管理里有一行")
        if overflow_row is not None:
            _click_row(overflow_row)
        _expect(
            problems,
            window.stackedWidget.currentWidget() is not None
            and window.stackedWidget.currentWidget().objectName() == plugin_routes[-1],
            "溢出的插件页面也应能打开",
        )
        first_row = page.row_for(plugin_routes[0])
        _expect(problems, first_row is not None, "第一个插件页面应在页面管理里有一行")
        _expect(
            problems,
            shown.index(plugin_routes[0]) > shown.index(top_builtin[-1]),
            f"插件页面应排在全部内置页面之后，实际 {shown}",
        )

        # ④ 页面登记被撤销：侧栏与清单都回到内置页
        widgets = [window._plugin_pages[key] for key in PAGE_KEYS if key in window._plugin_pages]
        api.clear()
        window._sync_plugin_pages()
        page.refresh()
        _expect(
            problems,
            list(window.sidebar_routes()) == [*top_builtin, *bottom_builtin],
            f"撤销登记后侧栏应回到内置顺序，实际 {list(window.sidebar_routes())}",
        )
        _expect(
            problems,
            [entry.route for entry in window.page_entries()] == [*top_builtin, *bottom_builtin],
            "撤销登记后不该残留插件页面",
        )
        _expect(
            problems,
            all(window.stackedWidget.indexOf(widget) < 0 for widget in widgets),
            "撤销登记后页面控件应从堆叠区卸载",
        )

        # ⑤ 插件贡献的真实页面：出现，并在插件禁用后消失
        _write_page_plugin(paths.PLUGIN_DIR)
        plugin_service.load()
        window._sync_plugin_pages()
        page.refresh()
        _expect(
            problems,
            PLUGIN_PAGE_KEY in window.plugin_pages(),
            f"插件页面应已装配，实际 {window.plugin_pages()}",
        )
        _expect(
            problems,
            PLUGIN_PAGE_ROUTE in window.sidebar_routes(),
            f"插件页面应出现在侧栏里，实际 {list(window.sidebar_routes())}",
        )
        _expect(problems, page.row_for(PLUGIN_PAGE_ROUTE) is not None, "插件页面应在页面管理里有一行")
        plugin_service.set_enabled(PLUGIN_ID, False)
        plugin_service.load()
        window._sync_plugin_pages()
        page.refresh()
        _expect(
            problems,
            PLUGIN_PAGE_KEY not in window.plugin_pages(),
            f"插件禁用后不该残留它的页面，实际 {window.plugin_pages()}",
        )
        _expect(
            problems,
            PLUGIN_PAGE_ROUTE not in window.sidebar_routes(),
            "插件禁用后侧栏项应消失",
        )
        routes = {entry.route for entry in window.page_entries()}
        _expect(
            problems,
            PLUGIN_PAGE_ROUTE not in routes and routes >= {*top_builtin, *bottom_builtin},
            f"插件页面消失后内置页清单应保持不变，实际 {sorted(routes)}",
        )
    finally:
        api.clear()
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else api)
        dispose_window(window)
    assert not problems, "导航工作台检查未通过：" + "；".join(problems)


@check("plugin_page_stack_alignment", "pages")
def plugin_page_stack_alignment(case: Case) -> None:
    """插件页面在堆叠区只能进一次：摘掉再装回来之后，动画栈记录仍与控件索引一一对应。

    qfluentwidgets 的动画栈（`aniInfos`）在重复 add 同一个控件时只把控件挪到末尾、
    不删旧记录，索引一旦错位，删页面时 `pop` 会命中错位项，留下指向已删除控件的记录，
    点侧栏切页就会 `RuntimeError: wrapped C/C++ object … has been deleted`。
    """
    from PyQt6.QtWidgets import QLabel

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service

    def factory():
        return QLabel("插件页面", None)

    plugin_id = "selfcheck.stackpage"
    api = AppUiApi()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    window = None
    problems: list[str] = []
    try:
        api.add_page("stackpage1", "堆叠页一", factory, plugin_id=plugin_id)
        api.add_page("stackpage2", "堆叠页二", factory, plugin_id=plugin_id)
        _fixture, window = build_window(case)
        view = window.stackedWidget.view

        def check_aligned(stage: str) -> None:
            _expect(
                problems,
                len(view.aniInfos) == view.count(),
                f"{stage}：动画栈记录数应与页面数一致，实际 aniInfos={len(view.aniInfos)}、页面={view.count()}",
            )
            for index in range(min(view.count(), len(view.aniInfos))):
                _expect(
                    problems,
                    view.aniInfos[index].widget is view.widget(index),
                    f"{stage}：第 {index} 条动画记录与栈里的控件不是同一个（索引错位）",
                )

        check_aligned("首次装配")
        api.remove_page("stackpage1")
        window._sync_plugin_pages()
        check_aligned("摘掉一个插件页面后")
        api.add_page("stackpage1", "堆叠页一", factory, plugin_id=plugin_id)
        window._sync_plugin_pages()
        check_aligned("重新登记后")
        for widget in window._plugin_pages.values():
            window.switchTo(widget)  # 索引错位时这一句就抛 RuntimeError，页面永远打不开
        # 侧栏点击走的是 `clicked(bool)` 信号，PyQt 按回调的形参个数投递这个布尔量：
        # 回调必须写成 0 参（写成 `lambda page=widget: …` 就会把 True 当页码，switchTo 里
        # `indexOf(True)` 直接 TypeError）。这里按真实点击路径发一次信号来验。
        panel = window.navigationInterface.panel
        for key, page in window._plugin_pages.items():
            item = panel.items.get(page.objectName())
            nav = getattr(item, "widget", None) if item is not None else None
            _expect(problems, nav is not None, f"{key}：侧栏里找不到对应项，没法验证点击")
            if nav is None:
                continue
            nav.clicked.emit(True)
            _expect(
                problems,
                window.stackedWidget.currentWidget() is page,
                f"{key}：发侧栏点击信号后没切到该页面（onClick 回调可能被 clicked(bool) 的布尔量顶掉）",
            )
    finally:
        api.clear()
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else api)
        if window is not None:
            dispose_window(window)
    assert not problems, "插件页面堆叠检查未通过：" + "；".join(problems)


@check("workbench_lists_builtin_pages", "pages")
def workbench_lists_builtin_pages(case: Case) -> None:
    """「页面管理」启动时就列出全部内置页面：没有插件页面时也不能是空页。

    `PageBase.auto_refresh` 只连信号、不会立刻刷新，而插件又是在主窗口之前载入的，
    所以这里不借助自检基座的逐个刷新，直接构造主窗口看页面自己有没有填一次。
    """
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service
    from app.ui.main_window import BUILTIN_PAGES, MainWindow

    api = AppUiApi()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    ensure_app()
    window = MainWindow()
    problems: list[str] = []
    try:
        rows = window.workbench_page.rows
        want = [title for _attr, title, _icon, _bottom in BUILTIN_PAGES]
        titles = [row.title_text for row in rows]
        _expect(problems, titles == want, f"页面管理应列出全部内置页面 {want}，实际 {titles}")
        for attr, title, _icon, _bottom in BUILTIN_PAGES:
            row = window.workbench_page.row_for(getattr(window, attr).objectName())
            _expect(problems, row is not None, f"页面管理缺少「{title}」的行")
            if row is not None:
                _expect(problems, getattr(row, "open_button", None) is not None, f"「{title}」行应有「打开」按钮")
        if rows:
            first = window.workbench_page.row_for(getattr(window, "home_page").objectName())
            if first is not None:
                _click_row(first)
            _expect(
                problems,
                window.stackedWidget.currentWidget() is window.home_page,
                f"点「首页」这一行应切到首页，实际 {window.stackedWidget.currentWidget()}",
            )
    finally:
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else api)
        dispose_window(window)
    assert not problems, "页面管理页未通过：" + "；".join(problems)


__all__ = ["navigation_workbench", "workbench_lists_builtin_pages"]
