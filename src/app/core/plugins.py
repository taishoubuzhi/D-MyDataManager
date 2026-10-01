"""插件清单的数据模型与解析。

一个插件可以是：

* 代码里的内置插件（`src/app/plugins/` 内，随程序分发，只能启用 / 禁用，不能删除）；
* 外部插件目录（放在运行期的 `plugins/` 下，可以导入、修改、删除）。

外部插件目录里必须有 `plugin.json`：

```json
{
  "id": "sample_viewer",
  "name": "示例查看器",
  "version": "1.0.0",
  "kind": "viewer",
  "entry": "viewer.py",
  "extensions": ["sample"],
  "capabilities": ["read"],
  "description": "说明文字",
  "author": "作者"
}
```

`entry` 指向的模块里要有 `register(api)`，通过 `api.add_viewer(...)` 注册查看器。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .viewers import normalize_suffix

MANIFEST_NAME = "plugin.json"

#: 插件类型：目前只有「打开方式」（查看器），后续可扩展
KIND_VIEWER = "viewer"
PLUGIN_KINDS: dict[str, str] = {KIND_VIEWER: "打开方式"}

SOURCE_BUILTIN = "builtin"
SOURCE_EXTERNAL = "external"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$")


class PluginError(Exception):
    """插件清单不合法或安装失败。"""


@dataclass
class PluginInfo:
    """插件的基本信息 + 运行状态（`enabled` / `error` 由插件服务填充）。"""

    id: str
    name: str
    version: str = ""
    kind: str = KIND_VIEWER
    description: str = ""
    author: str = ""
    entry: str = ""
    source: str = SOURCE_EXTERNAL
    path: Path | None = None
    extensions: tuple[str, ...] = field(default_factory=tuple)
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    error: str = ""
    note: str = ""

    @property
    def builtin(self) -> bool:
        """内置插件不能删除，也不能改清单，只能启用 / 禁用。"""
        return self.source == SOURCE_BUILTIN

    @property
    def kind_label(self) -> str:
        return PLUGIN_KINDS.get(self.kind, self.kind or "未知")

    @property
    def state_label(self) -> str:
        if self.error:
            return "异常"
        return "已启用" if self.enabled else "已禁用"

    @property
    def extensions_text(self) -> str:
        return "、".join(self.extensions) if self.extensions else "—"

    @property
    def source_label(self) -> str:
        return "内置" if self.builtin else "外部"

    def clone(self, **fields) -> "PluginInfo":
        data = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "description": self.description,
            "author": self.author,
            "entry": self.entry,
            "source": self.source,
            "path": self.path,
            "extensions": self.extensions,
            "capabilities": self.capabilities,
            "enabled": self.enabled,
            "error": self.error,
            "note": self.note,
        }
        data.update(fields)
        return PluginInfo(**data)


def _as_text(value: object, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _extension_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [part for part in re.split(r"[,\s;]+", value) if part]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = []
    seen: list[str] = []
    for item in items:
        suffix = normalize_suffix(item)
        if suffix and suffix not in seen:
            seen.append(suffix)
    return tuple(seen)


def parse_manifest(data: dict, path: Path | None = None, source: str = SOURCE_EXTERNAL) -> PluginInfo:
    """把 `plugin.json` 的字典转成 `PluginInfo`，不合法时抛 `PluginError`。"""
    if not isinstance(data, dict):
        raise PluginError("插件清单必须是一个 JSON 对象")
    plugin_id = _as_text(data.get("id"))
    if not plugin_id:
        raise PluginError("插件清单缺少 id")
    if not _ID_PATTERN.match(plugin_id):
        raise PluginError(f"插件 id 不合法：{plugin_id}（只允许字母、数字、下划线、点与短横线）")
    kind = _as_text(data.get("kind"), KIND_VIEWER) or KIND_VIEWER
    if kind not in PLUGIN_KINDS:
        raise PluginError(f"暂不支持的插件类型：{kind}")
    entry = _as_text(data.get("entry"))
    if not entry and source != SOURCE_BUILTIN:
        raise PluginError("插件清单缺少 entry（入口文件）")
    if entry and path is not None and not (Path(path) / entry).is_file():
        raise PluginError(f"入口文件不存在：{entry}")
    extensions = _extension_list(data.get("extensions"))
    if kind == KIND_VIEWER and not extensions and source != SOURCE_BUILTIN:
        raise PluginError("查看器插件至少要声明一个扩展名")
    capabilities = data.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = [part for part in re.split(r"[,\s;]+", capabilities) if part]
    return PluginInfo(
        id=plugin_id,
        name=_as_text(data.get("name"), plugin_id) or plugin_id,
        version=_as_text(data.get("version")),
        kind=kind,
        description=_as_text(data.get("description")),
        author=_as_text(data.get("author")),
        entry=entry,
        source=source,
        path=Path(path) if path is not None else None,
        extensions=extensions,
        capabilities=tuple(str(item) for item in capabilities),
        enabled=bool(data.get("enabled", True)),
    )


def load_manifest(plugin_dir: Path, source: str = SOURCE_EXTERNAL) -> PluginInfo:
    """读取插件目录里的 `plugin.json`。"""
    folder = Path(plugin_dir)
    manifest = folder / MANIFEST_NAME
    if not manifest.is_file():
        raise PluginError(f"缺少 {MANIFEST_NAME}：{folder.name}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"{MANIFEST_NAME} 读取失败：{exc}") from exc
    return parse_manifest(data, path=folder, source=source)
