"""模型调度门面：给其它插件用的 `run_batch()` / `acquire()` / `list_models()` 等。

协议 v2 之后这个门面属于**模型工具库自己**（以前在 `app.sdk.models`）：程序本体不再认识
「模型」这件事，只保留扩展点 `model.open`。

用法（依赖方在 `depends` 里声明 `lib.model`，然后静态导入）：

```python
from dm_plugin.lib.model.api import BatchRequest, run_batch
```

调度接口实例由 `ModelLibraryPlugin.setup()` 通过 `attach()` 注入，`teardown()` 时注入 `None`；
没注入（插件没启用，或者纯规则路径只导入类型）时：返回空列表一类的查询给空值，真正要跑模型
的 `acquire()` / `invoke()` / `run_batch()` 抛 `ModelError`——模型不能凭空变出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from app.sdk.console import console_for
from app.sdk.errors import SdkError

from .errors import ModelError

__all__ = [
    "MODEL_EXTENSION",
    "BatchRequest",
    "BatchResult",
    "ModelInfo",
    "ModelLease",
    "acquire",
    "attach",
    "available",
    "capabilities",
    "create_from_template",
    "download_model",
    "invoke",
    "list_models",
    "loaded",
    "model_by_id",
    "page_route",
    "provider",
    "requirements",
    "run_batch",
    "supports_batch",
    "templates",
]


class ModelInfo(Protocol):
    """模型记录：门面只认这些字段，真正的记录类由本插件的 `registry.py` 定义。"""

    id: str
    name: str
    kind: str
    state: str
    capabilities: tuple[str, ...]
    backend: str
    size_bytes: int
    plugin_id: str
    description: str


class ModelLease(Protocol):
    """一次模型使用租约：`invoke()` 调用，`close()` 归还（也支持 with 语句）。"""

    model_id: str
    capability: str
    adapter: str

    def invoke(
        self,
        task: str,
        payload: dict | None = None,
        *,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class BatchRequest:
    """批量调用里的一条请求。

    `task` / `payload` 和单条 `invoke()` 同义；`key` 由调用方随便填（常用条目 id），
    结果里原样带回，方便把结果对回自己的数据。
    """

    task: str
    payload: dict = field(default_factory=dict)
    key: str = ""
    model_id: str = ""


@dataclass(frozen=True)
class BatchResult:
    """批量调用里的一条结果：`ok` 为假时看 `error`。"""

    key: str
    ok: bool
    value: Any = None
    error: str = ""
    model_id: str = ""


#: 本模块的日志出口（插件统一走 SDK 控制台）
_console = console_for("lib.model")

#: 模型调度接口名：本插件在清单 `provides` 里声明，程序与其它插件按它取
MODEL_EXTENSION = "model.open"

#: 调度接口实例（`ModelLibraryPlugin.setup()` 注入，`teardown()` 清空）
_API: object | None = None


def attach(api: object | None) -> None:
    """注入 / 清空调度接口（只由本插件的生命周期调用）。"""
    global _API
    _API = api


# --------------------------------------------------------------------- 调度
def provider() -> object | None:
    """本插件提供的调度接口（`model.open`）；没载入时为 None。"""
    return _API


def available() -> bool:
    """有没有可用的模型接口（页面上据此提示「未启用模型工具库」）。"""
    return provider() is not None


def _api() -> object:
    api = provider()
    if api is None:
        raise ModelError("没有启用模型工具库（lib.model），无法调用模型")
    return api


def list_models(kind: str = "", capability: str = "") -> tuple[ModelInfo, ...]:
    """已登记的模型；`kind` 为 local/external，`capability` 按能力筛选。没有插件时为空。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.list_models(kind=kind, capability=capability))
    except Exception:
        _console.exception(f"读取模型列表失败：kind={kind} capability={capability}")
        return ()


def model_by_id(model_id: str) -> ModelInfo | None:
    """按 id 取模型记录；没有插件或找不到时返回 None。"""
    api = provider()
    if api is None:
        return None
    try:
        return api.model_by_id(model_id)
    except Exception:
        _console.exception(f"读取模型失败：{model_id}")
        return None


def capabilities() -> tuple[str, ...]:
    """所有已登记模型的能力并集（调用方据此判断能不能接）。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.capabilities())
    except Exception:
        _console.exception("读取模型能力失败")
        return ()


def loaded() -> tuple[str, ...]:
    """当前已加载（常驻）的模型 id。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.loaded())
    except Exception:
        _console.exception("读取已加载模型失败")
        return ()


def acquire(
    model_id: str = "",
    capability: str = "",
    task: str = "",
    timeout: float | None = None,
) -> ModelLease:
    """取一份模型租约：`model_id` 与 `capability` 至少给一个。

    插件负责按需加载、排队与超时；`timeout` 内拿不到就抛 `ModelBusyError`。
    """
    api = _api()
    try:
        return api.acquire(model_id=model_id, capability=capability, task=task, timeout=timeout)
    except ModelError:
        raise
    except Exception as exc:
        _console.exception(f"取模型租约失败：{model_id or capability}")
        raise ModelError(f"取模型失败：{exc}") from exc


def invoke(
    model_id: str = "",
    capability: str = "",
    task: str = "",
    payload: dict | None = None,
    *,
    stream: bool = False,
    timeout: float | None = None,
) -> Any:
    """一次性调用：内部取租约 → 调用 → 归还（长任务请自己 `acquire()`）。"""
    lease = acquire(model_id=model_id, capability=capability, task=task, timeout=timeout)
    try:
        return lease.invoke(task, payload, stream=stream, timeout=timeout)
    finally:
        close = getattr(lease, "close", None)
        if callable(close):
            close()


