"""任务 2：按规则轻量挂标签 —— 只用规则匹配，不调用模型。

规则本身存在共享库 `lib.autolabel` 里（任务 3 用同一份），本插件负责三件事：

* 一个「自动标签」配置页：管规则、手动跑一次（带进度与取消）；
* 导入页动作按钮：按规则把标签预填进导入表单（导入仍然由用户点按钮）；
* 数据管理页工具栏按钮与条目右键菜单：给选中/单个条目立刻挂标签。

与任务 3 的 `auto_tag` 在清单里互指 `conflicts`（用户 m42668）：两者都能载入，
但不能同时启用——同时启用时按载入顺序靠前者胜出，另一个保持禁用并提示与哪个插件冲突。
"""

from __future__ import annotations

from app.sdk import ExtensionPoint, Plugin, PluginError
from app.sdk import items as items_sdk

from dm_plugin.lib.autolabel.plugin import AUTOLABEL_EXTENSION

from . import runner

PLUGIN_ID = "auto_tag.rule"
PAGE_KEY = "auto_tag_rule"
PAGE_TITLE = "自动标签"
PAGE_ICON = "TAG"
PAGE_ORDER = 160

__all__ = [
    "AutoTagRulePlugin",
    "PAGE_ICON",
    "PAGE_KEY",
    "PAGE_ORDER",
    "PAGE_TITLE",
    "PLUGIN_ID",
]


class AutoTagRulePlugin(Plugin):
    """规则挂标签插件：页面 + 导入页预填 + 数据管理页两个入口。"""

    def setup(self, ctx) -> None:
        self._ctx = ctx
        self._api = ctx.require(AUTOLABEL_EXTENSION)
        self._register_page(ctx)
        ctx.contribute(
            ExtensionPoint.IMPORT_ACTION,
            {
                "text": "按规则预填标签",
                "callback": self._on_import,
                "icon": "TAG",
                "tip": "按规则匹配待导入的文件，把命中的标签填进标签框（不写库，导入还是你说了算）",
            },
            key="auto_tag_rule.import",
            description="按规则把标签预填到导入页",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {
                "text": "按规则挂标签",
                "callback": self._on_manage,
                "icon": "TAG",
                "tip": "给选中的条目按规则挂标签（不调用模型）",
            },
            key="auto_tag_rule.manage",
            description="给选中条目按规则挂标签",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {"text": "按规则挂标签", "callback": self._on_item, "icon": "TAG"},
            key="auto_tag_rule.item",
            description="给这一条按规则挂标签",
        )

    def _register_page(self, ctx) -> None:
        try:
            from .ui.page import AutoTagRulePage

            ctx.add_page(
                PAGE_KEY,
                PAGE_TITLE,
                lambda: AutoTagRulePage(ctx, self._api),
                icon=PAGE_ICON,
                order=PAGE_ORDER,
            )
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，自动标签页未注册：{}", exc)

    # ------------------------------------------------------------------ 入口

    def _rule_set(self):
        return self._api.rules()

    def _on_import(self, import_ctx) -> None:
        """导入页：只把标签预填进表单。"""
        paths = import_ctx.recent_paths()
        if not paths:
            import_ctx.toast("还没有待导入的文件")
            return
        names = runner.tags_for_paths(paths, self._rule_set())
        if not names:
            import_ctx.toast(f"{len(paths)} 个文件没有命中规则")
            return
        added = import_ctx.apply_tags(names)
        text = "、".join(names)
        if added:
            import_ctx.toast(f"已按规则预填 {added} 个标签：{text}")
        else:
            import_ctx.toast(f"这些标签已经填好了：{text}")

    def _on_manage(self, selection=None) -> None:
        """数据管理页工具栏：给选中的条目挂标签。"""
        refs = tuple(getattr(selection, "items", ()) or ())
        if not refs:
            self._toast("先选条目", "在列表里选几行，再点「按规则挂标签」。")
            return
        report = runner.run_rules(runner.plan_tags(refs, self._rule_set()), api=items_sdk)
        self._refresh(selection)
        items_sdk.notify_tags_changed()  # 整批挂完后标签页刷新一次
        self._report(report)

    def _on_item(self, item) -> None:
        """条目右键菜单：给这一条挂标签。"""
        item_id = getattr(item, "id", None)
        if item_id is None:
            return
        try:
            ref = items_sdk.get_item(int(item_id))
        except Exception as exc:  # 数据接口读不了就提示，不打断界面
            self._ctx.log.warning("读取条目失败：{}", exc)
            ref = None
        if ref is None:
            self._toast("找不到这个条目", "列表可能刚刷新过，重试一次。")
            return
        report = runner.run_rules(runner.plan_tags([ref], self._rule_set()), api=items_sdk)
        items_sdk.notify_changed()
        items_sdk.notify_tags_changed()
        self._report(report)

    # ------------------------------------------------------------------ 小工具

    def _refresh(self, selection) -> None:
        refresh = getattr(selection, "do_refresh", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                self._ctx.log.exception("刷新数据管理页失败")
        else:
            items_sdk.notify_changed()

    def _report(self, report) -> None:
        text = runner.summary_text(report)
        if report.failed:
            self._toast("按规则挂标签：有失败", "；".join(report.failed[:3]))
        elif not report.matched:
            self._toast("按规则挂标签", f"没有条目命中规则（{text}）")
        elif not report.written:
            self._toast("按规则挂标签", f"{text}；这些标签条目上都有了，没有新增")
        else:
            self._toast("按规则挂标签", text)

    def _toast(self, title: str, content: str = "") -> None:
        try:
            self._ctx.host.toast(title, content)
        except Exception:
            self._ctx.log.info("{}：{}", title, content)
