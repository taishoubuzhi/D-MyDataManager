"""批处理管线：条目 → 批量请求 → 结果 → 标签 / 关键词。

这里同样只有纯逻辑（唯一的外部依赖是 SDK 的 `BatchRequest` / `BatchResult` 数据类），
写库和界面都在上层插件里：

    plan = plan(items, purpose=PURPOSE_LABEL, table=book.table(PURPOSE_LABEL),
                registered=registered_map(models.templates()))
    results = run(plan, on_progress=on_progress, cancel=cancel)
    names = collect(plan, results, minimum=1, maximum=max_tags)
    for key, values in names.items():
        items.tag_items([key], values)
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

from app.sdk.errors import SdkError
from app.sdk import storage

if TYPE_CHECKING:  # 只用于类型标注：真正导入放到调用时（lib.model 可能没启用）
    from dm_plugin.lib.model.api import BatchRequest, BatchResult


def model_api():
    """模型工具库的门面模块；没启用时抛 SdkError（模型不能凭空变出来）。"""
    api = model_api_optional()
    if api is None:
        raise SdkError("这个功能需要模型工具库（lib.model）")
    return api

from .align import (
    DATATYPE_LABELS,
    PURPOSE_LABEL,
    PURPOSE_LABELS,
    AlignTable,
    normalize_datatype,
)

def model_api_optional():
    """模型工具库的门面模块；没启用（或没装）时返回 None。"""
    try:
        from dm_plugin.lib.model import api
    except ImportError:
        return None
    return api

#: `label` 产物是标签、`keyword` 产物是关键词，任务不同但提示词骨架一样。
DEFAULT_TASK = "chat"

#: 对齐模型按数据类型要跑的**生成式**任务：只有能出文字的类型才算「对齐信息」。
#: 视频不在里面——抽帧需要额外的解码器，而系统方案给视频配的 CLIP 是向量模型、出不了句子，
#: 硬发 `caption` 只会失败；这种类型由 `_explain_no_task()` 在日志里说明「只出向量、不参与提示词」。
ALIGN_TASKS = {
    "IMAGE": "caption",
    "AUDIO": "asr",
}

#: 把对齐信息拼进提示词的模板（对齐模型先读，主模型再基于它生成）。
ALIGN_TEMPLATE = "对齐信息（由对齐模型预先读出的内容，供你参考）：\n{text}"
DEFAULT_CAPABILITY = ""
DEFAULT_TEMPERATURE = 0.2

SYSTEM_PROMPT = (
    "你是文件整理助手，负责给文件挑标签。"
    "只输出一个 JSON 字符串数组，不要解释、不要 Markdown 代码块。"
)
LABEL_INSTRUCTION = "请给下面这个文件挑 {minimum}-{maximum} 个中文标签，按重要性排序："
KEYWORD_INSTRUCTION = "请给下面这个文件挑 {minimum}-{maximum} 个中文关键词，按重要性排序："

ITEM_TEMPLATE = "名称：{name}\n后缀：{suffix}\n类型：{type}\n路径：{path}"
TEXT_TEMPLATE = "内容摘录：\n{text}"

#: 解析模型输出时要去掉的装饰（项目符号、编号、引号、括号、代码块围栏）。
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.、)）])\s*")
_QUOTES = "「」『』\"'“”‘’【】[]()（）"
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def parse_names(
    text: object,
    *,
    limit: int = 0,
    known: Sequence[str] = (),
    keep_case: bool = False,
) -> tuple[str, ...]:
    """把模型输出解析成名字列表。

    认识的写法：JSON 数组（`["a","b"]`）、换行、逗号 / 顿号 / 分号、项目符号与编号、
    行尾的引号括号与 ``` 围栏。`known` 非空时**只保留库里已有的名字**。
    """
    raw = _FENCE.sub("", str(text or ""))
    names: list[str] = []
    payload = _load_json(raw)
    if payload is not None:
        for value in payload:
            _push(names, value, keep_case=keep_case)
    else:
        for line in raw.splitlines():
            line = _BULLET.sub("", line)
            for part in re.split(r"[,，、;；]", line):
                _push(names, part, keep_case=keep_case)
    if known:
        wanted = {str(name) for name in known}
        lowered = {str(name).lower() for name in known}
        names = [name for name in names if name in wanted or name.lower() in lowered]
    if limit > 0:
        names = names[: int(limit)]
    return tuple(names)


def _load_json(text: str) -> list | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        data = storage.loads(stripped)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        for key in ("names", "tags", "keywords", "items", "result", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [str(key) for key, value in data.items() if value]
    if isinstance(data, list):
        return data
    return None


def _push(names: list[str], value: object, *, keep_case: bool) -> None:
    if not isinstance(value, str):
        return
    text = value.strip().strip(_QUOTES).strip()
    text = _BULLET.sub("", text).strip()
    if not text or len(text) > 40:
        return
    if not keep_case:
        folded = text.lower()
        if any(item.lower() == folded for item in names):
            return
    elif text in names:
        return
    names.append(text)


def clamp(
    names: Iterable[str],
    *,
    minimum: int = 1,
    maximum: int = 0,
    available: Sequence[str] = (),
) -> tuple[str, ...]:
    """数量与来源过滤：少于 `minimum` 整条不要，多于 `maximum` 截断。"""
    allowed = {str(name) for name in available}
    picked: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if not text or text in picked:
            continue
        if allowed and text not in allowed:
            continue
        picked.append(text)
    if maximum > 0:
        picked = picked[: int(maximum)]
    if len(picked) < max(0, int(minimum)):
        return ()
    return tuple(picked)


def merge_names(*groups: Iterable[str], limit: int = 0) -> tuple[str, ...]:
    """合并多组名字（规则命中的 + 模型给的），去重保序。"""
    merged: list[str] = []
    for group in groups:
        for name in group or ():
            text = str(name or "").strip()
            if text and text not in merged:
                merged.append(text)
    if limit > 0:
        merged = merged[: int(limit)]
    return tuple(merged)


# --------------------------------------------------------------- 计划
@dataclass(frozen=True)
class PipelineItem:
    """一条排队等模型的条目。"""

    key: str
    item: object
    datatype: str
    model_id: str
    alignment_id: str = ""
    tags: tuple[str, ...] = ()

    @property
    def datatype_label(self) -> str:
        return DATATYPE_LABELS.get(self.datatype, self.datatype)

    @property
    def name(self) -> str:
        return str(getattr(self.item, "name", "") or self.key)


@dataclass(frozen=True)
class PipelinePlan:
    """一次批处理的完整计划：请求 + 每条请求对应的条目 + 跳过的条目及原因。"""

    purpose: str = PURPOSE_LABEL
    items: tuple[PipelineItem, ...] = ()
    requests: tuple[BatchRequest, ...] = ()
    skipped: tuple[tuple[object, str], ...] = ()
    by_key: Mapping[str, PipelineItem] = field(default_factory=dict)

    @property
    def purpose_label(self) -> str:
        return PURPOSE_LABELS.get(self.purpose, self.purpose)

    @property
    def total(self) -> int:
        return len(self.items) + len(self.skipped)

    def item_of(self, key: str) -> PipelineItem | None:
        return self.by_key.get(str(key))

    def skip_reason(self, key: str) -> str:
        for item, reason in self.skipped:
            if str(getattr(item, "id", "")) == str(key):
                return reason
        return ""


def default_prompt(
    item,
    datatype: str,
    *,
    purpose: str = PURPOSE_LABEL,
    minimum: int = 1,
    maximum: int = 8,
    text: str = "",
    align_text: str = "",
) -> str:
    """默认提示词：文件信息 +（可选）内容摘录 +（可选）对齐信息。"""
    instruction = LABEL_INSTRUCTION if purpose == PURPOSE_LABEL else KEYWORD_INSTRUCTION
    parts = [
        instruction.format(minimum=int(minimum), maximum=int(maximum)),
        ITEM_TEMPLATE.format(
            name=getattr(item, "name", "") or "",
            suffix=getattr(item, "suffix", "") or "",
            type=DATATYPE_LABELS.get(datatype, datatype),
            path=getattr(item, "file_path", "") or "",
        ),
    ]
    if text:
        parts.append(TEXT_TEMPLATE.format(text=str(text)[:2000]))
    if align_text:
        parts.append(ALIGN_TEMPLATE.format(text=str(align_text)[:1200]))
    return "\n\n".join(parts)


def default_payload(item, prompt: str, *, temperature: float = DEFAULT_TEMPERATURE) -> dict:
    """默认请求载荷：一段系统提示 + 一段用户提示（走聊天接口）。"""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": str(prompt)},
        ],
        "params": {"temperature": float(temperature)},
    }


def _result_text(value) -> str:
    """把批量结果里的内容取成一行文字（不同任务返回结构不一样）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for field in ("text", "generated_text", "caption", "summary"):
            text = value.get(field)
            if isinstance(text, str) and text.strip():
                return text.strip()
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, dict) and isinstance(first.get("generated_text"), str):
            return first["generated_text"].strip()
        if isinstance(first, str):
            return first.strip()
    return str(value).strip()[:2000]


def _warn(handler: Callable[[str], None] | None, message: str) -> None:
    """把对齐过程中的问题交给调用方（写日志 / 提示），处理器自己出错也不影响主流程。"""
    if handler is None:
        return
    try:
        handler(message)
    except Exception:
        pass


def _explain_no_task(
    handler: Callable[[str], None] | None,
    seen: set[str],
    book: AlignTable,
    datatype: str,
) -> None:
    """这个数据类型没有「出文字」的对齐任务（向量模型读不出句子），每种类型说一次就够。"""
    if handler is None or datatype in seen:
        return
    seen.add(datatype)
    row = book.row(datatype)
    wanted = str(getattr(row, "align_model", "") or getattr(row, "align_template", "") or "")
    if not wanted:
        return
    _warn(
        handler,
        f"{DATATYPE_LABELS.get(datatype, datatype)}配的对齐模型 {wanted} 只出向量、不参与提示词，"
        "本批直接用主模型",
    )


def align_texts(
    items: Sequence[object],
    *,
    table: AlignTable | None = None,
    registered: Mapping[str, str] | None = None,
    purpose: str = PURPOSE_LABEL,
    on_problem: Callable[[str], None] | None = None,
    is_file: Callable[[str], bool] | None = None,
) -> dict[str, str]:
    """先让**对齐模型**读一遍条目，返回 `{条目 id: 对齐信息}`。

    图片走图像描述（`caption`）、音频走语音识别（`asr`）；只有「配了对齐模型 + 有对应
    生成式任务 + 条目有 `file_path`」的条目才会跑，失败的直接跳过（对齐失败不该挡住主流程）。
    「为什么没跑」通过 `on_problem` 说出来——否则用户只看到主模型在干活，以为对齐模型没生效。
    """
    api = model_api_optional()
    book = table or AlignTable(purpose=purpose)
    exists = is_file or (lambda value: Path(value).is_file())
    requests: list[BatchRequest] = []
    explained: set[str] = set()
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            continue
        datatype = normalize_datatype(getattr(entry, "type", ""))
        task = ALIGN_TASKS.get(datatype)
        if not task:
            _explain_no_task(on_problem, explained, book, datatype)
            continue
        row = book.row(datatype)
        alignment = book.alignment(datatype, registered=registered)
        if not alignment:
            wanted = str(getattr(row, "align_model", "") or getattr(row, "align_template", "") or "")
            if wanted:
                _warn(
                    on_problem,
                    f"{DATATYPE_LABELS.get(datatype, datatype)}的对齐模型还没登记：{wanted}",
                )
            continue
        path = str(
            getattr(entry, "abs_path", "") or getattr(entry, "file_path", "") or ""
        )
        if not path:
            _warn(on_problem, f"条目「{key}」没有路径，跳过对齐模型")
            continue
        if not exists(path):
            # 相对路径直接丢给 worker 只会得到英文的 FileNotFoundError：先在主进程里核对一次
            _warn(on_problem, f"条目「{key}」的文件不在盘上：{path}")
            continue
        if api is None:  # 没启用模型工具库：对齐模型跑不了，但主流程照旧
            _warn(on_problem, "没有启用模型工具库（lib.model），跳过对齐模型")
            return {}
        requests.append(
            api.BatchRequest(task=task, payload={"input": path}, key=key, model_id=alignment)
        )
    if not requests:
        return {}
    try:
        results = api.run_batch(requests)
    except Exception as exc:
        _warn(on_problem, f"对齐模型读不动（{requests[0].model_id}）：{exc}")
        return {}  # 对齐失败不该挡住主流程
    texts: dict[str, str] = {}
    for result in results:
        if not getattr(result, "ok", False):
            _warn(
                on_problem,
                f"对齐失败（条目 {getattr(result, 'key', '')}）："
                f"{getattr(result, 'error', '') or '模型没有返回内容'}",
            )
            continue
        text = _result_text(getattr(result, "value", None))
        if text:
            texts[str(getattr(result, "key", "") or "")] = text
    return texts


def plan(
    items: Sequence[object],
    *,
    purpose: str = PURPOSE_LABEL,
    table: AlignTable | None = None,
    registered: Mapping[str, str] | None = None,
    prompt_builder: Callable[..., str] | None = None,
    payload_builder: Callable[..., dict] | None = None,
    task: str = DEFAULT_TASK,
    explicit_model: str = "",
    align_texts: Mapping[str, str] | None = None,
) -> PipelinePlan:
    """把条目排成批量请求。

    * 数据类型 → 模型：查 `table`（`table.resolved()` 已经处理了「预定义方案还没登记」）；
    * `explicit_model` 非空时所有条目都用它（用户在界面上指定了模型）；
    * 没有可用模型的条目进 `skipped`，原因直接可以显示给用户。
    """
    api = model_api_optional()
    if api is None:  # 纯规则路径不会走到这里；没有模型工具库时把条目全标成跳过
        return PipelinePlan(
            purpose=purpose,
            skipped=tuple((entry, "没有启用模型工具库（lib.model）") for entry in items or ()),
        )
    book = table or AlignTable(purpose=purpose)
    queued: list[PipelineItem] = []
    requests: list[BatchRequest] = []
    skipped: list[tuple[object, str]] = []
    for entry in items or ():
        key = str(getattr(entry, "id", "") or "")
        if not key:
            skipped.append((entry, "条目没有 id"))
            continue
        datatype = normalize_datatype(getattr(entry, "type", ""))
        model_id = explicit_model or book.resolved(datatype, registered=registered)
        if not model_id:
            skipped.append((entry, f"{DATATYPE_LABELS.get(datatype, datatype)}还没有可用模型"))
            continue
        aligned = dict(align_texts or {}).get(key, "")
        builder = prompt_builder
        if aligned and prompt_builder is None:
            # 有对齐信息时用默认提示词 + 对齐段（调用方自带 prompt_builder 就尊重调用方）
            def builder(item, datatype, _aligned=aligned):  # type: ignore[misc]
                return default_prompt(item, datatype, purpose=purpose, align_text=_aligned)

        built = _bulk_item(
            entry,
            datatype=datatype,
            model_id=model_id,
            alignment=book.alignment(datatype, registered=registered),
            purpose=purpose,
            prompt_builder=builder,
            payload_builder=payload_builder,
        )
        if built is None:
            skipped.append((entry, "没法组织请求内容"))
            continue
        item, _prompt, payload = built
        queued.append(item)
        requests.append(api.BatchRequest(task=task, payload=payload, key=key, model_id=model_id))
    by_key = {item.key: item for item in queued}
    return PipelinePlan(
        purpose=purpose,
        items=tuple(queued),
        requests=tuple(requests),
        skipped=tuple(skipped),
        by_key=by_key,
    )


def _bulk_item(entry, *, datatype, model_id, alignment, purpose, prompt_builder, payload_builder):
    builder = prompt_builder or (
        lambda item, datatype, text="": default_prompt(item, datatype, purpose=purpose)
    )
    prompt = str(builder(entry, datatype) or "")
    if not prompt.strip():
        return None
    maker = payload_builder or (lambda item, prompt: default_payload(item, prompt))
    payload = maker(entry, prompt)
    if not isinstance(payload, dict):
        return None
    item = PipelineItem(
        key=str(getattr(entry, "id", "") or ""),
        item=entry,
        datatype=datatype,
        model_id=model_id,
        alignment_id=alignment,
        tags=tuple(str(tag) for tag in (payload.pop("_tags", ()) or ())),
    )
    return item, prompt, payload


# --------------------------------------------------------------- 执行
def run(
    plan_obj: PipelinePlan,
    *,
    on_progress=None,
    cancel=None,
    max_workers: int | None = None,
) -> tuple[BatchResult, ...]:
    """交给模型工具库批量跑；没有任何请求时直接返回空。"""
    if not plan_obj.requests:
        return ()
    return model_api().run_batch(
        plan_obj.requests,
        on_progress=on_progress,
        cancel=cancel,
        max_workers=max_workers,
    )


def failed(results: Sequence[BatchResult]) -> tuple[BatchResult, ...]:
    return tuple(result for result in results if not result.ok)


def collect(
    plan_obj: PipelinePlan,
    results: Sequence[BatchResult],
    *,
    parse: Callable[[object], Sequence[str]] | None = None,
    limit: int = 0,
    minimum: int = 0,
    maximum: int = 0,
    available: Sequence[str] = (),
    known: Sequence[str] = (),
) -> dict[str, tuple[str, ...]]:
    """把结果整理成 `{条目 id: 名字}`；失败的、数量不够的都直接丢掉。"""
    reader = parse or (
        lambda value: parse_names(value, limit=limit, known=known)
    )
    collected: dict[str, tuple[str, ...]] = {}
    for result in results or ():
        if not result.ok:
            continue
        names = tuple(str(name) for name in reader(result.value))
        picked = clamp(names, minimum=minimum, maximum=maximum, available=available)
        if not picked:
            continue
        key = str(result.key or "")
        if not key:
            continue
        collected[key] = merge_names(collected.get(key, ()), picked)
    return collected


def pending_keys(plan_obj: PipelinePlan, results: Sequence[BatchResult]) -> tuple[str, ...]:
    """还没出结果的条目（取消 / 提前中断时界面要用）。"""
    done = {str(result.key) for result in results or ()}
    return tuple(item.key for item in plan_obj.items if item.key not in done)
