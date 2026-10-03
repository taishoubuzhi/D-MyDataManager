"""L2 页面结构检查：只断言骨架与规则，不碰业务行为。

页面骨架的「同一性」是这轮重写的核心诉求：九个页面必须都走 `PageBase` 的骨架、
标题区、正文边距与间距，并且页面代码里不出现内联样式、线程和写死的边距。
"""

from __future__ import annotations

import re

from .harness import PAGE_ATTRS, ROOT, Case, build_window, check, dispose_window, ensure_app, install_builtin_plugins

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


def _button_contents(button):
    """按钮的内容区：QSS 内边距会把它压到负宽，contentsRect() 看不出来。"""
    from PyQt6.QtWidgets import QStyle, QStyleOptionButton

    option = QStyleOptionButton()
    button.initStyleOption(option)
    return button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)


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


@check("plugin_action_buttons_visible", "pages")
def plugin_action_buttons_visible(case: Case) -> None:
    """插件详情的按钮栏按各按钮自己的文字宽度换行：标签不许被压窄截断。

    自适应流式布局会按最小宽度把一行均分，窄窗口下按钮被压到装不下文字；
    这里量真实几何：每个按钮不小于自己的 `sizeHint()`，按钮栏也装得下换行后的行。
    """
    install_builtin_plugins()
    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        page = window.plugin_page
        window.switchTo(page)
        app.processEvents()
        if not page.plugin_list.count():
            problems.append("插件列表为空，量不到详情按钮栏")
        else:
            page.plugin_list.setCurrentRow(0)
        app.processEvents()
        host = page.actions_host
        if not host.isVisible():
            problems.append("插件页不可见，量不到按钮栏几何")
        host.sync_height()
        app.processEvents()
        buttons = host.widgets()
        if len(buttons) < 7:
            problems.append(f"插件详情按钮栏应有 7 个按钮，实际 {len(buttons)} 个")
        if host.height() <= 0:
            problems.append("插件详情按钮栏高度为 0：按钮没有排布")
        for button in buttons:
            need = button.sizeHint().width()
            if button.width() < need:
                problems.append(f"按钮「{button.text()}」宽 {button.width()}，装不下文字（需要 {need}）：标签会被截断")
        bottom = max((button.geometry().y() + button.geometry().height() for button in buttons), default=0)
        if bottom > host.height():
            problems.append(f"按钮栏高 {host.height()}，装不下换行后的按钮（需要 {bottom}）")
        assert not problems, "插件详情按钮栏未通过：" + "；".join(problems)
    finally:
        dispose_window(window)

# --------------------------------------------------------------------- 按钮与布局偏好
def _icon_push_button_calls() -> list[str]:
    """静态找出「图标 + 文本」却仍在用 qfluentwidgets 按钮的地方（含插件与弹窗）。"""
    import ast

    found: list[str] = []
    for base in (ROOT / "src", ROOT / "plugins"):
        for path in sorted(base.rglob("*.py")):
            if path.name == "buttons.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name not in ("PushButton", "PrimaryPushButton"):
                    continue
                first = node.args[0]
                is_icon = isinstance(first, ast.Attribute) or (
                    isinstance(first, ast.Name) and "icon" in first.id.lower()
                )
                if is_icon:
                    where = path.relative_to(ROOT).as_posix()
                    found.append(f"{where}:{node.lineno}")
    return found


