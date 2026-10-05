"""任务 4 的「自动关键词」页：按数据类型对齐模型，整批生成关键词。

界面一律走集成界面工具库（`dm_plugin.builtin.lib.ui.plugin`）与共享控件
（`...lib.autolabel.ui.controls`），只 import PyQt6 的布局/定时器与 `FluentIcon`。
"""

from __future__ import annotations

import threading
from dataclasses import replace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk import items as items_sdk
from app.sdk import models as model_sdk
from app.sdk import ui as ui_sdk

from dm_plugin.lib.autolabel import align as align_tools
from dm_plugin.lib.autolabel import plugin as library
from dm_plugin.lib.autolabel.ui.controls import (
    AlignDialog,
    ProgressPanel,
    fill_align_table,
)
from dm_plugin.builtin.lib.ui.plugin import (
    FormDialog,
    ScrollPageTemplate,
    body_label,
    caption,
    confirm,
    line_edit,
    list_item,
    list_view,
    primary_button,
    push_button,
    read_only_table,
    spin_box,
    status_label,
    strong_label,
    text_area,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
    widget_row,
)

from .. import runner

PAGE_TITLE = "自动关键词"
PAGE_SUBTITLE = (
    "用模型给文件生成关键词：先给每种数据类型对齐一个模型，整批请求交给模型工具库，"
    "带进度与取消；每次生成几个在插件设置里调。"
)

#: 预览最多显示多少行
PREVIEW_LIMIT = 200

