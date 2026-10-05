"""任务 4：自动关键词——按数据类型对齐模型，整批生成关键词。

* 「自动关键词」配置页：对齐表（数据类型 → 关键词模型 + 对齐模型）、预定义方案
  一键使用、整批请求交给模型工具库（`app.sdk.models.run_batch()`），带进度与取消；
* 数据管理页工具栏按钮与条目右键菜单：关键词必须调模型，而这两个入口没有进度与
  取消的落点，所以只把用户送到页面（页面里有进度条与取消按钮）。

与任务 2/3 的两个插件都不互斥：关键词与标签是两套数据，可以同时在用。
"""

from __future__ import annotations

import queue
import threading

from app.sdk import ExtensionPoint, Plugin, PluginError

from dm_plugin.lib.autolabel.plugin import AUTOLABEL_EXTENSION

PLUGIN_ID = "auto_keyword"
PAGE_KEY = "auto_keyword"
PAGE_TITLE = "自动关键词"
PAGE_ICON = "DICTIONARY"
PAGE_ORDER = 162

__all__ = [
    "AutoKeywordPlugin",
    "PAGE_ICON",
    "PAGE_KEY",
    "PAGE_ORDER",
    "PAGE_TITLE",
    "PLUGIN_ID",
]


class AutoKeywordPlugin(Plugin):
    """模型生成关键词：一个页面 + 数据管理页的两个入口。"""

    def setup(self, ctx) -> None:
        self._ctx = ctx
        self._api = ctx.require(AUTOLABEL_EXTENSION)
        self._register_page(ctx)
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {
                "text": "自动生成关键词",
                "callback": self._on_manage,
                "icon": "DICTIONARY",
                "tip": "关键词要调模型生成，点开「自动关键词」页跑（有进度与取消）",
            },
            key="auto_keyword.manage",
            description="到页面里给选中条目生成关键词",
        )
        ctx.contribute(
            ExtensionPoint.MANAGE_ITEM_MENU,
            {"text": "自动生成关键词", "callback": self._on_item, "icon": "DICTIONARY"},
            key="auto_keyword.item",
            description="到页面里给这一条生成关键词",
        )

    def _register_page(self, ctx) -> None:
        try:
            from .ui.page import AutoKeywordPage

            ctx.add_page(
                PAGE_KEY,
                PAGE_TITLE,
                lambda: AutoKeywordPage(ctx, self._api),
                icon=PAGE_ICON,
                order=PAGE_ORDER,
            )
        except PluginError as exc:
            ctx.log.warning("宿主没有提供界面接口，自动关键词页未注册：{}", exc)

    # ------------------------------------------------------------------ 入口

    def _on_manage(self, selection=None) -> None:
        """数据管理页工具栏：**不跳页**，直接在这里起后台任务生成关键词。"""
        ids = [
            str(getattr(item, "id", "") or "") for item in (getattr(selection, "items", ()) or ())
        ]
        self._generate([item for item in ids if item], source="数据管理页工具栏")

    def _on_item(self, item) -> None:
        """条目右键菜单：同样在当前页后台生成（不跳页）。"""
        name = getattr(item, "name", "") or ""
        item_id = str(getattr(item, "id", "") or "")
        self._generate([item_id] if item_id else [], source=f"条目「{name}」" if name else "条目")

    def _generate(self, ids: list[str], *, source: str) -> None:
        """后台跑：规划 → 调模型 → 写库；**界面操作全部回主线程做**。

        工作线程只能碰数据 —— 在它里面调 `toast` / 控件会让整个程序卡住（用户 m02373）。
        """
        if getattr(self, "_running", False):
            self._toast("还在生成关键词", "上一轮还没结束，等它完成再试。")
            return
        self._running = True
        self._done: "queue.Queue[tuple[str, str]]" = queue.Queue()
        count_text = f"{len(ids)} 个条目" if ids else "全部条目"
        self._ctx.log.info("开始生成关键词（{}）：{}", source, count_text)
        self._toast("已开始生成关键词", f"{count_text}在后台跑，完成会提示（结果直接写进条目）。")

        def work() -> None:
            try:
                from app.sdk import items as items_sdk

                from dm_plugin.lib.autolabel import align as align_tools
                from dm_plugin.lib.autolabel import plugin as library

                from . import runner

                rows = list(items_sdk.list_items(user_id=items_sdk.current_user_id()))
                if ids:
                    wanted = set(ids)
                    picked = [
                        row for row in rows if str(getattr(row, "id", "") or "") in wanted
                    ]
                    rows = picked or rows
                if not rows:
                    self._done.put(("empty", "当前用户下一个条目都没有，先导点数据进来。"))
                    return
                book = self._api.align(reload=True)
                registered = library.registered_models()
                if not book.table(purpose=align_tools.PURPOSE_KEYWORD).ready_rows(
                    registered=registered
                ):
                    self._done.put(("empty", "对齐表里没有配好的模型：先到「自动关键词」页配一下。"))
                    return
                minimum, maximum = runner.option_values(self._ctx)
                plan = runner.plan_keywords(
                    rows,
                    table=book.table(purpose=align_tools.PURPOSE_KEYWORD),
                    registered=registered,
                    minimum=minimum,
                    maximum=maximum,
                    on_problem=lambda message: self._ctx.log.info("对齐模型：{}", message),
                )
                report = runner.run_keywords(plan, api=items_sdk)
                self._done.put(("ok", runner.summary_text(report)))
            except Exception as exc:  # 后台线程：失败也要让用户看见
                self._done.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="auto-keyword-manage").start()
        # 主线程轮询结果：工作线程只往队列里放，界面操作永远发生在主线程。
        try:
            from PyQt6.QtCore import QTimer

            self._poller = QTimer()
            self._poller.setInterval(200)
            self._poller.timeout.connect(self._drain_done)
            self._poller.start()
        except Exception as exc:  # 没有 Qt 事件循环（测试 / 无界面）：只写日志
            self._ctx.log.warning("没有界面事件循环，生成结果只写日志：{}", exc)

    def _drain_done(self) -> None:
        """主线程收尾：报告结果并刷新数据管理页（关键词当场可见）。"""
        try:
            kind, message = self._done.get_nowait()
        except queue.Empty:
            return
        try:
            self._poller.stop()
        except Exception:
            pass
        self._running = False
        if kind == "ok":
            self._ctx.log.info("生成关键词完成：{}", message)
            self._toast("生成关键词完成", message)
            try:
                from app.sdk import ui as ui_sdk

                ui_sdk.notify_items_changed()  # 让数据管理页立刻重载，关键词直接显示出来
                self._ctx.log.info("已通知数据管理页刷新")
            except Exception as exc:
                self._ctx.log.warning("通知数据管理页刷新失败：{}", exc)
        elif kind == "error":
            self._ctx.log.error("生成关键词失败：{}", message)
            self._toast("生成关键词失败", message[:200])
        else:
            self._ctx.log.warning("生成关键词：{}", message)
            self._toast("没有生成关键词", message)

    # ------------------------------------------------------------------ 小工具

    def _open_page(self, message: str) -> None:
        route = f"plugin.{PAGE_KEY}"
        opened = False
        try:
            from app.sdk import ui as ui_sdk

            opened = bool(ui_sdk.open_page(route))
        except PluginError as exc:
            self._ctx.log.warning("宿主没有提供界面接口，打不开自动关键词页：{}", exc)
        if opened:
            self._toast("自动关键词", message)
        else:
            self._toast("打不开自动关键词页", "先在插件页启用「自动关键词」插件。")

    def _toast(self, title: str, content: str = "") -> None:
        try:
            self._ctx.host.toast(title, content)
        except Exception:
            self._ctx.log.info("{}：{}", title, content)
