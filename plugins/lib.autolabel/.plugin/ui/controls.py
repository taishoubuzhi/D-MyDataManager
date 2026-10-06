"""自动标签 / 自动关键词插件共用的控件：进度面板、规则编辑弹窗、对齐表编辑弹窗。

界面一律走集成界面工具库（`dm_plugin.builtin.lib.ui.plugin`）里的工厂与弹窗外壳，
这里只有 PyQt6 的类型（布局改样式那种）与 `FluentIcon` 例外 —— 与模型插件同一条红线。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from PyQt6.QtWidgets import QWidget

from dm_plugin.builtin.lib.ui.plugin import (
    FormDialog,
    body_label,
    check_box,
    combo_box,
    fill_table,
    form_row,
    line_edit,
    progress_bar,
    push_button,
    status_label,
    strong_label,
    text_area,
    widget_column,
    widget_row,
)

from ..align import (
    DATATYPE_LABELS,
    PURPOSE_LABEL,
    PURPOSE_LABELS,
    AlignRow,
    normalize_datatype,
)
from ..rules import (
    FIELD_LABELS,
    FIELDS,
    KIND_LABELS,
    KINDS,
    KIND_MATCH,
    KIND_PROMPT,
    OP_LABELS,
    OPS,
    OP_IN,
    SOURCE_USER,
    Rule,
    RuleSet,
    split_pattern,
)

__all__ = [
    "AlignDialog",
    "ProgressPanel",
    "RuleDialog",
    "align_rows",
    "field_options",
    "fill_align_table",
    "fill_rule_table",
    "kind_options",
    "op_options",
    "rule_rows",
]


# --------------------------------------------------------------- 小工具
def _labeled(labels: Mapping[str, str], keys: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(labels.get(key, key) for key in keys), tuple(keys)


def kind_options():
    return _labeled(KIND_LABELS, KINDS)


def field_options():
    return _labeled(FIELD_LABELS, FIELDS)


def op_options():
    return _labeled(OP_LABELS, OPS)


def _blank_option(label: str, labels: Sequence[str], values: Sequence[str]):
    return (label,) + tuple(labels), ("",) + tuple(values)


# --------------------------------------------------------------- 进度面板
class ProgressPanel:
    """进度条 + 状态文字 + 取消按钮；页面把它 `addWidget(panel.widget)` 就行。"""

    def __init__(self, parent: QWidget | None = None, *, on_cancel=None) -> None:
        self.bar = progress_bar(parent, value=0)
        self.label = status_label(parent, "")
        self.cancel_button = push_button(parent, "取消", on_click=on_cancel)
        self.widget = widget_column(parent, self.bar, self.label, self.cancel_button)
        self.reset()

    @property
    def value(self) -> int:
        return int(self.bar.value())

    def reset(self) -> None:
        self.bar.setValue(0)
        self.label.setText("")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)

    def begin(self, text: str = "正在处理…") -> None:
        self.bar.setValue(0)
        self.label.setText(text)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)

    def set_progress(self, done: int, total: int) -> None:
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.label.setText(f"已完成 {done} / {total}")

    def set_status(self, text: str) -> None:
        self.label.setText(str(text))

    def finish(self, text: str = "完成") -> None:
        self.label.setText(str(text))
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)

    def fail(self, text: str) -> None:
        self.label.setText(str(text))
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)


# --------------------------------------------------------------- 规则编辑
class RuleDialog(FormDialog):
    """新增 / 修改一条规则；`dialog.exec()` 通过后用 `dialog.rule()` 取结果。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "编辑规则",
        rule: Rule | None = None,
        taken: Sequence[str] = (),
        kinds: Sequence[str] | None = None,
    ) -> None:
        """`kinds` 限定这个页面能选的规则类型：只给 `("match",)` 时不再出现「交模型判断」与提示词。"""
        super().__init__(parent, title=title, width=640, minimum_height=360)
        self._taken = {str(key) for key in taken if str(key)}
        if rule is not None:
            self._taken.discard(rule.key)
        self._rule = rule
        allowed = tuple(key for key in (kinds if kinds is not None else KINDS) if key in KINDS)
        self._allowed = allowed or KINDS
        existing = str(rule.kind) if rule is not None else ""
        self._locked_kind = existing if existing and existing not in self._allowed else ""
        self._show_kind = len(self._allowed) > 1 and not self._locked_kind
        self._key = line_edit(self, text=rule.key if rule else "", placeholder="英文小写，如 suffix.pdf")
        self._name = line_edit(self, text=rule.name if rule else "", placeholder="显示名称")
        self._enabled = check_box(self, text="启用这条规则", checked=bool(rule.enabled) if rule else True)

        kind_labels, kind_values = _labeled(KIND_LABELS, self._allowed)
        self._kind = combo_box(
            self,
            items=kind_labels,
            data=kind_values,
            value=(rule.kind if rule else KIND_MATCH),
            on_change=lambda _value: self._sync(),
        )
        field_labels, field_values = field_options()
        self._field = combo_box(
            self,
            items=field_labels,
            data=field_values,
            value=(rule.field if rule else "suffix"),
        )
        op_labels, op_values = op_options()
        self._op = combo_box(
            self,
            items=op_labels,
            data=op_values,
            value=(rule.op if rule else OP_IN),
        )
        self._pattern = line_edit(self, text=rule.pattern if rule else "", placeholder="doc,docx 或正则")
        self._tags = line_edit(
            self,
            text="、".join(rule.tags) if rule else "",
            placeholder="挂哪些标签，多个用顿号隔开",
        )
        self._prompt = None
        if KIND_PROMPT in self._allowed:
            self._prompt = text_area(
                self,
                text=rule.prompt if rule else "",
                read_only=False,
            )
            self._prompt.setMinimumHeight(90)

        self.add_row("规则标识", self._key)
        self.add_row("名称", self._name)
        if self._show_kind:
            self.add_row("类型", self._kind)
        elif self._locked_kind:
            self.add_row(
                "类型",
                body_label(
                    self,
                    f"{KIND_LABELS.get(self._locked_kind, self._locked_kind)}（本页不调用模型，改不了）",
                ),
            )
        if not self._locked_kind:
            self.add_row("匹配字段", self._field)
            self.add_row("匹配方式", self._op)
            self.add_row("匹配内容", self._pattern)
        self.add_row("要挂的标签", self._tags)
        if self._prompt is not None:
            self.add_row("提示词（交模型判断时用）", self._prompt)
        self.add_widget(self._enabled)
        self._hint = self.add_hint("")
        self.set_buttons(yes="保存", cancel="取消")
        self._sync()

    # ---------------------------------------------------------- 联动
    def _sync(self) -> None:
        is_prompt = self._prompt is not None and self._kind.currentData() == KIND_PROMPT
        if self._prompt is not None:
            self._prompt.setEnabled(is_prompt)
        self._pattern.setEnabled(not is_prompt)
        self._op.setEnabled(not is_prompt)
        self._field.setEnabled(not is_prompt)
        if self._locked_kind:
            self._hint.setText("这条规则是「交模型判断」，本页不跑模型；请到会调用模型的那一页改。")
            self.yesButton.setEnabled(False)
            return
        current = self.rule()
        problems = list(current.problems)
        key = current.key.strip()
        if not key:
            problems.insert(0, "规则标识不能为空")
        elif key in self._taken:
            problems.insert(0, f"规则标识已存在：{key}")
        self._hint.setText("；".join(problems) if problems else "这条规则看起来没问题")
        self.yesButton.setEnabled(not problems)

    def rule(self) -> Rule:
        """当前表单内容（不校验唯一性，`problems` 里会提示）。"""
        return Rule(
            key=self._key.text().strip(),
            name=self._name.text().strip(),
            kind=self._locked_kind or str(self._kind.currentData() or KIND_MATCH),
            enabled=bool(self._enabled.isChecked()),
            field=str(self._field.currentData() or "suffix"),
            op=str(self._op.currentData() or OP_IN),
            pattern=self._pattern.text(),
            tags=split_pattern(self._tags.text()),
            prompt=self._prompt.toPlainText() if self._prompt is not None else "",
            source=(self._rule.source if self._rule is not None else SOURCE_USER),
        )


