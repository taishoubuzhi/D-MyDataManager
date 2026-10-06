"""插件协议核心：清单白名单、依赖解析、`dm_plugin` 命名空间与载入阶段。

协议全文见 `plugins/PLUGIN_PROTOCOL.md`（设计稿 `.logs/_rewrite/plugin_refactor_plan.md`）。
这里只做「核心必须认识」的事：校验清单、解析依赖与版本范围、把插件目录挂成
`dm_plugin.<id>` 包、按阶段载入并把失败定位到具体阶段。
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...sdk.errors import DependencyError, PluginError, PluginManifestError
from ...sdk.plugin import Plugin, collect_plugin_classes
from ...sdk.version import SDK_VERSION, parse_range, satisfies
from .plugin_options import PluginOptionError, PluginOptionSpec, defaults, parse_options, settings_text
from ..runtime import jsonio
from ..runtime.module_data import load_module_data

_DATA = load_module_data(__file__, "plugins")

MANIFEST_NAME = _DATA.value("manifest_name")

SOURCE_BUILTIN = _DATA.value("source_builtin")
SOURCE_EXTERNAL = _DATA.value("source_external")

#: 插件 id 要能直接拼进 `dm_plugin.<id>` 的导入路径，所以必须是合法的小写标识符路径
PLUGIN_ID_PATTERN = re.compile(_DATA.value("plugin_id_pattern"))

#: 协议字段白名单：出现别的字段一律报错，避免「插件类型与插件混杂」式的冗余复发
PROTOCOL_FIELDS = frozenset(_DATA.value("protocol_fields"))

#: 已取消的类型字段
REMOVED_TYPE_FIELDS = tuple(_DATA.value("removed_type_fields"))

#: 已移出协议、改由 `data/` 承载的字段
REMOVED_DATA_FIELDS = tuple(_DATA.value("removed_data_fields"))

#: 已从协议里删掉的字段 → 现应改用的字段（写错时的提示）
REMOVED_PROTOCOL_FIELDS: dict[str, str] = dict(_DATA.value("removed_protocol_fields"))

#: 载入阶段（诊断、插件页与自检共用）
PHASES = tuple(_DATA.value("phases"))

DM_PACKAGE = _DATA.value("dm_package")

#: 代码目录：除 `plugin.py` 外的插件代码都放这里，运行期挂进包搜索路径
CODE_DIR_NAME = ".plugin"

#: 数据目录：`ctx.data("name")` 读 `<插件目录>/.data/name.json`
DATA_DIR_NAME = ".data"

#: 入口文件名（协议 v2 固定，`entry` 写别的值直接报错）
ENTRY_NAME = "plugin.py"


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
    conflicts: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    libraries: tuple[LibrarySpec, ...] = ()
    data: dict[str, str] = field(default_factory=dict)
    builtin: bool = False
    enabled: bool = True
    error: str = ""
    error_phase: str = ""
    conflict_with: tuple[str, ...] = ()
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
        if self.conflict_with:
            return "与插件冲突"
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
    def conflicts_text(self) -> str:
        return "、".join(self.conflicts) if self.conflicts else "无"

    @property
    def conflict_with_text(self) -> str:
        return "、".join(self.conflict_with)

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
        raise PluginManifestError(f"清单字段需要字符串或列表，收到：{type(value).__name__}")
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
    raise PluginManifestError(f"清单字段需要布尔值，收到：{type(value).__name__}")


def _require_id(value: object, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise PluginManifestError(f"插件清单缺少 {field_name}")
    if not PLUGIN_ID_PATTERN.match(text):
        raise PluginManifestError(
            f"插件 id 不合法：{text}（小写字母开头，点分段，每段只含小写字母、数字与下划线）"
        )
    return text


def _check_removed_fields(data: dict) -> None:
    for name in REMOVED_TYPE_FIELDS:
        if data.get(name) not in (None, "", (), []):
            raise PluginManifestError(f"插件清单已取消类型字段：{name}（协议不再区分插件类型）")
    for name in REMOVED_DATA_FIELDS:
        if data.get(name) not in (None, "", (), []):
            raise PluginManifestError(f"插件清单不再支持字段 {name}：请改用库插件提供的扩展接口")
    for name, instead in REMOVED_PROTOCOL_FIELDS.items():
        if data.get(name) not in (None, "", (), []):
            raise PluginManifestError(f"插件清单已取消字段 {name}：请改用 {instead}")


def _check_unknown_fields(data: dict) -> None:
    unknown = sorted(name for name in data if name not in PROTOCOL_FIELDS)
    if unknown:
        raise PluginManifestError("插件清单出现未知字段：" + "、".join(unknown))


def _check_api_version(spec: str) -> str:
    if not spec:
        raise PluginManifestError('插件清单缺少 api_version（适配的 SDK 版本范围，例如 ">=1.0 <2.0"）')
    try:
        parse_range(spec)
    except Exception as exc:
        raise PluginManifestError(f"api_version 不是合法版本范围：{spec}（{exc}）") from exc
    if not satisfies(SDK_VERSION, spec):
        raise PluginManifestError(f"需要 SDK 版本 {spec}，当前 {SDK_VERSION}")
    return spec


def parse_dependencies(value: object, plugin_id: str) -> tuple[PluginDependency, ...]:
    """解析 `depends`：支持 `["a", "b"]` 简写与 `[{"id": ..., "version": ..., "optional": ...}]`。"""
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        raise PluginManifestError("插件清单的 depends 必须是一个列表")
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
                raise PluginManifestError(f"依赖声明出现未知字段：{'、'.join(unknown)}")
        else:
            raise PluginManifestError("depends 的每一项必须是插件 id 或对象")
        if not PLUGIN_ID_PATTERN.match(dep_id):
            raise PluginManifestError(f"依赖插件 id 不合法：{dep_id}")
        if dep_id == plugin_id:
            raise PluginManifestError("插件不能依赖自己")
        if dep_id in seen:
            raise PluginManifestError(f"依赖插件重复声明：{dep_id}")
        if spec:
            try:
                parse_range(spec)
            except Exception as exc:
                raise PluginManifestError(f"依赖 {dep_id} 的版本范围不合法：{spec}（{exc}）") from exc
        seen.add(dep_id)
        result.append(PluginDependency(dep_id, spec, optional))
    return tuple(result)


def parse_conflicts(value: object, plugin_id: str) -> tuple[str, ...]:
    """解析 `conflicts`：声明与哪些插件不能同时启用（不再影响能否载入）。"""
    result = _as_list(value)
    for other in result:
        if not PLUGIN_ID_PATTERN.match(other):
            raise PluginManifestError(f"冲突插件 id 不合法：{other}")
        if other == plugin_id:
            raise PluginManifestError("插件不能与自己冲突")
    return result


def parse_libraries(value: object, plugin_id: str, path: Path | None) -> tuple[LibrarySpec, ...]:
    """解析 `libraries`：声明的模块必须存在、是 `.py` 且非空。"""
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        raise PluginManifestError("插件清单的 libraries 必须是一个列表")
    result: list[LibrarySpec] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise PluginManifestError("libraries 的每一项都必须是一个对象")
        name = _text(item.get("name"))
        module = _text(item.get("module"))
        if not name:
            raise PluginManifestError("libraries 缺少 name")
        if name in seen:
            raise PluginManifestError(f"库重复声明：{name}")
        if not module:
            raise PluginManifestError(f"库 {name} 缺少 module")
        if not module.endswith(".py"):
            raise PluginManifestError(f"库 {name} 的 module 必须是 .py 文件：{module}")
        if path is not None:
            target = Path(path) / module
            if not target.is_file():
                raise PluginManifestError(f"库 {name} 声明的模块不存在：{module}")
            if not target.stat().st_size:
                raise PluginManifestError(f"库 {name} 声明的模块是空文件：{module}")
        seen.add(name)
        result.append(LibrarySpec(name, module, _text(item.get("description"))))
    return tuple(result)


def scan_data_files(path: Path | None) -> dict[str, str]:
    """扫描 `.data/`：返回 `{键: 相对路径}`（键＝文件名去掉扩展名），按名字排序。

    协议 v2 不再在清单里声明数据文件——数据目录固定为 `.data/`，插件用
    `ctx.data("name")` / `ctx.data_path("name.json")` 读取。
    """
    if path is None:
        return {}
    folder = Path(path) / DATA_DIR_NAME
    if not folder.is_dir():
        return {}
    return {
        item.stem: f"{DATA_DIR_NAME}/{item.name}"
        for item in sorted(folder.iterdir())
        if item.is_file() and item.suffix.lower() == ".json"
    }


def parse_manifest(data: dict, path: Path | None = None, builtin: bool = False) -> PluginInfo:
    """按插件协议校验清单字典，返回 `PluginInfo`。"""
    if not isinstance(data, dict):
        raise PluginManifestError("插件清单必须是一个 JSON 对象")
    _check_removed_fields(data)
    _check_unknown_fields(data)
    plugin_id = _require_id(data.get("id"), "id")
    name = _text(data.get("name"))
    if not name:
        raise PluginManifestError("插件清单缺少 name")
    is_builtin = builtin or bool(data.get("builtin", False))
    folder = Path(path) if path is not None else None
    entry = _text(data.get("entry"))
    if not entry:
        if not is_builtin:
            raise PluginManifestError("插件清单缺少 entry（入口文件）")
        entry = ENTRY_NAME
    elif entry != ENTRY_NAME:
        raise PluginManifestError(f"协议 v2 的入口固定为 {ENTRY_NAME}，不能是：{entry}")
    if folder is not None and not (folder / ENTRY_NAME).is_file():
        raise PluginManifestError(f"入口文件不存在：{ENTRY_NAME}")
    class_name = _text(data.get("class"))
    if class_name and not class_name.isidentifier():
        raise PluginManifestError(f"class 必须是合法的类名：{class_name}")
    provides = _as_list(data.get("provides"))
    for item in provides:
        parts = item.split(".")
        if not item or not all(part.isidentifier() for part in parts):
            raise PluginManifestError(f"扩展接口名不合法：{item}")
    try:
        options = parse_options(data.get("options"))
    except PluginOptionError as exc:
        raise PluginManifestError(str(exc)) from exc
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
        conflicts=parse_conflicts(data.get("conflicts"), plugin_id),
        provides=provides,
        libraries=parse_libraries(data.get("libraries"), plugin_id, folder),
        data=scan_data_files(folder),
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
        raise PluginManifestError(f"缺少 {MANIFEST_NAME}：{folder.name}")
    try:
        data = jsonio.loads(manifest.read_bytes())
    except ValueError as exc:
        raise PluginManifestError(f"插件清单不是合法的 JSON：{exc}") from exc
    except OSError as exc:
        raise PluginManifestError(f"插件清单读取失败：{exc}") from exc
    return parse_manifest(data, path=folder, builtin=builtin)


# ---------------------------------------------------------------- 依赖解析


def _order_key(info: PluginInfo) -> tuple[int, str]:
    """排序键：内置优先，其次按 id 字典序，保证载入顺序稳定。"""
    return (0 if info.builtin else 1, info.id)


def resolve_dependencies(
    infos: list[PluginInfo], *, enabled_ids: set[str] | None = None
) -> tuple[list[PluginInfo], dict[str, tuple[str, str]], dict[str, tuple[str, ...]]]:
    """依赖解析：返回 (载入顺序, 插件 id → (阶段, 原因), 插件 id → 与之冲突的已启用插件)。

    缺依赖、版本不满足、循环依赖的插件不进入顺序列表。冲突不影响载入：冲突的插件
    照常进入顺序列表（照常能被载入、能提供库），只是**不能与对方同时启用**，所以它
    出现在第三项里，由调用方决定把谁置为禁用。

    `enabled_ids` 给出「最终会启用的插件」时，冲突只在双方都启用时才算，且载入顺序
    靠前的那个胜出、由后者让位（不传则该判断对所有已发现的插件生效）。
    """
    errors: dict[str, tuple[str, str]] = {}
    index: dict[str, PluginInfo] = {}
    for info in infos:
        if info.id in index:
            errors[info.id] = (PHASES[0], f"插件 id 重复：{info.id}")
            continue
        index[info.id] = info

    # 冲突是对称的：任意一方声明就算双方冲突
    peers: dict[str, set[str]] = {plugin_id: set() for plugin_id in index}
    for info in index.values():
        for other in info.conflicts:
            peer = index.get(other)
            if peer is None or peer is info:
                continue
            peers[info.id].add(other)
            peers[other].add(info.id)

    yielded: dict[str, tuple[str, ...]] = {}
    for info in index.values():
        if enabled_ids is not None and info.id not in enabled_ids:
            continue
        blockers = sorted(
            other
            for other in peers.get(info.id, ())
            if (enabled_ids is None or other in enabled_ids)
            and _order_key(index[other]) < _order_key(info)
        )
        if blockers:  # 先出现的那个胜出，这一个让位
            yielded[info.id] = tuple(blockers)

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
        edges[info.id] = {dep.id for dep in required}
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
    return ordered, errors, yielded


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


def drop_namespace(plugin_id: str) -> int:
    """丢掉 `dm_plugin.<plugin_id>` 及其子模块，返回丢掉的模块数。

    插件代码改动后只有重新导入才会生效，所以卸载与重载都要走这里。
    """
    prefix = f"{DM_PACKAGE}.{plugin_id}"
    dropped = 0
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)
            dropped += 1
    return dropped


def _package(name: str, folder: Path | None) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = _search_paths(folder)  # type: ignore[attr-defined]
    module.__package__ = name
    sys.modules[name] = module
    return module


def _search_paths(folder: Path | None) -> list[str]:
    """插件包的搜索路径：根目录（`plugin.py`）+ `.plugin/`（其余代码）。"""
    if folder is None:
        return []
    return [str(folder), str(Path(folder) / CODE_DIR_NAME)]


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


def layout_problems(folder: Path | str) -> tuple[str, ...]:
    """检查插件根目录是否符合协议 v2 布局，返回问题描述（空元组＝合格）。"""
    root = Path(folder)
    problems: list[str] = []
    if not (root / ENTRY_NAME).is_file():
        problems.append(f"缺少入口文件：{ENTRY_NAME}")
    if not (root / MANIFEST_NAME).is_file():
        problems.append(f"缺少清单：{MANIFEST_NAME}")
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name in ("__pycache__", ENTRY_NAME, MANIFEST_NAME, "PLUGIN.md", CODE_DIR_NAME, DATA_DIR_NAME):
            continue
        problems.append(f"根目录不该出现：{child.name}（代码放 {CODE_DIR_NAME}/，数据放 {DATA_DIR_NAME}/）")
    return tuple(problems)


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
        dotted = Path(name).with_suffix("").as_posix().replace("/", ".")
        # 协议 v2 的代码目录（`.plugin/`）不是包片段，导入时要去掉
        name = dotted[len(CODE_DIR_NAME) + 1 :] if dotted.startswith(CODE_DIR_NAME + ".") else dotted
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
    "CODE_DIR_NAME",
    "DATA_DIR_NAME",
    "DM_PACKAGE",
    "ENTRY_NAME",
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
    "drop_namespace",
    "entry_module_name",
    "import_entry",
    "import_library",
    "layout_problems",
    "load_manifest",
    "parse_dependencies",
    "parse_libraries",
    "parse_manifest",
    "plugin_class",
    "register_plugin_namespace",
    "reset_namespace",
    "resolve_dependencies",
    "scan_data_files",
]