@check("icon_text_buttons", "pages")
def icon_text_buttons(case: Case) -> None:
    """图标 + 文本的按钮全站统一走 IconTextButton：静态守来源，运行时验「简化显示」。"""
    install_builtin_plugins()
    app = ensure_app()
    from app.core.config import config
    from app.ui.framework import IconTextButton, IconTextPrimaryButton
    from app.ui.framework.buttons import SQUARE_PADDING

    problems = [
        f"{where} 仍在用 qfluentwidgets 按钮承载图标 + 文本，请改用 IconTextButton"
        for where in _icon_push_button_calls()
    ]
    _fixture, window = build_window(case, show=True)
    try:
        buttons = window.findChildren(IconTextButton) + window.findChildren(IconTextPrimaryButton)
        if len(buttons) < 20:
            problems.append(f"主窗口里只有 {len(buttons)} 个图标 + 文本按钮，按钮改造不完整")
        if not [button for button in buttons if not button.icon().isNull()]:
            problems.append("没有带图标的 IconTextButton，量不到简化显示")
        config.set(config.simpleDisplay, "full")
        app.processEvents()
        for button in buttons:
            if button.icon().isNull():
                if not button.text():
                    problems.append(f"没有图标的按钮「{button.full_text}」在简化显示下变成了空按钮")
                continue
            if button.text():
                problems.append(f"简化显示下按钮仍然显示文字：{button.text()!r}")
            if not button.toolTip():
                problems.append(f"简化显示下按钮「{button.full_text}」没有提示文字")
            contents = _button_contents(button)
            need = button.iconSize().width()
            if contents.width() < need or contents.height() < need:
                problems.append(
                    f"简化显示下按钮「{button.full_text}」的内容区 "
                    f"{contents.width()}x{contents.height()} 装不下 {need}px 的图标"
                )
        config.set(config.simpleDisplay, "none")
        app.processEvents()
        for button in buttons:
            if button.text() != button.full_text:
                problems.append(f"关掉简化显示后文字没有还原：{button.text()!r} != {button.full_text!r}")
            if button.icon().isNull() or not button.full_text:
                continue
            # 隐藏页 / 布局没激活的按钮不会自己重排：退出简化显示时必须把尺寸放回去，
            # 否则图标与文字挤在方形按钮里重叠（窗口还没显示该页时也要能抓到）。
            if button.width() <= button.iconSize().width() + 2 * SQUARE_PADDING:
                problems.append(
                    f"关掉简化显示后「{button.full_text}」还是方形 {button.width()}x{button.height()}，"
                    "图标与文字会重叠"
                )
        assert not problems, "图标 + 文本按钮未通过：" + "；".join(problems[:10])
    finally:
        config.set(config.simpleDisplay, "none")
        dispose_window(window)


@check("layout_preferences", "pages")
def layout_preferences(case: Case) -> None:
    """布局偏好写进配置：每页条数、两栏显隐、筛选分组折叠、分类栏默认收起、页码可见。"""
    from app.core.config import config
    from app.ui.components.category_tree import CategoryTree
    from app.ui.components.pager import DEFAULT_PAGE_SIZE, normalize_page_size

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        page = window.manage_page
        window.switchTo(page)
        app.processEvents()

        pager = page.pager
        if pager.page_size != normalize_page_size(config.pageSize.value):
            problems.append(f"分页条每页 {pager.page_size} 条与配置 {config.pageSize.value} 不一致")
        if pager.page_prefix.text() != "第" or "/ 共" not in pager.page_label.text():
            problems.append(f"分页条看不出第几页：{pager.page_prefix.text()!r}{pager.page_label.text()!r}")
        if pager.page_box.minimumWidth() < pager.page_box.sizeHint().width():
            problems.append("页码框被压窄，页码数字会被挤掉")

        index = pager.size_box.findData(100)
        if index < 0:
            problems.append("每页条数选项里没有 100 条")
        else:
            pager.size_box.setCurrentIndex(index)
            app.processEvents()
            if pager.page_size != 100 or config.pageSize.value != 100:
                problems.append(f"切到 100 条后分页条/配置为 {pager.page_size}/{config.pageSize.value}")
            config.set(config.pageSize, 50)

        page.tree_toggle_button.setChecked(False)
        app.processEvents()
        if config.showCategoryPanel.value or page.tree_card.isVisible():
            problems.append("关掉分类栏后配置或界面没有跟着变")
        page.tree_toggle_button.setChecked(True)

        section = page.filter_panel.type_section
        if not section.collapsed:
            problems.append("筛选分组默认应该是折叠的")
        section.set_collapsed(False)
        app.processEvents()
        if list(config.expandedFilters.value) != ["type"]:
            problems.append(f"展开的分组没有写进配置：{config.expandedFilters.value}")
        config.set(config.expandedFilters, [])

        probe = CategoryTree()
        probe.set_nodes([])
        if probe.topLevelItemCount() and probe.topLevelItem(0).isExpanded():
            problems.append("分类栏默认展开，应该默认全部收起")
        if page.tree.topLevelItemCount() and page.tree.topLevelItem(0).isExpanded():
            problems.append("数据管理页的分类栏默认展开，应该默认全部收起")
        assert not problems, "布局偏好未通过：" + "；".join(problems)
    finally:
        config.set(config.pageSize, DEFAULT_PAGE_SIZE)
        config.set(config.showCategoryPanel, True)
        config.set(config.showFilterPanel, True)
        config.set(config.expandedFilters, [])
        dispose_window(window)


