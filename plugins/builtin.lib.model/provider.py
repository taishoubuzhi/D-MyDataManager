"""`model.open` 接口实现：把管理器暴露给程序门面与其它插件。

跨插件调用方只走 `app.sdk.models`（→ 本对象的同名方法），所以这里的签名要和
`src/app/sdk/models.py` 的 `ModelInfo` / `ModelLease` 协议一致。
"""

from __future__ import annotations

from typing import Any

from .manager import Lease, ModelManager

__all__ = ["ModelOpenApi"]


class ModelOpenApi:
    """模型调度接口：查登记表、取租约、直接调用、管理登记项。"""

    def __init__(self, ctx, manager: ModelManager) -> None:
        self.ctx = ctx
        self.manager = manager

    # ------------------------------------------------------------ 查询
    def list_models(self, kind: str = "", capability: str = ""):
        return self.manager.list_models(kind=kind, capability=capability)

    def model_by_id(self, model_id: str):
        return self.manager.model_by_id(model_id)

    def capabilities(self) -> tuple[str, ...]:
        return self.manager.capabilities()

    def loaded(self) -> tuple[str, ...]:
        return self.manager.loaded()

    # ------------------------------------------------------------ 取用
    def acquire(self, model_id: str = "", capability: str = "", task: str = "", timeout: float | None = None) -> Lease:
        return self.manager.acquire(model_id=model_id, capability=capability, task=task, timeout=timeout)

    def invoke(
        self,
        model_id: str = "",
        capability: str = "",
        task: str = "",
        payload: dict | None = None,
        *,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Any:
        return self.manager.invoke(
            model_id=model_id,
            capability=capability,
            task=task,
            payload=payload,
            stream=stream,
            timeout=timeout,
        )

    # ------------------------------------------------------------ 管理（页面用）
    def add_local(self, *args, **kwargs):
        return self.manager.add_local(*args, **kwargs)

    def add_external(self, *args, **kwargs):
        return self.manager.add_external(*args, **kwargs)

    def update(self, record):
        return self.manager.update(record)

    def remove(self, model_id: str, *, delete_files: bool = False) -> bool:
        return self.manager.remove(model_id, delete_files=delete_files)

    def scan_directory(self, path, **kwargs):
        return self.manager.scan_directory(path, **kwargs)

    def unload(self, model_id: str) -> bool:
        return self.manager.unload(model_id)

    def unload_all(self) -> int:
        return self.manager.unload_all()

    def sweep_idle(self) -> int:
        return self.manager.sweep_idle()

    def running_info(self) -> dict:
        return self.manager.running_info()

    def save(self) -> bool:
        return self.manager.save()

    def reload(self) -> None:
        self.manager.reload()
