"""插件协议核心：清单白名单、依赖解析、`dm_plugin` 命名空间与载入阶段。

协议全文见 `plugins/PLUGIN_PROTOCOL.md`（设计稿 `.logs/_rewrite/plugin_refactor_plan.md`）。
这里只做「核心必须认识」的事：校验清单、解析依赖与版本范围、把插件目录挂成
`dm_plugin.<id>` 包、按阶段载入并把失败定位到具体阶段。
"""

from __future__ import annotations

import importlib
import json
import re
import sys
import types
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..sdk.errors import DependencyError, ManifestError, PluginError
from ..sdk.plugin import Plugin, collect_plugin_classes
from ..sdk.version import SDK_VERSION, parse_range, satisfies
from .plugin_options import PluginOptionError, PluginOptionSpec, defaults, parse_options, settings_text
from .version import MANAGER_VERSION

MANIFEST_NAME = "plugin.json"

SOURCE_BUILTIN = "builtin"
SOURCE_EXTERNAL = "external"

#: 插件 id 要能直接拼进 `dm_plugin.<id>` 的导入路径，所以必须是合法的小写标识符路径
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

#: 协议字段白名单：出现别的字段一律报错，避免「插件类型与插件混杂」式的冗余复发
PROTOCOL_FIELDS = frozenset(
    {
        "id",
        "name",
        "version",
        "api_version",
        "entry",
        "class",
        "manager_version",
        "depends",
        "incompatible",
        "load_after",
        "provides",
        "options",
        "data",
        "libraries",
        "description",
        "author",
        "builtin",
        "enabled",
    }
)

#: 已取消的类型字段
REMOVED_TYPE_FIELDS = ("kind", "kinds", "kind_label", "kind_description", "kind_requires_extensions")

#: 已移出协议、改由 `data/` 承载的字段
REMOVED_DATA_FIELDS = ("extensions", "capabilities")

#: 载入阶段（诊断、插件页与自检共用）
PHASES = ("manifest", "dependency", "import", "construct", "setup")

DM_PACKAGE = "dm_plugin"


@dataclass(frozen=True)
class PluginDependency:
    """一条依赖声明。"""

    id: str
    version: str = ""
    optional: bool = False

    @property
    def text(self) -> str:
        suffix = ""
        if self.version:
            suffix += f" {self.version}"
        if self.optional:
            suffix += "（可选）"
        return f"{self.id}{suffix}"


@dataclass(frozen=True)
class LibrarySpec:
    """库插件对外暴露的一个模块。"""

    name: str
    module: str
    description: str = ""

    @property
    def text(self) -> str:
        return f"{self.name} → {self.module}"


