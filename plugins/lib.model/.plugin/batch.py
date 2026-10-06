"""批量调度：同一模型的请求合到一次加载里跑完。

模型加载一次很贵（独立进程 / 显存），所以「同一模型的许多请求」不该各自 `acquire()` 一次。
批量任务按这条流水线走：

1. 先为每条请求解析它要用的模型（`model_id` 为空就按 `capability` 挑；写了 `model_id` 却挑不到就直接
   记成失败，不会退回去用别的模型），解析结果相同的归成一组；
2. 每组只 `acquire()` 一次租约，组内请求依次通过这份租约 `invoke()`（适配器是单会话的，
   组内串行才能保证顺序与显存安全）；
3. 组之间并行（线程池），并行度 = 组数、`max_resident`、`max_workers` 三者取最小；
4. 每条请求单独返回 `BatchOutcome`：单条失败不影响整批，`cancel()` 返回真时剩余请求标为「已取消」。

调用方（自动标签 / 自动关键词等插件）只管准备请求、收结果，不关心模型切换与加载次数。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.sdk.console import console_for
from .errors import ModelError

_console = console_for("lib.model")

__all__ = ["MAX_PARALLEL", "BatchItem", "BatchOutcome", "as_items", "run_batch"]

#: 默认并行度上限：组再多也不会同时加载太多模型
MAX_PARALLEL = 4


@dataclass(frozen=True)
class BatchItem:
    """一条待跑请求（与 `api.BatchRequest` 同形，便于两边互转）。"""

    task: str
    payload: dict = field(default_factory=dict)
    key: str = ""
    model_id: str = ""


@dataclass(frozen=True)
class BatchOutcome:
    """一条请求的结果；`ok` 为假时 `error` 说明原因。"""

    key: str
    ok: bool
    value: Any = None
    error: str = ""
    model_id: str = ""


def as_items(requests: Iterable[Any], *, default_model_id: str = "") -> list[BatchItem]:
    """把调用方给的对象 / 字典统一成 `BatchItem`（没有 key 时用序号当 key）。"""
    items: list[BatchItem] = []
    for index, raw in enumerate(requests or ()):
        task = str(_field(raw, "task", "") or "").strip()
        if not task:
            continue
        key = str(_field(raw, "key", "") or index + 1)
        model_id = str(_field(raw, "model_id", "") or default_model_id or "")
        payload = _field(raw, "payload", None)
        items.append(BatchItem(task=task, payload=dict(payload) if isinstance(payload, dict) else {}, key=key, model_id=model_id))
    return items


def run_batch(
    manager,
    requests: Iterable[Any],
    *,
    model_id: str = "",
    capability: str = "",
    on_progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    max_workers: int | None = None,
) -> tuple[BatchOutcome, ...]:
    """跑一批请求，返回与输入顺序一一对应的结果元组。"""
    items = as_items(requests, default_model_id=model_id)
    if not items:
        return ()

    results: list[BatchOutcome | None] = [None] * len(items)
    total = len(items)
    done = 0
    lock = threading.Lock()

    def finish(index: int, item: BatchItem, *, value: Any = None, error: str = "", used: str = "") -> None:
        nonlocal done
        results[index] = BatchOutcome(key=item.key, ok=not error, value=value, error=error, model_id=used)
        with lock:
            done += 1
            current = done
        _notify(on_progress, current, total)

    groups: dict[str, list[tuple[int, BatchItem]]] = {}
    for index, item in enumerate(items):
        try:
            # 明确写了 model_id 就不许退回到别的模型：挑不到就报错，避免整批悄悄换模型
            record = manager.resolve(
                model_id=item.model_id,
                capability=capability,
                task="" if item.model_id else item.task,
            )
        except Exception as exc:
            finish(index, item, error=_text(exc))
            continue
        groups.setdefault(record.id, []).append((index, item))

    def work(record_id: str, members: list[tuple[int, BatchItem]]) -> None:
        if _cancelled(cancel):
            for index, item in members:
                finish(index, item, error="已取消", used=record_id)
            return
        try:
            lease = manager.acquire(model_id=record_id, task=members[0][1].task)
        except Exception as exc:
            for index, item in members:
                finish(index, item, error=_text(exc), used=record_id)
            return
        _console.info(f"批量跑 {len(members)} 条请求：{record_id}")
        try:
            for index, item in members:
                if _cancelled(cancel):
                    finish(index, item, error="已取消", used=record_id)
                    continue
                try:
                    value = lease.invoke(item.task, item.payload)
                except Exception as exc:
                    _console.warning(f"批量请求失败（{item.key}）：{_text(exc)}")
                    finish(index, item, error=_text(exc), used=record_id)
                else:
                    finish(index, item, value=value, used=record_id)
        finally:
            lease.close()

    plan = list(groups.items())
    workers = _workers(manager, len(plan), max_workers)
    if workers <= 1:
        for record_id, members in plan:
            work(record_id, members)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, record_id, members) for record_id, members in plan]
            for future in futures:
                future.result()
    return tuple(
        result if result is not None else BatchOutcome(key=items[index].key, ok=False, error="未执行")
        for index, result in enumerate(results)
    )


# ------------------------------------------------------------------ 内部
def _field(raw: Any, name: str, fallback: Any) -> Any:
    if isinstance(raw, dict):
        return raw.get(name, fallback)
    return getattr(raw, name, fallback)


def _text(exc: BaseException) -> str:
    if isinstance(exc, ModelError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _notify(on_progress: Callable[[int, int], None] | None, done: int, total: int) -> None:
    if on_progress is None:
        return
    try:
        on_progress(done, total)
    except Exception:
        _console.debug("批量进度回调抛异常，已忽略")


def _cancelled(cancel: Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    try:
        if callable(cancel):
            return bool(cancel())
        is_set = getattr(cancel, "is_set", None)
        return bool(is_set()) if callable(is_set) else False
    except Exception:
        return False


def _workers(manager, count: int, max_workers: int | None) -> int:
    limit = int(max_workers) if max_workers else MAX_PARALLEL
    resident = int(getattr(getattr(manager, "settings", None), "max_resident", 0) or 0)
    if resident > 0:
        limit = min(limit, resident)
    return max(1, min(count, max(1, limit)))
