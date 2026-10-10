"""插件贡献在真实界面上的落地检查（界面扩展点契约见 src/app/sdk/points.py）。

被测对象是**本文件现写进隔离插件目录的探针插件**（`selfcheck.ui_probe`）：
仓库里不再自带界面扩展示例插件（那只是协议样板，删掉后干净检出也能跑这些检查）。

覆盖两件事：

1. 已接线的六个界面扩展点，贡献都能在真实窗口里看到并可用
   （概览卡片、设置卡片、工具栏按钮、右键菜单项、详情行、导入过滤器）；
2. 插件被禁用后它的界面贡献全部撤销，重新启用后恢复（贡献可枚举、可撤销）。
"""

from __future__ import annotations

from pathlib import Path

from .checks_plugins import _write_plugin
from .harness import Case, build_window, check, dispose_window, install_builtin_plugins

#: 探针插件 id；贡献文字与它在界面上的字样一致，断言直接引用这些常量。
PROBE_ID = "selfcheck.ui_probe"
KPI_TITLE = "探针计数"
KPI_SUB_TAIL = "本次会话导入"
SETTINGS_TITLE = "探针设置"
TOOLBAR_TEXT = "探针动作"
TOOLBAR_TIP = "探针插件贡献的工具栏按钮"
MENU_TEXT = "探针菜单项"
DETAIL_TITLE = "探针信息"
FILTER_NAME = "跳过 .tmp 文件"

# 探针源码：按扩展点契约各贡献一项、订阅要用到的三个事件。
# 单独成串是为了让「写夹具」与「读断言」在同一份文件里对齐，改一处就够。
_PROBE_SOURCE = '''"""自检探针插件：把界面扩展点各贡献一项，并订阅事件。

仓库里不再自带界面扩展示例插件（协议样板已删除），这个探针由
scripts/selfcheck/checks_contributions.py 现写进隔离插件目录，干净检出也能跑。
"""

from __future__ import annotations

from pathlib import Path

from qfluentwidgets import FluentIcon, SettingCard

from app.sdk import Events, ExtensionPoint, Plugin, PluginContext


def _accept(path: Path) -> bool:
    """导入过滤器：跳过 .tmp 临时文件。"""
    return path.suffix.lower() != ".tmp"


class UiProbePlugin(Plugin):
    """探针插件：贡献界面元素、订阅事件。"""

    def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._imported = 0
        self._theme = ""
        ctx.contribute(
            ExtensionPoint.HOME_KPI,
            {"title": "探针计数", "value": self._kpi_text, "sub": self._kpi_sub, "icon": "HEART"},
            key="probe.kpi",
            description="探针插件贡献的概览卡片",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {"text": "探针动作", "icon": "HEART", "tip": "探针插件贡献的工具栏按钮", "callback": self._on_toolbar},
            key="probe.toolbar",
            description="探针插件贡献的工具栏按钮",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {"text": "探针菜单项", "icon": "HEART", "callback": self._on_menu},
            key="probe.menu",
            description="探针插件贡献的右键菜单项",
        )
        ctx.contribute(
            ExtensionPoint.DETAIL_PANEL,
            {"title": "探针信息", "lines": self._detail_lines},
            key="probe.detail",
            description="探针插件贡献的详情行",
        )
        ctx.contribute(
            ExtensionPoint.IMPORT_FILTER,
            {"name": "跳过 .tmp 文件", "accept": _accept},
            key="probe.tmp",
            description="探针插件贡献的导入过滤器",
        )
        ctx.contribute(
            ExtensionPoint.SETTINGS_CARD,
            {"title": "探针设置", "factory": self._settings_card},
            key="probe.settings",
            description="探针插件贡献的设置卡片",
        )
        ctx.on(Events.ITEM_IMPORTED, self._on_imported)
        ctx.on(Events.PLUGIN_ENABLED, self._on_plugin_toggled)
        ctx.on(Events.PLUGIN_DISABLED, self._on_plugin_toggled)
        ctx.on(Events.THEME_CHANGED, self._on_theme_changed)

    # ---- 贡献的取值回调 --------------------------------------------------
    def _kpi_text(self) -> str:
        return str(self._imported)

    def _kpi_sub(self) -> str:
        theme = f" · 主题 {self._theme}" if self._theme else ""
        return f"探针插件 · 本次会话导入 {self._imported} 个{theme}"

    def _detail_lines(self, item) -> list[str]:
        return ["来自探针插件", f"文件名：{getattr(item, 'name', '') or '（未选中）'}"]

    def _settings_card(self, parent) -> SettingCard:
        return SettingCard(FluentIcon.HEART, "探针设置", "探针插件贡献的设置卡片", parent)

    # ---- 贡献的动作回调 --------------------------------------------------
    def _on_toolbar(self) -> None:
        self._ctx.host.toast("探针插件", "工具栏按钮来自 selfcheck.ui_probe")

    def _on_menu(self, item) -> None:
        self._ctx.host.toast("探针插件", f"右键菜单：{getattr(item, 'name', '') or '（未选中）'}")

    # ---- 事件订阅 --------------------------------------------------------
    def _on_imported(self, **payload) -> None:
        self._imported += 1
        self._ctx.log.info("探针插件收到导入事件：{}", payload.get("name", ""))

    def _on_plugin_toggled(self, **payload) -> None:
        self._ctx.log.info("探针插件收到插件开关事件：{}", payload)

    def _on_theme_changed(self, **payload) -> None:
        self._theme = str(payload.get("theme", "")) or self._theme
'''


