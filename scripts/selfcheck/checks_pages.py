"""L2 页面结构检查：只断言骨架与规则，不碰业务行为。

页面骨架的「同一性」是这轮重写的核心诉求：九个页面必须都走 `PageBase` 的骨架、
标题区、正文边距与间距，并且页面代码里不出现内联样式、线程和写死的边距。
"""

from __future__ import annotations

import re

from .harness import PAGE_ATTRS, ROOT, Case, build_window, check, dispose_window, ensure_app

PAGES_DIR = ROOT / "src" / "app" / "ui" / "pages"

#: 页面源码里不允许出现的写法（样式与线程必须下沉到 framework / components）。
FORBIDDEN_SNIPPETS = (
    ('setStyleSheet("', "页面里不允许内联样式字符串，请把样式下沉到 framework/theme.py"),
    ('setStyleSheet(f"', "页面里不允许内联样式字符串，请把样式下沉到 framework/theme.py"),
    ("rgba(", "颜色字面量请放到 framework/theme.py"),
    ("QColor(", "页面里不允许直接构造颜色，请从 framework 取主题色"),
    ("QThread(", "线程请放到 app/ui/components/ 下，页面只连信号"),
    ("app.ui.common", "app.ui.common 已删除，改用 app.ui.framework"),
    ("app.ui.widgets", "app.ui.widgets 已迁移为 app.ui.components"),
)

_MARGINS_RE = re.compile(r"setContentsMargins\(([^)]*)\)")
_INT_RE = re.compile(r"-?\d+")


@check("page_shells", "pages")
def page_shells(case: Case) -> None:
    """九个页面都走统一骨架：基类、objectName、标题区、正文边距与间距。"""
    from app.ui.framework import PAGE_MARGINS, PAGE_SPACING, PageBase, ScrollPage
    from app.ui.main_window import MainWindow

    ensure_app()
    window = MainWindow()
    problems: list[str] = []
    try:
        for attr in PAGE_ATTRS:
            page = getattr(window, attr, None)
            if page is None:
                problems.append(f"主窗口缺少页面属性 {attr}")
                continue
            if not isinstance(page, PageBase):
                problems.append(f"{attr} 不是 PageBase 子类：{type(page).__name__}")
                continue
            if page.objectName() != page.page_name:
                problems.append(f"{attr} 的 objectName={page.objectName()!r} 与 page_name={page.page_name!r} 不一致")
            if not page.page_title:
                problems.append(f"{attr} 没有 page_title")
            header = page.header
            if header is None:
                problems.append(f"{attr} 没有标题区（应调用 add_header）")
            elif header._title.text() != page._page_title:
                problems.append(f"{attr} 标题文案 {header._title.text()!r} 与 {page._page_title!r} 不一致")
            margins = page.body.contentsMargins()
            actual = (margins.left(), margins.top(), margins.right(), margins.bottom())
            if actual != PAGE_MARGINS:
                problems.append(f"{attr} 正文边距 {actual} != PAGE_MARGINS {PAGE_MARGINS}")
            if page.body.spacing() != PAGE_SPACING:
                problems.append(f"{attr} 正文间距 {page.body.spacing()} != PAGE_SPACING {PAGE_SPACING}")
            if isinstance(page, ScrollPage) and page.host.objectName() != f"{page.page_name}Host":
                problems.append(f"{attr} 滚动容器 objectName={page.host.objectName()!r} 不符合约定")
        assert not problems, "页面骨架不一致：" + "；".join(problems)
    finally:
        dispose_window(window)


