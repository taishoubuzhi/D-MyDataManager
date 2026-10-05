"""任务 3：自动标签（规则 + 模型）——页面里能切两套方案。

* 「自动标签」配置页：顶部切「规则 / 模型」，模型方案按数据类型对齐模型、
  整批交给模型工具库（`app.sdk.models.run_batch()`），带进度与取消；
* 导入页动作按钮：导入前还没有条目 id，只能按规则预填标签；
* 数据管理页工具栏按钮与条目右键菜单：先用规则立刻挂一批（同步、很快），
  想跑模型就照着提示去页面里跑。

与任务 2 的 `auto_tag.rule` 冲突（清单里的 `conflicts`）：两者都能载入，
但不能同时启用——同时启用时按载入顺序靠前者胜出，另一个保持禁用。
"""

from __future__ import annotations

from app.sdk import ExtensionPoint, Plugin, PluginError
from app.sdk import items as items_sdk

from dm_plugin.lib.autolabel.plugin import AUTOLABEL_EXTENSION

from . import runner

PLUGIN_ID = "auto_tag"
PAGE_KEY = "auto_tag"
PAGE_TITLE = "自动标签"
PAGE_ICON = "TAG"
PAGE_ORDER = 161

__all__ = [
    "AutoTagPlugin",
    "PAGE_ICON",
    "PAGE_KEY",
    "PAGE_ORDER",
    "PAGE_TITLE",
    "PLUGIN_ID",
]


class AutoTagPlugin(Plugin):
    """规则 + 模型两套自动标签方案：一个页面 + 导入页/管理页入口。"""

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
                "tip": "按规则匹配待导入的文件，把命中的标签填进标签框（导入前还没有条目，模型跑不了）",
            },
            key="auto_tag.import",
            description="按规则把标签预填到导入页",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {
                "text": "自动挂标签",
                "callback": self._on_manage,
                "icon": "TAG",
                "tip": "给选中的条目按规则立刻挂标签；要用模型方案，请到「自动标签」页跑（带进度与取消）",
            },
            key="auto_tag.manage",
            description="给选中条目挂标签",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {"text": "自动挂标签", "callback": self._on_item, "icon": "TAG"},
            key="auto_tag.item",
            description="给这一条挂标签",
        )

    def _register_page(self, ctx) -> None:
        try:
            from .ui.page import AutoTagPage

            ctx.add_page(
                PAGE_KEY,
                PAGE_TITLE,
                lambda: AutoTagPage(ctx, self._api),
                icon=PAGE_ICON,
                order=PAGE_ORDER,
            )
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，自动标签页未注册：{}", exc)

    # ------------------------------------------------------------------ 入口

    def _rule_set(self):
        return self._api.rules()

    def _on_import(self, import_ctx) -> None:
        """导入页：只把规则命中的标签预填进表单。"""
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
        """数据管理页工具栏：规则方案同步跑一遍，再提示模型方案在页面里跑。"""
        refs = tuple(getattr(selection, "items", ()) or ())
        if not refs:
            self._toast("先选条目", "在列表里选几行，再点「自动挂标签」。")
            return
        report = runner.run_labels(runner.plan_rule_only(refs, self._rule_set()), api=items_sdk)
        self._refresh(selection)
        self._report(report, extra="模型方案请到「自动标签」页跑（有进度和取消）。")

    def _on_item(self, item) -> None:
        """条目右键菜单：给这一条按规则挂标签。"""
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
        report = runner.run_labels(runner.plan_rule_only([ref], self._rule_set()), api=items_sdk)
        items_sdk.notify_changed()
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

    def _report(self, report, *, extra: str = "") -> None:
        text = runner.summary_text(report)
        if extra:
            text = f"{text}；{extra}"
        if report.failed:
            self._toast("自动挂标签：有失败", "；".join(report.failed[:3]))
        elif not report.matched:
            self._toast("自动挂标签", f"没有条目命中规则（{text}）")
        else:
            self._toast("自动挂标签", text)

    def _toast(self, title: str, content: str = "") -> None:
        try:
            self._ctx.host.toast(title, content)
        except Exception:
            self._ctx.log.info("{}：{}", title, content)
