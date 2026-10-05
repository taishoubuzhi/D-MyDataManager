"""SDK 扩展点与事件常量，以及扩展点贡献的数据结构。

程序在固定位置「留坑」（扩展点），插件用 `ctx.contribute(ExtensionPoint.XXX, 东西)` 往坑里放东西；
程序在状态变化时广播事件，插件用 `ctx.on(Events.XXX, 处理函数)` 订阅。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Contribution", "Events", "ExtensionPoint", "events"]


class ExtensionPoint:
    """程序开放的扩展点名称。

    插件用 ctx.contribute(point, value) 往坑里放东西；value 必须是对象，形状如下
    （程序侧统一由 app.ui.framework.contributions 取用，缺字段按默认值处理）：

    - VIEWER / EDITOR / PAGE：用 ctx.add_viewer(...) / ctx.add_editor(...) / ctx.add_page(...) 的字段；
    - MODEL：模型工具库用 ctx.provide(MODEL_EXTENSION, ...) 暴露调度接口（见 app.sdk.models）；
    - HOME_KPI：{"title": str, "value": 回调或字符串, "sub": str, "icon": str}；
    - SETTINGS_CARD：{"title": str, "factory": 回调(parent) -> QWidget}；
    - MANAGE_TOOLBAR：{"text": str, "callback": 回调(), "icon": str, "tip": str}
      （回调写成 0 个参数时按旧行为调用；写成 1 个参数时收到当前选中条目，
      见 `app.sdk.items.SelectionContext`）；
    - MANAGE_ITEM_MENU：{"text": str, "callback": 回调(item), "icon": str}；
    - DETAIL_PANEL：{"title": str, "lines": 回调(item) -> Iterable[str]}；
    - IMPORT_FILTER：{"name": str, "accept": 回调(path) -> bool}（返回 False 的文件不导入）；
    - IMPORT_ACTION：{"text": str, "callback": 回调(ctx), "icon": str, "tip": str}
      （数据导入页的动作按钮，ctx 见 `app.sdk.items.ImportContext`）；
    - IMPORT_HOOK / OPEN_RESOLVER：协议预留，程序侧尚未接线。
    """

    VIEWER = "app.viewer"
    EDITOR = "app.editor"
    MODEL = "app.model"
    PAGE = "app.ui.page"
    MANAGE_TOOLBAR = "app.ui.manage.toolbar"
    MANAGE_ITEM_MENU = "app.ui.manage.item_menu"
    DETAIL_PANEL = "app.ui.detail.panel"
    IMPORT_FILTER = "app.ui.import.filter"
    IMPORT_ACTION = "app.ui.import.action"
    HOME_KPI = "app.ui.home.kpi"
    SETTINGS_CARD = "app.ui.settings.card"
    IMPORT_HOOK = "app.data.import.hook"
    OPEN_RESOLVER = "app.item.open.resolver"

    _LABELS = {
        VIEWER: "查看器",
        EDITOR: "编辑器",
        MODEL: "模型",
        PAGE: "页面",
        MANAGE_TOOLBAR: "数据管理工具栏",
        MANAGE_ITEM_MENU: "条目菜单",
        DETAIL_PANEL: "详情面板",
        IMPORT_FILTER: "导入筛选",
        IMPORT_ACTION: "导入页功能",
        HOME_KPI: "概览卡片",
        SETTINGS_CARD: "设置卡片",
        IMPORT_HOOK: "导入钩子",
        OPEN_RESOLVER: "打开解析器",
    }

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(cls._LABELS)

    @classmethod
    def label(cls, point: str) -> str:
        text = str(point or "")
        return cls._LABELS.get(text, text or "未知扩展点")


class Events:
    """程序会广播的事件名称。"""

    LIBRARY_CHANGED = "library.changed"
    ITEM_IMPORTED = "item.imported"
    ITEM_DELETED = "item.deleted"
    ITEM_CHANGED = "item.changed"
    USER_CHANGED = "user.changed"
    THEME_CHANGED = "theme.changed"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"

    _LABELS = {
        LIBRARY_CHANGED: "库目录变化",
        ITEM_IMPORTED: "条目导入",
        ITEM_DELETED: "条目删除",
        ITEM_CHANGED: "条目变化",
        USER_CHANGED: "用户切换",
        THEME_CHANGED: "主题变化",
        PLUGIN_ENABLED: "插件启用",
        PLUGIN_DISABLED: "插件禁用",
    }

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(cls._LABELS)

    @classmethod
    def label(cls, event: str) -> str:
        text = str(event or "")
        return cls._LABELS.get(text, text or "未知事件")


# 方案里的写法是 `events`，这里保留一个小写别名方便对照文档
events = Events


@dataclass(frozen=True)
class Contribution:
    """一条扩展点贡献：谁（plugin_id）在哪个坑（point）放了什么（value）。"""

    point: str
    plugin_id: str
    value: object = None
    key: str = ""
    order: int = 100
    description: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def point_label(self) -> str:
        return ExtensionPoint.label(self.point)

    @property
    def name(self) -> str:
        return self.key or self.description or type(self.value).__name__

    @property
    def text(self) -> str:
        detail = self.description or self.key
        return f"{self.point_label}：{detail}" if detail else self.point_label
