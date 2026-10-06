"""界面扩展示例：把八个界面扩展点各放一个例子，并订阅事件。

这个插件是协议样板，照抄改写即可；删掉整个目录就能移除全部示例贡献。
"""

from __future__ import annotations

from pathlib import Path

from qfluentwidgets import FluentIcon, SettingCard

from app.sdk import Events, ExtensionPoint, Plugin, PluginContext
from app.sdk.manifest import value_of


def _accept(path: Path) -> bool:
    """导入过滤器：跳过 .tmp 临时文件。"""
    return path.suffix.lower() != ".tmp"


class UiExtensionSamplePlugin(Plugin):
    """示例插件：贡献界面元素、订阅事件。"""

    def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._imported = 0
        self._events = 0
        self._theme = ""
        ctx.contribute(
            ExtensionPoint.HOME_KPI,
            {"title": "示例计数", "value": self._kpi_text, "sub": self._kpi_sub, "icon": "HEART"},
            key="example.kpi",
            description="示例插件贡献的概览卡片",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {"text": "示例动作", "icon": "HEART", "tip": "示例插件贡献的工具栏按钮", "callback": self._on_toolbar},
            key="example.toolbar",
            description="示例插件贡献的工具栏按钮",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {"text": "示例菜单项", "icon": "HEART", "callback": self._on_menu},
            key="example.menu",
            description="示例插件贡献的右键菜单项",
        )
        ctx.contribute(
            ExtensionPoint.DETAIL_PANEL,
            {"title": "示例信息", "lines": self._detail_lines},
            key="example.detail",
            description="示例插件贡献的详情行",
        )
        ctx.contribute(
            ExtensionPoint.IMPORT_FILTER,
            {"name": "跳过 .tmp 文件", "accept": _accept},
            key="example.tmp",
            description="示例插件贡献的导入过滤器",
        )
        ctx.contribute(
            ExtensionPoint.SETTINGS_CARD,
            {"title": "示例设置", "factory": self._settings_card},
            key="example.settings",
            description="示例插件贡献的设置卡片",
        )
        ctx.on(Events.ITEM_IMPORTED, self._on_imported)
        ctx.on(Events.ITEM_DELETED, self._on_state_changed)
        ctx.on(Events.USER_CHANGED, self._on_state_changed)
        ctx.on(Events.LIBRARY_CHANGED, self._on_state_changed)
        ctx.on(Events.PLUGIN_ENABLED, self._on_plugin_toggled)
        ctx.on(Events.PLUGIN_DISABLED, self._on_plugin_toggled)
        ctx.on(Events.THEME_CHANGED, self._on_theme_changed)

    # ---- 贡献的取值回调 --------------------------------------------------
    def _kpi_text(self) -> str:
        return str(self._imported)

    def _kpi_sub(self) -> str:
        theme = f" · 主题 {self._theme}" if self._theme else ""
        return f"示例插件 · 本次会话导入 {self._imported} 个 · 事件 {self._events} 次{theme}"

    def _detail_lines(self, item) -> list[str]:
        return ["来自示例插件", f"文件名：{getattr(item, 'name', '') or '（未选中）'}"]

    def _settings_card(self, parent) -> SettingCard:
        about = str(value_of(self._ctx.data("info", {}), "about", "")) or "示例插件贡献的设置卡片"
        return SettingCard(FluentIcon.HEART, "示例设置", about, parent)

    # ---- 贡献的动作回调 --------------------------------------------------
    def _on_toolbar(self) -> None:
        self._ctx.host.toast("示例插件", "工具栏按钮来自 example.ui_extension")

    def _on_menu(self, item) -> None:
        self._ctx.host.toast("示例插件", f"右键菜单：{getattr(item, 'name', '') or '（未选中）'}")

    # ---- 事件订阅 --------------------------------------------------------
    def _on_imported(self, **payload) -> None:
        self._imported += 1
        self._ctx.log.info("示例插件收到导入事件：{}", payload.get("name", ""))

    def _on_state_changed(self, **payload) -> None:
        self._events += 1
        self._ctx.log.info("示例插件收到状态事件：{}", payload)

    def _on_plugin_toggled(self, **payload) -> None:
        self._ctx.log.info("示例插件收到插件开关事件：{}", payload)

    def _on_theme_changed(self, **payload) -> None:
        self._theme = str(payload.get("theme", "")) or self._theme
