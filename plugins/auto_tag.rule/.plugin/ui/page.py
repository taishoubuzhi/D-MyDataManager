"""任务 2 的「自动标签」页：规则表 + 运行区。

界面一律走集成界面工具库（`dm_plugin.builtin.lib.ui.plugin`）与共享控件
（`...lib.autolabel.ui.controls`），只 import PyQt6 的布局/定时器与 `FluentIcon`。
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk import items as items_sdk

from dm_plugin.lib.autolabel.rules import KIND_MATCH
from dm_plugin.lib.autolabel.ui.controls import ProgressPanel, RuleDialog, fill_rule_table
from dm_plugin.builtin.lib.ui.plugin import (
    ScrollPageTemplate,
    caption,
    confirm,
    primary_button,
    push_button,
    read_only_table,
    status_label,
    text_area,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
)

from .. import runner

PAGE_TITLE = "自动标签"
PAGE_SUBTITLE = (
    "按规则给文件挂标签：规则只看条目本身（名称、后缀、类型、路径，需要时读正文），"
    "命中就批量写进库里，全程不调用模型。"
)

#: 这一页的规则只用字段匹配；「交模型判断」的规则由会自动调用模型的那一页负责
RULE_KINDS = (KIND_MATCH,)

#: 预览最多显示多少行
PREVIEW_LIMIT = 200


class AutoTagRulePage(ScrollPageTemplate):
    """按规则挂标签的配置页。"""

    def __init__(self, ctx, api, parent: QWidget | None = None) -> None:
        super().__init__(PAGE_TITLE, PAGE_SUBTITLE, parent)
        self._ctx = ctx
        self._api = api
        self._rule_set = api.rules()
        self._running = False
        self._cancel = False
        self._done = 0
        self._total = 0
        self._result = None
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)
        self._build_rules()
        self._build_run()
        self.refresh()

    # ============================================================ 分区
    def _toolbar(self, parent: QWidget, *buttons) -> QWidget:
        bar = QWidget(parent)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return bar

    def _build_rules(self) -> None:
        card, layout = self.add_section(
            "规则",
            "规则由共享库 lib.autolabel 提供：出厂规则只读，你新建或改过的会写进 .configs/autolabel.rules.json。"
            "这一页的规则只用字段匹配，不调用模型；要按后缀配默认标签，直接建一条「匹配方式 = 是」的后缀规则即可。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.ADD, "新建规则", self._new_rule),
                push_button(card, "编辑", self._edit_rule),
                push_button(card, "删除", self._remove_rule),
                push_button(card, "恢复出厂", self._restore_rule),
                push_button(card, "刷新", self.refresh),
            )
        )
        self._rule_table = read_only_table(
            card, headers=("名称", "类型", "匹配", "要挂的标签", "状态")
        )
        self._rule_table.itemSelectionChanged.connect(self._sync_hint)
        layout.addWidget(self._rule_table)
        self._rule_hint = caption(card, "")
        layout.addWidget(self._rule_hint)

    def _build_run(self) -> None:
        card, layout = self.add_section(
            "运行",
            "对当前用户的全部条目跑一遍规则：只挂在规则里写明、且条目上还没有的标签。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.TAG, "开始挂标签", self._start),
                push_button(card, "预览（不写库）", self._preview),
                push_button(card, "刷新", self.refresh),
            )
        )
        self._scope_label = status_label(card, "")
        layout.addWidget(self._scope_label)
        self._panel = ProgressPanel(card, on_cancel=self._request_cancel)
        layout.addWidget(self._panel.widget)
        self._preview_box = text_area(card, read_only=True, monospace=True)
        self._preview_box.setMinimumHeight(150)
        layout.addWidget(self._preview_box)

    # ============================================================ 刷新
    def refresh(self) -> None:
        self._rule_set = self._api.rules(reload=True)
        fill_rule_table(self._rule_table, self._rule_set)
        self._sync_hint()
        user = items_sdk.current_user_id()
        count = len(items_sdk.list_items(user_id=user))
        where = f"当前用户 #{user}" if user else "当前用户：默认"
        self._scope_label.setText(
            f"{where}；库里有 {count} 个条目，启用 {len(self._rule_set.enabled)} 条规则。"
        )

    def _sync_hint(self) -> None:
        key = self._current_rule_key()
        self._rule_hint.setText(
            f"共 {len(self._rule_set.rules)} 条规则；选中的是「{key}」。" if key else f"共 {len(self._rule_set.rules)} 条规则。"
        )

    def _current_rule_key(self) -> str:
        row = self._rule_table.currentRow()
        rules = self._rule_set.rules
        if row < 0 or row >= len(rules):
            return ""
        return rules[row].key

    # ============================================================ 规则增删改
    def _taken_keys(self) -> list[str]:
        return [rule.key for rule in self._rule_set.rules]

    def _apply_rule(self, rule) -> None:
        self._rule_set = self._rule_set.with_rule(rule)
        self._save(self._rule_set)

    def _new_rule(self) -> None:
        dialog = RuleDialog(self, title="新建规则", taken=self._taken_keys(), kinds=RULE_KINDS)
        if dialog.exec():
            self._apply_rule(dialog.rule())

    def _edit_rule(self) -> None:
        key = self._current_rule_key()
        rule = self._rule_set.by_key(key) if key else None
        if rule is None:
            toast_info(self, "先选一条规则", "在规则表里点一行再编辑。")
            return
        dialog = RuleDialog(self, title="编辑规则", rule=rule, taken=self._taken_keys(), kinds=RULE_KINDS)
        if dialog.exec():
            self._apply_rule(dialog.rule())

    def _remove_rule(self) -> None:
        key = self._current_rule_key()
        rule = self._rule_set.by_key(key) if key else None
        if rule is None:
            toast_info(self, "先选一条规则", "在规则表里点一行再删除。")
            return
        if not self._confirm("删除规则", f"确定删掉规则「{rule.title}」吗？出厂规则删掉后可以用「恢复出厂」找回来。"):
            return
        self._rule_set = self._rule_set.without_key(rule.key)
        self._save(self._rule_set)

    def _restore_rule(self) -> None:
        key = self._current_rule_key()
        if not key:
            toast_info(self, "先选一条规则", "在规则表里点一行再恢复。")
            return
        if not self._rule_set.factory_changed(key):
            toast_info(self, "这条是出厂原样", "没有改动过，不需要恢复。")
            return
        self._rule_set = self._rule_set.restore_key(key)
        self._save(self._rule_set)

    # ============================================================ 保存
    def _save(self, rule_set) -> None:
        tags = []
        for rule in rule_set.rules:
            for tag in rule.tags:
                if tag not in tags:
                    tags.append(tag)
        if tags:
            items_sdk.ensure_tags(tags)
        if not self._api.save_rules(rule_set):
            toast_error(self, "保存失败", "规则文件写不进去，看看 .configs 目录的权限。")
            return
        self.refresh()

    def _confirm(self, title: str, content: str) -> bool:
        return confirm(self, title, content)

    # ============================================================ 运行
    def _rows(self) -> list:
        return list(items_sdk.list_items(user_id=items_sdk.current_user_id()))

    def _start(self) -> None:
        self._begin(dry=False)

    def _preview(self) -> None:
        self._begin(dry=True)

    def _begin(self, *, dry: bool) -> None:
        if self._running:
            toast_info(self, "还在跑", "等这一轮结束，或者点取消。")
            return
        rows = self._rows()
        if not rows:
            toast_warning(self, "没有条目", "当前用户下一个条目都没有，先导点数据进来。")
            return
        self._running = True
        self._cancel = False
        self._done = 0
        self._total = len(rows)
        self._result = None
        self._preview_box.setPlainText("")
        self._panel.begin("正在预览…" if dry else "正在匹配规则…")
        rule_set = self._rule_set

        def work() -> None:
            try:
                plan = runner.plan_tags(rows, rule_set, reader=items_sdk.read_text)
                if dry:
                    self._result = ("dry", plan, None)
                    return
                report = runner.run_rules(
                    plan,
                    api=items_sdk,
                    progress=self._on_progress,
                    cancel=lambda: self._cancel,
                )
                self._result = ("run", None, report)
            except Exception as exc:  # 后台线程不能把异常抛给 Qt
                self._result = ("error", None, exc)
            finally:
                self._running = False

        threading.Thread(target=work, daemon=True, name="auto-tag-rule").start()
        self._timer.start()

    def _on_progress(self, done: int, total: int) -> None:
        """后台线程只记数，界面由 `_tick()` 在界面线程里画。"""
        self._done = done
        self._total = total

    def _request_cancel(self) -> None:
        self._cancel = True
        self._panel.set_status("正在取消…")

    def _tick(self) -> None:
        if self._total:
            self._panel.set_progress(self._done, self._total)
        if self._result is None:
            return
        self._timer.stop()
        kind, plan, payload = self._result
        self._result = None
        if kind == "error":
            self._panel.fail(f"运行失败：{payload}")
            toast_error(self, "运行失败", str(payload))
            return
        if kind == "dry":
            self._show_preview(plan)
            self._panel.finish(f"预览完成：{runner.summary_text(runner.RunReport(plan))}")
            return
        report = payload
        self._panel.finish(runner.summary_text(report))
        if report.failed:
            toast_warning(self, "部分条目没写进去", runner.summary_text(report))
        elif report.cancelled:
            toast_info(self, "已取消", runner.summary_text(report))
        elif not report.matched:
            # 一条都没匹配上时不能说「完成」：用户会以为标签挂好了。
            toast_warning(self, "没有条目命中规则", runner.summary_text(report))
        elif not report.written:
            toast_info(self, "没有新增标签", f"{runner.summary_text(report)}；这些标签条目上都有了。")
        else:
            toast_success(self, "挂标签完成", runner.summary_text(report))
        items_sdk.notify_changed()

    def _show_preview(self, plan) -> None:
        lines = []
        for item in plan.matched[:PREVIEW_LIMIT]:
            rules = "、".join(item.rules)
            lines.append(f"{item.name or item.item_id}  ←  {'、'.join(item.tags)}   （{rules}）")
        if len(plan.matched) > PREVIEW_LIMIT:
            lines.append(f"……还有 {len(plan.matched) - PREVIEW_LIMIT} 个条目命中，只显示前 {PREVIEW_LIMIT} 个")
        if not lines:
            lines.append("没有条目命中任何规则。")
        if plan.notes:
            lines.extend(["", *plan.notes])
        self._preview_box.setPlainText("\n".join(lines))
