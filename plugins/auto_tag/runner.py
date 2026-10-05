"""自动标签（规则 + 模型）的执行逻辑：两套各出一份标签，合并后写库。

只依赖 `app.sdk.items` / `app.sdk.models` 与共享库 `lib.autolabel`（规则集、数据类型对齐表、
批量管线）；不碰界面、不碰数据库。规则集就是 `auto_tag.rule` 用的那份 `.configs/autolabel.rules.json`：
两个插件在清单里互指 `conflicts`、不会同时启用，所以不存在「两处同时跑」。

挂一次 = 规则标签 ∪ 模型标签（去重后受上下限约束）；两边给出的新标签都会先建进标签库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from app.sdk import items as items_sdk
from app.sdk.console import console_for
from app.sdk.models import BatchResult

from dm_plugin.lib.autolabel import pipeline
from dm_plugin.lib.autolabel.align import PURPOSE_LABEL, AlignTable
from dm_plugin.lib.autolabel.rules import FIELD_TEXT, KIND_MATCH, RuleSet

DEFAULT_MINIMUM = 1
DEFAULT_MAXIMUM = 5
DEFAULT_MERGE = True
MIN_OPTION = "min_tags"
MAX_OPTION = "max_tags"
MERGE_OPTION = "merge_rule_tags"

#: 规则要匹配「文件内容」时最多读多少字
TEXT_LIMIT = 2000

#: 控制台门面：挂标签的结果要能在程序控制台看到（「说成功却没挂上」就靠这行排查）
_console = console_for("auto_tag")


# ----------------------------------------------------------------- 选项
def option_values(ctx) -> tuple[int, int, bool]:
    """读插件选项：`(最少, 最多, 是否同时按规则挂)`，并把明显不合理的值夹回来。"""
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
    return minimum, maximum, bool(ctx.option(MERGE_OPTION, DEFAULT_MERGE))


def effective_max(maximum: int, known: Sequence[str]) -> int:
    """3.5：上限不允许超过库里现有标签总数。"""
    if maximum and known and len(known) < maximum:
        return len(known)
    return maximum


# ----------------------------------------------------------------- 规则
def _read_text(reader, item_id, limit: int) -> str:
    """`app.sdk.items.read_text()` 返回 `(正文, 编码, 是否截断)`，这里只取正文。"""
    value = reader(item_id, limit)
    if isinstance(value, (tuple, list)):
        return str(value[0] or "") if value else ""
    return str(value or "")


def _text_of(entry, reader, *, need: bool, limit: int) -> str:
    """要判正文就现读；读不到只记一条 warning，不让整条失败。"""
    if not need or reader is None:
        return ""
    try:
        return _read_text(reader, getattr(entry, "id", None), limit)
    except Exception as exc:
        _console.warning(f"自动挂标签：读正文失败（{exc}），这条的「文件内容」规则跳过")
        return ""


def rule_tags(
    items: Sequence[object],
    rule_set: RuleSet | None,
    *,
    reader=None,
    text_limit: int = TEXT_LIMIT,
) -> dict[str, tuple[str, ...]]:
    """`{条目 id: (规则命中的标签, ...)}` —— 只看 `match` 规则。"""
    if rule_set is None:
        return {}
    need_text = any(
        rule.field == FIELD_TEXT and rule.kind == KIND_MATCH for rule in rule_set.enabled
    )
    hit: dict[str, tuple[str, ...]] = {}
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        text = _text_of(entry, reader, need=need_text, limit=text_limit)
        names = tuple(rule_set.match_tags(entry, text=text))
        if names:
            hit[key] = names
    return hit


def prompt_hits(
    items: Sequence[object],
    rule_set: RuleSet | None,
    *,
    reader=None,
    text_limit: int = TEXT_LIMIT,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """提示词规则的两份产物：`({条目 id: 要挂的标签}, {条目 id: 补充要求})`。"""
    if rule_set is None:
        return {}, {}
    need_text = any(rule.field == FIELD_TEXT for rule in rule_set.enabled)
    tags: dict[str, tuple[str, ...]] = {}
    hints: dict[str, tuple[str, ...]] = {}
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        text = _text_of(entry, reader, need=need_text, limit=text_limit)
        for rule in rule_set.prompt_rules(entry, text=text):
            names = tuple(getattr(rule, "tags", ()) or ())
            if names:
                tags[key] = tuple(dict.fromkeys(tags.get(key, ()) + names))
            note = str(getattr(rule, "prompt", "") or "").strip()
            if note:
                hints[key] = tuple(dict.fromkeys(hints.get(key, ()) + (note,)))
    return tags, hints


# ----------------------------------------------------------------- 计划
@dataclass(frozen=True)
class LabelPlan:
    """一次运行的计划：模型请求 +（合并开关打开时）规则标签。"""

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
        """本次扫过的条目数（有请求时等于排进请求的条数，没有请求时等于条目总数）。"""
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
    task: str = pipeline.DEFAULT_TASK,
    reader=None,
    text_limit: int = TEXT_LIMIT,
    on_problem: Callable[[str], None] | None = None,
) -> LabelPlan:
    """排一次运行：规则标签 + 模型请求。

    模型那侧先让对齐模型（图片 → 图像描述、音频 → 语音识别）读一遍，把结果作为「对齐信息」
    拼进提示词；提示词规则给出的「补充要求」也拼进同一个提示词。
    """
    book = table or AlignTable(purpose=PURPOSE_LABEL)
    entries = tuple(items or ())
    rules_hit = rule_tags(entries, rule_set, reader=reader, text_limit=text_limit) if merge else {}
    prompt_hit, hints = (
        prompt_hits(entries, rule_set, reader=reader, text_limit=text_limit)
        if merge
        else ({}, {})
    )
    aligned = pipeline.align_texts(
        entries,
        table=book,
        registered=registered,
        purpose=PURPOSE_LABEL,
        on_problem=on_problem,
    )

    def prompt_builder(entry, datatype):
        key = str(getattr(entry, "id", "") or "")
        prompt = pipeline.default_prompt(
            entry,
            datatype,
            purpose=PURPOSE_LABEL,
            minimum=minimum,
            maximum=maximum,
            align_text=aligned.get(key, ""),
        )
        extra = hints.get(key, ())
        if extra:
            prompt = prompt + "\n\n补充要求：" + "；".join(extra)
        return prompt

    planned = pipeline.plan(
        entries,
        purpose=PURPOSE_LABEL,
        table=book,
        registered=registered,
        prompt_builder=prompt_builder,
        task=task,
        explicit_model=explicit_model,
    )
    return LabelPlan(
        pipeline=planned,
        table=book,
        minimum=minimum,
        maximum=maximum,
        merge=bool(merge),
        rule_tags=rules_hit,
        prompt_tags=prompt_hit,
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
    """`{条目 id: (标签, ...)}`：规则标签 ∪ 模型标签，去重后受上下限约束。

    合并时「最少几个」不能只拿模型结果判死——规则已经给足的条目照样算数，所以模型结果先在
    `minimum=0` 下收，最后对合并后的集合统一 `clamp()`。
    """
    merged: dict[str, list[str]] = {}
    if plan_obj.merge:
        for source in (plan_obj.rule_tags, plan_obj.prompt_tags):
            for key, names in source.items():
                merged.setdefault(str(key), []).extend(names)
    collected = pipeline.collect(
        plan_obj.pipeline,
        results,
        parse=parse,
        minimum=0 if plan_obj.merge else plan_obj.minimum,
        maximum=plan_obj.maximum,
        available=available,
        known=known,
    )
    for key, names in collected.items():
        merged.setdefault(str(key), []).extend(names)
    out: dict[str, tuple[str, ...]] = {}
    for key, names in merged.items():
        picked = pipeline.clamp(
            pipeline.merge_names(names),
            minimum=plan_obj.minimum,
            maximum=plan_obj.maximum,
            available=available,
        )
        if picked:
            out[key] = picked
    return out


def ensure_tags(collected: Mapping[str, tuple[str, ...]], *, api=None) -> int:
    """把要挂的标签先建进标签库，返回要保证存在的标签个数。

    模型与规则都可能给出库里还没有的标签；`tag_items()` 在标签不存在时的行为并不统一，
    所以挂之前先调一次 `app.sdk.items.ensure_tags()` 把它们建好（用户 m42577 / m42753）。
    """
    client = api or items_sdk
    ensure = getattr(client, "ensure_tags", None)
    names = sorted({name for values in collected.values() for name in values if name})
    if not names or not callable(ensure):
        return 0
    try:
        ensure(names)
    except Exception as exc:  # 建标签失败不该挡住挂标签
        _console.warning(f"自动挂标签：新建标签失败（{exc}），仍然尝试挂上去")
    return len(names)


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
    """命中条目的名字，最多列 limit 个（给控制台日志用）。"""
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
    """跑模型，把「规则标签 ∪ 模型标签」写进库。"""
    results = pipeline.run(
        plan_obj.pipeline,
        on_progress=progress,
        cancel=cancel,
        max_workers=max_workers,
    )
    collected = collect_labels(plan_obj, results, parse=parse, known=known, available=available)
    ensure_tags(collected, api=api)  # 库里没有的名字先建出来
    written, failed = write_tags(collected, api=api, cancel=cancel)
    stopped = cancel is not None and bool(cancel())
    pending = pipeline.pending_keys(plan_obj.pipeline, results)
    names = _matched_names(plan_obj, collected)
    source = "规则 + 模型" if plan_obj.merge else "模型"
    if failed:
        _console.warning(
            f"自动挂标签（{source}）：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
            f"写入 {written} 个标签，{len(failed)} 个标签没写进去（{failed[0][0]}：{failed[0][1]}）"
        )
    elif not collected:
        _console.info(
            f"自动挂标签（{source}）：扫描 {plan_obj.total} 个条目，没有标签可写"
            "（规则没命中，或模型没给出标签）"
        )
    elif written:
        _console.info(
            f"自动挂标签（{source}）：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
            f"写入 {written} 个标签"
        )
    else:
        _console.info(
            f"自动挂标签（{source}）：扫描 {plan_obj.total} 个条目，命中 {len(collected)} 个（{names}），"
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
    parts = [
        f"扫描 {report.plan.total} 个条目",
        f"命中 {len(report.matched)} 个",
        f"写入 {report.written} 个标签",
    ]
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
