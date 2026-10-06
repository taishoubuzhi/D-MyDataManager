"""清单机制扩展接口：插件读写「受程序管理的 JSON 清单」。

清单管的是「固定、但可能需要被外部替换或扩展」的注册表、列表与映射（模型模板清单、
运行环境方案、查看器/编辑器类型注册表……）；用户运行设置（`.configs/config.json`）不在其中。
协议见 `docs/MANIFEST_PROTOCOL.md`，实现见 `app.core.manifest`。

插件侧用法：

    from app import sdk

    for entry in sdk.manifest.entries():
        print(entry["id"], entry["owner"], entry["format"])
    rows = sdk.manifest.query("core.runtime")
    sdk.manifest.update("core.runtime", [{"key": "app_name", "value": "改名了"}])

插件自己的 `.data/*.json` 已经是统一清单格式，用 `ctx.data(name)` 读进来后可以直接解析：

    payload = ctx.data("viewer")
    record = sdk.manifest.record_of(payload, ctx.plugin_id)   # 单条记录（查看器 / 编辑器）
    items = sdk.manifest.items_of(payload)                    # 记录列表（模型模板 / 运行环境）
    about = sdk.manifest.value_of(payload, "about", "")       # 模块数据的某个值

约定：

- 清单 id 与插件 id 同规则（`^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$`）；
- 插件要登记自己目录下的数据文件，用 `register(path=..., id=..., kind=..., schema=...)`；
- 写入一律先备份后原子替换，备份保留最近 10 份（`.configs/backups/manifest/<id>/`）；
- 程序没提供实现时（脚本 / 单元测试），`available()` 返回 False，读接口返回空结果。

程序本体通过扩展接口 `app.manifest` 提供实现（见 `src/main.py`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from .errors import SdkError

__all__ = [
    "MANIFEST_EXTENSION",
    "available",
    "backup",
    "backups",
    "describe",
    "diff",
    "entries",
    "ids",
    "items_of",
    "load",
    "provider",
    "query",
    "raw",
    "record_of",
    "register",
    "reset",
    "update",
    "value_of",
    "write",
]

#: 清单机制接口：实现方是一个 `app.core.manifest.ManifestKit`。
MANIFEST_EXTENSION = "app.manifest"


def provider() -> object | None:
    """程序提供的清单接口（`app.manifest`）；没有（脚本、测试）时为 None。"""
    try:
        from ..core.plugins.extensions import extension_registry
    except Exception:
        return None
    return extension_registry.provider(MANIFEST_EXTENSION)


def available() -> bool:
    """有没有可用的清单接口（插件页据此提示「程序未提供清单接口」）。"""
    return provider() is not None


def _kit() -> Any:
    kit = provider()
    if kit is None:
        raise SdkError("程序没有提供清单接口 app.manifest")
    return kit


def ids() -> tuple[str, ...]:
    """已登记的全部清单 id。"""
    kit = provider()
    return tuple(kit.ids()) if kit is not None else ()


def entries() -> tuple[dict, ...]:
    """已登记清单的元信息（id、路径、归属、格式、是否受完整管理）。"""
    kit = provider()
    if kit is None:
        return ()
    return tuple(entry.as_dict() for entry in kit.entries())


def describe(manifest_id: str | None = None) -> Any:
    """清单元信息（给 id 返回一条，不给返回全部）。"""
    kit = provider()
    if kit is None:
        return [] if manifest_id is None else {}
    return kit.describe(manifest_id)


def load(manifest_id: str, *, validate: bool = True) -> Any:
    """读一份清单，返回 `app.core.manifest.ManifestData`。

    `items` 是 `key -> 项` 的字典，`value(key)` 取项的 `value` 字段。
    """
    return _kit().load(manifest_id, validate=validate)


def raw(manifest_id: str) -> Any:
    """原样返回文件内容（历史格式的清单只能这样读）。"""
    return _kit().raw(manifest_id)


def query(manifest_id: str, **filters: Any) -> list[dict]:
    """按字段筛选项（历史格式会报错，提示先迁移）。"""
    return list(_kit().query(manifest_id, **filters))


def diff(manifest_id: str, candidate: Any) -> list[str]:
    """对照当前文件与候选内容，返回差异行（空列表 = 没差别）。"""
    return list(_kit().diff(manifest_id, candidate))


def write(manifest_id: str, data: Any, *, backup: bool = True) -> Any:
    """整份替换清单内容（先校验、再备份、最后原子写）。"""
    return _kit().write(manifest_id, data, backup=backup)


def update(manifest_id: str, mutations: Any, *, backup: bool = True) -> Any:
    """按 key 变更：`{"key": ..., 字段...}` 合并，`{"key": ..., "remove": True}` 删项。"""
    return _kit().update(manifest_id, mutations, backup=backup)


def reset(manifest_id: str) -> Any:
    """用最近一次备份覆盖当前文件。"""
    return _kit().reset(manifest_id)


def backup(manifest_id: str) -> Path:
    """手动备份一份清单，返回备份文件路径。"""
    return Path(_kit().backup(manifest_id))


def backups(manifest_id: str) -> list[Path]:
    """已有备份（新的在前）。"""
    return list(_kit().backups(manifest_id))


def value_of(payload: Any, key: str, default: Any = None) -> Any:
    """从一份统一清单里取某项的 `value`（模块数据用）；没有就返回 `default`。"""
    item = record_of(payload, key)
    return item.get("value", default) if item else default


def items_of(payload: Any) -> list[dict]:
    """从一份统一清单里取 `items` 列表（每项都带非空 `key`）；不是清单就返回空表。

    插件的 `.data/*.json` 已经是统一清单格式（见 `docs/MANIFEST_PROTOCOL.md`），
    插件用 `ctx.data(name)` 读进来后用本函数取记录列表。
    """
    if not isinstance(payload, Mapping):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, Mapping) and str(item.get("key") or "")]


def record_of(payload: Any, key: str = "") -> dict:
    """从一份统一清单里取**一条**记录：按 `key` 精确匹配，`key` 为空时取第一条。

    查看器 / 编辑器插件的 `.data/viewer.json`、`.data/editor.json` 只有一条记录，
    `key` 就是插件 id；找不到返回空字典。
    """
    items = items_of(payload)
    if not items:
        return {}
    wanted = str(key or "")
    if wanted:
        for item in items:
            if str(item.get("key")) == wanted:
                return item
    return items[0] if not wanted else {}


def register(
    manifest_id: str,
    path: str | Path,
    *,
    kind: str = "catalog",
    owner: str = "",
    schema: str = "manifest",
    description: str = "",
    source: str = "",
) -> dict:
    """登记一份清单（插件把自己的数据文件交给清单机制管理时用）。"""
    from ..core.manifest.registry import ManifestEntry

    entry = _kit().register(
        ManifestEntry(
            id=manifest_id,
            path=Path(path),
            kind=kind,
            owner=owner,
            schema=schema,
            description=description,
        ),
        source=source,
    )
    logger.debug("已登记清单 {} → {}", entry.id, entry.path)
    return entry.as_dict()