@check("hover_hints", "pages")
def hover_hints(case: Case) -> None:
    """悬停提示：等待时间跟配置走、卡片提示能被子控件继承、按钮不截断且简化显示变方形。"""
    from PyQt6.QtWidgets import QStyle
    from qfluentwidgets import CaptionLabel

    from app.core.config import config
    from app.ui.framework import IconTextButton
    from app.ui.framework.tooltips import HINT_COLUMNS, describe, display_width, hover_delay, install_tooltips, wrap_hint

    app = ensure_app()
    install_tooltips(app)
    problems: list[str] = []
    style = app.style()
    wake = QStyle.StyleHint.SH_ToolTip_WakeUpDelay
    asleep = QStyle.StyleHint.SH_ToolTip_FallAsleepDelay
    if style.styleHint(wake) != hover_delay():
        problems.append(f"提示等待时间 {style.styleHint(wake)} 与配置 {hover_delay()} 不一致")
    if style.styleHint(asleep) != 0:
        problems.append("提示应每次重新等待（SH_ToolTip_FallAsleepDelay 应返回 0）")
    config.set(config.tooltipDelay, 500)
    if style.styleHint(wake) != 500:
        problems.append(f"改配置后等待时间没有立即生效：{style.styleHint(wake)}")
    config.set(config.tooltipDelay, 2000)

    _fixture, window = build_window(case, show=True)
    try:
        home = window.home_page
        window.switchTo(home)
        app.processEvents()

        header = home.header
        if header._subtitle.isVisible():
            problems.append("页面副标题仍然常显，应该改成悬停提示")
        if header._title.toolTip().strip() != header._subtitle.text().strip():
            problems.append("页面标题没有带上副标题作为悬停提示")

        badge = header.hint_badge
        if not badge.isVisible() or not badge.toolTip().strip():
            problems.append("页面标题旁边没有问号标识，用户看不出哪里能悬停")
        elif badge.toolTip().strip() != header._title.toolTip().strip():
            problems.append("问号标识的提示与标题上的提示不一致")

        for number, line in enumerate(wrap_hint("这是一段很长的说明文字，用来验证提示框会折成矩形。" * 4).splitlines(), 1):
            if display_width(line) > HINT_COLUMNS:
                problems.append(f"提示第 {number} 行宽 {display_width(line)}，超过 {HINT_COLUMNS} 会拉成长条")

        card = home._total_card
        if not card.toolTip():
            problems.append("KPI 卡片没有悬停说明")
        else:
            inner = card.findChild(CaptionLabel)
            if inner is None or describe(inner) != card.toolTip():
                problems.append("KPI 卡片里的子控件没有继承卡片的悬停说明")

        buttons = [widget for widget in home.actions_host.widgets() if isinstance(widget, IconTextButton)]
        if not buttons:
            problems.append("快捷操作区没有按钮")
        for button in buttons:
            if button.width() < button.sizeHint().width():
                problems.append(f"快捷操作按钮「{button.full_text}」被压窄：{button.width()} < {button.sizeHint().width()}")

        config.set(config.simpleDisplay, "full")
        app.processEvents()
        for button in buttons:
            if button.icon().isNull():
                continue
            if button.width() != button.height() or button.text():
                problems.append(
                    f"简化显示下按钮「{button.full_text}」没有变成方形图标按钮："
                    f"{button.width()}x{button.height()} {button.text()!r}"
                )
            if not button.toolTip():
                problems.append(f"简化显示下按钮「{button.full_text}」没有悬停提示")
        config.set(config.simpleDisplay, "none")
        app.processEvents()
        assert not problems, "悬停提示未通过：" + "；".join(problems[:10])
    finally:
        config.set(config.simpleDisplay, "none")
        config.set(config.tooltipDelay, 2000)
        dispose_window(window)


