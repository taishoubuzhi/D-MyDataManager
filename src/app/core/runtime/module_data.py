"""按模块读取同目录 JSON 数据文件。

core 的分层模块把「固定、但需要能整体替换、且对外可见」的数据放在自己目录下的
`<模块名>.json` 里（协议见 `docs/MANIFEST_PROTOCOL.md`）。本模块是导入期的薄适配层：
解析与结构校验都交给清单机制（`app.core.manifest`），这里只把错误翻译成
`ModuleDataError`、并把 `key` 从项里摘掉（读它的模块只关心字段本身）。

文件格式（必须项）：

    {"manifest": "1", "id": "core.runtime", "version": "1", "kind": "module-data",
     "items": [{"key": "app_name", "value": "D-MyDataManager"}]}

`items` 每项至少要有一个 `key`，其余字段交给读它的模块自己解释（最常用的是
`value`，也可以带 `label`、`description` 等说明性字段）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..manifest.errors import ManifestFormatError, ManifestNotFoundError
from ..manifest.kit import parse, read_json
from ..manifest.registry import ManifestEntry

REQUIRED_KEYS = ("manifest", "id", "version", "kind", "items")


class ModuleDataError(ManifestFormatError):
    """模块数据文件缺失、格式不合法，或缺少访问的键。"""


@dataclass(frozen=True)
class ModuleData:
    """一个模块数据文件的内容。"""

    path: Path
    meta: dict[str, Any]
    items: dict[str, dict[str, Any]]

    def value(self, key: str) -> Any:
        """取 `key` 这一项的 `value`；缺失即报错（数据文件是唯一事实来源）。"""
        item = self.items.get(key)
        if item is None:
            raise ModuleDataError(f"{self.path} 缺少数据项 {key!r}")
        if "value" not in item:
            raise ModuleDataError(f"{self.path} 的数据项 {key!r} 没有 value 字段")
        return item["value"]


def load_module_data(module_file: str, name: str) -> ModuleData:
    """读同目录下的 `<name>.json`。"""
    path = Path(module_file).with_name(f"{name}.json")
    entry = ManifestEntry(id=f"module.{name}", path=path, kind="module-data", owner="core")
    try:
        data = parse(entry, read_json(path))
    except (ManifestNotFoundError, ManifestFormatError) as exc:
        raise ModuleDataError(str(exc)) from exc
    items = {key: {name: value for name, value in item.items() if name != "key"} for key, item in data.items.items()}
    meta = {key: data.meta[key] for key in REQUIRED_KEYS if key != "items"}
    return ModuleData(path=path, meta=meta, items=items)


__all__ = ["ModuleData", "ModuleDataError", "REQUIRED_KEYS", "load_module_data"]
