"""任务 2：按规则给条目挂标签的执行逻辑。

这里只有纯计算：把条目（`app.sdk.items.ItemRef`）交给共享库的规则引擎，
算出「哪个条目要挂哪些标签」，再按标签分组批量写库。
导入页、管理页、条目菜单都只是调用者，界面逻辑不在这里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Callable, Iterable, Mapping, Sequence

from app.sdk import items as items_sdk
from app.sdk.console import console_for

from dm_plugin.lib.autolabel.rules import (
    FIELD_SUFFIX,
    FIELD_TEXT,
    KIND_MATCH,
    OP_IS,
    SOURCE_USER,
    Rule,
    RuleSet,
)

__all__ = [
    "TEXT_LIMIT",
    "FileRef",
    "ItemPlan",
    "RunReport",
    "TagPlan",
    "configure_suffix",
    "configured_suffix",
    "merge_suffixes",
    "plan_tags",
    "run_rules",
    "suffix_key",
    "summary_text",
    "tags_for_paths",
]

#: 规则要匹配「文件内容」时最多读多少字
TEXT_LIMIT = 2000

#: 控制台门面：挂标签的结果要能在程序控制台看到（「说成功却没挂上」就靠这行排查）
_console = console_for("auto_tag.rule")


def suffix_key(suffix: str) -> str:
    """`pdf` / `.PDF` 都归一成用户规则 `suffix.pdf`。"""
    return f"suffix.{str(suffix or '').strip().lstrip('.').lower()}"


def _needs_text(rule_set: RuleSet) -> bool:
    return any(
        rule.field == FIELD_TEXT for rule in rule_set.enabled if rule.kind == KIND_MATCH
    )


def _read_text(reader, item_id, limit: int) -> str:
    """`app.sdk.items.read_text()` 返回 `(正文, 编码, 是否截断)`，这里只取正文。"""
    value = reader(item_id, limit)
    if isinstance(value, (tuple, list)):
        return str(value[0] or "") if value else ""
    return str(value or "")


@dataclass(frozen=True)
class FileRef:
    """还没导入的文件：只有路径，够规则里的名称 / 后缀 / 路径三个字段用。"""

    file_path: str = ""

    @property
    def id(self):
        return None

    @property
    def name(self) -> str:
        return PurePath(self.file_path).name

    @property
    def suffix(self) -> str:
        return PurePath(self.file_path).suffix.lstrip(".").lower()

    @property
    def type(self) -> str:
        return ""


def tags_for_paths(paths: Iterable, rule_set: RuleSet) -> tuple[str, ...]:
    """导入前的预填：按路径跑一遍规则，返回要去重的标签（读不了正文，所以「文件内容」规则不参与）。"""
    names: list[str] = []
    for path in paths:
        for tag in rule_set.match_tags(FileRef(str(path))):
            if tag not in names:
                names.append(tag)
    return tuple(names)


@dataclass(frozen=True)
class ItemPlan:
    """一个条目的匹配结果。"""

    item_id: object
    name: str = ""
    tags: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    reason: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.tags)


@dataclass(frozen=True)
class TagPlan:
    """一批条目的匹配结果（还没写库）。"""

    items: tuple[ItemPlan, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def matched(self) -> tuple[ItemPlan, ...]:
        return tuple(item for item in self.items if item.matched)

    @property
    def skipped(self) -> tuple[ItemPlan, ...]:
        return tuple(item for item in self.items if not item.matched)

    @property
    def tag_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for item in self.items:
            for tag in item.tags:
                if tag not in names:
                    names.append(tag)
        return tuple(names)

    def ids_for(self, tag: str) -> tuple[object, ...]:
        return tuple(item.item_id for item in self.items if tag in item.tags)

    @property
    def groups(self) -> dict[str, tuple[object, ...]]:
        """标签 → 要挂这些标签的条目 id（批量写库按它分组）。"""
        return {tag: self.ids_for(tag) for tag in self.tag_names}


@dataclass(frozen=True)
class RunReport:
    """一次运行的结果：写了多少、哪里失败、是不是被取消。"""

    plan: TagPlan
    written: int = 0
    failed: tuple[str, ...] = ()
    cancelled: bool = False

    @property
    def matched(self) -> tuple[ItemPlan, ...]:
        return self.plan.matched

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def plan_tags(
    items: Iterable,
    rule_set: RuleSet,
    *,
    reader: Callable | None = None,
    text_limit: int = TEXT_LIMIT,
) -> TagPlan:
    """按规则算出每个条目要挂的标签，不写库。

    `reader(item_id, limit) -> (正文, 编码, 是否截断)`：只有启用了「文件内容」字段的
    规则才会被调用（默认用 `app.sdk.items.read_text`）。
    """
    rows = tuple(items)
    need_text = _needs_text(rule_set)
    read = reader or items_sdk.read_text
    plans: list[ItemPlan] = []
    notes: list[str] = []
    for row in rows:
        item_id = getattr(row, "id", None)
        name = str(getattr(row, "name", "") or "")
        if item_id is None:
            plans.append(ItemPlan(None, name, reason="条目没有 id"))
            continue
        text = ""
        if need_text:
            try:
                text = _read_text(read, item_id, text_limit)
            except Exception as exc:  # 读不出正文只影响「文件内容」规则
                notes.append(f"读取《{name}》正文失败：{exc}")
        matches = rule_set.match_rules(row, text=text)
        tags = rule_set.match_tags(row, text=text)
        plans.append(
            ItemPlan(
                item_id,
                name,
                tags=tags,
                rules=tuple(rule.key for rule in matches),
                reason="" if tags else "没有命中规则",
            )
        )
    return TagPlan(tuple(plans), tuple(notes))


def _matched_names(plans: Iterable, limit: int = 5) -> str:
    """命中条目的名字，最多列 limit 个（给控制台日志用：单条目挂载就是那一条的名字）。"""
    labels = [
        str(getattr(item, "name", "") or "") or str(getattr(item, "item_id", "") or "?")
        for item in plans
    ]
    if not labels:
        return ""
    if len(labels) > limit:
        return "、".join(labels[:limit]) + f" 等 {len(labels)} 个"
    return "、".join(labels)


def run_rules(
    plan: TagPlan,
    *,
    api=None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> RunReport:
    """把 `plan` 里的标签写进库：按标签分组批量写，`written` 是真实改动数。"""
    service = api or items_sdk
    total = len(plan.matched)
    done = 0
    written = 0
    failed: list[str] = []
    seen: set = set()
    for tag, ids in plan.groups.items():
        if cancel is not None and cancel():
            _console.info(f"按规则挂标签：已取消（已写入 {written} 个标签）")
            return RunReport(plan, written, tuple(failed), cancelled=True)
        try:
            written += int(service.tag_items(list(ids), [tag]) or 0)
        except Exception as exc:
            failed.append(f"挂标签「{tag}」失败：{exc}")
            _console.exception(f"按规则挂标签：「{tag}」写库失败")
        for item_id in ids:
            if item_id in seen:
                continue
            seen.add(item_id)
            done += 1
            if progress is not None:
                progress(done, total)
    names = _matched_names(plan.matched)
    if failed:
        _console.warning(
            f"按规则挂标签：扫描 {plan.total} 个条目，命中 {total} 个（{names}），写入 {written} 个标签，"
            f"{len(failed)} 处失败（{failed[0]}）"
        )
    elif not total:
        _console.info(f"按规则挂标签：扫描 {plan.total} 个条目，没有条目命中规则")
    elif written:
        _console.info(
            f"按规则挂标签：扫描 {plan.total} 个条目，命中 {total} 个（{names}），写入 {written} 个标签"
        )
    else:
        _console.info(
            f"按规则挂标签：扫描 {plan.total} 个条目，命中 {total} 个（{names}），"
            "但这些标签条目上都有了，没有新增"
        )
    return RunReport(plan, written, tuple(failed))


def configure_suffix(rule_set: RuleSet, suffix: str, tags: Sequence[str]) -> RuleSet:
    """给某个数据格式设置默认标签；标签为空表示这个格式不挂标签。"""
    key = suffix_key(suffix)
    clean: list[str] = []
    for tag in tags:
        text = str(tag or "").strip()
        if text and text not in clean:
            clean.append(text)
    if not clean:
        return rule_set.without_key(key)
    name = str(suffix or "").strip().lstrip(".").lower()
    return rule_set.with_rule(
        Rule(
            key=key,
            name=f"数据格式 {name}",
            kind=KIND_MATCH,
            enabled=True,
            field=FIELD_SUFFIX,
            op=OP_IS,
            pattern=name,
            tags=tuple(clean),
            source=SOURCE_USER,
        )
    )


def configured_suffix(rule_set: RuleSet, suffix: str) -> tuple[str, ...]:
    """某个格式当前的默认标签（没有配过就是空的）。"""
    rule = rule_set.by_key(suffix_key(suffix))
    if rule is None or not rule.enabled or rule.kind != KIND_MATCH:
        return ()
    return tuple(rule.tags)


def merge_suffixes(used: Mapping[str, int] | None, rule_set: RuleSet) -> tuple[str, ...]:
    """库里出现过的后缀 + 已经配过默认标签的后缀，归一成小写去点后排序。"""
    names: set[str] = set()
    for suffix in used or {}:
        text = str(suffix or "").strip().lstrip(".").lower()
        if text:
            names.add(text)
    for rule in rule_set.rules:
        if rule.kind == KIND_MATCH and rule.field == FIELD_SUFFIX and rule.op == OP_IS:
            text = rule.pattern.strip().lstrip(".").lower()
            if text:
                names.add(text)
    return tuple(sorted(names))


def summary_text(report: RunReport) -> str:
    """一行结果摘要，给提示条和进度面板用。"""
    plan = report.plan
    parts = [f"扫描 {plan.total} 个条目", f"命中 {len(plan.matched)} 个"]
    if report.written:
        parts.append(f"写入 {report.written} 个标签")
    if plan.notes:
        parts.append(f"{len(plan.notes)} 条提示")
    if report.failed:
        parts.append(f"{len(report.failed)} 处失败")
    if report.cancelled:
        parts.append("已取消")
    return "，".join(parts)
