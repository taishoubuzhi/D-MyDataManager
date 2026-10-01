"""插件服务：统一发现、校验、依赖排序、载入与启停。

内置插件与外部插件走同一条路径 —— 都在插件目录（`paths.PLUGIN_DIR`）下，
每个插件是一个子目录，含 `plugin.json`（协议见 core.plugins）与清单声明的入口文件。
内置标志由清单里的 `builtin: true` 决定：内置插件不能删除，但同样可以禁用。

载入顺序由 `depends` 决定：被依赖的插件先注册，声明依赖的插件才能在
`register(api)` 里用 `api.require("接口名")` 取到对方提供的扩展接口。
缺依赖或循环依赖的插件会带上 error，不参与载入。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from loguru import logger

from ..core import paths
from ..core.extensions import ExtensionRegistry, extension_registry
from ..core.plugin_kinds import (
    KIND_EXTENSION,
    KIND_PLUGIN_ID,
    KIND_VIEWER,
    plugin_kinds,
    register_builtin_kinds,
    valid_kind_name,
)
from ..core.plugin_options import coerce_option, defaults
from ..core.plugins import (
    MANIFEST_NAME,
    SOURCE_BUILTIN,
    SOURCE_EXTERNAL,
    PluginError,
    PluginInfo,
    load_manifest,
    sort_by_dependency,
    validate_kind,
)
from ..core.viewers import Viewer, viewer_registry

STATE_VERSION = 1
STATE_FILTERS = (("", "全部状态"), ("enabled", "已启用"), ("disabled", "已禁用"), ("error", "异常"))

SOURCE_FILTERS = (("", "全部来源"), (SOURCE_BUILTIN, "内置"), (SOURCE_EXTERNAL, "外部"))

#: 插件列表的排序方案：(值, 显示名)
PLUGIN_ORDERS = (
    ("default", "默认顺序"),
    ("name", "名称"),
    ("kind", "类型"),
    ("source", "来源"),
    ("author", "创建者"),
    ("state", "状态"),
    ("version", "版本"),
)


@dataclass(frozen=True)
class PluginContribution:
    """自定义类型插件的登记条目：`payload` 的含义只有声明该类型的插件自己知道。"""

    kind: str
    plugin_id: str
    name: str = ""
    args: tuple[object, ...] = ()
    fields: dict[str, object] = field(default_factory=dict)


class PluginApi:
    """交给插件的注册接口：登记条目、声明与使用扩展接口。"""

    def __init__(
        self,
        plugin_id: str,
        plugin_name: str = "",
        default_extensions: Sequence[str] = (),
        registry: ExtensionRegistry | None = None,
        options: dict | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.plugin_name = plugin_name
        self.default_extensions = tuple(default_extensions)
        self.registry = registry if registry is not None else extension_registry
        self.options: dict = dict(options or {})
        self.registered: list[Viewer] = []
        self.provided: list[str] = []
        self.contributions: dict[str, list[PluginContribution]] = {}

    def option(self, key: str, default: object = None) -> object:
        """读取用户在「插件选项」页里为本插件设置的值（没设置过返回选项声明的默认值）。"""
        return self.options.get(key, default)

    # ------------------------------------------------------------ 按类型登记
    def add(self, kind: str, name: str = "", *args: object, **fields: object) -> object:
        """按插件类型登记条目：类型有登记方法（查看器为 add_viewer）就路由过去。

        类型由类型插件声明，所以插件可以自定义新类型：这类类型的条目会记成
        `PluginContribution`，其他插件用 `api.entries(kind)` 读取。
        """
        if not valid_kind_name(kind):
            raise PluginError(f"插件类型不合法：{kind}")
        spec = plugin_kinds.get(kind)
        contributor = spec.contributor if spec is not None else ""
        if contributor:
            method = getattr(self, contributor, None)
            if not callable(method):
                raise PluginError(f"插件类型 {kind} 的登记方法不存在：{contributor}")
            return method(name, *args, **fields)  # type: ignore[operator]
        item = PluginContribution(
            kind=kind,
            plugin_id=self.plugin_id,
            name=str(name),
            args=tuple(args),
            fields=dict(fields),
        )
        self.contributions.setdefault(kind, []).append(item)
        return item

    def entries(self, kind: str = "") -> tuple[PluginContribution, ...]:
        """按类型读取本插件登记的条目（不传 kind 返回全部）。"""
        if kind:
            return tuple(self.contributions.get(kind, ()))
        return tuple(item for items in self.contributions.values() for item in items)

    # ------------------------------------------------------------ 查看器
    def add_viewer(
        self,
        name: str,
        extensions: Iterable[str] = (),
        factory: Callable[[Path, object], object] | None = None,
        kind: str = "text",
        description: str = "",
        capabilities: Sequence[str] = (),
        viewer_id: str = "",
        host: str = "",
    ) -> Viewer:
        if factory is not None and not callable(factory):
            raise PluginError(f"查看器「{name}」没有可用的控件工厂（插件需自行创建视图）")
        clean: list[str] = []
        for item in extensions or self.default_extensions:
            suffix = str(item).strip().lstrip(".").lower()
            if suffix and suffix not in clean:
                clean.append(suffix)
        if not viewer_id:
            viewer_id = f"{self.plugin_id}.{len(self.registered) + 1}"
        if host:
            self.require(host)  # 显示窗口由该扩展接口提供，缺了就注册失败
        viewer = Viewer(
            id=viewer_id,
            name=name,
            extensions=tuple(clean),
            kind=kind,
            plugin_id=self.plugin_id,
            factory=factory,
            host=host,
            description=description,
            capabilities=tuple(capabilities),
        )
        viewer_registry.register(viewer)
        self.registered.append(viewer)
        return viewer

    # ------------------------------------------------------ 扩展接口
    def provide(self, name: str, provider: object) -> None:
        """向其他插件暴露一个扩展接口。"""
        self.registry.provide(name, provider, self.plugin_id)
        if name not in self.provided:
            self.provided.append(name)

    def require(self, name: str) -> object:
        """取其他插件提供的扩展接口，缺失时报 PluginError。"""
        provider = self.registry.provider(name)
        if provider is None:
            raise PluginError(f"缺少扩展接口：{name}（请检查依赖插件是否已安装并启用）")
        return provider

    def has(self, name: str) -> bool:
        return name in self.registry

    def extensions(self) -> tuple[str, ...]:
        return self.registry.names()


class PluginService:
    """插件目录的发现、状态与载入。"""

    def __init__(self, plugin_dir: Path | str | None = None, state_file: Path | str | None = None) -> None:
        self._plugin_dir = Path(plugin_dir) if plugin_dir else None
        self._state_file = Path(state_file) if state_file else None
        self._viewers: dict[str, list[Viewer]] = {}
        self._apis: dict[str, PluginApi] = {}
        self._errors: dict[str, str] = {}
        self._bootstrap: dict[str, object] = {}
        self._loaded_plugins: list[PluginInfo] = []

    # ------------------------------------------------------------ 路径
    @property
    def plugin_dir(self) -> Path:
        return self._plugin_dir if self._plugin_dir is not None else paths.PLUGIN_DIR

    @property
    def state_file(self) -> Path:
        return self._state_file if self._state_file is not None else paths.PLUGIN_STATE_FILE

    # ------------------------------------------------------------ 状态
    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        plugins = data.get("plugins")
        return plugins if isinstance(plugins, dict) else {}

    def _write_state(self, plugins: dict) -> None:
        payload = {"version": STATE_VERSION, "plugins": plugins}
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("写入插件状态失败：{}", exc)

    def _save_state_for(self, plugin_id: str, **values: object) -> None:
        plugins = self._read_state()
        entry = plugins.get(plugin_id) or {}
        entry.update(values)
        plugins[plugin_id] = entry
        self._write_state(plugins)

    # ---------------------------------------------------------- 发现
    def scan(self) -> list[PluginInfo]:
        """扫描插件目录，返回清单校验后的插件（未套用启用状态）。"""
        root = self.plugin_dir
        if not root.exists():
            return []
        found: list[PluginInfo] = []
        for folder in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not folder.is_dir() or folder.name.startswith((".", "_")):
                continue
            if not (folder / MANIFEST_NAME).exists():
                continue
            try:
                found.append(load_manifest(folder))
            except PluginError as exc:
                found.append(
                    PluginInfo(
                        id=folder.name,
                        name=folder.name,
                        path=folder,
                        enabled=False,
                        error=str(exc),
                    )
                )
        return found

    def discover(self) -> list[PluginInfo]:
        """扫描 + 套用状态 + 依赖排序；缺依赖 / 循环依赖 / 依赖未启用的插件带 error。"""
        raw = self.scan()
        state = self._read_state()
        order, dep_errors = sort_by_dependency(raw)
        self._errors = dict(dep_errors)
        base_enabled: dict[str, bool] = {}
        for info in raw:
            entry = state.get(info.id) or {}
            base_enabled[info.id] = bool(entry.get("enabled", info.enabled)) and not info.error
        # 依赖在前，逐级传播「依赖不可用」
        available: dict[str, bool] = {}
        blocked: dict[str, str] = {}
        for info in order:
            missing = [dep for dep in info.depends if not available.get(dep, False)]
            if missing:
                blocked[info.id] = "依赖插件未启用：" + "、".join(missing)
                available[info.id] = False
            else:
                available[info.id] = base_enabled[info.id]
        self._errors.update(blocked)
        result: list[PluginInfo] = []
        for info in sorted(raw, key=lambda item: (0 if item.builtin else 1, item.id)):
            entry = state.get(info.id) or {}
            fields: dict[str, object] = {}
            for key in ("name", "description", "note"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    fields[key] = value
            values = entry.get("options")
            settings = defaults(info.options)
            if isinstance(values, dict):
                for spec in info.options:
                    if spec.key in values:
                        settings[spec.key] = coerce_option(spec, values[spec.key])
            fields["settings"] = settings
            error = info.error or dep_errors.get(info.id, "") or blocked.get(info.id, "")
            if error:
                fields["error"] = error
            fields["enabled"] = base_enabled[info.id] and not error and available.get(info.id, True)
            result.append(info.clone(**fields))
        position = {info.id: index for index, info in enumerate(order)}
        result.sort(key=lambda item: (position.get(item.id, len(order)), item.id))
        return result

    def errors(self) -> dict[str, str]:
        """最近一次 discover() 得到的依赖错误。"""
        return dict(self._errors)

    def builtin(self) -> list[PluginInfo]:
        return [info for info in self.discover() if info.builtin]

    def external(self) -> list[PluginInfo]:
        return [info for info in self.discover() if not info.builtin]

    def get(self, plugin_id: str) -> PluginInfo | None:
        for info in self.discover():
            if info.id == plugin_id:
                return info
        return None

    def all(
        self,
        kind: str = "",
        query: str = "",
        state: str = "",
        source: str = "",
        author: str = "",
        order: str = "default",
        reverse: bool = False,
    ) -> list[PluginInfo]:
        """按类型 / 来源 / 创建者 / 状态 / 关键词筛选插件，并按指定方案排序。"""
        keyword = str(query or "").strip().lower()
        result: list[PluginInfo] = []
        for info in self.discover():
            if kind and info.kind != kind:
                continue
            if source and info.source != source:
                continue
            if author and info.author_text != author:
                continue
            if state == "enabled" and not (info.enabled and not info.error):
                continue
            if state == "disabled" and (info.enabled or info.error):
                continue
            if state == "error" and not info.error:
                continue
            if keyword and keyword not in self._search_text(info).lower():
                continue
            result.append(info)
        if order and order != "default":
            result.sort(key=lambda item: self._order_value(item, order), reverse=bool(reverse))
        elif reverse:
            result = list(reversed(result))
        return result

    @staticmethod
    def _order_value(info: PluginInfo, order: str) -> tuple:
        if order == "name":
            return (info.name.lower(), info.id)
        if order == "kind":
            return (info.kind_label.lower(), info.name.lower())
        if order == "source":
            return (info.source_label, info.name.lower())
        if order == "author":
            return (info.author_text.lower(), info.name.lower())
        if order == "state":
            return (info.state_label, info.name.lower())
        if order == "version":
            return (info.version_text, info.name.lower())
        return (info.name.lower(), info.id)

    def authors(self) -> tuple[str, ...]:
        """所有插件里出现过的创建者（按名称排序），供界面做筛选。"""
        found = {info.author_text for info in self.discover()}
        return tuple(sorted(found, key=str.lower))

    def options_of(self, plugin_id: str) -> dict[str, object]:
        """该插件当前的选项值（含未设置项），未知插件返回空字典。"""
        info = self.get(plugin_id)
        return dict(info.settings) if info is not None else {}

    def set_option(self, plugin_id: str, key: str, value: object) -> object:
        """保存一个插件选项的值，返回规范化后的值。"""
        info = self.get(plugin_id)
        if info is None:
            raise ValueError(f"插件不存在：{plugin_id}")
        spec = info.option_spec(key)
        if spec is None:
            raise ValueError(f"插件「{info.name}」没有选项：{key}")
        normalized = coerce_option(spec, value)
        plugins = self._read_state()
        entry = plugins.get(plugin_id) or {}
        options = entry.get("options")
        if not isinstance(options, dict):
            options = {}
        options[key] = normalized
        entry["options"] = options
        plugins[plugin_id] = entry
        self._write_state(plugins)
        return normalized

    def reset_options(self, plugin_id: str) -> bool:
        """清掉该插件的全部选项设置，恢复清单里声明的默认值。"""
        plugins = self._read_state()
        entry = plugins.get(plugin_id)
        if not isinstance(entry, dict) or "options" not in entry:
            return False
        entry.pop("options", None)
        if entry:
            plugins[plugin_id] = entry
        else:
            plugins.pop(plugin_id, None)
        self._write_state(plugins)
        return True

    @staticmethod
    def _search_text(info: PluginInfo) -> str:
        return " ".join(
            [
                info.name,
                info.id,
                info.description,
                info.author,
                info.note,
                info.kind_label,
                info.source_label,
                " ".join(info.extensions),
                " ".join(info.capabilities),
                " ".join(info.depends),
                " ".join(info.provides),
            ]
        )

    # ------------------------------------------------------ 程序本体接口
    def bootstrap(self, name: str, provider: object) -> None:
        """登记程序本体提供的扩展接口（如 `app.ui`）：每次载入插件后都会重新提供，插件可直接依赖。

        提供者可以实现 `sync_plugins(plugin_ids)`，每次载入插件后会被调用一次，
        用来把自身持有的插件产物（例如界面页面）与当前载入的插件集合对齐。
        """
        self._bootstrap[name] = provider
        extension_registry.provide(name, provider, "")

    def bootstrap_names(self) -> tuple[str, ...]:
        """已登记的程序本体扩展接口名。"""
        return tuple(self._bootstrap)

    def _provide_bootstrap(self) -> None:
        for name, provider in self._bootstrap.items():
            extension_registry.provide(name, provider, "")

    def _sync_bootstrap(self) -> None:
        loaded = tuple(self._apis)
        for provider in self._bootstrap.values():
            sync = getattr(provider, "sync_plugins", None)
            if not callable(sync):
                continue
            try:
                sync(loaded)
            except Exception:  # 界面同步失败不应该影响插件载入
                logger.exception("程序本体接口同步失败：{}", type(provider).__name__)

    # ---------------------------------------------------------- 载入
    def load_viewers(self) -> int:
        """按依赖顺序载入所有启用的插件，返回注册的查看器数量。"""
        viewer_registry.clear()
        extension_registry.clear()
        plugin_kinds.clear()
        register_builtin_kinds()
        self._provide_bootstrap()
        self._viewers = {}
        self._apis = {}
        self._loaded_plugins = []
        order, dep_errors = sort_by_dependency(self.scan())
        infos = {info.id: info for info in self.discover()}
        ordered: list[PluginInfo] = []
        for info in order:
            current = infos.get(info.id, info)
            if current.error:
                continue
            ordered.append(current)
        count = 0
        for info in ordered:
            if not info.enabled:
                continue  # 非查看器类型的插件同样要载入：它们为别的插件提供扩展接口
            try:
                self._declare_kinds(info)
                validate_kind(info)
                viewers = self._load_plugin(info) if info.entry else []
                self._viewers[info.id] = viewers
                self._loaded_plugins.append(info)
                count += len(viewers)
            except PluginError as exc:
                extension_registry.drop_plugin(info.id)
                self._errors[info.id] = str(exc)
                logger.warning("插件载入失败：{}（{}）", info.id, exc)
            except Exception:
                extension_registry.drop_plugin(info.id)
                self._errors[info.id] = "插件载入时发生未预期的错误，详见日志"
                logger.exception("插件载入异常：{}", info.id)
        self._sync_bootstrap()
        return count

    def loaded_plugins(self) -> list[PluginInfo]:
        """上一次载入成功的插件（按依赖顺序，含只声明类型的插件）。"""
        return list(self._loaded_plugins)

    def loaded_summary(self, viewers: int = 0) -> str:
        """把这次载入的插件汇总成一句启动日志：总数、来源、各插件类型的数量。"""
        plugins = self._loaded_plugins
        if not plugins:
            return "本次没有载入任何插件"
        builtin = sum(1 for info in plugins if info.builtin)
        kinds: dict[str, int] = {}
        for info in plugins:
            label = plugin_kinds.label(info.kind) if info.kind else "未分类"
            kinds[label] = kinds.get(label, 0) + 1
        kind_text = "、".join(
            f"{label} {amount} 个"
            for label, amount in sorted(kinds.items(), key=lambda item: (-item[1], item[0]))
        )
        text = (
            f"共载入 {len(plugins)} 个插件"
            f"（内置 {builtin} 个、外部 {len(plugins) - builtin} 个）：{kind_text}"
        )
        if viewers:
            text += f"；共注册 {viewers} 个查看器"
        return text

    def _declare_kinds(self, info: PluginInfo) -> None:
        """把类型插件声明的插件类型登记进全局类型表（类型插件是纯数据插件）。"""
        if not info.kinds:
            return
        provider = extension_registry.provider(KIND_EXTENSION)
        declare = getattr(provider, "declare", None)
        if not callable(declare):
            raise PluginError(
                f"缺少插件类型接口「{KIND_EXTENSION}」：请先启用插件类型插件 {KIND_PLUGIN_ID}"
            )
        for spec in info.kinds:
            if spec.contributor and not hasattr(PluginApi, spec.contributor):
                raise PluginError(f"插件类型 {spec.id} 的登记方法不存在：{spec.contributor}")
            declare(spec, info.id)

    def load(self) -> int:
        """统一载入入口：重建注册表并返查看器数量。"""
        return self.load_viewers()

    def _load_plugin(self, info: PluginInfo) -> list[Viewer]:
        register = self._resolve_register(info)
        api = PluginApi(
            plugin_id=info.id,
            plugin_name=info.name,
            default_extensions=info.extensions,
            registry=extension_registry,
            options=info.settings,
        )
        register(api)
        self._apis[info.id] = api
        return api.registered

    def _resolve_register(self, info: PluginInfo) -> Callable[[PluginApi], None]:
        if not info.entry or info.path is None:
            raise PluginError(f"插件缺少入口文件：{info.id}")
        entry = Path(info.path) / info.entry
        if not entry.exists():
            raise PluginError(f"入口文件不存在：{info.entry}")
        module_name = "dm_plugin_" + re.sub(r"\W", "_", info.id)
        spec = importlib.util.spec_from_file_location(module_name, entry)
        if spec is None or spec.loader is None:
            raise PluginError(f"无法载入插件模块：{info.entry}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError(f"插件入口缺少 register(api)：{info.entry}")
        return register

    def viewers_of(self, plugin_id: str) -> list[Viewer]:
        if not self._viewers:
            self.load_viewers()
        return list(self._viewers.get(plugin_id, []))

    def contributions(self, kind: str = "") -> tuple[PluginContribution, ...]:
        """所有已载入插件登记的自定义类型条目（不传 kind 返回全部）。"""
        return tuple(item for api in self._apis.values() for item in api.entries(kind))

    # ---------------------------------------------------------- 操作
    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        info = self.get(plugin_id)
        if info is None:
            return False
        if enabled and info.error:
            logger.warning("插件处于异常状态，无法启用：{}（{}）", plugin_id, info.error)
            return False
        self._save_state_for(plugin_id, enabled=bool(enabled))
        return True

    def update(
        self,
        plugin_id: str,
        name: str | None = None,
        description: str | None = None,
        note: str | None = None,
    ) -> PluginInfo | None:
        values: dict[str, str] = {}
        for key, value in (("name", name), ("description", description), ("note", note)):
            if value is not None:
                values[key] = str(value).strip()
        if values:
            self._save_state_for(plugin_id, **values)
        return self.get(plugin_id)

    def remove(self, plugin_id: str) -> bool:
        info = self.get(plugin_id)
        if info is None or info.builtin or info.path is None:
            return False
        try:
            shutil.rmtree(info.path, ignore_errors=True)
        except OSError as exc:
            logger.warning("删除插件目录失败：{}", exc)
            return False
        plugins = self._read_state()
        plugins.pop(plugin_id, None)
        self._write_state(plugins)
        self.load_viewers()
        return True

    def import_plugin(self, source: str | Path, overwrite: bool = False) -> PluginInfo:
        """从目录或 .zip 导入外部插件，返回导入后的插件信息。"""
        origin = Path(source)
        if not origin.exists():
            raise PluginError(f"路径不存在：{origin}")
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        cleanup: Path | None = None
        try:
            if origin.is_dir():
                folder = self._find_plugin_root(origin)
            else:
                temporary = self.plugin_dir / f".import-{uuid.uuid4().hex[:8]}"
                temporary.mkdir(parents=True, exist_ok=True)
                cleanup = temporary
                folder = self._find_plugin_root(self._extract_zip(origin, temporary))
            info = load_manifest(folder)
            target = self.plugin_dir / info.id
            if target.exists() and not overwrite:
                raise PluginError(f"插件已存在：{info.id}（可勾选覆盖安装）")
            if info.path is not None and info.path.resolve() == target.resolve():
                raise PluginError(f"插件已经在插件目录里：{info.id}")
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(folder, target)
            self._mark_external(target)
            self.load_viewers()
            return load_manifest(target)
        finally:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)

    @staticmethod
    def _mark_external(folder: Path) -> None:
        """导入进来的插件一律算外部插件，避免伪造内置标志。"""
        manifest = folder / MANIFEST_NAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if data.get("builtin"):
            data["builtin"] = False
            manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _find_plugin_root(root: Path) -> Path:
        if (root / MANIFEST_NAME).exists():
            return root
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and (folder / MANIFEST_NAME).exists():
                return folder
        raise PluginError(f"找不到 {MANIFEST_NAME}：{root.name}")

    @staticmethod
    def _extract_zip(archive_path: Path, target: Path) -> Path:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    name = member.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise PluginError(f"压缩包里有不安全的路径：{member.filename}")
                archive.extractall(target)
        except zipfile.BadZipFile as exc:
            raise PluginError(f"无法读取压缩包：{exc}") from exc
        return target


plugin_service = PluginService()

__all__ = [
    "PLUGIN_ORDERS",
    "SOURCE_FILTERS",
    "STATE_FILTERS",
    "STATE_VERSION",
    "PluginApi",
    "PluginContribution",
    "PluginError",
    "PluginInfo",
    "PluginService",
    "plugin_kinds",
    "SOURCE_BUILTIN",
    "SOURCE_EXTERNAL",
    "plugin_service",
]