class AutoKeywordPage(ScrollPageTemplate):
    """关键词的配置与运行页。"""

    def __init__(self, ctx, api, parent: QWidget | None = None) -> None:
        super().__init__(PAGE_TITLE, PAGE_SUBTITLE, parent)
        self._ctx = ctx
        self._api = api
        self._book = api.align()
        self._registered: dict[str, str] = {}
        self._minimum, self._maximum = runner.option_values(ctx)
        self._running = False
        self._cancel = False
        self._done = 0
        self._total = 0
        self._result = None
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)
        self._build_align()
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

    def _table(self):
        return self._book.table(purpose=align_tools.PURPOSE_KEYWORD)

    def _build_align(self) -> None:
        card, layout = self.add_section(
            "数据类型对齐",
            "「系统方案」一列只读：出厂给每种数据类型配好的主模型与对齐模型。选中一行点"
            "「设置对齐」打开编辑框，勾上「启用系统方案」就让这一行跟随系统（模型与对齐模型都用"
            "系统配好的，不让单独改），取消勾选可以自己挑；改坏了用「重置 / 全部重置」还原。"
            "表里每一行都是这个数据类型自己的设置（方案 / 主模型 / 对齐模型 / 状态），可以各不相同，"
            "改完立刻生效并自动选中刚改的那一行（备注显示在下面这行提示里）；状态列按这一行的主模型"
            "与对齐模型有没有登记来显示。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.EDIT, "设置对齐", self._edit_align),
                push_button(card, "一键补全", self._complete),
                push_button(card, "检查缺失", self._check_requirements),
                push_button(card, "重置", self._clear_align),
                push_button(card, "全部重置", self._reset_all),
                push_button(card, "打开模型页", self._open_model_page),
            )
        )
        # 表格默认自动换行、行高随内容：系统方案这类长内容直接完整显示（下面还有标识说这行是
        # 「系统方案」还是「手动设置」，不再需要单独的显示模式切换）。
        self._align_table = read_only_table(
            card, headers=("数据类型", "系统方案", "关键词模型", "对齐模型", "状态")
        )
        self._align_table.itemSelectionChanged.connect(self._sync_hint)
        layout.addWidget(self._align_table)
        self._align_hint = caption(card, "")
        layout.addWidget(self._align_hint)
        self._options_label = caption(card, "")
        layout.addWidget(self._options_label)

    def _build_run(self) -> None:
        card, layout = self.add_section(
            "运行",
            "对当前用户的全部条目跑一遍：只对配了模型的数据类型发请求，"
            "跳过的和失败的会在下面说明。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.TAG, "开始生成关键词", self._start),
                push_button(card, "预览（不写库）", self._preview),
                push_button(card, "设置数量", self._edit_numbers),
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
    def _drop_dead_rows(self) -> None:
        """把指向「已被删除的模型」的行清回未登记。

        模型页删掉模型后，对齐表里那份 `model_id` 就成了死引用：状态列一直显示未就绪，
        「一键补全」也因为「已经有 id」而不重新登记。清掉之后这里回到「未登记：<方案 key>」，
        再点「一键补全」就能重建。
        """
        known = library.known_model_ids()
        if known is None:
            return
        purpose = align_tools.PURPOSE_KEYWORD
        changed = False
        for row in self._table().rows:
            if (row.model_id and row.model_id not in known) or (
                row.align_model and row.align_model not in known
            ):
                self._book = self._book.set_row(purpose, replace(row, model_id="", align_model=""))
                changed = True
        if changed:
            self._save_align()

    def refresh(self) -> None:
        self._book = self._api.align(reload=True)
        self._registered = library.registered_models()
        self._drop_dead_rows()
        self._minimum, self._maximum = runner.option_values(self._ctx)
        fill_align_table(
            self._align_table,
            self._table().rows,
            registered=self._registered,
            factory=self._factory_map(),
            names=self._model_names(),
        )
        limit = self._maximum or "不限"
        self._options_label.setText(
            f"关键词数量：最少 {self._minimum} 个、最多 {limit} 个（点「设置数量」当场改）"
        )
        self._scope_label.setText(self._scope_text())
        self._sync_hint()
        # 数据管理页的「自动生成关键词」入口：请求写进了插件设置，这里读到就清掉并自动开跑。
        try:
            pending = bool(self._ctx.option("pending_keyword_request", False))
            items = [str(item) for item in (self._ctx.option("pending_keyword_items", []) or [])]
            self._pending_ids = items if pending else list(getattr(self, "_pending_ids", []))
            if pending:
                self._ctx.set_option("pending_keyword_request", False)
                self._ctx.set_option("pending_keyword_items", [])
                self._ctx.log.info(
                    "收到自动生成关键词请求：{}",
                    f"{len(items)} 个条目" if items else "全部条目",
                )
                QTimer.singleShot(0, self._start)
        except Exception as exc:  # pragma: no cover - 请求消费失败不该挡住刷新
            self._ctx.log.warning("处理关键词请求失败：{}", exc)

    def _scope_text(self) -> str:
        rows = len(items_sdk.list_items(user_id=items_sdk.current_user_id()))
        ready = len(self._table().ready_rows(registered=self._registered))
        return (
            f"当前用户：{items_sdk.current_user_id() or '默认'}；"
            f"库里有 {rows} 个条目；对齐表配好 {ready} 个数据类型。"
        )

    # ============================================================ 选择与提示
    def _current_datatype(self) -> str:
        row = self._align_table.currentRow()
        rows = self._table().rows
        if 0 <= row < len(rows):
            return rows[row].datatype
        return ""

    def _sync_hint(self) -> None:
        row = self._selected_row()
        if row is None:
            self._align_hint.setText("选中一行可以看这个数据类型对齐到哪个模型。")
            return
        target = row.resolved(registered=self._registered) or "还没配模型"
        align = row.resolved_align(registered=self._registered)
        extra = f"；前置：{align}" if align else ""
        state = "启用" if row.enabled else "已停用"
        source = "系统方案" if row.use_preset else "手动设置"
        note = f"　备注：{row.note}" if row.note else ""
        self._align_hint.setText(f"{row.datatype_label}：{target}{extra}（{source}，{state}）{note}")

    # ============================================================ 对齐表
    def _edit_align(self) -> None:
        row = self._selected_row()
        if row is None:
            toast_info(self, "先选数据类型", "在表里点一个数据类型。")
            return
        model_labels, model_values = library.model_choices()
        template_labels, template_values = library.template_choices()
        dialog = AlignDialog(
            self.window(),
            purpose=align_tools.PURPOSE_KEYWORD,
            row=row,
            preset=self._factory_map().get(row.datatype),
            model_labels=model_labels,
            model_values=model_values,
            template_labels=template_labels,
            template_values=template_values,
            registered=library.registered_models(),
            names=self._model_names(),
        )
        if dialog.exec():
            result = dialog.row_result()
            self._book = self._book.set_row(align_tools.PURPOSE_KEYWORD, result)
            self._save_align()
            self._select_datatype(result.datatype)

    def _select_datatype(self, datatype: str) -> None:
        """保存后把表格选中行放回被改的那一行，用户一眼就能看到变化。"""
        for index, table_row in enumerate(self._table().rows):
            if table_row.datatype == datatype:
                self._align_table.selectRow(index)
                break

    def _model_names(self) -> dict[str, str]:
        """`{model_id: 登记时起的名字}`：表格与编辑框显示同一个名字，不和方案 key 混着来。"""
        labels, values = library.model_choices()
        return dict(zip(values, labels))

    def _clear_align(self) -> None:
        datatype = self._current_datatype()
        if not datatype:
            toast_info(self, "先选数据类型", "在表里点一个数据类型。")
            return
        self._book = self._book.reset_row(align_tools.PURPOSE_KEYWORD, datatype)
        self._save_align()

    def _selected_row(self) -> align_tools.AlignRow | None:
        """对齐表里当前选中的那一行。"""
        datatype = self._current_datatype()
        return self._table().row(datatype) if datatype else None

    def _factory_map(self) -> dict:
        """出厂方案：`{数据类型: AlignRow}`，给「系统方案」一列与编辑框的「启用系统方案」用。"""
        table = self._book.factory_table(align_tools.PURPOSE_KEYWORD)
        return {row.datatype: row for row in table.rows}

    def _reset_all(self) -> None:
        """把整张表还原成出厂方案（丢掉所有自定义对齐）。"""
        if not confirm(
            self, "全部重置", "把这张对齐表还原成系统方案？你改过的模型与对齐模型都会丢掉。"
        ):
            return
        self._book = self._book.clear_user()
        self._save_align()

    def _complete(self) -> None:
        """一键补全：登记还缺的模型，缺权重时一次确认后下载，最后报还差什么。"""
        row = self._selected_row()
        if row is None:
            toast_info(self, "先选数据类型", "在对齐表里点一个数据类型。")
            return
        self._offer_downloads(self._register_missing(row))

    def _register_missing(self, row: align_tools.AlignRow) -> align_tools.AlignRow:
        """把这一行方案里还没登记的模型登记成草稿，返回更新后的行。"""
        pending = self._pending_templates(row)
        if not pending:
            return row
        update = row
        done: list[str] = []
        for key, role in pending:
            try:
                brief = model_sdk.create_from_template(key, name=key)
            except Exception as exc:
                toast_error(self, "登记失败", f"{key}：{exc}")
                return row
            model_id = str(brief.get("model_id") or "")
            if not model_id:
                toast_error(self, "登记失败", f"{key}：模型工具库没给出 model_id")
                return row
            if role == "前置":
                update = replace(update, align_model=model_id, align_template="")
            else:
                update = replace(update, model_id=model_id, template="")
            done.append(f"{role} {brief.get('name') or key}")
        self._book = self._book.set_row(align_tools.PURPOSE_KEYWORD, update)
        self._save_align()
        toast_success(self, "已登记草稿", "、".join(done) + "；只登记，还没下载权重。")
        return update

    def _offer_downloads(self, row: align_tools.AlignRow) -> None:
        """缺权重时只弹一个确认框；确认后把主模型与对齐模型的权重都排进下载队列。"""
        targets: list[tuple[str, str, str]] = []
        for model_id, role in (
            (row.resolved(registered=self._registered), "模型"),
            (row.resolved_align(registered=self._registered), "对齐模型"),
        ):
            if not model_id:
                continue
            gap = model_sdk.requirements(model_id)
            files = tuple(str(item) for item in gap.get("missing_files") or ())
            if not gap.get("ok") and files:
                targets.append((model_id, role, str(gap.get("hint") or "缺模型文件")))
        if not targets:
            self._report_gap(row, prefix="")
            return
        names = "；".join(f"{role}：{hint}" for _id, role, hint in targets)
        if not confirm(self, "要下载模型文件吗", f"{names}。现在下载吗？可以在「模型」页暂停或取消。"):
            self._report_gap(row, prefix="")
            return
        queued: list[str] = []
        for model_id, role, _files in targets:
            try:
                result = model_sdk.download_model(model_id)
            except Exception as exc:
                toast_error(self, "下载没排上", f"{role} {model_id}：{exc}")
                continue
            if result.get("ok"):
                queued.extend(str(item) for item in result.get("queued") or ())
            else:
                toast_error(
                    self,
                    "下载没排上",
                    f"{role} {model_id}：{result.get('hint') or '模型工具库没给出原因'}",
                )
        if queued:
            toast_success(self, "已加入下载队列", "、".join(queued) + "；进度看「模型」页。")

    def _save_align(self) -> None:
        if self._api.save_align(self._book):
            toast_success(self, "已保存", "对齐表写进 .configs/autolabel.align.json（关键词表）。")
        else:
            toast_error(self, "保存失败", "对齐表写不进去，看看 .configs 目录的权限。")
        self.refresh()

    # ============================================================ 预定义方案
    def _pending_templates(self, row: align_tools.AlignRow) -> list[tuple[str, str]]:
        """方案里还没着落的预定义方案 key → [(key, 角色)]（角色：模型 / 前置）。"""
        pending: list[tuple[str, str]] = []
        if (
            row.template
            and not row.model_id
            and not row.resolved(registered=self._registered)
            and row.template not in self._registered
        ):
            pending.append((row.template, "模型"))
        if (
            row.align_template
            and not row.align_model
            and not row.resolved_align(registered=self._registered)
            and row.align_template not in self._registered
        ):
            pending.append((row.align_template, "前置"))
        return pending

    def _report_gap(self, row: align_tools.AlignRow, *, prefix: str = "") -> None:
        """报这一行的模型是否齐了：先看有没有没登记的方案，再看权重与运行环境。"""
        if not row.enabled:
            toast_info(
                self, "这一行是停用的", f"{row.datatype_label} 在「设置对齐」里被停用了。"
            )
            return
        pending = [key for key, _role in self._pending_templates(row)]
        if pending:
            toast_warning(
                self,
                "还有模型没登记",
                prefix + "先点「一键补全」登记：" + "、".join(pending),
            )
            return
        ids = [
            model_id
            for model_id in (
                row.resolved(registered=self._registered),
                row.resolved_align(registered=self._registered),
            )
            if model_id
        ]
        issues: list[str] = []
        for model_id in ids:
            gap = model_sdk.requirements(model_id)
            if not bool(gap.get("ok")):
                missing = "、".join(str(item) for item in gap.get("missing") or ())
                issues.append(f"{model_id}：{gap.get('hint') or missing or '还没准备好'}")
        if not issues:
            toast_success(
                self,
                "可以跑了",
                prefix + f"{row.datatype_label} 的模型、权重与运行环境都齐了。",
            )
            return
        toast_warning(self, "还缺东西", prefix + "；".join(issues))
        if confirm(self, "去模型页", "现在打开模型页下载权重 / 装运行环境吗？"):
            self._open_model_page()

    def _check_requirements(self) -> None:
        row = self._selected_row()
        if row is None:
            toast_info(self, "先选数据类型", "在预定义方案列表里点一个数据类型。")
            return
        self._report_gap(row, prefix="")

    def _edit_numbers(self) -> None:
        """当场改「最少 / 最多生成几个关键词」（写进插件设置）。"""
        dialog = FormDialog(self.window(), title="关键词数量", width=460)
        minimum_box = spin_box(dialog, value=self._minimum, minimum=1, maximum=50, suffix="个")
        maximum_box = spin_box(dialog, value=self._maximum, minimum=0, maximum=50, suffix="个")
        dialog.add_row("最少生成", minimum_box)
        dialog.add_row("最多生成", maximum_box)
        dialog.add_hint("最多 0 表示不限；模型给得比「最少」还少时整条作废，不勉强写。")
        dialog.set_buttons(yes="保存", cancel="取消")
        if not dialog.exec():
            return
        minimum = int(minimum_box.value())
        maximum = int(maximum_box.value())
        if maximum and maximum < minimum:
            maximum = minimum
        try:
            self._ctx.set_option("min_keywords", minimum)
            self._ctx.set_option("max_keywords", maximum)
        except Exception as exc:
            toast_error(self, "保存失败", str(exc))
            return
        self._minimum, self._maximum = runner.option_values(self._ctx)
        limit = self._maximum or "不限"
        self._options_label.setText(f"关键词数量：最少 {self._minimum} 个、最多 {limit} 个")
        toast_success(self, "已保存", f"最少 {self._minimum} 个、最多 {limit} 个。")

    def _open_model_page(self) -> None:
        route = model_sdk.page_route()
        if route and ui_sdk.open_page(route):
            toast_info(self, "已打开模型页", "在模型页里下载权重、装运行环境。")
            return
        toast_warning(self, "打不开模型页", "没启用模型工具库插件？")

    # ============================================================ 运行
    def _rows(self) -> list:
        rows = list(items_sdk.list_items(user_id=items_sdk.current_user_id()))
        # 从数据管理页发起时只跑选中的那些条目（跑完这一轮就清掉范围）。
        wanted = {str(item) for item in getattr(self, "_pending_ids", []) if item}
        if wanted:
            picked = [row for row in rows if str(getattr(row, "id", "") or "") in wanted]
            if picked:
                return picked
        return rows

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
        self._ctx.log.info("{}关键词：{} 个条目", "预览" if dry else "开始生成", len(rows))
        self._pending_ids = []  # 这一轮已经用掉了范围
        book = self._book
        registered = self._registered
        minimum, maximum = self._minimum, self._maximum
        if not book.table(purpose=align_tools.PURPOSE_KEYWORD).ready_rows(registered=registered):
            toast_warning(self, "对齐表是空的", "先给数据类型配上模型。")
            return
        self._running = True
        self._cancel = False
        self._done = 0
        self._total = len(rows)
        self._result = None
        self._preview_box.setPlainText("")
        self._panel.begin("正在预览…" if dry else "正在处理…")

        def work() -> None:
            try:
                plan = runner.plan_keywords(
                    rows,
                    table=book.table(purpose=align_tools.PURPOSE_KEYWORD),
                    registered=registered,
                    minimum=minimum,
                    maximum=maximum,
                    on_problem=lambda message: self._ctx.log.info("对齐模型：{}", message),
                )
                if dry:
                    self._result = ("dry", plan, None)
                    return
                report = runner.run_keywords(
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

        threading.Thread(target=work, daemon=True, name="auto-keyword").start()
        self._timer.start()

    def _on_progress(self, done: int, total: int) -> None:
        """后台线程只记数，界面由 `_tick()` 在界面线程里画。"""
        self._done = done
        self._total = total or self._total

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
            self._panel.finish(
                f"预览完成：扫描 {plan.total} 个条目，会发 {len(plan.requests)} 个请求，"
                f"跳过 {len(plan.skipped)} 个"
            )
            return
        report = payload
        self._panel.finish(runner.summary_text(report))
        if report.failed:
            toast_warning(self, "部分关键词没写进去", runner.summary_text(report))
        elif report.cancelled:
            toast_info(self, "已取消", runner.summary_text(report))
        else:
            toast_success(self, "生成关键词完成", runner.summary_text(report))
        items_sdk.notify_changed()
        self._scope_label.setText(self._scope_text())

    def _show_preview(self, plan) -> None:
        report = runner.KeywordReport(plan=plan, collected=runner.collect_keywords(plan, ()))
        lines = list(runner.preview_rows(report, limit=PREVIEW_LIMIT))
        if plan.requests:
            lines.append("")
            lines.append(f"会向模型发 {len(plan.requests)} 个请求")
        self._preview_box.setPlainText("\n".join(lines))