def supports_batch() -> bool:
    """调度接口有没有实现批量调用（`run_batch`）。"""
    api = provider()
    return api is not None and callable(getattr(api, "run_batch", None))


def run_batch(
    requests: Iterable[BatchRequest],
    *,
    model_id: str = "",
    capability: str = "",
    on_progress: Callable[..., None] | None = None,
    cancel: Callable[[], bool] | None = None,
    max_workers: int | None = None,
) -> tuple[BatchResult, ...]:
    """把一批请求交给调度接口统一跑，一次返回全部结果（顺序与 `requests` 对应）。

    同一个模型只加载一次、按类型分批，避免调用方为几千个条目反复取租约 / 切换模型：
    这件事由本插件的 `batch.py` 实现，调用方只管准备请求、收结果。

    - `on_progress(done, total)`：每完成一条回调一次（在工作线程里）；
    - `cancel()`：返回 True 表示调用方要求尽快停下（已跑完的不回滚）；
    - `requests[i].model_id` 可以为空，表示按 `capability` 自动挑模型；
    - 调度接口没实现批量调用时抛 `SdkError`；单条失败写在 `BatchResult.error` 里，不中断整批。
    """
    api = _api()
    runner = getattr(api, "run_batch", None)
    if not callable(runner):
        raise SdkError("模型工具库不支持批量请求（请更新模型工具库插件）")
    prepared = list(requests)
    try:
        results = runner(
            prepared,
            model_id=model_id,
            capability=capability,
            on_progress=on_progress,
            cancel=cancel,
            max_workers=max_workers,
        )
    except ModelError:
        raise
    except Exception as exc:
        _console.exception(f"批量调用模型失败：{model_id or capability}")
        raise ModelError(f"批量调用失败：{exc}") from exc
    return tuple(results)


def templates(capability: str = "", lightweight_only: bool = False) -> tuple[dict, ...]:
    """预定义方案（出厂模板，每条带 `lightweight` 标识）；没有插件时为空。"""
    api = provider()
    lister = getattr(api, "templates", None) if api is not None else None
    if not callable(lister):
        return ()
    try:
        return tuple(dict(row) for row in lister(capability=capability, lightweight_only=lightweight_only))
    except Exception:
        _console.exception(f"读取模型模板失败：capability={capability}")
        return ()


def create_from_template(key: str, name: str = "") -> dict:
    """按预定义方案登记一个**草稿**：只写登记表，不下载权重、不装运行环境。

    返回草稿的简要信息（`model_id` / `name` / `state` / `draft` 等）；挑不到模板抛 `ModelError`。
    """
    api = _api()
    maker = getattr(api, "create_from_template", None)
    if not callable(maker):
        raise SdkError("模型工具库不支持预定义方案（请更新模型工具库插件）")
    try:
        record = maker(key, name=name)
    except ModelError:
        raise
    except Exception as exc:
        _console.exception(f"按预定义方案登记模型失败：{key}")
        raise ModelError(f"登记模型草稿失败：{exc}") from exc
    return _record_brief(record)


def download_model(model_id: str) -> dict:
    """把某个本地模型缺的权重排进模型页的下载队列（**调用方负责先让用户确认**）。

    返回 `{"ok": bool, "model_id": str, "queued": [...], "hint": str}`；
    调度接口不支持这种方法时抛 `SdkError`。
    """
    api = _api()
    handler = getattr(api, "download", None)
    if not callable(handler):
        raise SdkError("模型工具库不支持一键下载（请更新模型工具库插件）")
    try:
        return dict(handler(model_id))
    except SdkError:
        raise
    except Exception as exc:
        _console.exception(f"一键下载权重失败：{model_id}")
        raise SdkError(f"一键下载失败：{exc}") from exc


def page_route() -> str:
    """模型管理页的路由（可直接喂给 `ui.open_page()`）；没有模型插件时为空串。"""
    api = provider()
    route = getattr(api, "page_route", None) if api is not None else None
    if not callable(route):
        return ""
    try:
        return str(route())
    except Exception:
        _console.exception("读取模型页路由失败")
        return ""


def requirements(model_id: str) -> dict:
    """这个模型还缺什么才能跑：`{"ok": bool, "missing": [...], "hint": str}`。"""
    api = provider()
    checker = getattr(api, "requirements", None) if api is not None else None
    if not callable(checker):
        return {"ok": False, "missing": ["未启用模型工具库"], "hint": "未启用模型工具库"}
    try:
        return dict(checker(model_id))
    except Exception as exc:
        _console.exception(f"读取模型缺口失败：{model_id}")
        return {"ok": False, "missing": [str(exc)], "hint": f"读取模型缺口失败：{exc}"}


def _record_brief(record: object) -> dict:
    """把模型记录压成普通字典：调用方不该依赖本插件的记录类。"""
    state = str(getattr(record, "state", "") or "")
    capabilities = getattr(record, "capabilities", ()) or ()
    return {
        "backend": str(getattr(record, "backend", "") or ""),
        "capabilities": [str(item) for item in capabilities],
        "draft": state == "draft",
        "kind": str(getattr(record, "kind", "") or ""),
        "model_id": str(getattr(record, "id", "") or ""),
        "name": str(getattr(record, "name", "") or ""),
        "state": state,
    }
