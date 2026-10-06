"""自动标签（模型）：按数据类型对齐模型，用模型给条目挂标签。

* 「自动标签」配置页：对齐表（数据类型 → 标签模型 + 对齐模型）、
  「设置对齐 / 一键补全 / 检查缺失 / 重置 / 全部重置 / 打开模型页」，整批请求交给模型工具库
  （`dm_plugin.lib.model.api.run_batch()`），带进度与取消；
* 数据管理页工具栏按钮与条目右键菜单：直接在这里起后台任务挂标签（结果写进条目、完成后刷新列表）。

规则方案在另一个插件 `auto_tag.rule` 里：两者**不互斥**，可以同时启用——规则的归规则、模型的归模型，
先后跑一遍就等于「规则标签 ∪ 模型标签」。
"""

from __future__ import annotations

import queue
import threading

from app.sdk import ExtensionPoint, Plugin, PluginError

from dm_plugin.lib.autolabel.plugin import AUTOLABEL_EXTENSION

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
    """模型挂标签：一个页面 + 数据管理页的两个入口。"""

    def setup(self, ctx) -> None:
        self._ctx = ctx
        self._api = ctx.require(AUTOLABEL_EXTENSION)
        self._register_page(ctx)
        ctx.contribute(
            ExtensionPoint.MANAGE_TOOLBAR,
            {
                "text": "自动挂标签",
                "callback": self._on_manage,
                "icon": "TAG",
                "tip": "给选中的条目用模型挂标签，完成会提示（结果直接写进条目）",
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

    def _on_manage(self, selection=None) -> None:
        """数据管理页工具栏：**不跳页**，直接在这里起后台任务挂标签。"""
        ids = [
            str(getattr(item, "id", "") or "") for item in (getattr(selection, "items", ()) or ())
        ]
        self._generate([item for item in ids if item], source="数据管理页工具栏")

    def _on_item(self, item) -> None:
        """条目右键菜单：同样在当前页后台挂标签（不跳页）。"""
        name = getattr(item, "name", "") or ""
        item_id = str(getattr(item, "id", "") or "")
        self._generate([item_id] if item_id else [], source=f"条目「{name}」" if name else "条目")

    def _generate(self, ids: list[str], *, source: str) -> None:
        """后台跑：规划 → 调模型 → 写库；**界面操作全部回主线程做**。

        工作线程只能碰数据 —— 在它里面调 `toast` / 控件会让整个程序卡住（用户 m02373）。
        """
        if getattr(self, "_running", False):
            self._toast("还在挂标签", "上一轮还没结束，等它完成再试。")
            return
        self._running = True
        self._done: "queue.Queue[tuple[str, str]]" = queue.Queue()
        count_text = f"{len(ids)} 个条目" if ids else "全部条目"
        self._ctx.log.info("开始挂标签（{}）：{}", source, count_text)
        self._toast("已开始挂标签", f"{count_text}在后台跑，完成会提示（结果直接写进条目）。")

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
                rule_set = self._api.rules(reload=True)
                registered = library.registered_models()
                if not book.table(purpose=align_tools.PURPOSE_LABEL).ready_rows(
                    registered=registered
                ):
                    self._done.put(("empty", "对齐表里没有配好的模型：先到「自动标签」页配一下。"))
                    return
                minimum, maximum, merge = runner.option_values(self._ctx)
                plan = runner.plan_labels(
                    rows,
                    table=book.table(purpose=align_tools.PURPOSE_LABEL),
                    registered=registered,
                    rule_set=rule_set,
                    merge=merge,
                    minimum=minimum,
                    maximum=runner.effective_max(maximum, items_sdk.tag_names()),
                    reader=items_sdk.read_text,
                    on_problem=lambda message: self._ctx.log.info("对齐模型：{}", message),
                )
                report = runner.run_labels(plan, api=items_sdk)
                self._done.put(("ok", runner.summary_text(report)))
            except Exception as exc:  # 后台线程：失败也要让用户看见
                self._done.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="auto-tag-manage").start()
        # 主线程轮询结果：工作线程只往队列里放，界面操作永远发生在主线程。
        try:
            from PyQt6.QtCore import QTimer

            self._poller = QTimer()
            self._poller.setInterval(200)
            self._poller.timeout.connect(self._drain_done)
            self._poller.start()
        except Exception as exc:  # 没有 Qt 事件循环（测试 / 无界面）：只写日志
            self._ctx.log.warning("没有界面事件循环，挂标签结果只写日志：{}", exc)

    def _drain_done(self) -> None:
        """主线程收尾：报告结果并刷新数据管理页（标签当场可见）。"""
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
            self._ctx.log.info("自动挂标签完成：{}", message)
            self._toast("自动挂标签完成", message)
            try:
                from app.sdk import items as items_sdk
                from app.sdk import ui as ui_sdk

                ui_sdk.notify_items_changed()  # 让数据管理页立刻重载，标签直接显示出来
                items_sdk.notify_tags_changed()  # 新建的标签在标签页当场可见
                self._ctx.log.info("已通知数据管理页刷新")
            except Exception as exc:
                self._ctx.log.warning("通知数据管理页刷新失败：{}", exc)
        elif kind == "error":
            self._ctx.log.error("自动挂标签失败：{}", message)
            self._toast("自动挂标签失败", message[:200])
        else:
            self._ctx.log.warning("自动挂标签：{}", message)
            self._toast("没有挂标签", message)

    # ------------------------------------------------------------------ 小工具

    def _toast(self, title: str, content: str = "") -> None:
        try:
            self._ctx.host.toast(title, content)
        except Exception:
            self._ctx.log.info("{}：{}", title, content)
