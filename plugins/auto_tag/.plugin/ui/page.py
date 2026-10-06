"""任务 3 的「自动标签」页：规则 / 模型两套方案共用一套运行区。

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
from dm_plugin.lib.model import api as model_sdk
from app.sdk import ui as ui_sdk

from dm_plugin.lib.autolabel import align as align_tools
from dm_plugin.lib.autolabel import plugin as library
from dm_plugin.lib.autolabel.ui.controls import (
    AlignDialog,
    ProgressPanel,
    RuleDialog,
    fill_align_table,
    fill_rule_table,
)
from dm_plugin.builtin.lib.ui.plugin import (
    FormDialog,
    ScrollPageTemplate,
    caption,
    check_box,
    confirm,
    line_edit,
    list_item,
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
)

from .. import runner

PAGE_TITLE = "自动标签"
PAGE_SUBTITLE = (
    "用模型给文件挂标签：按数据类型对齐模型，整批交给模型工具库；"
    "标签数量上下限在插件设置里调。"
)

#: 预览最多显示多少行
PREVIEW_LIMIT = 200


class AutoTagPage(ScrollPageTemplate):
    """模型自动标签的配置页：数据类型对齐表 + 运行区。"""

    def __init__(self, ctx, api, parent: QWidget | None = None) -> None:
        super().__init__(PAGE_TITLE, PAGE_SUBTITLE, parent)
        self._ctx = ctx
        self._api = api
        self._book = api.align()
        self._rule_set = api.rules()
        self._registered: dict[str, str] = {}
        self._minimum, self._maximum, self._merge = runner.option_values(ctx)
        self._running = False
        self._cancel = False
        self._done = 0
        self._total = 0
        self._result = None
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)
        self._build_options()
        self._build_rules()
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
        return self._book.table(purpose=align_tools.PURPOSE_LABEL)

    def _build_options(self) -> None:
        card, layout = self.add_section(
            "数量",
            "模型按下面的数据类型对齐表挑模型、整批交给模型工具库跑；打开「同时按规则挂标签」时，"
            "先按规则算一份标签、再让模型算一份，两份合并去重后一起挂（这就是「规则标签 ∪ 模型标签」）。",
        )
        self._options_label = caption(card, "")
        layout.addWidget(self._options_label)

    # ============================================================ 规则
    def _build_rules(self) -> None:
        card, layout = self.add_section(
            "规则",
            "和「自动标签（规则）」插件共用同一份 .configs/autolabel.rules.json：这里改的就是那份规则。"
            "规则标签在「挂标签」时与模型标签合并去重；两个标签插件在清单里互指冲突，不能同时启用。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.ADD, "新建规则", self._new_rule),
                push_button(card, "编辑", self._edit_rule),
                push_button(card, "删除", self._remove_rule),
                push_button(card, "恢复出厂", self._restore_rule),
            )
        )
        self._rule_table = read_only_table(
            card, headers=("名称", "类型", "匹配", "要挂的标签", "状态")
        )
        self._rule_table.itemSelectionChanged.connect(self._sync_rule_hint)
        layout.addWidget(self._rule_table)
        self._rule_hint = caption(card, "")
        layout.addWidget(self._rule_hint)

    def _current_rule_key(self) -> str:
        row = self._rule_table.currentRow()
        rules = self._rule_set.rules
        if 0 <= row < len(rules):
            return rules[row].key
        return ""

    def _taken_keys(self) -> list[str]:
        return [rule.key for rule in self._rule_set.rules]

    def _sync_rule_hint(self) -> None:
        key = self._current_rule_key()
        rule = self._rule_set.by_key(key) if key else None
        if rule is None:
            self._rule_hint.setText("选中一条规则可以看它的匹配方式与状态。")
        elif rule.problems:
            self._rule_hint.setText(f"{rule.title}：{'；'.join(rule.problems)}")
        else:
            self._rule_hint.setText(
                f"{rule.title}：{rule.field_text} → {'、'.join(rule.tags) or '交给模型'}"
            )

    def _apply_rule(self, rule) -> None:
        self._rule_set = self._rule_set.with_rule(rule)
        self._save_rules()

    def _new_rule(self) -> None:
        dialog = RuleDialog(self, title="新建规则", taken=self._taken_keys())
        if dialog.exec():
            self._apply_rule(dialog.rule())

    def _edit_rule(self) -> None:
        key = self._current_rule_key()
        rule = self._rule_set.by_key(key) if key else None
        if rule is None:
            toast_info(self, "先选规则", "在表里点一条要改的规则。")
            return
        taken = [item for item in self._taken_keys() if item != rule.key]
        dialog = RuleDialog(self, title="编辑规则", rule=rule, taken=taken)
        if dialog.exec():
            self._apply_rule(dialog.rule())

    def _remove_rule(self) -> None:
        key = self._current_rule_key()
        rule = self._rule_set.by_key(key) if key else None
        if rule is None:
            toast_info(self, "先选规则", "在表里点一条要删的规则。")
            return
        if not confirm(self, "删除规则", f"确定不要「{rule.title}」了吗？"):
            return
        self._rule_set = self._rule_set.without_key(rule.key)
        self._save_rules()

    def _restore_rule(self) -> None:
        key = self._current_rule_key()
        if not key:
            toast_info(self, "先选规则", "在表里点一条要恢复的规则。")
            return
        self._rule_set = self._rule_set.restore_key(key)
        self._save_rules()

    def _save_rules(self) -> None:
        names = [name for rule in self._rule_set.rules for name in rule.tags]
        try:
            items_sdk.ensure_tags(names, user_id=items_sdk.current_user_id())
            items_sdk.notify_tags_changed()
        except Exception:
            pass
        if self._api.save_rules(self._rule_set):
            toast_success(self, "已保存", "用户规则写进 .configs/autolabel.rules.json。")
        else:
            toast_error(self, "保存失败", "规则文件写不进去，看看 .configs 目录的权限。")
        self.refresh()

    def _build_align(self) -> None:
        card, layout = self.add_section(
            "数据类型对齐",
            "表里每一行都是这个数据类型自己的设置（方案 / 主模型 / 对齐模型 / 状态），可以各不相同，"
            "改完立刻生效并自动选中刚改的那一行（备注显示在下面这行提示里）；状态列按这一行的主模型"
            "与对齐模型有没有登记来显示。",
        )
        view = QWidget(card)
        view_layout = QVBoxLayout(view)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(8)
        view_layout.addWidget(
            strong_label(view, "数据类型对齐（点「设置对齐」，在编辑框里勾「启用系统方案」就跟随系统）")
        )
        layout.addWidget(
            self._toolbar(
                view,
                primary_button(view, FluentIcon.EDIT, "设置对齐", self._edit_align),
                push_button(view, "一键补全", self._complete),
                push_button(view, "检查缺失", self._check_requirements),
                push_button(view, "重置", self._clear_align),
                push_button(view, "全部重置", self._reset_all),
                push_button(view, "打开模型页", self._open_model_page),
            )
        )
        self._align_table = read_only_table(
            view, headers=("数据类型", "方案", "主模型", "对齐模型", "状态")
        )
        self._align_table.itemSelectionChanged.connect(self._sync_hint)
        view_layout.addWidget(self._align_table)
        self._align_hint = caption(view, "")
        view_layout.addWidget(self._align_hint)
        layout.addWidget(view)

    def _build_run(self) -> None:
        card, layout = self.add_section(
            "运行",
            "对当前用户的全部条目跑一遍：只对对齐表里配了模型的数据类型发请求，跳过的会在下面说明。",
        )
        layout.addWidget(
            self._toolbar(
                card,
                primary_button(card, FluentIcon.TAG, "开始挂标签", self._start),
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
        「一键补全」也因为「已经有 id」而不重新登记。清掉之后回到「未登记：<方案 key>」，
        再点「一键补全」就能重建。
        """
        known = library.known_model_ids()
        if known is None:
            return
        purpose = align_tools.PURPOSE_LABEL
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
        self._rule_set = self._api.rules(reload=True)
        self._registered = library.registered_models()
        self._drop_dead_rows()
        self._minimum, self._maximum, self._merge = runner.option_values(self._ctx)
        fill_rule_table(self._rule_table, self._rule_set)
        fill_align_table(
            self._align_table,
            self._book.table().rows,
            registered=self._registered,
            factory=self._factory_map(),
            names=self._model_names(),
        )
        limit = self._maximum or "不限"
        self._options_label.setText(
            f"标签数量：最少 {self._minimum} 个、最多 {limit} 个；"
            f"同时按规则挂标签：{'开' if self._merge else '关'}（点「设置数量」当场改）"
        )
        self._scope_label.setText(self._scope_text())
        self._sync_hint()
        self._sync_rule_hint()

    def _scope_text(self) -> str:
        rows = len(items_sdk.list_items(user_id=items_sdk.current_user_id()))
        ready = len(self._book.table().ready_rows(registered=self._registered))
        return (
            f"当前用户：{items_sdk.current_user_id() or '默认'}；"
            f"库里有 {rows} 个条目；对齐表配好 {ready} 个数据类型；"
            f"启用 {len(self._rule_set.enabled)} 条规则。"
        )

    # ============================================================ 选择与提示
    def _current_datatype(self) -> str:
        row = self._align_table.currentRow()
        rows = self._book.table().rows
        if 0 <= row < len(rows):
            return rows[row].datatype
        return ""

    def _sync_hint(self) -> None:
        datatype = self._current_datatype()
        row = self._book.table().row(datatype) if datatype else None
        if row is None:
            self._align_hint.setText("选中一行可以看这个数据类型对齐到哪个模型。")
        else:
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
            purpose=align_tools.PURPOSE_LABEL,
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
            self._book = self._book.set_row(align_tools.PURPOSE_LABEL, result)
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
        self._book = self._book.reset_row(align_tools.PURPOSE_LABEL, datatype)
        self._save_align()

    def _selected_row(self) -> align_tools.AlignRow | None:
        """对齐表里当前选中的那一行。"""
        datatype = self._current_datatype()
        return self._book.table().row(datatype) if datatype else None

    def _factory_map(self) -> dict:
        """出厂方案：`{数据类型: AlignRow}`，给「系统方案」一列与编辑框的「启用系统方案」用。"""
        table = self._book.factory_table(align_tools.PURPOSE_LABEL)
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
        self._book = self._book.set_row(align_tools.PURPOSE_LABEL, update)
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
            toast_success(self, "已保存", "对齐表写进 .configs/autolabel.align.json。")
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
                prefix + "先点「一键使用」登记：" + "、".join(pending),
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
        """当场改标签数量（写进插件设置）。"""
        dialog = FormDialog(self.window(), title="标签数量", width=460)
        minimum_box = spin_box(dialog, value=self._minimum, minimum=1, maximum=50, suffix="个")
        maximum_box = spin_box(dialog, value=self._maximum, minimum=1, maximum=50, suffix="个")
        merge_box = check_box(dialog, text="规则命中的标签与模型标签合并", checked=self._merge)
        dialog.add_row("最少挂", minimum_box)
        dialog.add_row("最多挂", maximum_box)
        dialog.add_row("规则 + 模型", merge_box)
        dialog.add_hint(
            "打开合并：先按规则算一份、再让模型算一份，两份去重后一起挂（上下限按合并后的集合算）；"
            "关掉就只挂模型的标签。最多大于库里标签总数时以标签总数为准。"
        )
        dialog.set_buttons(yes="保存", cancel="取消")
        if not dialog.exec():
            return
        minimum = int(minimum_box.value())
        maximum = int(maximum_box.value())
        if maximum < minimum:
            maximum = minimum
        merge = bool(merge_box.isChecked())
        try:
            self._ctx.set_option("min_tags", minimum)
            self._ctx.set_option("max_tags", maximum)
            self._ctx.set_option("merge_rule_tags", merge)
        except Exception as exc:
            toast_error(self, "保存失败", str(exc))
            return
        self._minimum, self._maximum, self._merge = runner.option_values(self._ctx)
        limit = self._maximum or "不限"
        self._options_label.setText(
            f"标签数量：最少 {self._minimum} 个、最多 {limit} 个；"
            f"同时按规则挂标签：{'开' if self._merge else '关'}"
        )
        toast_success(self, "已保存", f"最少 {self._minimum} 个、最多 {limit} 个。")

    def _open_model_page(self) -> None:
        route = model_sdk.page_route()
        if route and ui_sdk.open_page(route):
            toast_info(self, "已打开模型页", "在模型页里下载权重、装运行环境。")
            return
        toast_warning(self, "打不开模型页", "没启用模型工具库插件？")

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
        book = self._book
        registered = self._registered
        rule_set = self._rule_set
        minimum, maximum, merge = self._minimum, self._maximum, self._merge
        if not book.table().ready_rows(registered=registered):
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
                plan = runner.plan_labels(
                    rows,
                    table=book.table(),
                    registered=registered,
                    rule_set=rule_set,
                    merge=merge,
                    minimum=minimum,
                    maximum=runner.effective_max(maximum, items_sdk.tag_names()),
                    reader=items_sdk.read_text,
                    on_problem=lambda message: self._ctx.log.info("对齐模型：{}", message),
                )
                if dry:
                    self._result = ("dry", plan, None)
                    return
                report = runner.run_labels(
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

        threading.Thread(target=work, daemon=True, name="auto-tag-model").start()
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
                f"预览完成：扫描 {plan.total} 个条目，规则命中 {len(plan.rule_tags)} 个，"
                f"会发 {len(plan.requests)} 个请求"
            )
            return
        report = payload
        self._panel.finish(runner.summary_text(report))
        if report.failed:
            toast_warning(self, "部分标签没写进去", runner.summary_text(report))
        elif report.cancelled:
            toast_info(self, "已取消", runner.summary_text(report))
        elif not report.matched:
            # 没命中 / 没产出标签时不能说「完成」：用户会以为标签挂好了。
            toast_warning(self, "没有标签可写", runner.summary_text(report))
        elif not report.written:
            toast_info(self, "没有新增标签", f"{runner.summary_text(report)}；这些标签条目上都有了。")
        else:
            toast_success(self, "挂标签完成", runner.summary_text(report))
        items_sdk.notify_changed()
        # 整批挂完后只刷新一次标签页：新建的标签（以及标签用量）当场可见
        items_sdk.notify_tags_changed()
        self._scope_label.setText(self._scope_text())

    def _show_preview(self, plan) -> None:
        report = runner.LabelReport(plan=plan, collected=runner.collect_labels(plan, ()))
        lines = list(runner.preview_rows(report, limit=PREVIEW_LIMIT))
        if plan.requests:
            lines.append("")
            lines.append(f"会向模型发 {len(plan.requests)} 个请求；规则命中的标签：{len(plan.rule_tags)} 个条目")
        self._preview_box.setPlainText("\n".join(lines))
