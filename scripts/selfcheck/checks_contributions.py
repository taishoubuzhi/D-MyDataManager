"""插件贡献在真实界面上的落地检查（界面扩展点契约见 src/app/sdk/points.py）。

覆盖两件事：

1. 已接线的六个界面扩展点，贡献都能在真实窗口里看到并可用
   （概览卡片、设置卡片、工具栏按钮、右键菜单项、详情行、导入过滤器）；
2. 插件被禁用后它的界面贡献全部撤销，重新启用后恢复（贡献可枚举、可撤销）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .harness import ROOT, Case, build_window, check, dispose_window, install_builtin_plugins

#: 界面扩展示例插件的 id（仓库自带，是这些检查的被测对象）。
SAMPLE_ID = "example.ui_extension"


def _sample_manifest() -> dict:
    """读示例插件的清单，确认被测对象还在仓库里。"""
    manifest = ROOT / "plugins" / SAMPLE_ID / "plugin.json"
    assert manifest.is_file(), f"缺少界面扩展示例插件：{manifest}"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data.get("id") == SAMPLE_ID, f"示例插件清单 id 不是 {SAMPLE_ID}：{data.get('id')!r}"
    return data


def _kpi_card(home, problems: list[str]):
    """概览页上的插件卡片（贡献只有一个，多于一或没有都算错）。"""
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

    _sample_manifest()
    install_builtin_plugins()
    # 示例插件清单里是 enabled: false（默认不打扰正式界面），这里显式启用后再建窗口
    assert plugin_service.set_enabled(SAMPLE_ID, True), "启用示例插件应成功"
    plugin_service.load()
    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        home = window.home_page
        card = _kpi_card(home, problems)
        if card is not None:
            captions = [label.text() for label in card.findChildren(CaptionLabel)]
            if "示例计数" not in captions:
                problems.append(f"概览卡片标题应来自贡献的 title，实际 {captions}")
            if not card._value.text().isdigit():
                problems.append(f"示例计数的值应来自贡献回调，实际 {card._value.text()!r}")
            before = int(card._value.text()) if card._value.text().isdigit() else 0
            plugin_service.publish(Events.ITEM_IMPORTED, item_id="selfcheck", name="示例.txt")
            home.refresh()
            card = _kpi_card(home, problems)
            if card is not None:
                expected = before + 1
                if card._value.text() != str(expected):
                    problems.append(f"收到导入事件后示例计数应为 {expected}，实际 {card._value.text()!r}")
                if f"本次会话导入 {expected} 个" not in card._sub.text():
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
        if "示例设置" not in card_titles:
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
            if button.text() != "示例动作":
                problems.append(f"工具栏按钮文字应来自贡献，实际 {button.text()!r}")
            if button.toolTip() != "示例插件贡献的工具栏按钮":
                problems.append(f"工具栏按钮提示应来自贡献，实际 {button.toolTip()!r}")
            button.click()  # 回调里的 toast 在没有活动窗口时退化为日志
        assert manage._items, "数据管理页没有可见数据，无法验证插件菜单与详情行"
        item = manage._items[0]
        texts = [action.text() for action in manage._build_menu(item).actions()]
        if "示例菜单项" not in texts:
            problems.append(f"右键菜单应含插件贡献项，实际 {texts}")
        elif texts[-1] != "示例菜单项":
            problems.append(f"插件菜单项应排在最后，实际 {texts}")
        lines = manage._plugin_detail_lines(item)
        if not any(line.startswith("示例信息") for line in lines):
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

    _sample_manifest()
    install_builtin_plugins()
    assert plugin_service.set_enabled(SAMPLE_ID, False), "禁用示例插件应成功"
    plugin_service.load()
    assert not plugin_service.point_items(ExtensionPoint.HOME_KPI), "禁用后概览扩展点不应还有贡献"

    _fixture, window = build_window(case)
    try:
        assert window.home_page.plugin_cards == [], "禁用后概览页不应有插件卡片"
        assert window.settings_page._plugin_group() is None, "禁用后设置页不应有插件分组"
        assert window.manage_page._plugin_buttons == [], "禁用后工具栏不应有插件按钮"

        assert plugin_service.set_enabled(SAMPLE_ID, True), "重新启用示例插件应成功"
        plugin_service.load()
        window.home_page.refresh()
        window.manage_page._sync_plugin_buttons()
        assert len(window.home_page.plugin_cards) == 1, "重新启用后刷新概览页应恢复插件卡片"
        assert len(window.manage_page._plugin_buttons) == 1, "重新启用后工具栏应恢复插件按钮"
        assert window.settings_page._plugin_group() is not None, "重新启用后设置页应能再取到插件分组"
    finally:
        dispose_window(window)
