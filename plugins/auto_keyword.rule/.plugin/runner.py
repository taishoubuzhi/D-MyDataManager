"""按规则挂关键词的纯逻辑：规划 + 写库，插件入口与页面共用。

跟 `auto_tag.rule` 的 runner 是一个模子，只把「标签」换成「关键词」：

- 规则模型、字段取值、匹配算子全部复用共享库 `dm_plugin.lib.autolabel.rules`；
- 规则里的 `tags` 在这里就是「要挂的关键词」（`RuleDialog` 只是把它显示成「要挂的关键词」）；
- 写库走 `app.sdk.items.add_keywords(item_ids, words)`，宿主那边天然去重，重复追加不会翻倍。

不碰 Qt、不碰界面，也不 import 程序内部模块，所以夹具用例能直接调。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from app.sdk import console as console_sdk
from app.sdk import items as items_sdk

from dm_plugin.lib.autolabel.rules import FIELD_TEXT

__all__ = [
    "FileRef",
    "ItemPlan",
    "KeywordPlan",
    "KeywordReport",
    "TEXT_LIMIT",
    "keywords_for_paths",
    "plan_keywords",
    "run_keywords",
    "scan_keywords",
    "suffix_key",
    "summary_text",
]

#: 读正文时最多看这么多字符（跟自动标签保持一致，够匹配又不至于把大文件读穿）。
TEXT_LIMIT = 2000

_console = console_sdk.console_for("auto_keyword.rule")


def suffix_key(suffix: str) -> str:
    """把一个后缀变成规则 key，例如 `pdf` → `suffix.pdf`。"""
    return f"suffix.{str(suffix).lstrip('.').lower()}"


def _needs_text(rule_set) -> bool:
    """有没有启用中的规则要看正文；没有就不用白读一遍文本。"""
    return any(rule.field == FIELD_TEXT for rule in getattr(rule_set, "enabled", ()))


def _read_text(reader: Callable[[int], Any] | None, item_id: int, limit: int) -> str:
    if not callable(reader):
        return ""
    try:
        value = reader(item_id, limit=limit)
    except TypeError:
        # 插件自带的假 reader 可能只收一个参数（`app.sdk.items.read_text` 收 `limit`）。
        value = reader(item_id)
    if isinstance(value, tuple):
        value = value[0] if value else ""
    return str(value or "")


@dataclass(frozen=True)
class FileRef:
    """导入页只有路径、还没有条目的场合，用它顶一下 `ItemRef`。"""

    file_path: str = ""

    @property
    def id(self) -> None:
        return None

    @property
    def name(self) -> str:
        return PurePath(str(self.file_path).replace("\\", "/")).name

    @property
    def suffix(self) -> str:
        name = self.name
        if "." not in name:
            return ""
        return name.rsplit(".", 1)[-1].lstrip(".").lower()

    @property
    def type(self) -> str:
        return ""


def keywords_for_paths(paths: Iterable[Any], rule_set) -> tuple[str, ...]:
    """导入页预填：按路径算该挂哪些关键词（只看名字与后缀，不读正文）。"""
    words: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for word in rule_set.match_tags(FileRef(str(path))):
            key = str(word).casefold()
            if key in seen:
                continue
            seen.add(key)
            words.append(str(word))
    return tuple(words)


@dataclass(frozen=True)
class ItemPlan:
    """一个条目打算挂什么关键词。"""

    item_id: int | None
    name: str = ""
    keywords: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    reason: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.keywords)


@dataclass(frozen=True)
class KeywordPlan:
    """整批条目的规划结果。`notes` 是「读不出正文」之类的提示，不算失败。"""

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
    def keyword_names(self) -> tuple[str, ...]:
        words: list[str] = []
        seen: set[str] = set()
        for item in self.matched:
            for word in item.keywords:
                key = str(word).casefold()
                if key in seen:
                    continue
                seen.add(key)
                words.append(str(word))
        return tuple(words)

    def ids_for(self, word: str) -> tuple[int, ...]:
        key = str(word).casefold()
        return tuple(
            item.item_id
            for item in self.matched
            if item.item_id is not None
            and any(str(name).casefold() == key for name in item.keywords)
        )

    @property
    def batches(self) -> tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]:
        """把「挂同一组关键词」的条目并成一批，减少写库次数。"""
        grouped: dict[tuple[str, ...], list[int]] = {}
        for item in self.matched:
            if item.item_id is None:
                continue
            grouped.setdefault(tuple(item.keywords), []).append(int(item.item_id))
        return tuple((words, tuple(ids)) for words, ids in grouped.items())


@dataclass(frozen=True)
class KeywordReport:
    """写库结果。`ok` 表示这一轮没有失败、也没被取消。"""

    plan: KeywordPlan
    written: int = 0
    failed: tuple[str, ...] = ()
    cancelled: bool = False

    @property
    def matched(self) -> tuple[ItemPlan, ...]:
        return self.plan.matched

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def plan_keywords(
    items: Iterable[Any],
    rule_set,
    *,
    reader: Callable[[int], Any] | None = None,
    text_limit: int = TEXT_LIMIT,
) -> KeywordPlan:
    """把条目逐条过一遍规则，算出每个条目要挂的关键词（不写库）。"""
    plans: list[ItemPlan] = []
    notes: list[str] = []
    want_text = _needs_text(rule_set)
    for row in items:
        item_id = getattr(row, "id", None)
        name = str(getattr(row, "name", "") or "")
        if item_id is None:
            plans.append(ItemPlan(None, name, reason="条目没有 id"))
            continue
        text = ""
        if want_text:
            try:
                text = _read_text(reader, int(item_id), text_limit)
            except Exception as exc:  # noqa: BLE001 - 读不出正文不算失败，记条提示
                notes.append(f"读取《{name or item_id}》正文失败：{exc}")
        matches = rule_set.match_rules(row, text=text)
        words = rule_set.match_tags(row, text=text)
        plans.append(
            ItemPlan(
                int(item_id),
                name,
                tuple(str(word) for word in words),
                tuple(rule.title for rule in matches),
                "" if words else "没有命中规则",
            )
        )
    return KeywordPlan(tuple(plans), tuple(notes))


def run_keywords(
    plan: KeywordPlan,
    *,
    api: Any = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> KeywordReport:
    """按规划写库：同一组关键词的条目一次写完，逐批回报进度。"""
    service = api or items_sdk
    batches = plan.batches
    total = len(batches)
    written = 0
    failed: list[str] = []
    for index, (words, ids) in enumerate(batches, start=1):
        if callable(cancel) and cancel():
            _console.info(f"按规则挂关键词：已取消（已写入 {written} 个关键词）")
            return KeywordReport(plan, written, tuple(failed), cancelled=True)
        try:
            written += int(service.add_keywords(list(ids), list(words)) or 0)
        except Exception as exc:  # noqa: BLE001 - 一批坏掉不该拖垮整批
            failed.append(f"挂关键词「{'、'.join(words)}」失败：{exc}")
            _console.exception(f"按规则挂关键词失败：{'、'.join(words)}")
        if callable(progress):
            try:
                progress(index, total)
            except Exception:  # noqa: BLE001 - 进度回调出错不影响写库
                pass
    report = KeywordReport(plan, written, tuple(failed))
    if failed:
        _console.warning(
            f"按规则挂关键词：{len(failed)} 处失败（条目 {plan.total} 个，命中 {len(plan.matched)} 个）"
        )
    elif not plan.matched:
        _console.info(f"按规则挂关键词：{plan.total} 个条目都没有命中规则")
    elif not written:
        _console.info(f"按规则挂关键词：命中 {len(plan.matched)} 个条目，关键词都已经有了")
    else:
        _console.info(
            f"按规则挂关键词：命中 {len(plan.matched)} 个条目，写入 {written} 个关键词"
        )
    return report


def scan_keywords(
    items: Iterable[Any] | None = None,
    *,
    api: Any = None,
    limit: int = 0,
) -> tuple[tuple[str, int], ...]:
    """扫描现有条目里的关键词，按出现次数从多到少返回 `(关键词, 出现次数)`。

    程序本体没有关键词清单这种东西，所以「扫描当前系统的关键词」只能这样来：
    读一遍可见条目，把它们身上的关键词数出来，再交给人挑。
    """
    service = api or items_sdk
    if items is None:
        try:
            user_id = service.current_user_id()
            rows = list(service.list_items(user_id=user_id))
        except Exception as exc:  # noqa: BLE001 - 扫不出来就当作空
            _console.warning(f"扫描条目关键词失败：{exc}")
            rows = []
    else:
        rows = list(items)
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for row in rows:
        for word in getattr(row, "keywords", ()) or ():
            text = str(word).strip()
            if not text:
                continue
            key = text.casefold()
            labels.setdefault(key, text)
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], labels[pair[0]].casefold()))
    result = tuple((labels[key], count) for key, count in ordered)
    if limit > 0:
        return result[: int(limit)]
    return result


def summary_text(report: KeywordReport) -> str:
    """一句话总结，给提示框用。"""
    plan = report.plan
    text = f"扫描 {plan.total} 个条目，命中 {len(plan.matched)} 个"
    if report.written:
        text = f"{text}，写入 {report.written} 个关键词"
    if plan.notes:
        text = f"{text}，{len(plan.notes)} 条提示"
    if report.failed:
        text = f"{text}，{len(report.failed)} 处失败"
    if report.cancelled:
        text = f"{text}（已取消）"
    return text
