"""任务 4 的执行逻辑：条目 → 按数据类型对齐的模型请求 → 关键词 → 写库。

只有纯逻辑（唯一的外部依赖是 SDK 的 `BatchRequest` / `BatchResult`）：界面与
数据库都在上层 `plugin.py` / `ui/page.py`，方便单测直接对着它跑。

关键词与标签共用共享库 `lib.autolabel` 的批处理管线，区别只有
`purpose=PURPOSE_KEYWORD`（提示词换成「挑关键词」）和产物写到 `items.add_keywords()`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from app.sdk import items as items_sdk
from app.sdk.models import BatchResult

from dm_plugin.lib.autolabel import pipeline
from dm_plugin.lib.autolabel.align import PURPOSE_KEYWORD, AlignTable

#: 出厂默认：至少 3 个、最多 8 个关键词
DEFAULT_MINIMUM = 3
DEFAULT_MAXIMUM = 8
MIN_OPTION = "min_keywords"
MAX_OPTION = "max_keywords"
#: 预览最多显示多少行
PREVIEW_LIMIT = 200


# ----------------------------------------------------------------- 选项
def _read_option(ctx, key: str, default: int) -> int:
    """读一个整数选项：空值 / 坏值都回默认，不把界面搞崩。"""
    try:
        raw = ctx.option(key, default)
    except Exception:
        return default
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def option_values(ctx) -> tuple[int, int]:
    """读插件选项：`(最少, 最多)`；`最多` 为 0 表示不限。"""
    minimum = max(1, _read_option(ctx, MIN_OPTION, DEFAULT_MINIMUM))
    maximum = _read_option(ctx, MAX_OPTION, DEFAULT_MAXIMUM)
    if maximum < 0:
        maximum = 0
    if maximum and maximum < minimum:
        maximum = minimum
    return minimum, maximum


# ----------------------------------------------------------------- 计划
@dataclass(frozen=True)
class KeywordPlan:
    """一次运行的计划：按数据类型排好的模型请求 + 跳过的条目。"""

    pipeline: pipeline.PipelinePlan
    table: AlignTable = field(default_factory=lambda: AlignTable(purpose=PURPOSE_KEYWORD))
    minimum: int = DEFAULT_MINIMUM
    maximum: int = DEFAULT_MAXIMUM
    entries: int = 0

    @property
    def purpose_label(self) -> str:
        return self.pipeline.purpose_label

    @property
    def total(self) -> int:
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


def plan_keywords(
    items: Sequence[object],
    *,
    table: AlignTable | None = None,
    registered: Mapping[str, str] | None = None,
    minimum: int = DEFAULT_MINIMUM,
    maximum: int = DEFAULT_MAXIMUM,
    explicit_model: str = "",
    task: str = pipeline.DEFAULT_TASK,
    on_problem: Callable[[str], None] | None = None,
) -> KeywordPlan:
    """按「数据类型 → 关键词模型」的对齐表排一批请求；没配模型的数据类型直接跳过。"""
    book = table or AlignTable(purpose=PURPOSE_KEYWORD)
    entries = tuple(items or ())

    # 先让对齐模型（图片 → 图像描述、音频 → 语音识别）读一遍，把结果拼进提示词
    aligned = pipeline.align_texts(
        entries,
        table=book,
        registered=registered,
        purpose=PURPOSE_KEYWORD,
        on_problem=on_problem,
    )

    def prompt_builder(entry, datatype):
        return pipeline.default_prompt(
            entry,
            datatype,
            purpose=PURPOSE_KEYWORD,
            minimum=minimum,
            maximum=maximum,
            align_text=aligned.get(str(getattr(entry, "id", "") or ""), ""),
        )

    planned = pipeline.plan(
        entries,
        purpose=PURPOSE_KEYWORD,
        table=book,
        registered=registered,
        prompt_builder=prompt_builder,
        task=task,
        explicit_model=explicit_model,
    )
    return KeywordPlan(
        pipeline=planned,
        table=book,
        minimum=minimum,
        maximum=maximum,
        entries=len(entries),
    )


# ----------------------------------------------------------------- 结果
def collect_keywords(
    plan_obj: KeywordPlan,
    results: Sequence[BatchResult],
    *,
    parse: Callable[[object], Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """`{条目 id: (关键词, ...)}`；失败、数量不够的直接丢掉（关键词没有全集上限）。"""
    return pipeline.collect(
        plan_obj.pipeline,
        results,
        parse=parse,
        minimum=plan_obj.minimum,
        maximum=plan_obj.maximum,
    )


@dataclass(frozen=True)
class KeywordReport:
    """一次运行的账：写进去多少、哪些关键词失败、是否被取消。"""

    plan: KeywordPlan
    results: tuple = ()
    collected: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    written: int = 0
    failed: tuple = ()
    cancelled: bool = False

    @property
    def matched(self) -> tuple[str, ...]:
        return tuple(key for key, words in self.collected.items() if words)

    @property
    def word_names(self) -> tuple[str, ...]:
        return pipeline.merge_names(*self.collected.values())

    @property
    def groups(self) -> dict[str, tuple[str, ...]]:
        groups: dict[str, list[str]] = {}
        for key, words in self.collected.items():
            for word in words:
                groups.setdefault(word, []).append(key)
        return {word: tuple(keys) for word, keys in groups.items()}

    @property
    def skipped(self) -> tuple:
        return self.plan.skipped

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def write_keywords(
    collected: Mapping[str, tuple[str, ...]],
    *,
    api=None,
    cancel=None,
) -> tuple[int, tuple[tuple[str, str], ...]]:
    """按关键词分组批量写；返回 `(真实写入数, ((关键词, 错误), ...))`。"""
    client = api or items_sdk
    groups: dict[str, list[str]] = {}
    for key, words in collected.items():
        for word in words:
            groups.setdefault(word, []).append(str(key))
    written = 0
    failed: list[tuple[str, str]] = []
    for word, keys in groups.items():
        if cancel is not None and cancel():
            break
        try:
            written += int(client.add_keywords(keys, [word]) or 0)
        except Exception as exc:
            failed.append((word, str(exc)))
    return written, tuple(failed)


def run_keywords(
    plan_obj: KeywordPlan,
    *,
    api=None,
    progress=None,
    cancel=None,
    max_workers: int | None = None,
    parse: Callable[[object], Sequence[str]] | None = None,
) -> KeywordReport:
    """跑模型，把关键词写进库。"""
    results = pipeline.run(
        plan_obj.pipeline,
        on_progress=progress,
        cancel=cancel,
        max_workers=max_workers,
    )
    collected = collect_keywords(plan_obj, results, parse=parse)
    written, failed = write_keywords(collected, api=api, cancel=cancel)
    stopped = cancel is not None and bool(cancel())
    pending = pipeline.pending_keys(plan_obj.pipeline, results)
    return KeywordReport(
        plan=plan_obj,
        results=tuple(results),
        collected=collected,
        written=written,
        failed=failed,
        cancelled=bool(pending) and stopped,
    )


def summary_text(report: KeywordReport) -> str:
    parts = [
        f"扫描 {report.plan.total} 个条目",
        f"{len(report.matched)} 个出了关键词",
        f"写入 {report.written} 个关键词",
    ]
    if report.plan.skipped:
        parts.append(f"跳过 {len(report.plan.skipped)} 个")
    if report.failed:
        parts.append(f"{len(report.failed)} 个关键词没写进去")
    if report.cancelled:
        parts.append("已取消")
    return "，".join(parts)


def preview_rows(report: KeywordReport, *, limit: int = PREVIEW_LIMIT) -> tuple[str, ...]:
    """预览/结果列表：`名称 ← 关键词1、关键词2`。"""
    lines: list[str] = []
    for key, words in report.collected.items():
        if len(lines) >= limit:
            lines.append(f"……还有 {len(report.collected) - limit} 个条目，只显示前 {limit} 个")
            break
        item = report.plan.item_of(key)
        label = getattr(getattr(item, "item", None), "name", "") or key
        lines.append(f"{label}  ←  {'、'.join(words)}")
    if not lines:
        lines.append("没有条目出关键词。")
    for entry, reason in report.plan.skipped:
        name = getattr(entry, "name", "") or getattr(entry, "id", "") or "条目"
        lines.append(f"跳过 {name}：{reason}")
    return tuple(lines)
