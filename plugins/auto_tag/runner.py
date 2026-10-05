"""任务 3（`auto_tag`）的执行逻辑：规则方案 + 模型方案 + 合并写库。

这里不碰界面、不碰数据库：只依赖 `app.sdk.items` / `app.sdk.models` 与共享库
`lib.autolabel`（规则模型、数据类型对齐表、批量管线）。规则方案与任务 2
（`auto_tag.rule`）读的是共享库里的同一份规则集，所以两套方案的结果可以
直接合并。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable, Iterable, Mapping, Sequence

from app.sdk import items as items_sdk
from app.sdk.console import console_for
from app.sdk.models import BatchResult

from dm_plugin.lib.autolabel import pipeline
from dm_plugin.lib.autolabel.align import PURPOSE_LABEL, AlignTable
from dm_plugin.lib.autolabel.rules import (
    FIELD_TEXT,
    KIND_MATCH,
    KIND_PROMPT,
    Rule,
    RuleSet,
)

#: 条目的正文最多读多少字（给 `text` 字段的规则判断用）
TEXT_LIMIT = 2000
DEFAULT_MINIMUM = 1
DEFAULT_MAXIMUM = 5
MIN_OPTION = "min_tags"
MAX_OPTION = "max_tags"
MERGE_OPTION = "merge_rule_tags"

#: 控制台门面：挂标签的结果要能在程序控制台看到（「说成功却没挂上」就靠这行排查）
_console = console_for("auto_tag")


# ----------------------------------------------------------------- 选项
def option_values(ctx) -> tuple[int, int, bool]:
    """读插件选项：`(最少, 最多, 是否合并规则标签)`，并把明显不合理的值夹回来。"""
    try:
        minimum = int(ctx.option(MIN_OPTION, DEFAULT_MINIMUM) or DEFAULT_MINIMUM)
    except (TypeError, ValueError):
        minimum = DEFAULT_MINIMUM
    try:
        maximum = int(ctx.option(MAX_OPTION, DEFAULT_MAXIMUM) or 0)
    except (TypeError, ValueError):
        maximum = 0
    minimum = max(1, minimum)
    if maximum and maximum < minimum:
        maximum = minimum
    return minimum, maximum, bool(ctx.option(MERGE_OPTION, False))


def effective_max(maximum: int, known: Sequence[str]) -> int:
    """3.5：上限不允许超过库里现有标签总数。"""
    if maximum and known and len(known) < maximum:
        return len(known)
    return maximum


# ----------------------------------------------------------------- 规则
@dataclass(frozen=True)
class ImportFile:
    """导入前的一个文件：只有路径，够规则引擎取名字/后缀/类型。"""

    file_path: str

    @property
    def id(self) -> None:
        return None

    @property
    def name(self) -> str:
        return PurePath(self.file_path or "").name

    @property
    def suffix(self) -> str:
        return PurePath(self.file_path or "").suffix.lower().lstrip(".")

    @property
    def type(self) -> str:
        return ""


def tags_for_paths(paths: Iterable[object], rule_set: RuleSet) -> tuple[str, ...]:
    """导入前按路径匹配规则标签（顺序 = 规则顺序，去重）。"""
    names: list[str] = []
    for path in paths or ():
        for name in rule_set.match_tags(ImportFile(file_path=str(path))):
            if name not in names:
                names.append(name)
    return tuple(names)


def text_rules(rule_set: RuleSet) -> tuple[Rule, ...]:
    """启用、且需要读正文的规则（不管规则类型，判断这些条目才值得去读文件）。"""
    return tuple(rule for rule in rule_set.enabled if rule.field == FIELD_TEXT)


def prompt_rules(rule_set: RuleSet) -> tuple[Rule, ...]:
    """启用、且要交给模型判断的规则。"""
    return tuple(rule for rule in rule_set.enabled if rule.kind == KIND_PROMPT)


def read_texts(items: Iterable[object], rule_set: RuleSet, *, reader=None, text_limit: int = TEXT_LIMIT) -> dict[str, str]:
    """`{条目 id: 正文}`；没有正文规则或读不出来就留空。"""
    if reader is None or not text_rules(rule_set):
        return {}
    texts: dict[str, str] = {}
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        try:
            value = reader(key, text_limit)
        except Exception:
            continue
        if isinstance(value, tuple):
            text = str(value[0] or "") if value else ""
        else:
            text = str(value or "")
        if text:
            texts[key] = text
    return texts


def rule_tags(items: Iterable[object], rule_set: RuleSet, *, texts: Mapping[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """规则方案命中的标签：`{条目 id: (标签, ...)}`（没命中的不出现）。"""
    table = texts or {}
    hit: dict[str, tuple[str, ...]] = {}
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        names = tuple(rule_set.match_tags(entry, text=table.get(key, "")))
        if names:
            hit[key] = names
    return hit


def prompt_tags_of(entry, rule_set: RuleSet, *, text: str = "") -> tuple[str, ...]:
    """单个条目命中的 `prompt` 规则自带标签（3.6：这些标签参与合并）。"""
    names: list[str] = []
    for rule in prompt_rules(rule_set):
        if not rule.matches_fields(entry, text=text):
            continue
        for name in rule.tags:
            if name not in names:
                names.append(name)
    return tuple(names)


def prompt_tags(items: Iterable[object], rule_set: RuleSet, *, texts: Mapping[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """命中的 `prompt` 规则自带的标签：`{条目 id: (标签, ...)}`。"""
    table = texts or {}
    hit: dict[str, tuple[str, ...]] = {}
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        names = prompt_tags_of(entry, rule_set, text=table.get(key, ""))
        if names:
            hit[key] = names
    return hit


def prompt_extras(entry, rule_set: RuleSet, *, text: str = "") -> tuple[str, ...]:
    """条目命中的 `prompt` 规则文本，会追加到模型提示词后面。"""
    return tuple(
        rule.prompt.strip()
        for rule in prompt_rules(rule_set)
        if rule.prompt.strip() and rule.matches_fields(entry, text=text)
    )


# ----------------------------------------------------------------- 计划
@dataclass(frozen=True)
class LabelPlan:
    """一次运行的计划：模型请求 + 规则标签（合并用）。"""

    pipeline: pipeline.PipelinePlan
    table: AlignTable = field(default_factory=lambda: AlignTable(purpose=PURPOSE_LABEL))
    minimum: int = DEFAULT_MINIMUM
    maximum: int = DEFAULT_MAXIMUM
    merge: bool = False
    rule_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    prompt_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    entries: int = 0

    @property
    def purpose_label(self) -> str:
        return self.pipeline.purpose_label

    @property
    def total(self) -> int:
        """本次扫过的条目数（有请求时等于排进请求的条数，规则方案等于条目总数）。"""
        return self.pipeline.total if self.pipeline.items else self.entries

    @property
    def items(self) -> tuple:
        return self.pipeline.items

    @property
    def requests(self) -> tuple:
        return self.pipeline.requests

    @property
    def skipped(self) -> tuple:
        return self.pipeline.skipped

    def item_of(self, key: str):
        return self.pipeline.item_of(key)

    def skip_reason(self, key: str) -> str:
        return self.pipeline.skip_reason(key)


def plan_labels(
    items: Sequence[object],
    *,
    table: AlignTable | None = None,
    registered: Mapping[str, str] | None = None,
    rule_set: RuleSet | None = None,
    merge: bool = False,
    minimum: int = DEFAULT_MINIMUM,
    maximum: int = DEFAULT_MAXIMUM,
    explicit_model: str = "",
    reader=None,
    text_limit: int = TEXT_LIMIT,
    task: str = pipeline.DEFAULT_TASK,
    on_problem: Callable[[str], None] | None = None,
) -> LabelPlan:
    """模型方案：按数据类型对齐模型，排成一批请求；合并开关打开时顺带算规则标签。"""
    book = table or AlignTable(purpose=PURPOSE_LABEL)
    entries = tuple(items or ())
    rules = rule_set or RuleSet()
    texts = read_texts(entries, rules, reader=reader, text_limit=text_limit)
    # 先让对齐模型（图片 → 图像描述、音频 → 语音识别）读一遍，把结果拼进提示词
    aligned = pipeline.align_texts(
        entries,
        table=book,
        registered=registered,
        purpose=PURPOSE_LABEL,
        on_problem=on_problem,
    )

    def prompt_builder(entry, datatype):
        text = texts.get(str(getattr(entry, "id", "") or ""), "")
        prompt = pipeline.default_prompt(
            entry,
            datatype,
            purpose=PURPOSE_LABEL,
            minimum=minimum,
            maximum=maximum,
            text=text,
            align_text=aligned.get(str(getattr(entry, "id", "") or ""), ""),
        )
        extras = prompt_extras(entry, rules, text=text)
        if extras:
            prompt = "\n".join([prompt, "补充要求：", *[f"- {line}" for line in extras]])
        return prompt

    def payload_builder(entry, prompt):
        payload = pipeline.default_payload(entry, prompt)
        key = str(getattr(entry, "id", "") or "")
        names = prompt_tags_of(entry, rules, text=texts.get(key, ""))
        if names:
            payload["_tags"] = list(names)
        return payload

    planned = pipeline.plan(
        entries,
        purpose=PURPOSE_LABEL,
        table=book,
        registered=registered,
        prompt_builder=prompt_builder,
        payload_builder=payload_builder,
        task=task,
        explicit_model=explicit_model,
    )
    return LabelPlan(
        pipeline=planned,
        table=book,
        minimum=minimum,
        maximum=maximum,
        merge=merge,
        rule_tags=rule_tags(entries, rules, texts=texts) if merge else {},
        prompt_tags=prompt_tags(entries, rules, texts=texts) if merge else {},
        entries=len(entries),
    )


def plan_rule_only(
    items: Sequence[object],
    rule_set: RuleSet,
    *,
    reader=None,
    text_limit: int = TEXT_LIMIT,
    purpose: str = PURPOSE_LABEL,
) -> LabelPlan:
    """规则方案：不发模型请求，命中的标签直接当作「已收齐」的结果。"""
    entries = tuple(items or ())
    texts = read_texts(entries, rule_set, reader=reader, text_limit=text_limit)
    return LabelPlan(
        pipeline=pipeline.PipelinePlan(purpose=purpose, items=(), requests=(), skipped=(), by_key={}),
        table=AlignTable(purpose=purpose),
        minimum=1,
        maximum=0,
        merge=True,
        rule_tags=rule_tags(entries, rule_set, texts=texts),
        prompt_tags={},
        entries=len(entries),
    )


# ----------------------------------------------------------------- 结果
def collect_labels(
    plan_obj: LabelPlan,
    results: Sequence[BatchResult],
    *,
    parse: Callable[[object], Sequence[str]] | None = None,
    known: Sequence[str] = (),
    available: Sequence[str] = (),
) -> dict[str, tuple[str, ...]]:
    """`{条目 id: (标签, ...)}`：模型结果（受上下限约束）+ 规则标签（合并开关打开时）。"""
    merged: dict[str, list[str]] = {}
    if plan_obj.merge:
        for source in (plan_obj.rule_tags, plan_obj.prompt_tags):
            for key, names in source.items():
                merged.setdefault(key, []).extend(names)
    for key, names in pipeline.collect(
        plan_obj.pipeline,
        results,
        parse=parse,
        minimum=plan_obj.minimum,
        maximum=plan_obj.maximum,
        available=available,
        known=known,
    ).items():
        merged.setdefault(key, []).extend(names)
    collected: dict[str, tuple[str, ...]] = {}
    for key, names in merged.items():
        picked = pipeline.clamp(
            pipeline.merge_names(names),
            minimum=plan_obj.minimum,
            maximum=plan_obj.maximum,
            available=available,
        )
        if picked:
            collected[key] = picked
    return collected


@dataclass(frozen=True)
class LabelReport:
    """一次运行的账：写进去多少、哪些标签失败、是否被取消。"""

    plan: LabelPlan
    results: tuple = ()
    collected: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    written: int = 0
    failed: tuple = ()
    cancelled: bool = False

    @property
    def matched(self) -> tuple[str, ...]:
        return tuple(key for key, names in self.collected.items() if names)

    @property
    def tag_names(self) -> tuple[str, ...]:
        return pipeline.merge_names(*self.collected.values())

    @property
    def groups(self) -> dict[str, tuple[str, ...]]:
        groups: dict[str, list[str]] = {}
        for key, names in self.collected.items():
            for name in names:
                groups.setdefault(name, []).append(key)
        return {name: tuple(keys) for name, keys in groups.items()}

    @property
    def skipped(self) -> tuple:
        return self.plan.skipped

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def write_tags(
    collected: Mapping[str, tuple[str, ...]],
    *,
    api=None,
    cancel=None,
) -> tuple[int, tuple[tuple[str, str], ...]]:
    """按标签分组批量写；返回 `(真实写入数, ((标签, 错误), ...))`。"""
    client = api or items_sdk
    groups: dict[str, list[str]] = {}
    for key, names in collected.items():
        for name in names:
            groups.setdefault(name, []).append(str(key))
    written = 0
    failed: list[tuple[str, str]] = []
    for name, keys in groups.items():
        if cancel is not None and cancel():
            break
        try:
            written += int(client.tag_items(keys, [name]) or 0)
        except Exception as exc:
            failed.append((name, str(exc)))
    return written, tuple(failed)


def _matched_names(plan_obj, keys: Iterable, limit: int = 5) -> str:
    """命中条目的名字，最多列 limit 个（给控制台日志用：单条目挂载就是那一条的名字）。"""
    labels: list[str] = []
    for key in keys:
        entry = plan_obj.item_of(key) if hasattr(plan_obj, "item_of") else None
        name = str(getattr(getattr(entry, "item", None), "name", "") or "")
        labels.append(name or str(key))
    if not labels:
        return ""
    if len(labels) > limit:
        return "、".join(labels[:limit]) + f" 等 {len(labels)} 个"
    return "、".join(labels)


def run_labels(
    plan_obj: LabelPlan,
    *,
    api=None,
    progress=None,
    cancel=None,
    max_workers: int | None = None,
    parse: Callable[[object], Sequence[str]] | None = None,
    known: Sequence[str] = (),
    available: Sequence[str] = (),
) -> LabelReport:
    """跑模型（或只跑规则），把标签写进库。"""
    results = pipeline.run(
        plan_obj.pipeline,
        on_progress=progress,
        cancel=cancel,
        max_workers=max_workers,
    )
    collected = collect_labels(plan_obj, results, parse=parse, known=known, available=available)
    written, failed = write_tags(collected, api=api, cancel=cancel)
    stopped = cancel is not None and bool(cancel())
    pending = pipeline.pending_keys(plan_obj.pipeline, results)
    names = _matched_names(plan_obj, collected)
    if failed:
        _console.warning(
            f"自动挂标签：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
            f"写入 {written} 个标签，{len(failed)} 个标签没写进去（{failed[0][0]}：{failed[0][1]}）"
        )
    elif not collected:
        _console.info(
            f"自动挂标签：扫描 {plan_obj.total} 个条目，没有标签可写"
            "（规则没命中，或模型方案没给出标签）"
        )
    elif written:
        _console.info(
            f"自动挂标签：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
            f"写入 {written} 个标签"
        )
    else:
        _console.info(
            f"自动挂标签：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
            "但这些标签条目上都有了，没有新增"
        )
    return LabelReport(
        plan=plan_obj,
        results=tuple(results),
        collected=collected,
        written=written,
        failed=failed,
        cancelled=bool(pending) and stopped,
    )


def summary_text(report: LabelReport) -> str:
    parts = [f"扫描 {report.plan.total} 个条目", f"命中 {len(report.matched)} 个", f"写入 {report.written} 个标签"]
    if report.plan.skipped:
        parts.append(f"跳过 {len(report.plan.skipped)} 个")
    if report.failed:
        parts.append(f"{len(report.failed)} 个标签没写进去")
    if report.cancelled:
        parts.append("已取消")
    return "，".join(parts)


def preview_rows(report: LabelReport, *, limit: int = 200) -> tuple[str, ...]:
    """预览/结果列表：`名称 ← 标签1、标签2`。"""
    lines: list[str] = []
    for key, names in report.collected.items():
        if len(lines) >= limit:
            lines.append(f"……还有 {len(report.collected) - limit} 个条目，只显示前 {limit} 个")
            break
        item = report.plan.item_of(key)
        label = getattr(getattr(item, "item", None), "name", "") or key
        lines.append(f"{label}  ←  {'、'.join(names)}")
    if not lines:
        lines.append("没有条目命中。")
    for entry, reason in report.plan.skipped:
        name = getattr(entry, "name", "") or getattr(entry, "id", "") or "条目"
        lines.append(f"跳过 {name}：{reason}")
    return tuple(lines)