def _install_probe() -> None:
    """把探针插件写进隔离插件目录并装好内置插件（两部分都在临时目录里）。"""
    from app.core.runtime import paths

    _write_plugin(
        Path(paths.PLUGIN_DIR),
        PROBE_ID,
        {
            "id": PROBE_ID,
            "name": "界面探针",
            "version": "1.0.0",
            "api_version": ">=1.0 <2.0",
            "description": "自检用的界面扩展点探针插件",
            "author": "D-MyDataManager",
            "enabled": False,  # 与正式界面插件一样默认不打扰，检查里显式启用
            "entry": "plugin.py",
        },
        {"plugin.py": _PROBE_SOURCE},
    )
    install_builtin_plugins()


def _kpi_card(home, problems: list[str]):
    """概览页上的插件卡片（只有探针在贡献，多于一或没有都算错）。"""
    cards = home.plugin_cards
    if len(cards) != 1:
        problems.append(f"概览页应有 1 张插件卡片，实际 {len(cards)}")
        return None
    return cards[0]


@check("plugin_ui_contributions", "pages")
def plugin_ui_contributions(case: Case) -> None:
    """六个界面扩展点的贡献在真实窗口里生效。"""
    from qfluentwidgets import CaptionLabel, SettingCard, SettingCardGroup

    from app.sdk import Events
    from app.services.plugin_service import plugin_service

    _install_probe()
    # 探针清单里是 enabled: false（默认不打扰正式界面），这里显式启用后再建窗口
    assert plugin_service.set_enabled(PROBE_ID, True), "启用探针插件应成功"
    plugin_service.load()
    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        home = window.home_page
        card = _kpi_card(home, problems)
        if card is not None:
            captions = [label.text() for label in card.findChildren(CaptionLabel)]
            if KPI_TITLE not in captions:
                problems.append(f"概览卡片标题应来自贡献的 title，实际 {captions}")
            if not card._value.text().isdigit():
                problems.append(f"探针计数的值应来自贡献回调，实际 {card._value.text()!r}")
            before = int(card._value.text()) if card._value.text().isdigit() else 0
            plugin_service.publish(Events.ITEM_IMPORTED, item_id="selfcheck", name="探针.txt")
            home.refresh()
            card = _kpi_card(home, problems)
            if card is not None:
                expected = before + 1
                if card._value.text() != str(expected):
                    problems.append(f"收到导入事件后探针计数应为 {expected}，实际 {card._value.text()!r}")
                if f"{KPI_SUB_TAIL} {expected} 个" not in card._sub.text():
                    problems.append(f"副标题应显示贡献回调的结果，实际 {card._sub.text()!r}")

        settings = window.settings_page
        if settings._plugin_group() is None:
            problems.append("设置页应因插件贡献而出现分组")
        groups = settings.findChildren(SettingCardGroup)
        group_titles = [group.titleLabel.text() for group in groups]
        if "插件" not in group_titles:
            problems.append(f"设置页应有标题为「插件」的分组，实际 {group_titles}")
        card_titles = [
            item.titleLabel.text() for group in groups for item in group.findChildren(SettingCard)
        ]
        if SETTINGS_TITLE not in card_titles:
            problems.append(f"设置卡片应来自贡献，实际 {card_titles}")

        settings._on_theme_changed("light")  # 页面广播 theme.changed，插件收到后写进卡片副标题
        home.refresh()
        card = _kpi_card(home, problems)
        if card is not None and "主题 light" not in card._sub.text():
            problems.append(f"收到主题事件后副标题应显示主题，实际 {card._sub.text()!r}")

        manage = window.manage_page
        buttons = manage._plugin_buttons
        if len(buttons) != 1:
            problems.append(f"数据管理工具栏应有 1 个插件按钮，实际 {len(buttons)}")
        else:
            button = buttons[0]
            if button.text() != TOOLBAR_TEXT:
                problems.append(f"工具栏按钮文字应来自贡献，实际 {button.text()!r}")
            if button.toolTip() != TOOLBAR_TIP:
                problems.append(f"工具栏按钮提示应来自贡献，实际 {button.toolTip()!r}")
            button.click()  # 回调里的 toast 在没有活动窗口时退化为日志
        assert manage._items, "数据管理页没有可见数据，无法验证插件菜单与详情行"
        item = manage._items[0]
        texts = [action.text() for action in manage._build_menu(item).actions()]
        if MENU_TEXT not in texts:
            problems.append(f"右键菜单应含插件贡献项，实际 {texts}")
        elif texts[0] != "直接打开" or texts.index(MENU_TEXT) != 1:
            # 「查看器」「编辑器」都是子菜单，不进 actions()；贡献项要排在它们后面、内置项前面
            problems.append(f"插件菜单项应紧跟「查看器」「编辑器」子菜单之后，实际 {texts}")
        if MENU_TEXT in texts:
            # 回归：贡献项的回调必须拿到被右键的数据项，不能拿到贡献对象本身
            from app.services.plugin_service import PluginHost

            toasts: list[tuple[str, str]] = []
            original_toast = PluginHost.toast
            PluginHost.toast = lambda self, title, content="": toasts.append((title, content))
            try:
                action = next(
                    action
                    for action in manage._build_menu(item).actions()
                    if action.text() == MENU_TEXT
                )
                action.trigger()
            finally:
                PluginHost.toast = original_toast
            if not toasts or str(item.name) not in toasts[-1][1]:
                problems.append(f"插件菜单回调应收到被点的条目 {item.name!r}，实际 {toasts}")
        lines = manage._plugin_detail_lines(item)
        if not any(line.startswith(DETAIL_TITLE) for line in lines):
            problems.append(f"详情行应含插件贡献，实际 {lines}")
        elif str(item.name) not in "；".join(lines):
            problems.append(f"详情行应带上当前条目信息，实际 {lines}")

        import_page = window.import_page
        workdir = Path(case.root) / "contrib_import"
        workdir.mkdir(parents=True, exist_ok=True)
        keep = workdir / "keep.txt"
        skip = workdir / "skip.tmp"
        keep.write_text("keep", encoding="utf-8")
        skip.write_text("skip", encoding="utf-8")
        import_page._directory = None
        import_page._files = [str(keep), str(skip)]
        sources = [Path(path).name for path, _subdir in import_page._collect_sources()]
        if sources != ["keep.txt"]:
            problems.append(f"导入过滤器应跳过 .tmp 文件，实际 {sources}")
    finally:
        dispose_window(window)
    assert not problems, "插件界面贡献检查未通过：" + "；".join(problems)