@check("prose_moved_to_hints", "pages")
def prose_moved_to_hints(case: Case) -> None:
    """页面上铺开的整段说明改成悬停提示：说明控件不常显，文案挂到对应控件或标题上。"""
    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        manage = window.manage_page
        window.switchTo(manage)
        app.processEvents()
        if manage.category_hint.isVisible():
            problems.append("数据管理页的分类说明仍然铺在卡片上")
        if manage.tree.toolTip() != manage.category_hint.text():
            problems.append("分类树的悬停提示与说明文案不一致")
        if not manage.filter_card.toolTip():
            problems.append("筛选栏没有悬停说明")

        imp = window.import_page
        window.switchTo(imp)
        app.processEvents()
        if imp.user_hint.isVisible() or imp.category_hint.isVisible():
            problems.append("导入页的字段说明仍然铺在表单上")
        if imp.user_box.toolTip() != imp.user_hint.text():
            problems.append("导入用户下拉框的悬停提示与说明文案不一致")
        if imp.category_box.toolTip() != imp.category_hint.text():
            problems.append("导入分类下拉框的悬停提示与说明文案不一致")

        archive = window.archive_page
        window.switchTo(archive)
        app.processEvents()
        if archive.caption.isVisible():
            problems.append("存档页的整段说明仍然铺在页面上")
        if archive.header is None or "标记存档" not in archive.header._title.toolTip():
            problems.append("存档页说明没有挂到标题的悬停提示上")

        user = window.user_page
        window.switchTo(user)
        app.processEvents()
        if user.caption.isVisible():
            problems.append("用户页的整段说明仍然铺在页面上")
        if user.header is None or not user.header._title.toolTip().strip():
            problems.append("用户页说明没有挂到标题的悬停提示上")

        plugins = window.plugin_page
        window.switchTo(plugins)
        app.processEvents()
        if plugins.header is None or "重建查看器注册表" not in plugins.header._title.toolTip():
            problems.append("插件页说明没有挂到标题的悬停提示上")

        opened = window.open_with_page
        window.switchTo(opened)
        app.processEvents()
        if opened.header is None or "自定义程序" not in opened.header._title.toolTip():
            problems.append("打开方式页说明没有挂到标题的悬停提示上")

        assert not problems, "说明文字未改成悬停提示：" + "；".join(problems)
    finally:
        dispose_window(window)


@check("number_setting_cards", "pages")
def number_setting_cards(case: Case) -> None:
    """数字配置除滑块外还能用输入框精准设置：范围跟配置项一致、两边双向同步。"""
    from qfluentwidgets import SpinBox

    from app.ui.framework import NumberSettingCard

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    restore: list[tuple[object, int]] = []
    try:
        page = window.settings_page
        window.switchTo(page)
        app.processEvents()
        cards = page.findChildren(NumberSettingCard)
        if len(cards) < 8:
            problems.append(f"设置页只有 {len(cards)} 张数字配置卡，滑块旁边都该能直接输入")
        for card in cards:
            name = card.titleLabel.text()
            item = card.configItem
            spin = card.findChild(SpinBox)
            if spin is None:
                problems.append(f"「{name}」没有输入框")
                continue
            if card.valueLabel.isVisible():
                problems.append(f"「{name}」仍显示旧的只读数值标签")
            if (spin.minimum(), spin.maximum()) != tuple(item.range):
                problems.append(f"「{name}」输入框范围 {spin.minimum()}~{spin.maximum()} 与配置 {item.range} 不一致")
            restore.append((item, int(item.value)))
            if spin.value() != item.value:
                problems.append(f"「{name}」输入框初值 {spin.value()} 与配置 {item.value} 不一致")
            spin.setValue(spin.maximum())
            app.processEvents()
            if item.value != spin.maximum() or card.slider.value() != spin.maximum():
                problems.append(f"「{name}」输入框改成 {spin.maximum()} 后配置 {item.value}、滑块 {card.slider.value()} 没跟上")
            card.slider.setValue(spin.minimum())
            app.processEvents()
            if item.value != spin.minimum() or spin.value() != spin.minimum():
                problems.append(f"「{name}」滑块改回 {spin.minimum()} 后配置 {item.value}、输入框 {spin.value()} 没跟上")
        assert not problems, "数字配置输入框未通过：" + "；".join(problems[:10])
    finally:
        for item, value in restore:
            item.value = value
        dispose_window(window)


