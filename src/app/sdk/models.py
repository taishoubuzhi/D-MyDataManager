"""模型扩展接口：程序本体只留一个扩展点与调度门面。

程序自己不实现任何模型调用，也不保存模型注册表：

1. 没启用模型插件时，`list_models()` 返回空、`acquire()` / `invoke()` 直接抛 `SdkError`；
2. 「有哪些模型、怎么下载、怎么加载、用哪个后端跑」全部由模型插件库
   `builtin.lib.model` 实现，并通过扩展接口 `model.open` 暴露成 `ModelOpenApi`；
3. 这里的函数都是门面：有插件就转调插件，没有插件就报错（模型不能凭空变出来）。

插件之间对接模型统一走这里：按 `model_id` 或 `capability` 取用，模型由插件内部按需加载，
调用方拿到的是一份租约（`ModelLease`），用完 `close()` 让插件按引用计数与空闲策略卸载。
"""

from __future__ import annotations

from typing import Any, Protocol

from loguru import logger

from .errors import ModelError, SdkError


class ModelInfo(Protocol):
    """模型记录：程序与本门面只认这些字段，真正的记录类由模型插件定义。"""

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


#: 模型调度接口（模型插件库实现）：注册表查询 / 取租约 / 直接调用 / 已加载列表
MODEL_EXTENSION = "model.open"


# --------------------------------------------------------------------- 调度
def provider() -> object | None:
    """模型插件提供的调度接口（`model.open`）；没启用模型插件时为 None。"""
    from ..core.extensions import extension_registry

    return extension_registry.provider(MODEL_EXTENSION)


def available() -> bool:
    """有没有可用的模型接口（页面上据此提示「未启用模型工具库」）。"""
    return provider() is not None


def _api() -> object:
    api = provider()
    if api is None:
        raise SdkError("程序没有提供模型接口 app.model")
    return api


def list_models(kind: str = "", capability: str = "") -> tuple[ModelInfo, ...]:
    """已登记的模型；`kind` 为 local/external，`capability` 按能力筛选。没有插件时为空。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.list_models(kind=kind, capability=capability))
    except Exception:
        logger.exception("读取模型列表失败：kind={} capability={}", kind, capability)
        return ()


def model_by_id(model_id: str) -> ModelInfo | None:
    """按 id 取模型记录；没有插件或找不到时返回 None。"""
    api = provider()
    if api is None:
        return None
    try:
        return api.model_by_id(model_id)
    except Exception:
        logger.exception("读取模型失败：{}", model_id)
        return None


def capabilities() -> tuple[str, ...]:
    """所有已登记模型的能力并集（调用方据此判断能不能接）。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.capabilities())
    except Exception:
        logger.exception("读取模型能力失败")
        return ()


def loaded() -> tuple[str, ...]:
    """当前已加载（常驻）的模型 id。"""
    api = provider()
    if api is None:
        return ()
    try:
        return tuple(api.loaded())
    except Exception:
        logger.exception("读取已加载模型失败")
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
        logger.exception("取模型租约失败：{}", model_id or capability)
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


__all__ = [
    "MODEL_EXTENSION",
    "ModelInfo",
    "ModelLease",
    "acquire",
    "available",
    "capabilities",
    "invoke",
    "list_models",
    "loaded",
    "model_by_id",
    "provider",
]