@check("plugin_contribution_lifecycle", "pages")
def plugin_contribution_lifecycle(case: Case) -> None:
    """插件禁用后界面贡献全部撤销，重新启用后随页面刷新恢复。"""
    from app.sdk import ExtensionPoint
    from app.services.plugin_service import plugin_service

    _install_probe()
    assert plugin_service.set_enabled(PROBE_ID, False), "禁用探针插件应成功"
    plugin_service.load()
    assert not plugin_service.point_items(ExtensionPoint.HOME_KPI), "禁用后概览扩展点不应还有贡献"

    _fixture, window = build_window(case)
    try:
        assert window.home_page.plugin_cards == [], "禁用后概览页不应有插件卡片"
        assert window.settings_page._plugin_group() is None, "禁用后设置页不应有插件分组"
        assert window.manage_page._plugin_buttons == [], "禁用后工具栏不应有插件按钮"

        assert plugin_service.set_enabled(PROBE_ID, True), "重新启用探针插件应成功"
        plugin_service.load()
        window.home_page.refresh()
        window.manage_page._sync_plugin_buttons()
        assert len(window.home_page.plugin_cards) == 1, "重新启用后刷新概览页应恢复插件卡片"
        assert len(window.manage_page._plugin_buttons) == 1, "重新启用后工具栏应恢复插件按钮"
        assert window.settings_page._plugin_group() is not None, "重新启用后设置页应能再取到插件分组"
    finally:
        dispose_window(window)