@check("plugin_display", "pages")
def plugin_display(case: Case) -> None:
    """插件页按插件协议的信息展示：列表两行文字 + 右侧状态徽章，详情用徽章与分栏而不是一长串文本。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QStyleOptionViewItem

    from app.ui.components import BADGES_ROLE, SUBTITLE_ROLE, TITLE_ROLE, PluginItemDelegate
    from app.ui.components.plugin_delegate import MIN_ROW_HEIGHT

    install_builtin_plugins()
    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        page = window.plugin_page
        window.switchTo(page)
        app.processEvents()
        listing = page.plugin_list
        delegate = listing.itemDelegate()
        if not isinstance(delegate, PluginItemDelegate):
            problems.append("插件列表没有用自绘代理，右侧画不出状态徽章")
        if listing.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            problems.append("插件列表还能横向滚动，状态徽章会被挤出可视区")
        if listing.count() == 0:
            problems.append("插件列表是空的，量不到展示效果")
        for row in range(listing.count()):
            item = listing.item(row)
            if not item.data(BADGES_ROLE):
                problems.append(f"第 {row + 1} 行没有状态徽章数据")
            if not item.data(TITLE_ROLE) or not item.data(SUBTITLE_ROLE):
                problems.append(f"第 {row + 1} 行没有两行文字数据：{item.text()!r}")
            if "内置" not in item.text() or "·" not in item.text():
                problems.append(f"第 {row + 1} 行的文字丢了既有信息：{item.text()!r}")
        if listing.count():
            row_height = listing.visualItemRect(listing.item(0)).height()
            if row_height < MIN_ROW_HEIGHT:
                problems.append(f"插件行高 {row_height} 装不下两行文字，至少要 {MIN_ROW_HEIGHT}")
            if isinstance(delegate, PluginItemDelegate):
                blank = QStyleOptionViewItem()
                blank.rect = listing.visualItemRect(listing.item(0))
                blank.widget = listing
                delegate.initStyleOption(blank, listing.model().index(0, 0))
                if blank.text:
                    problems.append("代理没有清掉基类文本，右侧徽章会被文字重新盖住")
                _plugin_rows_clear_of_badges(listing, problems)

        listing.setCurrentRow(0)
        app.processEvents()
        info = page._selected_info()
        if info is None:
            problems.append("选中插件后没有拿到插件信息")
        else:
            for badge, text, tone in (
                (page.kind_badge, info.kind_label, "plain"),
                (page.source_badge, info.source_label, "plain"),
                (page.state_badge, info.state_label, info.state_tone),
            ):
                if not badge.isVisible():
                    problems.append(f"详情徽章没有显示：{text}")
                elif badge.text() != text or badge.tone != tone:
                    problems.append(f"详情徽章「{badge.text()}/{badge.tone}」与插件信息「{text}/{tone}」不一致")
            if not page.detail_meta.text().startswith(info.id):
                problems.append(f"详情里的标识没有以插件 id 开头：{page.detail_meta.text()!r}")
            if "插件选项" not in page.detail_options.text():
                problems.append(f"详情里没有插件选项一节：{page.detail_options.text()!r}")
        assert not problems, "插件页展示未通过：" + "；".join(problems[:10])
    finally:
        dispose_window(window)

@check("hint_badges_on_titles", "pages")
def hint_badges_on_titles(case: Case) -> None:
    """问号标识只留在页面标题旁：卡片、表单行与列表都不再各挂一个。"""
    from app.ui.framework import HintBadge, PageHeader

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        badges = window.findChildren(HintBadge)
        headers = window.findChildren(PageHeader)
        if not headers:
            problems.append("整窗一个页面标题都没有，量不到问号标识")
        if len(badges) != len(headers):
            problems.append(f"问号标识 {len(badges)} 个、页面标题 {len(headers)} 个，应该只在标题旁边出现")
        for badge in badges:
            parent = badge.parentWidget()
            while parent is not None and not isinstance(parent, PageHeader):
                parent = parent.parentWidget()
            if parent is None:
                owner = badge.parentWidget()
                problems.append(f"问号标识挂在了非页面标题上：{type(owner).__name__}")
        for header in headers:
            if len(header.findChildren(HintBadge)) != 1:
                problems.append("有的页面标题旁边没有（或不止一个）问号标识")
        cards = (
            window.home_page._total_card,
            window.manage_page.filter_card,
            window.manage_page.category_hint.parentWidget(),
            window.import_page.user_hint.parentWidget(),
        )
        for card in cards:
            if card.findChild(HintBadge) is not None:
                problems.append(f"卡片 {type(card).__name__} 上还挂着问号标识，说明入口只该留在标题")
        assert not problems, "问号标识未收敛到页面标题：" + "；".join(problems[:10])
    finally:
        dispose_window(window)


@check("icon_text_labels", "pages")
def icon_text_labels(case: Case) -> None:
    """文本标签图标化：平时图标 + 文字，简化显示只留图标并把文字挪进悬停提示。"""
    from app.core.config import config
    from app.ui.framework import IconTextLabel
    from app.ui.framework.labels import ICON_LABEL_SIZE

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        manage = window.manage_page
        window.switchTo(manage)
        manage.filter_toggle_button.setChecked(True)
        app.processEvents()
        panel = manage.filter_panel
        if not panel.isVisible():
            problems.append("筛选栏没有显示，量不到图标化标签")
        labels = panel.findChildren(IconTextLabel)
        if len(labels) < 3:
            problems.append(f"筛选栏里只有 {len(labels)} 个图标化标签，分组标题应该都带图标")
        for label in labels:
            if label._icon_label.pixmap().isNull():
                problems.append(f"「{label.text()}」没有图标")
            if label.toolTip() != label.text():
                problems.append(f"「{label.text()}」的悬停提示与文字不一致：{label.toolTip()!r}")
            if label._icon_only:
                if label._text_label.isVisible():
                    problems.append(f"图标标签「{label.text()}」不该显示文字")
            elif not label._text_label.isVisible():
                problems.append(f"平时「{label.text()}」看不到文字")

        config.set(config.simpleDisplay, "full")
        app.processEvents()
        for label in labels:
            if label._text_label.isVisible():
                problems.append(f"简化显示下「{label.text()}」还占着文字")
            elif label.toolTip() != label.text():
                problems.append(f"简化显示下「{label.text()}」没有把文字挪进悬停提示：{label.toolTip()!r}")

        # 先回到平时（非简化）布局，量「图标贴左、文字紧跟图标」：简化显示下文字标签会被布局收起
        config.set(config.simpleDisplay, "none")
        app.processEvents()
        opened = window.open_with_page
        window.switchTo(opened)
        app.processEvents()
        others = opened.findChildren(IconTextLabel)
        if not others:
            problems.append("打开方式页的字段标签还没有图标化")
        _assert_label_alignment(list(labels) + list(others), problems)
        config.set(config.simpleDisplay, "full")
        app.processEvents()
        for label in others:
            if label._text_label.isVisible():
                problems.append(f"简化显示下「{label.text()}」还占着文字")
        assert not problems, "图标化标签未通过：" + "；".join(problems[:10])
    finally:
        config.set(config.simpleDisplay, "none")
        dispose_window(window)


@check("setting_change_toasts", "pages")
def setting_change_toasts(case: Case) -> None:
    """切换任何一项配置都会在右上角给出提示：不再有静默的开关与下拉框。"""
    from PyQt6.QtTest import QTest
    from qfluentwidgets import SwitchSettingCard

    from app.core.config import config
    from app.ui.framework import NumberSettingCard
    from app.ui.pages.settings_page import TOAST_DELAY_MS, ComboSettingCard

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    page = window.settings_page
    problems: list[str] = []
    recorded: list[str] = []
    queued: list[str] = []
    toasts: list[tuple[str, str]] = []
    values: dict[object, object] = {}
    indexes: dict[object, int] = {}
    try:
        window.switchTo(page)
        app.processEvents()
        page.toast_success = lambda title, content="": toasts.append((str(title), str(content)))  # type: ignore[method-assign]
        original_queue = page._queue_setting_toast

        def queue(card, detail: str) -> None:
            queued.append(f"{card.titleLabel.text()}·{detail}")
            original_queue(card, detail)

        page._queue_setting_toast = queue  # type: ignore[method-assign]

        switches = page.findChildren(SwitchSettingCard)
        combos = page.findChildren(ComboSettingCard)
        if not switches or not combos:
            problems.append(f"设置页量到 {len(switches)} 张开关卡、{len(combos)} 张下拉卡")

        def recheck(done: list[str], what: str) -> None:
            if not done:
                problems.append(f"切换{what}后右上角没有任何提示")

        for card in switches:
            values[card.configItem] = card.configItem.value
            recorded.clear()
            queued.clear()
            toasts.clear()
            card.switchButton.setChecked(not card.switchButton.isChecked())
            app.processEvents()
            recorded.extend(queued or [f"{title}·{content}" for title, content in toasts])
            recheck(recorded, f"开关「{card.titleLabel.text()}」")

        for card in combos:
            indexes[card] = card.combo.currentIndex()
            recorded.clear()
            queued.clear()
            toasts.clear()
            card.combo.setCurrentIndex(1 if card.combo.currentIndex() == 0 else 0)
            app.processEvents()
            recorded.extend(queued or [f"{title}·{content}" for title, content in toasts])
            recheck(recorded, f"下拉「{card.titleLabel.text()}」")

        number = page.findChildren(NumberSettingCard)[0]
        values[number.configItem] = number.configItem.value
        toasts.clear()
        start = number.slider.minimum()
        for step in range(start, min(start + 6, number.slider.maximum() + 1)):
            number.slider.setValue(step)
            app.processEvents()
        QTest.qWait(TOAST_DELAY_MS + 250)
        if len(toasts) != 1:
            problems.append(f"连续拖动滑块 6 次弹了 {len(toasts)} 条提示，应该合并成一条")
        elif not toasts[0][0]:
            problems.append("配置提示没有标题")
        assert not problems, "配置切换提示未通过：" + "；".join(problems[:10])
    finally:
        config.set(config.simpleDisplay, "none")
        for item, value in values.items():
            item.value = value
        for card, index in indexes.items():
            card.combo.setCurrentIndex(index)
        app.processEvents()
        dispose_window(window)
def _assert_label_alignment(labels, problems: list[str]) -> None:
    """图标化标签的内部对齐：图标贴左、固定大小，文字紧跟其后（宽容器里不会被推到中间）。"""
    from app.ui.framework.labels import ICON_LABEL_SIZE

    for label in labels:
        if label._icon is None:
            # 没有图标的标签（如「打开方式」这类分节说明）只有文字，本来就贴左
            continue
        icon_label = label._icon_label
        if (icon_label.width(), icon_label.height()) != (ICON_LABEL_SIZE, ICON_LABEL_SIZE):
            problems.append(
                f"「{label.text()}」的图标没有固定成 {ICON_LABEL_SIZE}px："
                f"{icon_label.width()}x{icon_label.height()}"
            )
        if icon_label.x() != 0:
            problems.append(f"「{label.text()}」的图标没有贴左：x={icon_label.x()}")
        if not label._icon_only and label._text_label.x() != ICON_LABEL_SIZE + 6:
            problems.append(
                f"「{label.text()}」的文字没有紧跟图标：x={label._text_label.x()}，"
                f"应为 {ICON_LABEL_SIZE + 6}"
            )


def _plugin_rows_clear_of_badges(listing, problems: list[str]) -> None:
    """渲染列表视口：文字只画到徽章左侧的留白处，不能压到状态胶囊下面。"""
    from PyQt6.QtGui import QColor, QFontMetrics, QPixmap

    from app.ui.components import BADGES_ROLE
    from app.ui.framework.badges import BADGE_MARGIN, badges_width

    app = ensure_app()
    app.processEvents()
    pixmap = QPixmap(listing.viewport().size())
    pixmap.fill(QColor("#ff00ff"))
    listing.viewport().render(pixmap)
    image = pixmap.toImage()
    width = listing.viewport().width()
    for row in range(min(3, listing.count())):
        item = listing.item(row)
        rect = listing.visualItemRect(item)
        badges = item.data(BADGES_ROLE) or ()
        reserve = badges_width(QFontMetrics(listing.font()), badges) + BADGE_MARGIN
        limit = width - 1 - reserve
        for x in range(limit + 1, min(image.width(), limit + BADGE_MARGIN + 1)):
            for y in range(max(0, rect.top()), min(image.height(), rect.bottom() + 1)):
                color = image.pixelColor(x, y)
                if (
                    abs(color.red() - color.green()) < 14
                    and abs(color.green() - color.blue()) < 14
                    and color.red() < 170
                ):
                    problems.append(f"第 {row + 1} 行的文字压到了右侧徽章（x={x}，留白只到 {limit}）")
                    break
            else:
                continue
            break


@check("user_card_info", "pages")
def user_card_info(case: Case) -> None:
    """用户卡片信息行：一长串摘要改成一行一条的图标说明，固定宽度下不会被裁。"""
    from app.ui.framework import IconTextLabel
    from app.ui.pages.user_page import CARD_WIDTH, card_info_lines, card_summary

    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        page = window.user_page
        window.resize(1280, 900)
        window.switchTo(page)
        page.refresh()
        app.processEvents()
        cards = page.cards()
        if not cards:
            problems.append("用户页没有卡片，量不到信息行")
        for card in cards:
            info = next(
                (item for item in page.service.list_users() if int(item.user.id) == int(card.user_id)),
                None,
            )
            if info is None:
                problems.append(f"卡片 {card.user_id} 找不到对应用户")
                continue
            lines = card_info_lines(info)
            texts = [text for _icon, text in lines]
            if len(lines) != 3:
                problems.append(f"用户卡片信息行应为 3 项，实际 {len(lines)}：{texts}")
            shown = [label.text() for label in card.findChildren(IconTextLabel) if not label._icon_only]
            for text in texts:
                if text not in shown:
                    problems.append(f"卡片 {card.user_id} 上看不到信息行 {text!r}，实际 {shown}")
            for label in card.findChildren(IconTextLabel):
                if label._icon_label.pixmap().isNull():
                    problems.append(f"信息行「{label.text()}」没有图标")
                if label.sizeHint().width() > CARD_WIDTH:
                    problems.append(
                        f"信息行「{label.text()}」比卡片还宽：{label.sizeHint().width()} > {CARD_WIDTH}"
                    )
                elif label.width() < label.sizeHint().width():
                    problems.append(
                        f"信息行「{label.text()}」被裁：宽 {label.width()} < 需要 {label.sizeHint().width()}"
                    )
            expected = card_summary(
                item_count=info.item_count,
                category_count=info.category_count,
                created_at=info.user.created_at,
            )
            if card.toolTip() != expected:
                problems.append(f"卡片 {card.user_id} 的完整摘要没有放进悬停提示：{card.toolTip()!r}")
        assert not problems, "用户卡片信息行未通过：" + "；".join(problems[:10])
    finally:
        dispose_window(window)

@check("simple_modes", "pages")
def simple_modes(case: Case) -> None:
    """简化显示三挡位：不简化 / 默认（只简化不会混淆的图标）/ 完全简化。"""
    from app.core.config import config
    from app.ui.framework import IconTextButton, IconTextLabel, IconTextPrimaryButton, simple_mode
    from app.ui.pages.settings_page import ComboSettingCard

    problems: list[str] = []
    validator = config.simpleDisplay.validator
    if validator.correct(True) != "full" or validator.correct(False) != "none":
        problems.append("旧版布尔值没有换算成挡位（true → 完全简化、false → 不简化）")
    if validator.correct("没有这个挡位") != "default":
        problems.append("非法的简化挡位没有回到「默认」")

    install_builtin_plugins()
    app = ensure_app()
    _fixture, window = build_window(case, show=True)
    original = config.simpleDisplay.value
    try:
        page = window.plugin_page
        window.switchTo(page)
        app.processEvents()
        host = page.actions_host
        found = host.findChildren(IconTextButton) + host.findChildren(IconTextPrimaryButton)
        # IconTextPrimaryButton 继承 IconTextButton，findChildren 会把主色按钮算两遍，这里按对象身份去重
        buttons = list({id(button): button for button in found}.values())
        editors = [getattr(page, f"_{field}_button") for field in ("name", "description", "note")]
        if len(buttons) < 7:
            problems.append(f"插件页操作区只有 {len(buttons)} 个按钮，量不到挡位差异")
        if len({button.full_text for button in buttons}) != len(buttons):
            problems.append("插件页操作区的按钮文字有重复，量不准挡位差异")

        config.set(config.simpleDisplay, "none")
        app.processEvents()
        if simple_mode() != "none":
            problems.append(f"「不简化」挡位读回来是 {simple_mode()!r}")
        for button in buttons:
            if not button.text():
                problems.append(f"「不简化」挡位下按钮「{button.full_text}」没有文字")
        config.set(config.simpleDisplay, "full")
        app.processEvents()
        for button in buttons:
            if button.text():
                problems.append(f"「完全简化」挡位下按钮「{button.full_text}」还显示文字")

        config.set(config.simpleDisplay, "default")
        app.processEvents()
        if simple_mode() != "default":
            problems.append(f"「默认」挡位读回来是 {simple_mode()!r}")
        for button in editors:
            if not button.text():
                problems.append(f"「默认」挡位下共用同一枚图标的按钮「{button.full_text}」不该简化")
        for button in (page.toggle_button, page.options_button, page.reveal_button, page.delete_button):
            if button.text():
                problems.append(f"「默认」挡位下图标唯一的按钮「{button.full_text}」应该简化")

        config.set(config.simpleDisplay, True)
        if simple_mode() != "full":
            problems.append("通过配置写入旧版 true 没有换算成「完全简化」")

        combos = [
            card
            for card in window.settings_page.findChildren(ComboSettingCard)
            if card.titleLabel.text() == "简化显示"
        ]
        if len(combos) != 1:
            problems.append(f"设置页的「简化显示」不是三挡下拉（找到 {len(combos)} 张卡）")
        else:
            labels = [combos[0].combo.itemText(index) for index in range(combos[0].combo.count())]
            if labels != ["不简化", "默认", "完全简化"]:
                problems.append(f"「简化显示」下拉的选项是 {labels}")

        window.switchTo(window.user_page)
        config.set(config.simpleDisplay, "full")
        app.processEvents()
        keepers = [
            label
            for label in window.user_page.findChildren(IconTextLabel)
            if label._icon is not None and label._keep_text
        ]
        if len(keepers) < 3:
            problems.append(f"用户页只找到 {len(keepers)} 行「完全简化下也要保留文字」的信息标签")
        for label in keepers:
            if not label._text_label.isVisible():
                problems.append(f"用户页信息行「{label.text()}」在完全简化下丢了文字")
    finally:
        config.set(config.simpleDisplay, original)
        app.processEvents()
        dispose_window(window)
    assert not problems, "简化挡位未通过：" + "；".join(problems[:10])
