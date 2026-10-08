"""清单登记表：清单 id → 文件路径 + 归属 + 格式。

登记表是「程序知道有哪些清单」的唯一来源：`describe()` 列出它，`load()` 按它找文件。
包内置的清单在这里登记；插件想在运行期追加，可以 `ManifestKit.register()`。

格式分两种：

- `manifest`：已经是统一清单格式（见 `docs/MANIFEST_PROTOCOL.md`），可查询 / 可校验；
- `legacy`：历史的自定义 JSON（每个插件一份），只登记与读取，等各自批次迁移成 `manifest`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..runtime import paths

__all__ = ["FORMAT_LEGACY", "FORMAT_MANIFEST", "ManifestEntry", "ManifestRegistry", "builtin_entries"]

#: 统一清单格式
FORMAT_MANIFEST = "manifest"
#: 待迁移的历史格式
FORMAT_LEGACY = "legacy"


@dataclass(frozen=True)
class ManifestEntry:
    """一份清单的登记信息。"""

    id: str
    path: Path
    kind: str
    owner: str
    format: str = FORMAT_MANIFEST
    schema: str = "manifest"
    description: str = ""

    @property
    def managed(self) -> bool:
        """是否受清单机制完整管理（可查询 / 可校验 / 可变更）。"""
        return self.format == FORMAT_MANIFEST

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": str(self.path),
            "kind": self.kind,
            "owner": self.owner,
            "format": self.format,
            "schema": self.schema,
            "description": self.description,
            "managed": self.managed,
        }


def _plugin_data(plugin_id: str, name: str) -> Path:
    return paths.PLUGIN_DIR / plugin_id / ".data" / name


def builtin_entries() -> tuple[ManifestEntry, ...]:
    """程序内置的清单登记表。

    只有 `.configs/plugins.json`（`core.plugin_state`）还是历史格式：它是用户的
    插件开关状态，格式由插件服务自己定，登记只为备份 / 重置。插件 `.data/` 下的
    数据文件已在批 H5 与批 I 全部迁成统一格式。
    """
    core = paths.APP_DIR  # core 包目录（src/app/core）
    entries = [
        ManifestEntry(
            id="core.runtime",
            path=core / "runtime" / "runtime.json",
            kind="module-data",
            owner="core",
            description="应用名与版本、日志格式与模式表、口令散列参数",
        ),
        ManifestEntry(
            id="core.plugins",
            path=core / "plugins" / "plugins.json",
            kind="module-data",
            owner="core",
            description="插件协议：清单字段白名单、id 规则、生命周期阶段、选项类型表",
        ),
        ManifestEntry(
            id="core.plugin_state",
            path=paths.PLUGIN_STATE_FILE,
            kind="registry",
            owner="core",
            format=FORMAT_LEGACY,
            schema="legacy",
            description="插件启用状态与选项（历史格式：可读取 / 备份 / 重置）",
        ),
        ManifestEntry(
            id="lib.model.model_list",
            path=_plugin_data("lib.model", "model_list.json"),
            kind="catalog",
            owner="lib.model",
            description="模型模板清单：本地模型的可选项与下载来源",
        ),
        ManifestEntry(
            id="lib.model.runtime_profiles",
            path=_plugin_data("lib.model", "runtime_profiles.json"),
            kind="profiles",
            owner="lib.model",
            description="运行环境方案：每个 profile 一个独立 venv 与依赖清单",
        ),
        ManifestEntry(
            id="lib.model.api_templates",
            path=_plugin_data("lib.model", "api_templates.json"),
            kind="catalog",
            owner="lib.model",
            description="接口服务模板：供应商、base_url 与在售模型",
        ),
        ManifestEntry(
            id="lib.autolabel.rules",
            path=_plugin_data("lib.autolabel", "rules.json"),
            kind="registry",
            owner="lib.autolabel",
            description="自动标签出厂规则（统一清单格式，批 H5 迁移）",
        ),
        ManifestEntry(
            id="lib.autolabel.align",
            path=_plugin_data("lib.autolabel", "align.json"),
            kind="registry",
            owner="lib.autolabel",
            description="数据类型 ↔ 模型对齐表（统一清单格式，批 H5 迁移）",
        ),
    ]
    for plugin_id in (
        "builtin.viewer.archive",
        "builtin.viewer.audio",
        "builtin.viewer.image",
        "builtin.viewer.markdown",
        "builtin.viewer.spreadsheet",
        "builtin.viewer.text",
        "builtin.viewer.video",
    ):
        entries.append(
            ManifestEntry(
                id=f"{plugin_id}.viewer",
                path=_plugin_data(plugin_id, "viewer.json"),
                kind="registry",
                owner=plugin_id,
                description="查看器类型注册表：扩展名、类型、宿主与能力",
            )
        )
    for plugin_id in ("builtin.editor.text", "builtin.editor.office"):
        entries.append(
            ManifestEntry(
                id=f"{plugin_id}.editor",
                path=_plugin_data(plugin_id, "editor.json"),
                kind="registry",
                owner=plugin_id,
                description="编辑器类型注册表：扩展名、类型、宿主与能力",
            )
        )
    entries.append(
        ManifestEntry(
            id="example.ui_extension.info",
            path=_plugin_data("example.ui_extension", "info.json"),
            kind="module-data",
            owner="example.ui_extension",
            description="示例插件的展示数据（说明、贡献的扩展点与订阅的事件）",
        )
    )
    return tuple(entries)


class ManifestRegistry:
    """登记表：按 id 增删查。"""

    def __init__(self, entries=None) -> None:
        self._entries: dict[str, ManifestEntry] = {}
        self._builtin = entries is None
        for entry in entries if entries is not None else builtin_entries():
            self.register(entry)
        self._sources: dict[str, str] = {}

    def _refresh_builtin(self) -> None:
        """内置清单的路径按当前 paths 现算，避免登记表冻结在 import 那一刻。"""
        if not self._builtin:
            return
        for entry in builtin_entries():
            self._entries[entry.id] = entry

    # 登记 ---------------------------------------------------------------
    def register(self, entry: ManifestEntry, *, source: str = "") -> ManifestEntry:
        if not isinstance(entry, ManifestEntry):
            raise TypeError("登记的必须是 ManifestEntry")
        if entry.id in self._entries:
            raise ValueError(f"清单 id 重复：{entry.id}")
        self._entries[entry.id] = entry
        if source:
            self._sources[entry.id] = source
        return entry

    def entry(self, manifest_id: str) -> ManifestEntry:
        self._refresh_builtin()
        try:
            return self._entries[str(manifest_id)]
        except KeyError:
            from .errors import ManifestNotFoundError

            raise ManifestNotFoundError(f"没有登记名为 {manifest_id!r} 的清单") from None

    def entries(self) -> tuple[ManifestEntry, ...]:
        self._refresh_builtin()
        return tuple(self._entries.values())

    def ids(self) -> tuple[str, ...]:
        self._refresh_builtin()
        return tuple(self._entries)

    def owner_entries(self, owner: str) -> tuple[ManifestEntry, ...]:
        self._refresh_builtin()
        return tuple(entry for entry in self._entries.values() if entry.owner == owner)

    def drop_owner(self, owner: str) -> tuple[str, ...]:
        """注销某个插件登记的全部清单（插件卸载 / 停用时用）。"""
        gone = [key for key, entry in self._entries.items() if entry.owner == owner]
        for key in gone:
            self._entries.pop(key, None)
            self._sources.pop(key, None)
        return tuple(gone)

    def drop(self, manifest_id: str) -> bool:
        """注销单份清单，返回是否真的注销了。

        运行期建立又删除的临时清单（见 `app.core.journals`）走这条路：它用完即删，
        登记信息再留着只会让 `ids()` 越滚越长，`describe()` 里出现指向已删文件的空条目。
        内置清单即使被注销，下次 `_refresh_builtin()` 也会按 id 加回来，所以这里不设白名单。
        """
        key = str(manifest_id)
        if key not in self._entries:
            return False
        self._entries.pop(key, None)
        self._sources.pop(key, None)
        return True

    def __contains__(self, manifest_id: object) -> bool:
        self._refresh_builtin()
        return str(manifest_id) in self._entries

    def __len__(self) -> int:
        self._refresh_builtin()
        return len(self._entries)
