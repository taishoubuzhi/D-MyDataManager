"""自动关键词（规则）：插件入口。

干了三件事：

1. 注册「自动关键词」页——配规则（复用 `lib.autolabel` 的规则模型）+ 管关键词库；
2. 导入页加一个「按规则预填关键词」按钮；
3. 数据管理页加工具栏按钮与条目右键菜单，能给选中的条目挂关键词。

全程只走规则匹配，不调用模型；写库用 `app.sdk.items.add_keywords`。

与模型方案 `auto_keyword` 在清单里互指 `conflicts`（用户 m43110）：两者都能载入，但不能同时启用——
要先用规则挂一批、再换 `auto_keyword` 补其余条目，按顺序换着跑。
"""

from __future__ import annotations

from app.sdk import ExtensionPoint, Plugin, PluginError
from app.sdk import items as items_sdk

from . import runner, store

__all__ = [
    "AutoKeywordRulePlugin",
    "PAGE_ICON",
    "PAGE_KEY",
    "PAGE_ORDER",
    "PAGE_TITLE",
    "PLUGIN_ID",
]

PLUGIN_ID = "auto_keyword.rule"
PAGE_KEY = "auto_keyword_rule"
PAGE_TITLE = "自动关键词（规则）"
PAGE_ICON = "DICTIONARY"
PAGE_ORDER = 170


class KeywordApi:
    """页面用的一层门面：规则与关键词库都从这里进出，顺带缓存一次。"""

    def __init__(self) -> None:
        self._rules = None
        self._library: tuple[str, ...] | None = None

    def rules(self, *, reload: bool = False):
        if reload or self._rules is None:
            self._rules = store.load_rules()
        return self._rules

    def save_rules(self, rule_set) -> bool:
        ok = bool(store.save_rules(rule_set))
        if ok:
            self._rules = rule_set
        return ok

    def library(self) -> tuple[str, ...]:
        if self._library is None:
            self._library = store.load_library()
        return self._library

    def save_library(self, words) -> bool:
        ok = bool(store.save_library(words))
        if ok:
            self._library = store.normalize_keywords(words)
        return ok


class AutoKeywordRulePlugin(Plugin):
    """插件本体：注册页面与三个入口。"""

    def setup(self, ctx) -> None:
        self._ctx = ctx
        self._api = KeywordApi()
        self._register_page(ctx)
        ctx.contribute(
            ExtensionPoint.IMPORT_ACTION,
            {
                "text": "按规则预填关键词",
                "callback": self._on_import,
                "icon": "DICTIONARY",
                "tip": "按规则给待导入的文件算关键词，预填进导入页（不写库，导入还是你说了算）",
            },
            key="auto_keyword_rule.import",
            description="按规则把关键词预填到导入页",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {
                "text": "按规则挂关键词",
                "callback": self._on_manage,
                "icon": "DICTIONARY",
                "tip": "给选中的条目按规则挂关键词（不调用模型）",
            },
            key="auto_keyword_rule.manage",
            description="给选中的条目按规则挂关键词",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {
                "text": "按规则挂关键词",
                "callback": self._on_item,
                "icon": "DICTIONARY",
            },
            key="auto_keyword_rule.item",
            description="给这一条按规则挂关键词",
        )

    def _register_page(self, ctx) -> None:
        try:
            from .ui.page import AutoKeywordRulePage
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，自动关键词页未注册：{}", exc)
            return
        ctx.add_page(
            PAGE_KEY,
            PAGE_TITLE,
            lambda: AutoKeywordRulePage(ctx, self._api),
            icon=PAGE_ICON,
            order=PAGE_ORDER,
        )

    # ---- 三个入口 ----------------------------------------------------------

    def _on_import(self, import_ctx) -> None:
        paths = import_ctx.recent_paths()
        if not paths:
            import_ctx.toast("还没有待导入的文件")
            return
        words = runner.keywords_for_paths(paths, self._api.rules())
        if not words:
            import_ctx.toast(f"{len(paths)} 个文件没有命中规则")
            return
        text = "、".join(words)
        added = import_ctx.apply_keywords(words)
        if added:
            import_ctx.toast(f"已按规则预填 {added} 个关键词：{text}")
        else:
            import_ctx.toast(f"这些关键词已经填好了：{text}")

    def _on_manage(self, selection=None) -> None:
        refs = tuple(getattr(selection, "items", ()) or ())
        if not refs:
            self._toast("先选条目", "在列表里选几行，再点「按规则挂关键词」。")
            return
        report = runner.run_keywords(
            runner.plan_keywords(refs, self._api.rules(), reader=items_sdk.read_text),
            api=items_sdk,
        )
        self._refresh(selection)
        items_sdk.notify_changed()
        self._report(report)

    def _on_item(self, item) -> None:
        item_id = getattr(item, "id", None)
        if item_id is None:
            return
        try:
            ref = items_sdk.get_item(int(item_id))
        except Exception as exc:  # noqa: BLE001 - 取不到就当没这条
            self._ctx.log.warning("读取条目失败：{}", exc)
            ref = None
        if ref is None:
            self._toast("找不到这个条目", "列表可能刚刷新过，重试一次。")
            return
        report = runner.run_keywords(
            runner.plan_keywords([ref], self._api.rules(), reader=items_sdk.read_text),
            api=items_sdk,
        )
        items_sdk.notify_changed()
        self._report(report)

    # ---- 小工具 ------------------------------------------------------------

    def _refresh(self, selection) -> None:
        refresh = getattr(selection, "do_refresh", None)
        if callable(refresh):
            try:
                refresh()
                return
            except Exception:  # noqa: BLE001 - 刷新失败不影响结果
                self._ctx.log.exception("刷新数据管理页失败")
        items_sdk.notify_changed()

    def _report(self, report: runner.KeywordReport) -> None:
        text = runner.summary_text(report)
        if report.failed:
            self._toast("按规则挂关键词：有失败", "；".join(report.failed[:3]))
        elif not report.matched:
            self._toast("按规则挂关键词", f"没有条目命中规则（{text}）")
        elif not report.written:
            self._toast("按规则挂关键词", f"{text}；这些关键词条目上都有了，没有新增")
        else:
            self._toast("按规则挂关键词", text)

    def _toast(self, title: str, content: str = "") -> None:
        try:
            self._ctx.host.toast(title, content)
        except Exception:  # noqa: BLE001 - 没界面时退回日志
            self._ctx.log.info("{}：{}", title, content)