# --------------------------------------------------------------- 对齐表编辑
def _model_choices(
    model_labels: Sequence[str],
    model_values: Sequence[str],
    template_labels: Sequence[str],
    template_values: Sequence[str],
    *,
    blank: str,
    registered: Mapping[str, str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """一个下拉装两类来源：本地下过的模型（`model:` model_id）与还没登记的出厂方案（`preset:` 方案 key）。

    已经登记成模型的出厂方案不再以「系统方案」形态出现——同一个模型只该有一个名字：要跟随
    系统方案就勾上面的复选框，要自己挑就在「已登记」里按模型名挑。`registered` 是
    `{方案 key: 已登记 model_id}`。
    """
    items = [blank]
    values = [""]
    for label, value in zip(template_labels, template_values):
        if str((registered or {}).get(str(value), "")):
            continue  # 已经登记成模型了：改由「已登记」组按模型名出现
        items.append(f"{label}（系统方案）")
        values.append(f"preset:{value}")
    for label, value in zip(model_labels, model_values):
        items.append(f"{label}（已登记）")
        values.append(f"model:{value}")
    return tuple(items), tuple(values)


def _split_choice(value: object) -> tuple[str, str]:
    """把下拉里的值拆回 `(model_id, template)`。"""
    raw = str(value or "")
    if raw.startswith("preset:"):
        return "", raw[len("preset:") :]
    if raw.startswith("model:"):
        return raw[len("model:") :], ""
    return "", ""


class AlignDialog(FormDialog):
    """改一个数据类型的模型选择：勾上就用系统方案，不勾就自己从已登记模型里挑。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        purpose: str = PURPOSE_LABEL,
        row: AlignRow | None = None,
        preset: AlignRow | None = None,
        model_labels: Sequence[str] = (),
        model_values: Sequence[str] = (),
        template_labels: Sequence[str] = (),
        template_values: Sequence[str] = (),
        registered: Mapping[str, str] | None = None,
        names: Mapping[str, str] | None = None,
    ) -> None:
        datatype = normalize_datatype(row.datatype if row is not None else "")
        super().__init__(
            parent,
            title=f"{PURPOSE_LABELS.get(purpose, purpose)} · {DATATYPE_LABELS.get(datatype, datatype)}",
            width=640,
            minimum_height=320,
        )
        self._purpose = purpose
        self._datatype = datatype
        self._preset_row = preset
        preset_model = str(getattr(preset, "template", "") or getattr(preset, "model_id", "") or "")
        preset_align = str(
            getattr(preset, "align_template", "") or getattr(preset, "align_model", "") or ""
        )
        self._preset_model_id = preset_model
        self._preset_align_id = preset_align
        self._names = {str(key): str(value) for key, value in (names or {}).items()}

        def display(model_id: str) -> str:
            """模型 id → 登记时起的名字（没有就退回 id 本身）。"""
            return self._names.get(model_id, "") or model_id

        def preset_display(key: str) -> str:
            """系统方案里的方案 key → 展示文字：已登记就显示登记模型的名字。"""
            if not key:
                return ""
            model_id = str((registered or {}).get(key, ""))
            return display(model_id) if model_id else f"{key}（未登记）"

        # 系统方案：只读展示，勾上就强制用它
        self._system = check_box(
            self,
            text="启用系统方案（强制使用系统规定的模型与对齐模型）",
            checked=bool(row.use_preset) if row is not None else True,
        )
        self._system_value = combo_box(
            self,
            items=(
                f"系统方案：{preset_display(preset_model)}"
                if preset_model
                else "（系统没给这个类型配模型）",
            ),
            data=(preset_model,),
            value=preset_model,
        )
        self._system_align_value = combo_box(
            self,
            items=(
                f"系统方案：{preset_display(preset_align)}"
                if preset_align
                else "（系统没给这个类型配对齐模型）",
            ),
            data=(preset_align,),
            value=preset_align,
        )

        # 手动：主模型 / 对齐模型各一个下拉，选项来自「系统方案已注册 + 本地已登记」
        labels, values = _model_choices(
            model_labels, model_values, template_labels, template_values,
            blank="（不用，这个数据类型跳过）",
            registered=registered,
        )
        current_model = str(row.template or "") if row is not None and row.template else str(row.model_id or "") if row is not None else ""
        current_prefix = "preset:" if row is not None and row.template else "model:"
        self._model = combo_box(
            self, items=labels, data=values, value=(f"{current_prefix}{current_model}" if current_model else "")
        )
        labels, values = _model_choices(
            model_labels, model_values, template_labels, template_values,
            blank="（不用对齐模型）",
            registered=registered,
        )
        current_align = str(row.align_template or "") if row is not None and row.align_template else str(row.align_model or "") if row is not None else ""
        current_align_prefix = "preset:" if row is not None and row.align_template else "model:"
        self._align = combo_box(
            self, items=labels, data=values,
            value=(f"{current_align_prefix}{current_align}" if current_align else ""),
        )
        self._enabled = check_box(self, text="启用这个数据类型", checked=bool(row.enabled) if row else True)
        self._note = line_edit(self, text=row.note if row else "", placeholder="备注（给自己看的）")

        self.add_widget(
            widget_row(
                self,
                strong_label(self, f"数据类型：{DATATYPE_LABELS.get(datatype, datatype)}"),
                body_label(self, "主模型负责生成，对齐模型先把图片描述 / 音频转写出来"),
            )
        )
        self.add_widget(self._system)
        self._system_model_row = widget_row(
            self, strong_label(self, "主模型"), self._system_value
        )
        self._system_align_row = widget_row(
            self, strong_label(self, "对齐模型"), self._system_align_value
        )
        self.add_widget(self._system_model_row)
        self.add_widget(self._system_align_row)
        self._model_row = widget_row(self, strong_label(self, "主模型"), self._model)
        self._align_row = widget_row(self, strong_label(self, "对齐模型"), self._align)
        self.add_widget(self._model_row)
        self.add_widget(self._align_row)
        self.add_row("备注", self._note)
        self.add_widget(self._enabled)
        self.add_hint(
            "勾上「启用系统方案」时模型与对齐模型都用系统规定的（改不了）；"
            "取消勾选后可以自己挑，「系统方案」组是出厂已注册的模型，「已登记」组是你本地下过的模型。"
        )
        self.set_buttons(yes="保存", cancel="取消")

        if not (preset_model or preset_align):
            self._system.setEnabled(False)
            self._system.setChecked(False)
        self._system.toggled.connect(self._sync_system)
        self._sync_system()

    def _sync_system(self) -> None:
        use_system = bool(self._system.isChecked())
        self._system_model_row.setVisible(use_system)
        self._system_align_row.setVisible(use_system)
        self._model_row.setVisible(not use_system)
        self._align_row.setVisible(not use_system)

    def row_result(self) -> AlignRow:
        if self._system.isChecked():
            return AlignRow(
                datatype=self._datatype,
                model_id="",
                template=self._preset_model_id,
                align_model="",
                align_template=self._preset_align_id,
                enabled=bool(self._enabled.isChecked()),
                note=self._note.text().strip(),
                use_preset=True,
            )
        model_id, template = _split_choice(self._model.currentData())
        align_model, align_template = _split_choice(self._align.currentData())
        return AlignRow(
            datatype=self._datatype,
            model_id=model_id,
            template=template,
            align_model=align_model,
            align_template=align_template,
            enabled=bool(self._enabled.isChecked()),
            note=self._note.text().strip(),
            use_preset=False,
        )


# --------------------------------------------------------------- 表格填充
def rule_rows(rule_set: RuleSet) -> list[list[str]]:
    """规则表（名称 / 类型 / 匹配 / 标签 / 状态）。"""
    rows: list[list[str]] = []
    for rule in rule_set.rules:
        if not rule.enabled:
            state = "已关闭"
        elif rule.problems:
            state = rule.problems[0]
        elif rule.kind == KIND_PROMPT:
            state = "交模型判断"
        else:
            state = "匹配"
        rows.append([rule.title, rule.kind_label, rule.field_text, rule.tag_text or "—", state])
    return rows


def preset_align_text(row: AlignRow | None) -> str:
    """只读的「系统方案」文字：主模型（+ 对齐模型），来自出厂 `data/align.json`。"""
    if row is None:
        return "—"
    main = row.template or row.model_id or "未设置"
    extra = row.align_template or row.align_model
    return f"{main}　前置：{extra}" if extra else main


def align_rows(
    rows: Sequence[AlignRow],
    *,
    registered: Mapping[str, str] | None = None,
    factory: Mapping[str, AlignRow] | None = None,
    names: Mapping[str, str] | None = None,
) -> list[list[str]]:
    """对齐表（数据类型 / 方案 / 主模型 / 对齐模型 / 状态）。

    「方案」一列显示的就是**这一行的设置本身**：`启用` = 勾了「启用系统方案」、`自定义` =
    自己挑过、`已停用` = 这一行被关掉（优先显示）。不能用「内容与出厂相同」去推断跟随——
    用户取消勾选后如果选的还是系统方案里的同一个模型，内容当然与出厂相同，但设置就是「自定义」。
    「主模型」「对齐模型」两列按**这一行实际会用的模型**显示：没登记的写
    「未登记：<预定义方案 key>」，让人看见系统配的是哪个方案；状态由 `AlignRow.status_text()`
    给（缺对齐模型也算没就绪——否则图片缺 clip 会显示「就绪」）。
    """
    def label(model_id: str) -> str:
        """模型 id → 登记时起的名字：表格和编辑框显示同一个名字，别一会儿 id 一会儿方案 key。"""
        return str((names or {}).get(model_id, "")) or model_id

    table: list[list[str]] = []
    for row in rows:
        model = row.resolved(registered=registered)
        if not model:
            model = f"未登记：{row.template}" if row.template else row.target_text
        else:
            model = label(model)
        align = row.resolved_align(registered=registered)
        if not align:
            align = f"未登记：{row.align_template}" if row.align_template else row.align_text
        else:
            align = label(align)
        follows = bool(row.use_preset)
        if not row.enabled:
            plan_text = "已停用"  # 「启用这个数据类型」在表格上也看得见
        else:
            plan_text = "启用" if follows else "自定义"
        table.append([
            row.datatype_label,
            plan_text,
            model,
            align,
            row.status_text(registered=registered),
        ])
    return table


def fill_rule_table(table, rule_set: RuleSet) -> None:
    fill_table(table, rule_rows(rule_set), headers=("名称", "类型", "匹配", "标签", "状态"))


def fill_align_table(
    table,
    rows: Sequence[AlignRow],
    *,
    registered: Mapping[str, str] | None = None,
    factory: Mapping[str, AlignRow] | None = None,
    names: Mapping[str, str] | None = None,
) -> None:
    fill_table(
        table,
        align_rows(rows, registered=registered, factory=factory, names=names),
        headers=("数据类型", "方案", "主模型", "对齐模型", "状态"),
    )
