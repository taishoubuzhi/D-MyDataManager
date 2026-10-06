"""清单工具包：读取 / 查询 / 对照 / 变更 / 重置 / 备份。

一份清单就是一个 JSON 文件（格式见 `docs/MANIFEST_PROTOCOL.md`）：

```json
{
  "manifest": "1",
  "id": "core.runtime",
  "version": "1",
  "kind": "module-data",
  "items": [{"key": "app_name", "value": "D-MyDataManager"}]
}
```

约定：`items` 里每一项都必须有 `key`，其余字段随 `kind` 自定义；同一份清单里
`key` 不得重复。读取用 `orjson`（没装就退回标准库），校验用 `fastjsonschema`
（没装就只做必须项与最小结构校验），写入走 `.part` + `os.replace` 原子替换，
每次变更前把旧文件备份到 `.configs/backups/manifest/<id>/<时间戳>.json`（保留最近 10 份）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..runtime import paths
from .errors import (
    ManifestBackupError,
    ManifestError,
    ManifestFormatError,
    ManifestNotFoundError,
    ManifestValidationError,
)
from .registry import (
    FORMAT_LEGACY,
    FORMAT_MANIFEST,
    ManifestEntry,
    ManifestRegistry,
    builtin_entries,
)

try:  # orjson 是推荐依赖（批 K 会写进 requirements）；缺失不影响功能
    import orjson as _orjson
except Exception:  # pragma: no cover - 取决于运行环境
    _orjson = None

try:  # 校验器同样是可缺依赖
    import fastjsonschema as _fastjsonschema
except Exception:  # pragma: no cover - 取决于运行环境
    _fastjsonschema = None

_logger = logging.getLogger(__name__)

#: 清单协议版本（顶层 `manifest` 字段）
MANIFEST_VERSION = "1"
#: 顶层必须项
REQUIRED_KEYS = ("manifest", "id", "version", "kind", "items")
#: 支持的清单类型
KINDS = ("module-data", "registry", "profiles", "catalog")
#: 清单 id 规则（与插件 id 同规则）
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
#: 备份目录保留份数
BACKUP_KEEP = 10
#: 原子写入用的临时后缀
PART_SUFFIX = ".part"

_SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
_COMPILED: dict[str, Any] = {}
_PART_SEQ = 0
#: `ManifestData.value()` 不传默认值时的「没有默认值」标记
_MISSING = object()


# ---------------------------------------------------------------- JSON 读写
def read_json(path: str | Path) -> Any:
    """读 JSON 文件（缺失抛 `ManifestNotFoundError`，内容坏抛 `ManifestFormatError`）。"""
    target = Path(path)
    try:
        payload = target.read_bytes()
    except FileNotFoundError:
        raise ManifestNotFoundError(f"清单文件不存在：{target}") from None
    except OSError as exc:
        raise ManifestError(f"读不了清单文件 {target}：{exc}") from exc
    try:
        if _orjson is not None:
            return _orjson.loads(payload)
        return json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ManifestFormatError(f"清单文件不是合法 JSON：{target}（{exc}）") from exc


def dump_json(data: Any) -> bytes:
    """把数据序列化成带缩进的 UTF-8 JSON 字节（结尾补换行）。"""
    if _orjson is not None:
        text = _orjson.dumps(data, option=_orjson.OPT_INDENT_2).decode("utf-8")
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    return (text if text.endswith("\n") else text + "\n").encode("utf-8")


def write_json_atomic(path: str | Path, data: Any) -> Path:
    """原子写：先写同目录的 `.part` 再 `os.replace`，中途失败不会留下半截文件。"""
    global _PART_SEQ
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _PART_SEQ += 1
    part = target.with_name(f"{target.name}.{os.getpid()}.{_PART_SEQ}{PART_SUFFIX}")
    try:
        part.write_bytes(dump_json(data))
        os.replace(part, target)
    except OSError as exc:
        try:
            part.unlink()
        except OSError:
            pass
        raise ManifestError(f"写不了清单文件 {target}：{exc}") from exc
    return target


# ------------------------------------------------------------------ 校验
def _schema_path(name: str) -> Path:
    return _SCHEMA_DIR / f"{name}.json"


def schema_names() -> tuple[str, ...]:
    """可用的 schema 名（`schema/` 下的文件名去掉后缀）。"""
    if not _SCHEMA_DIR.is_dir():
        return ()
    return tuple(sorted(item.stem for item in _SCHEMA_DIR.iterdir() if item.suffix == ".json"))


def load_schema(name: str) -> Any:
    """读一份 JSON Schema（带缓存）。"""
    if name in _COMPILED:
        return _COMPILED[name]
    path = _schema_path(name)
    if not path.is_file():
        raise ManifestValidationError(f"没有名为 {name!r} 的 schema（{path}）")
    schema = read_json(path)
    _COMPILED[name] = schema
    return schema


def validate(entry: ManifestEntry, data: Any) -> None:
    """按登记表的 schema 校验内容；校验器缺失时退化为最小结构校验。"""
    schema = load_schema(entry.schema)
    if _fastjsonschema is not None:
        try:
            _fastjsonschema.validate(schema, data)
        except _fastjsonschema.JsonSchemaException as exc:
            raise ManifestValidationError(f"{entry.id}（{entry.path}）不符合 {entry.schema} 约定：{exc.message}") from exc
        return
    _validate_minimal(entry, data)


def _validate_minimal(entry: ManifestEntry, data: Any) -> None:
    """没有 fastjsonschema 时的兜底校验：顶层对象 + 必须项 + items 形状。"""
    if entry.schema == "legacy":
        if not isinstance(data, (dict, list)):
            raise ManifestValidationError(f"{entry.id} 的顶层必须是对象或数组")
        return
    if not isinstance(data, dict):
        raise ManifestValidationError(f"{entry.id}（{entry.path}）的顶层必须是对象")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ManifestValidationError(f"{entry.id}（{entry.path}）缺少必须项：{'、'.join(missing)}")
    if not isinstance(data["items"], list):
        raise ManifestValidationError(f"{entry.id}（{entry.path}）的 items 必须是数组")
    for index, item in enumerate(data["items"]):
        if not isinstance(item, dict) or not str(item.get("key", "")):
            raise ManifestValidationError(f"{entry.id}（{entry.path}）的 items[{index}] 缺少 key")


def parse(entry: ManifestEntry, data: Any) -> "ManifestData":
    """把读到的 JSON 解析成 `ManifestData`（只做结构解析，不做 schema 校验）。"""
    if entry.format == FORMAT_LEGACY:
        return ManifestData(entry=entry, meta={}, items={}, raw=data)
    if not isinstance(data, dict):
        raise ManifestFormatError(f"{entry.id}（{entry.path}）的顶层必须是对象")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ManifestFormatError(f"{entry.id}（{entry.path}）缺少必须项：{'、'.join(missing)}")
    raw_items = data["items"]
    if not isinstance(raw_items, list):
        raise ManifestFormatError(f"{entry.id}（{entry.path}）的 items 必须是数组")
    items: dict[str, dict] = {}
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ManifestFormatError(f"{entry.id}（{entry.path}）的 items[{index}] 必须是对象")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise ManifestFormatError(f"{entry.id}（{entry.path}）的 items[{index}] 缺少 key")
        if key in items:
            raise ManifestFormatError(f"{entry.id}（{entry.path}）的键重复：{key!r}")
        items[key] = dict(item)
    meta = {key: value for key, value in data.items() if key != "items"}
    return ManifestData(entry=entry, meta=meta, items=items, raw=data)


# ------------------------------------------------------------------ 数据结构
@dataclass(frozen=True)
class ManifestData:
    """一份清单的内容：`meta` 是除 items 外的字段，`items` 是 key → 项。"""

    entry: ManifestEntry
    meta: dict
    items: dict[str, dict]
    raw: Any = None

    @property
    def path(self) -> Path:
        return self.entry.path

    @property
    def version(self) -> str:
        return str(self.meta.get("version", ""))

    @property
    def kind(self) -> str:
        return str(self.meta.get("kind", self.entry.kind))

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.items)

    def get(self, key: str, default: Any = None) -> dict:
        return self.items.get(key, default)

    def value(self, key: str, default: Any = _MISSING) -> Any:
        """取某项的 `value` 字段。

        不传 `default` 时严格：缺项或缺 `value` 字段直接报错（模块数据文件在导入期
        用它取常量，宁可启动失败也不能悄悄用错值）；传了 `default` 就退化为宽松取值。
        """
        item = self.items.get(key)
        if item is None:
            if default is _MISSING:
                raise ManifestFormatError(f"{self.path} 缺少数据项 {key!r}")
            return default
        if "value" not in item:
            if default is _MISSING:
                raise ManifestFormatError(f"{self.path} 的数据项 {key!r} 没有 value 字段")
            return default
        return item["value"]

    def list(self) -> list[dict]:
        """按文件里的顺序返回全部项。"""
        order = [str(item.get("key")) for item in (self.raw or {}).get("items", [])] if isinstance(self.raw, dict) else []
        if order:
            return [self.items[key] for key in order if key in self.items]
        return list(self.items.values())

    def filters(self, **filters: Any) -> list[dict]:
        """按字段筛选（值为元组 / 列表时表示「命中其中任意一个」）。"""
        result = []
        for item in self.list():
            if all(_matches(item.get(key), value) for key, value in filters.items()):
                result.append(item)
        return result


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (tuple, list, set, frozenset)):
        return actual in expected
    return actual == expected


# ------------------------------------------------------------------- 工具包
class ManifestKit:
    """清单机制的门面：插件与程序本体都用这一套接口。

    `kit = ManifestKit()` 用内置登记表 + 默认备份目录；测试可以传入自己的
    `ManifestRegistry` 与 `backup_root`。
    """

    def __init__(self, registry: ManifestRegistry | None = None, *, backup_root: str | Path | None = None) -> None:
        self.registry = registry if registry is not None else ManifestRegistry()
        self._backup_root = Path(backup_root) if backup_root is not None else None

    @property
    def backup_root(self) -> Path:
        """备份根目录：没显式指定时按**当前** `paths.CONFIG_DIR` 现算。

        与 `ManifestRegistry._refresh_builtin()` 是同一个道理：模块级 `manifest_kit` 在
        import 时就建好了，若把 `paths.CONFIG_DIR` 冻在那一刻，测试（每个用例都会重挂
        资源根）与运行期改配置都会把备份写到别处，甚至互相覆盖。
        """
        if self._backup_root is not None:
            return self._backup_root
        return paths.CONFIG_DIR / "backups" / "manifest"

    # 登记 ---------------------------------------------------------------
    def register(self, entry: ManifestEntry, *, source: str = "") -> ManifestEntry:
        return self.registry.register(entry, source=source)

    def entries(self) -> tuple[ManifestEntry, ...]:
        return self.registry.entries()

    def ids(self) -> tuple[str, ...]:
        return self.registry.ids()

    def describe(self, manifest_id: str | None = None) -> Any:
        """看清单的元信息：id、路径、归属、格式、大小、项数、是否可读。"""
        if manifest_id is None:
            return [self.describe(item.id) for item in self.registry.entries()]
        entry = self.registry.entry(manifest_id)
        info = entry.as_dict()
        info["exists"] = entry.path.is_file()
        if info["exists"]:
            stat = entry.path.stat()
            info["size"] = stat.st_size
            info["mtime"] = stat.st_mtime
        else:
            info["size"] = 0
            info["mtime"] = 0.0
        if entry.managed:
            try:
                data = self.load(manifest_id, validate=False)
                info["version"] = data.version
                info["kind"] = data.kind
                info["count"] = len(data.items)
                info["error"] = ""
            except ManifestError as exc:
                info["version"] = ""
                info["count"] = 0
                info["error"] = str(exc)
        else:
            info["version"] = ""
            raw = self.raw(manifest_id) if info["exists"] else None
            info["count"] = len(raw) if isinstance(raw, list) else 0
            info["error"] = ""
        return info

    # 读 -----------------------------------------------------------------
    def load(self, manifest_id: str, *, validate: bool = True) -> ManifestData:
        """读一份清单（`validate=False` 只做结构解析，方便看坏文件里到底有什么）。"""
        entry = self.registry.entry(manifest_id)
        data = read_json(entry.path)
        if validate:
            validate_manifest(entry, data)
        return parse(entry, data)

    def raw(self, manifest_id: str) -> Any:
        """原样返回文件内容（`legacy` 清单只有这条路可走）。"""
        return read_json(self.registry.entry(manifest_id).path)

    def query(self, manifest_id: str, **filters: Any) -> list[dict]:
        """按字段筛选项（`legacy` 清单不可查，会报错提示改用 `raw()`）。"""
        data = self.load(manifest_id)
        if not data.entry.managed:
            raise ManifestFormatError(f"{manifest_id} 还是历史格式（legacy），请用 raw() 读，迁移后再查询")
        return data.filters(**filters)

    # 对照 ---------------------------------------------------------------
    def diff(self, manifest_id: str, candidate: Any) -> list[str]:
        """对照当前文件与候选内容，返回人类可读的差异行（空列表 = 没差别）。"""
        entry = self.registry.entry(manifest_id)
        wanted = candidate if not isinstance(candidate, (str, Path)) else read_json(candidate)
        if not entry.managed:
            current = read_json(entry.path)
            if current == wanted:
                return []
            return [f"~ {entry.id}：整份内容不同（历史格式，只能整体比）"]
        left = parse(entry, read_json(entry.path)).items
        right = parse(entry, wanted).items
        return _item_lines(entry.id, left, right)

    # 写 -----------------------------------------------------------------
    def write(self, manifest_id: str, data: Mapping, *, backup: bool = True) -> ManifestData:
        """整份替换清单内容（先校验、再备份、最后原子写）。"""
        entry = self.registry.entry(manifest_id)
        if not entry.managed:
            raise ManifestFormatError(f"{manifest_id} 还是历史格式（legacy），迁移成统一格式后才能写")
        payload = dict(data)
        validate_manifest(entry, payload)
        if backup and entry.path.is_file():
            self.backup(manifest_id)
        write_json_atomic(entry.path, payload)
        return self.load(manifest_id, validate=False)

    def update(self, manifest_id: str, mutations: Iterable[Mapping], *, backup: bool = True) -> ManifestData:
        """按 key 变更：给出 `{"key": ..., ...}` 合并字段，`{"key": ..., "remove": True}` 删项。"""
        current = self.load(manifest_id, validate=False)
        if not current.entry.managed:
            raise ManifestFormatError(f"{manifest_id} 还是历史格式（legacy），迁移成统一格式后才能改")
        items = {key: dict(item) for key, item in current.items.items()}
        for patch in mutations:
            key = str(patch.get("key", "") or "")
            if not key:
                raise ManifestFormatError(f"变更项缺少 key：{patch!r}")
            if patch.get("remove"):
                items.pop(key, None)
                continue
            merged = dict(items.get(key, {}))
            merged.update({name: value for name, value in patch.items() if name != "remove"})
            merged["key"] = key
            items[key] = merged
        payload = dict(current.meta)
        payload["items"] = [items[key] for key in sorted(items)]
        return self.write(manifest_id, payload, backup=backup)

    def reset(self, manifest_id: str) -> ManifestData:
        """用最近一次备份覆盖当前文件。"""
        entry = self.registry.entry(manifest_id)
        saved = self.backups(manifest_id)
        if not saved:
            raise ManifestBackupError(f"{manifest_id} 没有可用的备份")
        data = read_json(saved[0])
        validate_manifest(entry, data)
        write_json_atomic(entry.path, data)
        return self.load(manifest_id, validate=False)

    # 备份 ---------------------------------------------------------------
    def backup_dir(self, manifest_id: str) -> Path:
        self.registry.entry(manifest_id)  # 未登记直接报错
        return self.backup_root / manifest_id

    def backups(self, manifest_id: str) -> list[Path]:
        """已有的备份，新的在前。"""
        folder = self.backup_dir(manifest_id)
        if not folder.is_dir():
            return []
        return sorted((item for item in folder.glob("*.json") if item.is_file()), key=lambda item: item.name, reverse=True)

    def backup(self, manifest_id: str) -> Path:
        """把当前文件复制到备份目录，并只保留最近 `BACKUP_KEEP` 份。"""
        entry = self.registry.entry(manifest_id)
        if not entry.path.is_file():
            raise ManifestBackupError(f"{manifest_id} 还没有文件（{entry.path}），无从备份")
        folder = self.backup_dir(manifest_id)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp}.json"
        seq = 1
        while target.exists():
            seq += 1
            target = folder / f"{stamp}-{seq}.json"
        try:
            target.write_bytes(entry.path.read_bytes())
        except OSError as exc:
            raise ManifestBackupError(f"备份 {manifest_id} 失败：{exc}") from exc
        self._prune(folder)
        return target

    @staticmethod
    def _prune(folder: Path) -> None:
        files = sorted((item for item in folder.glob("*.json") if item.is_file()), key=lambda item: item.name, reverse=True)
        for stale in files[BACKUP_KEEP:]:
            try:
                stale.unlink()
            except OSError as exc:  # 删不掉只记日志，不能因为清理失败让变更回滚
                _logger.warning("清理旧备份失败：%s：%s", stale, exc)


def validate_manifest(entry: ManifestEntry, data: Any) -> None:
    """校验内容：历史格式只查顶层形状，统一格式先解析再走 schema。"""
    if entry.format == FORMAT_LEGACY:
        _validate_minimal(entry, data)
        return
    parse(entry, data)
    validate(entry, data)


def _item_lines(manifest_id: str, left: dict[str, dict], right: dict[str, dict]) -> list[str]:
    lines: list[str] = []
    for key in sorted(set(right) - set(left)):
        lines.append(f"+ {manifest_id}.{key}")
    for key in sorted(set(left) - set(right)):
        lines.append(f"- {manifest_id}.{key}")
    for key in sorted(set(left) & set(right)):
        before, after = left[key], right[key]
        if before == after:
            continue
        fields = sorted(set(before) | set(after))
        changes = [f"{name}: {before.get(name)!r} → {after.get(name)!r}" for name in fields if before.get(name) != after.get(name)]
        lines.append(f"~ {manifest_id}.{key}（{'；'.join(changes)}）")
    return lines


#: 全局共享的工具包（程序本体用；插件通过 `app.sdk.manifest` 拿到同一个）
manifest_kit = ManifestKit()

__all__ = [
    "BACKUP_KEEP",
    "KINDS",
    "MANIFEST_VERSION",
    "REQUIRED_KEYS",
    "ManifestData",
    "ManifestKit",
    "dump_json",
    "load_schema",
    "manifest_kit",
    "parse",
    "read_json",
    "schema_names",
    "validate",
    "validate_manifest",
    "write_json_atomic",
]