@check("page_navigation", "pages")
def page_navigation(case: Case) -> None:
    """九个页面都装配进导航栈，且每个页面在栈里唯一。"""
    from app.ui.main_window import MainWindow

    ensure_app()
    window = MainWindow()
    try:
        stack = window.stackedWidget
        names = [stack.widget(i).objectName() for i in range(stack.count())]
        problems = []
        for attr in PAGE_ATTRS:
            page = getattr(window, attr)
            if stack.indexOf(page) < 0:
                problems.append(f"{attr}（{page.objectName()}）没有装配进导航栈")
            if names.count(page.objectName()) != 1:
                problems.append(f"{attr} 在导航栈里出现 {names.count(page.objectName())} 次")
        assert not problems, "导航装配异常：" + "；".join(problems)
    finally:
        dispose_window(window)


@check("pages_style_guard", "pages")
def pages_style_guard(case: Case) -> None:
    """静态守卫：页面源码里没有内联样式、线程、写死边距与旧模块路径。"""
    problems: list[str] = []
    for path in sorted(PAGES_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for snippet, reason in FORBIDDEN_SNIPPETS:
            if snippet in text:
                problems.append(f"{rel} 出现 {snippet}：{reason}")
        for args in _MARGINS_RE.findall(text):
            if args.strip().startswith("*"):
                continue
            if any(int(value) != 0 for value in _INT_RE.findall(args)):
                problems.append(f"{rel} 写死了边距 setContentsMargins({args})，请用 framework 的 token")
    assert not problems, "页面样式规则被破坏：" + "；".join(problems)


# --------------------------------------------------------------------- 装配
def _holder(page):
    """页面正文宿主：滚动页取 host，普通页取自身。"""
    widget = page.widget() if hasattr(page, "widget") else None
    return widget if widget is not None else page


def _direct_widgets(layout) -> list:
    """布局里（含一层子布局）直接摆放的控件。"""
    from PyQt6.QtWidgets import QLayout

    found = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            found.append(widget)
            continue
        child = item.layout()
        if isinstance(child, QLayout):
            for sub in range(child.count()):
                sub_widget = child.itemAt(sub).widget()
                if sub_widget is not None:
                    found.append(sub_widget)
    return found


@check("style_uniformity", "pages")
def style_uniformity(case: Case) -> None:
    """页面尺寸统一：正文边距、无原生按钮、无写死强调色、面板卡片边距只用 token。"""
    from PyQt6.QtWidgets import QPushButton, QWidget
    from qfluentwidgets import CardWidget

    from app.ui.framework import DETAIL_MARGINS, PAGE_MARGINS, PAGE_SPACING, PANEL_MARGINS

    _fixture, window = build_window(case)
    problems: list[str] = []
    panel_margins = {tuple(PANEL_MARGINS), tuple(DETAIL_MARGINS)}
    legacy_accents = ("#0a84ff", "#0078d4", "rgba(10, 132, 255", "rgba(0, 120, 212")
    try:
        for attr in PAGE_ATTRS:
            page = getattr(window, attr)
            holder = _holder(page)
            box = page.body.contentsMargins()
            margins = (box.left(), box.top(), box.right(), box.bottom())
            if margins != tuple(PAGE_MARGINS) or page.body.spacing() != PAGE_SPACING:
                problems.append(
                    f"{attr} 正文边距/间距为 {margins}/{page.body.spacing()}，应为 {PAGE_MARGINS}/{PAGE_SPACING}"
                )
            for widget in holder.findChildren(QPushButton):
                # Fluent 的 PushButton 也是 QPushButton 子类，只有 Python 类名仍是 QPushButton 的才是原生按钮。
                if type(widget).__name__ == "QPushButton":
                    problems.append(f"{attr} 使用了原生 QPushButton：{widget.text()}")
            for widget in holder.findChildren(QWidget):
                stylesheet = widget.styleSheet()
                for legacy in legacy_accents:
                    if legacy in stylesheet:
                        problems.append(f"{attr} 的 {type(widget).__name__} 仍写死强调色 {legacy}")
            for card in _direct_widgets(page.body):
                if not isinstance(card, CardWidget) or card.layout() is None:
                    continue
                box = card.layout().contentsMargins()
                got = (box.left(), box.top(), box.right(), box.bottom())
                if got not in panel_margins:
                    problems.append(f"{attr} 的面板卡片边距为 {got}，应为 {PANEL_MARGINS} 或 {DETAIL_MARGINS}")
        assert not problems, "页面样式不统一：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("theme_background", "pages")
def theme_background(case: Case) -> None:
    """切到浅色后不允许还有「按旧调色板实绘深色」的控件（运行时切主题最容易漏）。"""
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QWidget
    from qfluentwidgets import Theme, isDarkTheme, setTheme

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    restore_theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
    restore_page = window.stackedWidget.currentWidget()
    setTheme(Theme.LIGHT)
    app.processEvents()
    try:
        for attr in PAGE_ATTRS:
            page = getattr(window, attr)
            window.switchTo(page)
            app.processEvents()
            seen: set[str] = set()
            for widget in page.findChildren(QWidget):
                if not widget.isVisible() or widget.width() * widget.height() < 400:
                    continue
                if not widget.autoFillBackground():
                    continue
                color = widget.palette().color(QPalette.ColorRole.Window)
                if color.lightness() >= 90:
                    continue
                label = f"{type(widget).__name__}({widget.objectName()})"
                if label in seen:
                    continue
                seen.add(label)
                problems.append(f"{attr} 的 {label} 仍按旧调色板实绘深色 {color.name()}")
        assert not problems, "主题背景异常：" + "；".join(problems[:12])
    finally:
        setTheme(restore_theme)
        app.processEvents()
        if restore_page is not None:
            window.switchTo(restore_page)
            app.processEvents()
        dispose_window(window)


@check("scroll_backgrounds", "pages")
def scroll_backgrounds(case: Case) -> None:
    """滚动区按 qfluentwidgets 的写法透明：只清视口会让滚动区自己实绘底色、换肤后留旧主题色。"""
    from PyQt6.QtWidgets import QScrollArea

    _fixture, window = build_window(case)
    problems: list[str] = []
    checked = 0
    try:
        for attr in PAGE_ATTRS:
            page = getattr(window, attr)
            areas = list(page.findChildren(QScrollArea))
            if isinstance(page, QScrollArea):
                areas.insert(0, page)
            for area in areas:
                checked += 1
                if "background: transparent" not in area.styleSheet():
                    problems.append(f"{attr} 的滚动区 {type(area).__name__} 没有透明规则，会按调色板实绘底色")
                host = area.widget()
                if host is not None and "background: transparent" not in host.styleSheet():
                    problems.append(f"{attr} 的滚动区 {type(area).__name__} 内层容器没有透明规则")
        assert checked >= 3, f"九页里只找到 {checked} 个滚动区，检查无意义"
        assert not problems, "滚动区底色未按上游写法透明：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("home_kpis", "pages")
def home_kpis(case: Case) -> None:
    """首页首屏就显示统计：KPI 卡片与 `overview()` 一致（不许只在信号触发后才刷新）。"""
    from app.repositories import ArchiveRepository
    from app.services import UserService, overview

    _fixture, window = build_window(case)
    try:
        page = window.home_page
        session = page.session
        stats = overview(session, user_id=UserService(session).current_id())
        assert stats["total"] >= 2, f"夹具数据没进统计（total={stats['total']}），检查无意义"
        cards = {
            "数据总量": (page._total_card, str(stats["total"])),
            "分类": (page._category_card, str(stats["categories"])),
            "标签数": (page._tag_card, str(stats["tags"])),
            "用户数": (page._user_card, str(len(UserService(session).list_users()))),
            "存档数": (page._archive_card, str(ArchiveRepository(session).count())),
        }
        problems = [
            f"首页「{title}」显示 {card._value.text()!r}，应为 {want!r}"
            for title, (card, want) in cards.items()
            if card._value.text() != want
        ]
        assert not problems, "首页统计不一致：" + "；".join(problems)
    finally:
        dispose_window(window)