@dataclass
class PluginInfo:
    """一个插件的协议信息（载入后还会带上贡献与阶段错误）。"""

    id: str
    name: str
    version: str = ""
    api_version: str = ""
    description: str = ""
    author: str = ""
    entry: str = ""
    class_name: str = ""
    path: Path | None = None
    manifest: dict = field(default_factory=dict)
    depends: tuple[PluginDependency, ...] = ()
    incompatible: tuple[str, ...] = ()
    load_after: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    libraries: tuple[LibrarySpec, ...] = ()
    data: dict[str, str] = field(default_factory=dict)
    manager_version: str = ""
    builtin: bool = False
    enabled: bool = True
    error: str = ""
    error_phase: str = ""
    note: str = ""
    options_title: str = ""
    options: tuple[PluginOptionSpec, ...] = ()
    settings: dict[str, object] = field(default_factory=dict)
    classes: tuple[str, ...] = field(default=(), repr=False)
    contributions: tuple[str, ...] = field(default=(), repr=False)

    # ------------------------------------------------------------ 展示
    @property
    def source(self) -> str:
        return SOURCE_BUILTIN if self.builtin else SOURCE_EXTERNAL

    @property
    def source_label(self) -> str:
        return "内置" if self.builtin else "外部"

    @property
    def state_label(self) -> str:
        if self.error:
            return "载入失败" if self.error_phase else "异常"
        return "已启用" if self.enabled else "已禁用"

    @property
    def kind_label(self) -> str:
        """插件类型：声明了 libraries 的是库插件，其余是功能插件。"""
        return "库插件" if self.libraries else "功能插件"

    @property
    def state_tone(self) -> str:
        """状态徽章的色调（ok / plain / error），颜色由界面层决定。"""
        if self.error:
            return "error"
        return "ok" if self.enabled else "plain"

    @property
    def depends_ids(self) -> tuple[str, ...]:
        return tuple(dep.id for dep in self.depends)

    @property
    def depends_text(self) -> str:
        return "、".join(dep.text for dep in self.depends) if self.depends else "无"

    @property
    def incompatible_text(self) -> str:
        return "、".join(self.incompatible) if self.incompatible else "无"

    @property
    def libraries_text(self) -> str:
        return "、".join(spec.text for spec in self.libraries) if self.libraries else "—"

    @property
    def data_text(self) -> str:
        return "、".join(f"{key} → {value}" for key, value in self.data.items()) if self.data else "—"

    @property
    def contributions_text(self) -> str:
        return "、".join(self.contributions) if self.contributions else "—"

    @property
    def classes_text(self) -> str:
        return "、".join(self.classes) if self.classes else "—"

    @property
    def provides_text(self) -> str:
        return "、".join(self.provides) if self.provides else "—"

    @property
    def manager_text(self) -> str:
        return self.manager_version or f">={MANAGER_VERSION}"

    @property
    def api_text(self) -> str:
        return self.api_version or f">={SDK_VERSION}"

    @property
    def author_text(self) -> str:
        return self.author or "未填写"

    @property
    def version_text(self) -> str:
        return self.version or "未填写"

    @property
    def error_text(self) -> str:
        if not self.error:
            return "—"
        if self.error_phase:
            return f"[{self.error_phase}] {self.error}"
        return self.error

    @property
    def options_label(self) -> str:
        return self.options_title or f"{self.name} 选项"

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    @property
    def settings_text(self) -> str:
        return settings_text(self.options, self.settings) or "默认"

    def option_spec(self, key: str) -> PluginOptionSpec | None:
        for spec in self.options:
            if spec.key == key:
                return spec
        return None

    def clone(self, **fields: object) -> "PluginInfo":
        return replace(self, **fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 清单解析


def _text(value: object) -> str:
    return str(value or "").strip()


def _as_list(value: object) -> tuple[str, ...]:
    """把字符串或字符串列表规范成去重元组。"""
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        raw: list = value.replace(",", " ").replace(";", " ").split()
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise ManifestError(f"清单字段需要字符串或列表，收到：{type(value).__name__}")
    result: list[str] = []
    for item in raw:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _flag(value: object, default: bool = False) -> bool:
    """清单里的布尔字段：缺省取默认值，写了就必须是 JSON 布尔。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ManifestError(f"清单字段需要布尔值，收到：{type(value).__name__}")


def _require_id(value: object, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise ManifestError(f"插件清单缺少 {field_name}")
    if not PLUGIN_ID_PATTERN.match(text):
        raise ManifestError(
            f"插件 id 不合法：{text}（小写字母开头，点分段，每段只含小写字母、数字与下划线）"
        )
    return text


def _check_removed_fields(data: dict) -> None:
    for name in REMOVED_TYPE_FIELDS:
        if data.get(name) not in (None, "", (), []):
            raise ManifestError(f"插件清单已取消类型字段：{name}（协议不再区分插件类型）")
    for name in REMOVED_DATA_FIELDS:
        if data.get(name) not in (None, "", (), []):
            raise ManifestError(f"插件清单不再支持字段 {name}：请放进 data/ 由对应库插件读取")


def _check_unknown_fields(data: dict) -> None:
    unknown = sorted(name for name in data if name not in PROTOCOL_FIELDS)
    if unknown:
        raise ManifestError("插件清单出现未知字段：" + "、".join(unknown))


def _check_manager_version(spec: str) -> str:
    if not spec:
        return ""
    try:
        parse_range(spec)
    except Exception as exc:  # 版本范围写错也要能被指出
        raise ManifestError(f"manager_version 不是合法版本范围：{spec}（{exc}）") from exc
    if not satisfies(MANAGER_VERSION, spec):
        raise ManifestError(f"需要程序版本 {spec}，当前 {MANAGER_VERSION}")
    return spec


def _check_api_version(spec: str) -> str:
    if not spec:
        raise ManifestError('插件清单缺少 api_version（适配的 SDK 版本范围，例如 ">=1.0 <2.0"）')
    try:
        parse_range(spec)
    except Exception as exc:
        raise ManifestError(f"api_version 不是合法版本范围：{spec}（{exc}）") from exc
    if not satisfies(SDK_VERSION, spec):
        raise ManifestError(f"需要 SDK 版本 {spec}，当前 {SDK_VERSION}")
    return spec


def parse_dependencies(value: object, plugin_id: str) -> tuple[PluginDependency, ...]:
    """解析 `depends`：支持 `["a", "b"]` 简写与 `[{"id": ..., "version": ..., "optional": ...}]`。"""
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError("插件清单的 depends 必须是一个列表")
    result: list[PluginDependency] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            dep_id, spec, optional = _text(item), "", False
        elif isinstance(item, dict):
            dep_id = _text(item.get("id"))
            spec = _text(item.get("version"))
            optional = bool(item.get("optional", False))
            unknown = sorted(name for name in item if name not in {"id", "version", "optional"})
            if unknown:
                raise ManifestError(f"依赖声明出现未知字段：{'、'.join(unknown)}")
        else:
            raise ManifestError("depends 的每一项必须是插件 id 或对象")
        if not PLUGIN_ID_PATTERN.match(dep_id):
            raise ManifestError(f"依赖插件 id 不合法：{dep_id}")
        if dep_id == plugin_id:
            raise ManifestError("插件不能依赖自己")
        if dep_id in seen:
            raise ManifestError(f"依赖插件重复声明：{dep_id}")
        if spec:
            try:
                parse_range(spec)
            except Exception as exc:
                raise ManifestError(f"依赖 {dep_id} 的版本范围不合法：{spec}（{exc}）") from exc
        seen.add(dep_id)
        result.append(PluginDependency(dep_id, spec, optional))
    return tuple(result)


def parse_libraries(value: object, plugin_id: str, path: Path | None) -> tuple[LibrarySpec, ...]:
    """解析 `libraries`：声明的模块必须存在、是 `.py` 且非空。"""
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError("插件清单的 libraries 必须是一个列表")
    result: list[LibrarySpec] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ManifestError("libraries 的每一项都必须是一个对象")
        name = _text(item.get("name"))
        module = _text(item.get("module"))
        if not name:
            raise ManifestError("libraries 缺少 name")
        if name in seen:
            raise ManifestError(f"库重复声明：{name}")
        if not module:
            raise ManifestError(f"库 {name} 缺少 module")
        if not module.endswith(".py"):
            raise ManifestError(f"库 {name} 的 module 必须是 .py 文件：{module}")
        if path is not None:
            target = Path(path) / module
            if not target.is_file():
                raise ManifestError(f"库 {name} 声明的模块不存在：{module}")
            if not target.stat().st_size:
                raise ManifestError(f"库 {name} 声明的模块是空文件：{module}")
        seen.add(name)
        result.append(LibrarySpec(name, module, _text(item.get("description"))))
    return tuple(result)


def parse_data(value: object, path: Path | None) -> dict[str, str]:
    """解析 `data`：键非空、路径相对且不越界、文件存在。"""
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ManifestError("插件清单的 data 必须是一个对象（键 → 相对路径）")
    result: dict[str, str] = {}
    for key, raw in value.items():
        name = _text(key)
        rel = _text(raw)
        if not name:
            raise ManifestError("data 的键不能为空")
        if not rel:
            raise ManifestError(f"data 的 {name} 缺少路径")
        target = Path(rel)
        if target.is_absolute() or ".." in target.parts:
            raise ManifestError(f"data 的 {name} 路径必须相对插件目录：{rel}")
        if path is not None and not (Path(path) / target).is_file():
            raise ManifestError(f"data 的 {name} 指向的文件不存在：{rel}")
        result[name] = rel
    return result


def parse_manifest(data: dict, path: Path | None = None, builtin: bool = False) -> PluginInfo:
    """按插件协议校验清单字典，返回 `PluginInfo`。"""
    if not isinstance(data, dict):
        raise ManifestError("插件清单必须是一个 JSON 对象")
    _check_removed_fields(data)
    _check_unknown_fields(data)
    plugin_id = _require_id(data.get("id"), "id")
    name = _text(data.get("name"))
    if not name:
        raise ManifestError("插件清单缺少 name")
    is_builtin = builtin or bool(data.get("builtin", False))
    folder = Path(path) if path is not None else None
    entry = _text(data.get("entry"))
    if not entry:
        if not is_builtin:
            raise ManifestError("插件清单缺少 entry（入口文件）")
    elif folder is not None and not (folder / entry).is_file():
        raise ManifestError(f"入口文件不存在：{entry}")
    class_name = _text(data.get("class"))
    if class_name and not class_name.isidentifier():
        raise ManifestError(f"class 必须是合法的类名：{class_name}")
    provides = _as_list(data.get("provides"))
    for item in provides:
        parts = item.split(".")
        if not item or not all(part.isidentifier() for part in parts):
            raise ManifestError(f"扩展接口名不合法：{item}")
    try:
        options = parse_options(data.get("options"))
    except PluginOptionError as exc:
        raise ManifestError(str(exc)) from exc
    return PluginInfo(
        id=plugin_id,
        name=name,
        version=_text(data.get("version")),
        api_version=_check_api_version(_text(data.get("api_version"))),
        description=_text(data.get("description")),
        author=_text(data.get("author")),
        entry=entry,
        class_name=class_name,
        path=folder,
        manifest=dict(data),
        depends=parse_dependencies(data.get("depends"), plugin_id),
        incompatible=_as_list(data.get("incompatible")),
        load_after=_as_list(data.get("load_after")),
        provides=provides,
        libraries=parse_libraries(data.get("libraries"), plugin_id, folder),
        data=parse_data(data.get("data"), folder),
        manager_version=_check_manager_version(_text(data.get("manager_version"))),
        builtin=is_builtin,
        enabled=_flag(data.get("enabled"), True),
        options=options,
        settings=defaults(options),
    )


def load_manifest(plugin_dir: Path, builtin: bool = False) -> PluginInfo:
    """读取插件目录下的 plugin.json 并校验。"""
    folder = Path(plugin_dir)
    manifest = folder / MANIFEST_NAME
    if not manifest.is_file():
        raise ManifestError(f"缺少 {MANIFEST_NAME}：{folder.name}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"插件清单不是合法的 JSON：{exc}") from exc
    except OSError as exc:
        raise ManifestError(f"插件清单读取失败：{exc}") from exc
    return parse_manifest(data, path=folder, builtin=builtin)


# ---------------------------------------------------------------- 依赖解析


def _order_key(info: PluginInfo) -> tuple[int, str]:
    """排序键：内置优先，其次按 id 字典序，保证载入顺序稳定。"""
    return (0 if info.builtin else 1, info.id)


def resolve_dependencies(infos: list[PluginInfo]) -> tuple[list[PluginInfo], dict[str, tuple[str, str]]]:
    """依赖解析：返回 (载入顺序, 插件 id → (阶段, 原因))。

    缺依赖、版本不满足、冲突、循环依赖的插件不进入顺序列表。
    """
    errors: dict[str, tuple[str, str]] = {}
    index: dict[str, PluginInfo] = {}
    for info in infos:
        if info.id in index:
            errors[info.id] = (PHASES[0], f"插件 id 重复：{info.id}")
            continue
        index[info.id] = info

    edges: dict[str, set[str]] = {}
    for info in index.values():
        required: list[PluginDependency] = []
        for dep in info.depends:
            target = index.get(dep.id)
            if target is None:
                if not dep.optional:
                    errors[info.id] = (PHASES[1], f"缺少依赖插件：{dep.id}")
                continue
            if dep.version and target.version and not satisfies(target.version, dep.version):
                errors[info.id] = (
                    PHASES[1],
                    f"依赖插件 {dep.id} 版本不满足：需要 {dep.version}，当前 {target.version}",
                )
                continue
            required.append(dep)
        conflicts = [other for other in info.incompatible if other in index]
        if conflicts:
            errors[info.id] = (PHASES[1], "与插件不兼容：" + "、".join(conflicts))
        edges[info.id] = {dep.id for dep in required}
        for dep in info.load_after:
            if dep in index and dep not in errors:
                edges[info.id].add(dep)
        edges[info.id].discard(info.id)

    remaining = {info.id: info for info in index.values() if info.id not in errors}
    done: set[str] = set()
    ordered: list[PluginInfo] = []
    while remaining:
        ready = sorted(
            (info for info in remaining.values() if edges.get(info.id, set()) <= done),
            key=_order_key,
        )
        if not ready:
            break
        for info in ready:
            ordered.append(info)
            done.add(info.id)
            remaining.pop(info.id, None)
    for info in sorted(remaining.values(), key=_order_key):
        path = [*sorted(edges.get(info.id, set())), info.id]
        errors[info.id] = (PHASES[1], "插件依赖存在循环：" + " → ".join(path))
    return ordered, errors


# ------------------------------------------------------------ dm_plugin 包


def reset_namespace() -> int:
    """清掉上一次载入留下的 `dm_plugin.<id>` 模块（库模块改动后需要重新导入）。"""
    dropped = 0
    for name in list(sys.modules):
        if name == DM_PACKAGE or name.startswith(DM_PACKAGE + "."):
            if name != DM_PACKAGE:
                dropped += 1
            sys.modules.pop(name, None)
    return dropped


def _package(name: str, folder: Path | None) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(folder)] if folder is not None else []  # type: ignore[attr-defined]
    module.__package__ = name
    sys.modules[name] = module
    return module


def register_plugin_namespace(info: PluginInfo) -> str:
    """把插件目录挂成 `dm_plugin.<id>` 包，依赖方可以静态导入它暴露的库模块。"""
    if info.path is None:
        raise PluginError(f"插件没有目录，无法注册导入路径：{info.id}")
    if DM_PACKAGE not in sys.modules:
        _package(DM_PACKAGE, None)
    parts = info.id.split(".")
    for size in range(1, len(parts)):
        prefix = ".".join([DM_PACKAGE, *parts[:size]])
        if prefix not in sys.modules:
            _package(prefix, None)
    leaf = f"{DM_PACKAGE}.{info.id}"
    _package(leaf, Path(info.path))
    return leaf


def entry_module_name(info: PluginInfo) -> str:
    """入口文件对应的模块名（`plugin.py` → `dm_plugin.<id>.plugin`）。"""
    stem = Path(info.entry).with_suffix("").as_posix().replace("/", ".")
    return f"{DM_PACKAGE}.{info.id}.{stem}"


def import_entry(info: PluginInfo) -> types.ModuleType:
    """导入插件入口模块（插件目录已注册成包，库模块也走同一条导入路径）。"""
    register_plugin_namespace(info)
    return importlib.import_module(entry_module_name(info))


def import_library(plugin_id: str, module: str = "") -> types.ModuleType:
    """按库模块取插件模块：`module` 可以是模块名或相对插件目录的 .py 路径。"""
    name = str(module or "").strip()
    if name.endswith(".py"):
        name = Path(name).with_suffix("").as_posix().replace("/", ".")
    full = f"{DM_PACKAGE}.{plugin_id}" + (f".{name}" if name else "")
    try:
        return importlib.import_module(full)
    except ImportError as exc:
        raise PluginError(f"取不到插件库：{plugin_id}.{name or ''}（{exc}）") from exc


def plugin_class(info: PluginInfo, module: types.ModuleType) -> type[Plugin]:
    """按 `class` 字段或「入口文件里唯一的 Plugin 子类」取插件类。"""
    if info.class_name:
        found = getattr(module, info.class_name, None)
        if found is None:
            raise PluginError(f"入口文件里没有类 {info.class_name}：{info.entry}")
        if not isinstance(found, type) or not issubclass(found, Plugin):
            raise PluginError(f"{info.class_name} 不是 app.sdk.Plugin 的子类")
        return found
    classes = collect_plugin_classes(module)
    if not classes:
        raise PluginError(f"入口文件里没有 app.sdk.Plugin 子类：{info.entry}")
    if len(classes) > 1:
        names = "、".join(item.__name__ for item in classes)
        raise PluginError(f"入口文件里有多个 Plugin 子类，请在清单用 class 指定：{names}")
    return classes[0]


__all__ = [
    "DM_PACKAGE",
    "MANIFEST_NAME",
    "PHASES",
    "PLUGIN_ID_PATTERN",
    "PROTOCOL_FIELDS",
    "SOURCE_BUILTIN",
    "SOURCE_EXTERNAL",
    "DependencyError",
    "LibrarySpec",
    "PluginDependency",
    "PluginError",
    "PluginInfo",
    "entry_module_name",
    "import_entry",
    "import_library",
    "load_manifest",
    "parse_data",
    "parse_dependencies",
    "parse_libraries",
    "parse_manifest",
    "plugin_class",
    "register_plugin_namespace",
    "reset_namespace",
    "resolve_dependencies",
]
