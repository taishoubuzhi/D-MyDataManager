"""模型管理器：登记表读写 + 按需加载 / 引用计数 / 淘汰 / 空闲卸载。

设计要点（其它模块与页面都按这个契约使用）：
- 模型**不常驻**：`acquire()` 才加载，用引用计数守住，没人用了按 `max_resident` 与 `idle_unload_sec` 淘汰；
- 同一模型同时只加载一次（单飞），加载期间其它请求在条件变量上排队，超时抛 `ModelBusyError`；
- 淘汰 = `Adapter.stop()`（worker 会被杀进程，真正释放显存）；
- 所有公开方法都可在任意线程调用（页面在工作线程里下载 / 加载）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.sdk.console import console_for

from app.sdk.errors import ModelBusyError, ModelError

from .adapters import AdapterError, build_adapter
from .constants import KIND_EXTERNAL, KIND_LOCAL, STATE_DRAFT, STATE_ERROR, STATE_LOADING, STATE_READY
from .paths import local_dir
from .record import ModelRecord
from .registry import ModelRegistry, model_registry
from .settings import ModelSettings, load_settings

_console = console_for("builtin.lib.model")

__all__ = ["Lease", "ModelManager", "WEIGHT_SUFFIXES", "model_manager"]

#: 认为是「权重文件」的后缀（扫描 / 识别用）
WEIGHT_SUFFIXES = (
    ".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth", ".ckpt", ".msgpack", ".h5", ".tflite", ".mlmodel", ".npz",
)


class Lease:
    """一次使用租约：借出时已加载，`close()` 归还（引用计数减一）。"""

    def __init__(self, manager: "ModelManager", record: ModelRecord, capability: str, adapter) -> None:
        self._manager = manager
        self._record = record
        self._closed = False
        self.model_id = record.id
        self.capability = capability
        self.adapter = getattr(adapter, "name", "")
        self._adapter = adapter

    @property
    def record(self) -> ModelRecord:
        return self._record

    def invoke(self, task: str, payload: dict | None = None, *, stream: bool = False, timeout: float | None = None):
        if self._closed:
            raise ModelError(f"租约已归还：{self.model_id}")
        adapter = self._manager.adapter_of(self.model_id)
        if adapter is None:
            raise ModelError(f"模型已被卸载：{self.model_id}")
        try:
            self._manager.mark_used(self.model_id)
            return adapter.invoke(task, payload, stream=stream, timeout=timeout)
        except AdapterError as exc:
            raise ModelError(str(exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._manager.release(self.model_id)

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def info(self) -> dict:
        return {"model_id": self.model_id, "capability": self.capability, "adapter": self.adapter}


class ModelManager:
    """插件内唯一的模型调度器。"""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        settings: ModelSettings | None = None,
    ) -> None:
        # 不能用 `or`：空的 ModelRegistry 因为 __len__ == 0 是假值，会被换成全局登记表
        self.registry = registry if registry is not None else model_registry
        self.settings = settings if settings is not None else load_settings()
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._adapters: dict[str, object] = {}
        self._refs: dict[str, int] = {}
        self._loading: set[str] = set()
        self._last_used: dict[str, float] = {}

    # ------------------------------------------------------------ 基础
    def reload(self) -> ModelManager:
        """重读设置与登记表（页面「刷新」用）。"""
        self.settings = load_settings()
        self.registry.load()
        return self

    def save(self) -> bool:
        return self.registry.save()

    def list_models(self, kind: str = "", capability: str = "") -> tuple[ModelRecord, ...]:
        return self.registry.find(kind=kind, capability=capability)

    def model_by_id(self, model_id: str) -> ModelRecord | None:
        return self.registry.get(model_id)

    def capabilities(self) -> tuple[str, ...]:
        return self.registry.capabilities()

    def loaded(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._adapters))

    def adapter_of(self, model_id: str):
        with self._lock:
            return self._adapters.get(str(model_id))

    def running_info(self) -> dict[str, dict]:
        with self._lock:
            return {key: adapter.info() for key, adapter in self._adapters.items()}

    # ------------------------------------------------------------ 登记
    def add_local(self, name: str, *, capabilities=(), description="", files=(), source=None, runtime=None) -> ModelRecord:
        record = ModelRecord.new_local(
            name,
            existing=self.registry.ids(),
            capabilities=tuple(capabilities),
            description=description,
            files=tuple(files),
            source=dict(source or {}),
            runtime=dict(runtime or {}),
        )
        record.sync_files()
        record.state = STATE_READY if record.files else STATE_DRAFT
        self.registry.add(record)
        self.save()
        return record

    def add_external(
        self,
        name: str,
        *,
        base_url: str,
        model: str = "",
        capabilities=("chat",),
        description="",
        adapter: str = "",
        api_key_ref: str = "",
        headers=None,
        params=None,
    ) -> ModelRecord:
        api = {
            "base_url": str(base_url).rstrip("/"),
            "model": str(model),
            "api_key_ref": str(api_key_ref),
            "headers": dict(headers or {}),
            "params": dict(params or {}),
        }
        if adapter:
            api["adapter"] = adapter
        record = ModelRecord.new_external(
            name,
            existing=self.registry.ids(),
            capabilities=tuple(capabilities),
            description=description,
            api=api,
        )
        record.state = STATE_READY
        self.registry.add(record)
        self.save()
        return record

    def update(self, record: ModelRecord) -> ModelRecord:
        record.touch()
        self.registry.update(record)
        self.save()
        return record

    def remove(self, model_id: str, *, delete_files: bool = False) -> bool:
        record = self.registry.get(model_id)
        if record is None:
            return False
        self.unload(model_id)
        if record.is_local and delete_files:
            self._delete_files(model_id)
        self.registry.remove(model_id)
        return self.save()

    def _delete_files(self, model_id: str) -> None:
        import shutil

        target = local_dir(model_id)
        if target.is_dir():
            try:
                shutil.rmtree(target)
                _console.info(f"已删除模型目录：{target}")
            except OSError as exc:
                _console.warning(f"删除模型目录失败：{target}（{exc}）")

    def scan_directory(self, path, *, capabilities=(), recursive: bool = True) -> list[ModelRecord]:
        """扫描目录，把「看起来像权重」的目录登记成本地模型（拖进来即可用）。"""
        root = Path(path)
        if not root.is_dir():
            raise ModelError(f"不是目录：{root}")
        found: list[ModelRecord] = []
        candidates = [root] if _has_weights(root) else []
        if not candidates:
            for child in sorted(root.iterdir()):
                if child.is_dir() and _has_weights(child):
                    candidates.append(child)
            if recursive and not candidates:
                for child in sorted(root.rglob("*")):
                    if child.is_dir() and _has_weights(child):
                        candidates.append(child)
        for folder in candidates:
            name = folder.name if folder != root else root.name
            # 按扫描到的原目录找旧登记：不能拿 make_local_id(name, ids) 去找，
            # 那样重扫时算出的 id 已经带 -2 后缀，等于每次重扫都新登记一条。
            existing = next(
                (
                    item
                    for item in self.registry.find(kind=KIND_LOCAL)
                    if str(item.source.get("path") or "") == str(folder)
                ),
                None,
            )
            if existing is not None:
                existing.sync_files()
                self.registry.update(existing)
                found.append(existing)
                continue
            record = ModelRecord.new_local(
                name,
                existing=self.registry.ids(),
                capabilities=tuple(capabilities),
                files=tuple(_relative_files(folder)),
                source={"provider": "local", "path": str(folder)},
            )
            record.sync_files()
            record.state = STATE_READY
            self.registry.add(record)
            found.append(record)
        if found:
            self.save()
        return found

    # ------------------------------------------------------------ 取用
    def acquire(self, model_id: str = "", capability: str = "", task: str = "", timeout: float | None = None) -> Lease:
        record = self._resolve(model_id=model_id, capability=capability, task=task)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with self._cond:
            # 单飞：同一个模型正在加载时排队
            while record.id in self._loading:
                if not self._cond.wait(_remaining(deadline)):
                    raise ModelBusyError(f"模型正在加载，等待超时：{record.name}")
            if record.id in self._adapters:
                self._refs[record.id] = self._refs.get(record.id, 0) + 1
                self._last_used[record.id] = time.monotonic()
                return Lease(self, record, self._capability_of(record, capability), self._adapters[record.id])
            if not self._evict_for_slot(deadline):
                raise ModelBusyError(f"常驻模型数已达上限（{self.settings.max_resident}），暂时没有空闲槽位")
            self._loading.add(record.id)
        try:
            adapter = self._load(record)
        except Exception:
            with self._cond:
                self._loading.discard(record.id)
                self._cond.notify_all()
            raise
        with self._cond:
            self._loading.discard(record.id)
            self._adapters[record.id] = adapter
            self._refs[record.id] = 1
            self._last_used[record.id] = time.monotonic()
            self._cond.notify_all()
        _console.info(f"模型已加载：{record.name}（{adapter.info()}）")
        return Lease(self, record, self._capability_of(record, capability), adapter)

    def invoke(self, model_id="", capability="", task="", payload=None, *, stream=False, timeout=None):
        lease = self.acquire(model_id=model_id, capability=capability, task=task, timeout=timeout)
        try:
            return lease.invoke(task, payload, stream=stream, timeout=timeout)
        finally:
            lease.close()

    def release(self, model_id: str) -> None:
        with self._cond:
            key = str(model_id)
            if key not in self._refs:
                return
            self._refs[key] = max(0, self._refs[key] - 1)
            self._last_used[key] = time.monotonic()
            self._cond.notify_all()

    def mark_used(self, model_id: str) -> None:
        with self._lock:
            self._last_used[str(model_id)] = time.monotonic()

    def unload(self, model_id: str) -> bool:
        """强制卸载（有人用着也卸）：先停适配器再摘登记。"""
        key = str(model_id)
        with self._lock:
            adapter = self._adapters.pop(key, None)
            self._refs.pop(key, None)
            self._last_used.pop(key, None)
        if adapter is None:
            return False
        _stop(adapter)
        _console.info(f"模型已卸载：{key}")
        return True

    def unload_all(self) -> int:
        with self._lock:
            keys = list(self._adapters)
        return sum(1 for key in keys if self.unload(key))

    def sweep_idle(self) -> int:
        """空闲超时且没人用的模型自动卸载（插件里的定时器调用）。"""
        idle = self.settings.idle_unload_sec
        if idle <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            stale = [
                key
                for key, used in self._last_used.items()
                if self._refs.get(key, 0) <= 0 and now - used >= idle
            ]
        return sum(1 for key in stale if self.unload(key))

    def shutdown(self) -> None:
        """插件卸载：停干净所有子进程。"""
        self.unload_all()

    # ------------------------------------------------------------ 内部
    def _resolve(self, model_id: str = "", capability: str = "", task: str = "") -> ModelRecord:
        record = self.registry.get(model_id) if model_id else None
        if record is None and capability:
            for candidate in self.registry.find(capability=capability):
                if candidate.ready:
                    record = candidate
                    break
        if record is None and task:
            for candidate in self.registry.all():
                if task in candidate.capabilities and candidate.ready:
                    record = candidate
                    break
        if record is None:
            raise ModelError(f"没有找到可用模型：{model_id or capability or task}")
        if record.is_local and not record.ready:
            raise ModelError(f"模型还没就绪（{record.state_label}）：{record.name}")
        return record

    @staticmethod
    def _capability_of(record: ModelRecord, capability: str) -> str:
        return capability or (record.capabilities[0] if record.capabilities else "")

    def _load(self, record: ModelRecord):
        try:
            record.state = STATE_LOADING
            self.registry.update(record)
            adapter = build_adapter(record, self.settings)
            adapter.start()
            record.state = STATE_READY
            self.registry.update(record)
            return adapter
        except (AdapterError, ModelError) as exc:
            record.state = STATE_ERROR
            self.registry.update(record)
            _console.warning(f"模型加载失败：{record.name}（{exc}）")
            raise ModelError(f"模型加载失败：{exc}") from exc
        except Exception as exc:
            record.state = STATE_ERROR
            self.registry.update(record)
            _console.exception(f"模型加载异常：{record.name}")
            raise ModelError(f"模型加载失败：{exc}") from exc

    def _evict_for_slot(self, deadline: float | None) -> bool:
        """腾出一个常驻槽位（只在锁内调用）：先挑空闲最久、没人用的。"""
        limit = self.settings.max_resident
        while len(self._adapters) >= limit:
            idle_keys = [key for key in self._adapters if self._refs.get(key, 0) <= 0]
            if not idle_keys:
                return False
            victim = min(idle_keys, key=lambda key: self._last_used.get(key, 0.0))
            adapter = self._adapters.pop(victim, None)
            self._refs.pop(victim, None)
            self._last_used.pop(victim, None)
            if adapter is not None:
                _stop(adapter)
                _console.info(f"常驻上限已满，已卸载最久未用的模型：{victim}")
        return True


def _stop(adapter) -> None:
    try:
        adapter.stop()
    except Exception:
        _console.exception(f"停止适配器失败：{getattr(adapter, 'name', adapter)}")


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _has_weights(folder: Path) -> bool:
    """目录自己这一层有没有权重文件（下载中的 `.part` 不算）。

    只看顶层：递归判断会让「装着一堆模型的上层目录」也算成模型，
    `scan_directory()` 于是把整棵目录当成一个模型登记。嵌套模型由 `rglob` 分支找。
    """
    if not folder.is_dir():
        return False
    for item in folder.iterdir():
        if item.is_file() and item.suffix.lower() in WEIGHT_SUFFIXES:
            return True
    return (folder / "config.json").is_file()


def _relative_files(folder: Path) -> list[str]:
    return sorted(item.relative_to(folder).as_posix() for item in folder.rglob("*") if item.is_file())


def make_local_id(name: str, existing=()) -> str:
    from .record import make_id

    return make_id(KIND_LOCAL, name, existing)


#: 全局管理器（插件 setup 时 reload()）
model_manager = ModelManager()
