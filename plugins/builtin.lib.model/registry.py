"""模型注册表：登记项集合 + `registry.json` 读写。

注册表是模型工具库的唯一真相：页面、适配器、`model.open` 接口都从这里取记录。
"""

from __future__ import annotations

from pathlib import Path

from app.sdk.console import console_for

from app.sdk import storage

from .constants import KIND_EXTERNAL, KIND_LOCAL
from .paths import registry_file
from .record import ModelRecord

_console = console_for("builtin.lib.model")

__all__ = ["ModelRegistry", "model_registry", "reset"]

SCHEMA_VERSION = 1


class ModelRegistry:
    """进程内的模型集合：读写 JSON、增删改查、按类型 / 能力筛选。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._items: dict[str, ModelRecord] = {}

    # ------------------------------------------------------------ 落盘
    @property
    def path(self) -> Path:
        return self._path or registry_file()

    def load(self) -> int:
        """从磁盘读入（覆盖内存），返回载入条数。"""
        payload = storage.read_json(self.path, {})
        raw = payload.get("models") if isinstance(payload, dict) else None
        self._items = {}
        for item in raw or ():
            try:
                record = ModelRecord.from_dict(item)
            except Exception:
                _console.warning(f"跳过损坏的模型记录：{item}")
                continue
            if record.id:
                self._items[record.id] = record
        _console.info(f"模型登记表已载入：{len(self._items)} 条")
        return len(self._items)

    def save(self) -> bool:
        payload = {"version": SCHEMA_VERSION, "models": [item.to_dict() for item in self.all()]}
        return storage.write_json(self.path, payload)

    # ------------------------------------------------------------ 查询
    def all(self) -> tuple[ModelRecord, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def get(self, model_id: str) -> ModelRecord | None:
        return self._items.get(str(model_id or ""))

    def find(self, kind: str = "", capability: str = "") -> tuple[ModelRecord, ...]:
        return tuple(item for item in self.all() if item.matches(kind=kind, capability=capability))

    def locals(self) -> tuple[ModelRecord, ...]:
        return self.find(kind=KIND_LOCAL)

    def externals(self) -> tuple[ModelRecord, ...]:
        return self.find(kind=KIND_EXTERNAL)

    def capabilities(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.all():
            for capability in item.capabilities:
                if capability not in seen:
                    seen.append(capability)
        return tuple(seen)

    # ------------------------------------------------------------ 修改
    def add(self, record: ModelRecord) -> ModelRecord:
        if not record.id:
            raise ValueError("模型记录缺少 id")
        self._items[record.id] = record
        return record

    def update(self, record: ModelRecord) -> ModelRecord:
        record.touch()
        self._items[record.id] = record
        return record

    def remove(self, model_id: str) -> bool:
        return self._items.pop(str(model_id or ""), None) is not None

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, model_id: object) -> bool:
        return str(model_id) in self._items


#: 全局注册表：插件 setup() 里 load()，teardown() 里 clear()
model_registry = ModelRegistry()


def reset() -> None:
    """插件卸载时清空注册表（磁盘文件保留）。"""
    model_registry.clear()
