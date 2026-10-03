"""插件服务：插件目录的发现、状态、载入与诊断。

协议与校验在 `app.core.plugin_core`，插件作者用的接口在 `app.sdk`：
插件就是继承 `app.sdk.Plugin` 的一个类，核心负责清单解析、依赖排序、
命名空间注册与按阶段载入（manifest → dependency → import → construct → setup）。
本服务只做「发现 + 状态 + 编排 + 诊断」四件事，插件失败只标记该插件。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from loguru import logger

from ..core import paths
from ..core.app_ui import APP_UI_EXTENSION, AppUiApi
from ..core.extensions import ExtensionRegistry, extension_registry
from ..core.plugin_core import (
    DM_PACKAGE,
    MANIFEST_NAME,
    PHASES,
    SOURCE_BUILTIN,
    SOURCE_EXTERNAL,
    PluginError,
    PluginInfo,
    import_entry,
    import_library,
    load_manifest,
    plugin_class,
    register_plugin_namespace,
    reset_namespace,
    resolve_dependencies,
)
from ..core.plugin_options import coerce_option, defaults
from ..sdk import (
    Contribution,
    Events,
    ExtensionPoint,
    Plugin,
    PluginContext,
    SdkError,
    register_dependency_lookup,
    register_library_resolver,
)
from ..sdk.editors import EDITOR_EXTENSION, EditorFactory, EditorInfo, EditorOpener, editor_api
from ..sdk.viewers import OPEN_EXTENSION, ViewerFactory, ViewerInfo, ViewerOpener, open_api

STATE_VERSION = 1

#: 清单声明了扩展接口却没注册时写进备注的前缀（便于识别并清理自己写的那条）。
PROVIDES_NOTE_PREFIX = "清单声明的扩展接口没有注册："

STATE_FILTERS = (
    ("", "全部状态"),
    ("enabled", "已启用"),
    ("disabled", "已禁用"),
    ("error", "载入失败"),
)

SOURCE_FILTERS = (("", "全部来源"), (SOURCE_BUILTIN, "内置"), (SOURCE_EXTERNAL, "外部"))

#: 插件列表的排序方案：(值, 显示名)
PLUGIN_ORDERS = (
    ("default", "默认顺序"),
    ("name", "名称"),
    ("source", "来源"),
    ("author", "创建者"),
    ("state", "状态"),
    ("version", "版本"),
    ("contribution", "贡献"),
)

#: 插件列表按扩展点筛选：(扩展点, 显示名)
CONTRIBUTION_FILTERS = (("", "全部贡献"),) + tuple(
    (point, ExtensionPoint.label(point)) for point in ExtensionPoint.values()
)


class PluginHost:
    """交给插件的程序服务句柄（`ctx.host`）：只暴露读数据与少量动作。"""

    def __init__(self, service: "PluginService") -> None:
        self._service = service

    def viewers(self) -> tuple[ViewerInfo, ...]:
        """当前登记的查看器（含其他插件注册的）。"""
        api = open_api()
        return tuple(api.viewers()) if api is not None else ()

    def editors(self) -> tuple[EditorInfo, ...]:
        """当前登记的编辑器（含其他插件注册的）。"""
        api = editor_api()
        return tuple(api.editors()) if api is not None else ()

    def refresh_path(self, path: str | Path) -> int:
        """库内文件被外部（如编辑器）改写后，重算对应条目指纹并广播刷新，返回命中条目数。"""
        target = Path(path)
        count = 0
        try:
            from ..db.database import session_scope
            from ..repositories import ItemFilter
            from .item_service import ItemService

            wanted = target.resolve()
            with session_scope() as session:
                service = ItemService(session)
                filters = ItemFilter(include_hidden=True, include_deleted=True)
                for item in service.items.query(filters):
                    resolved = service.file_path_of(item)
                    if resolved is None or resolved.resolve() != wanted:
                        continue
                    if service.refresh_file(item):
                        count += 1
        except Exception:
            logger.exception("刷新库内文件失败：{}", target)
        if count:
            from ..core.signals import signalBus

            signalBus.itemsChanged.emit()
        logger.info("编辑器保存后刷新库内文件：{}（命中 {} 项）", target.name, count)
        return count

    def item_path(self, item) -> str:
        """数据项在库内的真实文件路径（没有磁盘文件时返回空串）。"""
        item_id = getattr(item, "id", None)
        if not item_id:
            return ""
        try:
            from ..db.database import new_session
            from ..db.models import DataItem
            from .item_service import ItemService

            session = new_session()
            try:
                fresh = session.get(DataItem, int(item_id))
                if fresh is None:
                    return ""
                path = ItemService(session).file_path_of(fresh)
                return str(path) if path is not None else ""
            finally:
                session.close()
        except Exception:
            logger.warning("读取数据项文件路径失败：{}", item_id)
            return ""

    def file_formats(self) -> dict[str, int]:
        """库里实际出现的文件格式（小写、不带点）→ 数量，供插件列出可配置的格式。"""
        try:
            from ..db.database import new_session

            from .item_service import ItemService

            with new_session() as session:
                counts = ItemService(session).extensions_in_use()
            return {str(key): int(value) for key, value in counts.items()}
        except Exception:
            logger.warning("读取库内文件格式失败，插件看到的是空字典")
            return {}

    def sample_path(self, suffix: str) -> str:
        """库里某个格式的一个现存文件路径（没有就返回空串），供插件做「测试打开」。"""
        wanted = str(suffix or "").strip().lower().lstrip(".")
        if not wanted:
            return ""
        try:
            from ..db.database import new_session
            from ..repositories import ItemFilter
            from .item_service import ItemService

            filters = ItemFilter(include_hidden=True, include_deleted=True)
            with new_session() as session:
                service = ItemService(session)
                for item in service.items.query(filters):
                    path = Path(item.file_path or item.source_path)
                    if path.suffix.lower().lstrip(".") != wanted:
                        continue
                    resolved = service.file_path_of(item)
                    if resolved is not None:
                        return str(resolved)
            return ""
        except Exception:
            logger.warning("查找样本文件失败：{}", suffix)
            return ""

    def open_path(self, path: str | Path) -> bool:
        """用系统默认程序打开文件。"""
        from ..core.shell import open_default

        return open_default(Path(path))

    def reveal_path(self, path: str | Path) -> bool:
        """在系统文件管理器里定位文件。"""
        from ..core.shell import reveal

        return reveal(Path(path))

    def current_user(self) -> str:
        """当前用户 id（没有数据库或用户时返回空串）。"""
        try:
            from ..db.database import new_session

            with new_session() as session:
                from .user_service import UserService

                return str(UserService(session).current_id())
        except Exception:
            logger.warning("读取当前用户失败，插件看到的是空值")
            return ""

    def toast(self, title: str, content: str = "") -> None:
        """弹一条界面提示；没有窗口（自检、命令行）时只写日志。"""
        try:
            from PyQt6.QtWidgets import QApplication

            from ..ui.framework.feedback import toast_info
        except Exception:  # 没有界面环境
            logger.info("插件提示：{} {}", title, content)
            return
        parent = QApplication.activeWindow()
        if parent is None:
            logger.info("插件提示：{} {}", title, content)
            return
        toast_info(parent, title, content)


class PluginService:
    """插件目录的发现、状态、载入与诊断（实现 `ContextServices` 协议）。"""

    def __init__(
        self,
        plugin_dir: Path | str | None = None,
        state_file: Path | str | None = None,
    ) -> None:
        self._plugin_dir = Path(plugin_dir) if plugin_dir else None
        self._state_file = Path(state_file) if state_file else None
        self._errors: dict[str, tuple[str, str]] = {}
        self._bootstrap: dict[str, object] = {}
        self._settings: dict[str, dict[str, Any]] = {}
        self._plugins: dict[str, Plugin] = {}
        self._contexts: dict[str, PluginContext] = {}
        self._contributions: dict[str, list[Contribution]] = {}
        self._handlers: dict[str, list[tuple[str, Callable[..., None]]]] = {}
        self._report: list[tuple[str, str, bool, str, float]] = []
        self._infos: dict[str, PluginInfo] = {}
        self._loaded: list[PluginInfo] = []
        self._pages: AppUiApi | None = None
        self.host = PluginHost(self)

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
            if not (folder / MANIFEST_NAME).is_file():
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
                        error_phase=PHASES[0],
                    )
                )
        return found

    def discover(self) -> list[PluginInfo]:
        """扫描 + 套用状态 + 依赖排序；缺依赖 / 循环 / 依赖未启用的插件带 error。"""
        raw = self.scan()
        state = self._read_state()
        order, dep_errors = resolve_dependencies(raw)
        base_enabled: dict[str, bool] = {}
        for info in raw:
            entry = state.get(info.id) or {}
            base_enabled[info.id] = bool(entry.get("enabled", info.enabled)) and not info.error
        # 依赖在前，逐级传播「依赖不可用」
        available: dict[str, bool] = {}
        blocked: dict[str, tuple[str, str]] = {}
        for info in order:
            missing = [dep for dep in info.depends_ids if not available.get(dep, False)]
            if missing:
                blocked[info.id] = (PHASES[1], "依赖插件未启用：" + "、".join(missing))
                available[info.id] = False
            else:
                available[info.id] = base_enabled[info.id]
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
            phase, message = dep_errors.get(info.id) or blocked.get(info.id) or ("", "")
            error = info.error or message
            if error:
                fields["error"] = error
                fields["error_phase"] = info.error_phase or phase
            fields["enabled"] = base_enabled[info.id] and not error and available.get(info.id, True)
            fields["contributions"] = self._contribution_labels(info.id)
            result.append(info.clone(**fields))
        position = {info.id: index for index, info in enumerate(order)}
        result.sort(key=lambda item: (position.get(item.id, len(order)), item.id))
        self._infos = {info.id: info for info in result}
        self._errors = {info.id: (info.error_phase, info.error) for info in result if info.error}
        return result

    def errors(self) -> dict[str, str]:
        """最近一次 discover() 得到的错误（`插件 id → [阶段] 说明`）。"""
        return {plugin_id: info.error_text for plugin_id, info in self._infos.items() if info.error}

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
        query: str = "",
        state: str = "",
        source: str = "",
        author: str = "",
        contribution: str = "",
        order: str = "default",
        reverse: bool = False,
    ) -> list[PluginInfo]:
        """按贡献 / 来源 / 创建者 / 状态 / 关键词筛选插件，并按指定方案排序。"""
        keyword = str(query or "").strip().lower()
        result: list[PluginInfo] = []
        for info in self.discover():
            if contribution and contribution not in self._points_of(info.id):
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
        if order == "source":
            return (info.source_label, info.name.lower())
        if order == "author":
            return (info.author_text.lower(), info.name.lower())
        if order == "state":
            return (info.state_label, info.name.lower())
        if order == "version":
            return (info.version_text, info.name.lower())
        if order == "contribution":
            return (info.contributions_text, info.name.lower())
        return (info.name.lower(), info.id)

    def authors(self) -> tuple[str, ...]:
        """所有插件里出现过的创建者（按名称排序），供界面做筛选。"""
        found = {info.author_text for info in self.discover()}
        return tuple(sorted(found, key=str.lower))

    @staticmethod
    def _search_text(info: PluginInfo) -> str:
        return " ".join(
            [
                info.name,
                info.id,
                info.description,
                info.author,
                info.note,
                info.source_label,
                info.contributions_text,
                info.libraries_text,
                " ".join(info.depends_ids),
                " ".join(info.provides),
                " ".join(info.data),
            ]
        )

    def _points_of(self, plugin_id: str) -> tuple[str, ...]:
        return tuple(sorted({item.point for item in self._contributions.get(plugin_id, ())}))

    def _contribution_labels(self, plugin_id: str) -> tuple[str, ...]:
        return tuple(sorted({ExtensionPoint.label(item.point) for item in self._contributions.get(plugin_id, ())}))

    # ------------------------------------------------------------ 选项
    def options_of(self, plugin_id: str) -> dict[str, object]:
        """该插件当前的选项值（含未设置项），未知插件返回空字典。"""
        if plugin_id in self._settings:
            return dict(self._settings[plugin_id])
        info = self.get(plugin_id)
        return dict(info.settings) if info is not None else {}

    def options(self, plugin_id: str) -> dict[str, Any]:
        """`ContextServices`：插件读自己的全部选项值。"""
        return self.options_of(plugin_id)

    def option(self, plugin_id: str, key: str, default: Any = None) -> Any:
        """`ContextServices`：插件读自己的一个选项值。"""
        return self.options_of(plugin_id).get(key, default)

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
        values = entry.get("options")
        if not isinstance(values, dict):
            values = {}
        values[key] = normalized
        entry["options"] = values
        plugins[plugin_id] = entry
        self._write_state(plugins)
        if plugin_id in self._settings:
            self._settings[plugin_id][key] = normalized
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
        info = self.get(plugin_id)
        if plugin_id in self._settings and info is not None:
            self._settings[plugin_id] = defaults(info.options)
        return True

    # ------------------------------------------------------ 程序本体接口
    def bootstrap(self, name: str, provider: object) -> None:
        """登记程序本体提供的扩展接口（如 `app.ui`）：每次载入插件后都会重新提供。

        提供者可以实现 `sync_plugins(plugin_ids)`，每次载入插件后被调用一次，
        用来把自身持有的插件产物（例如界面页面）与当前载入的插件集合对齐。
        """
        self._bootstrap[name] = provider
        if name == APP_UI_EXTENSION and isinstance(provider, AppUiApi):
            self._pages = provider
        extension_registry.provide(name, provider, "")

    def bootstrap_names(self) -> tuple[str, ...]:
        """已登记的程序本体扩展接口名。"""
        return tuple(self._bootstrap)

    def _provide_bootstrap(self) -> None:
        for name, provider in self._bootstrap.items():
            extension_registry.provide(name, provider, "")

    def _sync_bootstrap(self) -> None:
        loaded = tuple(self._plugins)
        for provider in self._bootstrap.values():
            sync = getattr(provider, "sync_plugins", None)
            if not callable(sync):
                continue
            try:
                sync(loaded)
            except Exception:  # 界面同步失败不应该影响插件载入
                logger.exception("程序本体接口同步失败：{}", type(provider).__name__)

    # ------------------------------------------------ ContextServices 实现
    def provide(self, plugin_id: str, name: str, obj: object) -> None:
        """向其他插件暴露一个扩展接口。"""
        extension_registry.provide(name, obj, plugin_id)

    def require(self, plugin_id: str, name: str) -> object:
        """取其他插件提供的扩展接口，缺失时报 PluginError。"""
        provider = extension_registry.provider(name)
        if provider is None:
            raise PluginError(f"缺少扩展接口：{name}（请检查依赖插件是否已安装并启用）")
        return provider

    def has(self, plugin_id: str, name: str) -> bool:
        """某个扩展接口是否可用。"""
        return name in extension_registry

    def contribute(
        self,
        plugin_id: str,
        point: str,
        value: object,
        *,
        key: str = "",
        order: int = 100,
        description: str = "",
        **extra: object,
    ) -> Contribution:
        """登记一条扩展点贡献；插件卸载时自动撤销。"""
        if point not in ExtensionPoint.values():
            raise PluginError(f"未知扩展点：{point}（可用：{'、'.join(ExtensionPoint.values())}）")
        item = Contribution(
            point=point,
            plugin_id=plugin_id,
            value=value,
            key=str(key),
            order=int(order),
            description=str(description),
            extra=dict(extra),
        )
        self._contributions.setdefault(plugin_id, []).append(item)
        return item

    def contributions(self, plugin_id: str = "", point: str = "") -> tuple[Contribution, ...]:
        """已载入插件登记的贡献（可按插件与扩展点过滤）。"""
        if plugin_id:
            items = tuple(self._contributions.get(plugin_id, ()))
        else:
            items = tuple(item for group in self._contributions.values() for item in group)
        if point:
            items = tuple(item for item in items if item.point == point)
        return items

    def point_items(self, point: str) -> tuple[Contribution, ...]:
        """某个扩展点上的全部贡献，按 order、插件 id、键排序（界面用）。"""
        items = [item for item in self.contributions(point=point)]
        items.sort(key=lambda item: (item.order, item.plugin_id, item.key))
        return tuple(items)

    def on(self, plugin_id: str, event: str, handler: Callable[..., None]) -> None:
        """订阅一个事件。"""
        if event not in Events.values():
            raise PluginError(f"未知事件：{event}（可用：{'、'.join(Events.values())}）")
        if not callable(handler):
            raise PluginError(f"事件 {event} 的处理函数不可调用")
        self._handlers.setdefault(event, []).append((plugin_id, handler))

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        """`ContextServices`：插件广播一个事件。"""
        self.publish(event, **payload)

    def publish(self, event: str, **payload: Any) -> int:
        """把事件发给订阅了它的插件，返回成功处理的个数（程序内部也用它广播）。"""
        handlers = list(self._handlers.get(event, ()))
        done = 0
        for plugin_id, handler in handlers:
            try:
                handler(**payload)
                done += 1
            except Exception:
                logger.exception("插件 {} 处理事件 {} 失败", plugin_id, event)
        return done

    def add_viewer(
        self,
        plugin_id: str,
        name: str,
        *,
        extensions: Iterable[str] = (),
        factory: ViewerFactory | None = None,
        opener: ViewerOpener | None = None,
        kind: str = "text",
        description: str = "",
        capabilities: Sequence[str] = (),
        viewer_id: str = "",
        host: str = "",
        order: int = 100,
    ) -> Contribution:
        """注册一个查看器，并记下对应贡献。"""
        if factory is not None and not callable(factory):
            raise PluginError(f"查看器「{name}」没有可用的控件工厂（插件需自行创建视图）")
        clean: list[str] = []
        for item in extensions:
            suffix = str(item).strip().lstrip(".").lower()
            if suffix and suffix not in clean:
                clean.append(suffix)
        if not clean:
            raise PluginError(f"查看器「{name}」没有声明任何扩展名")
        if not viewer_id:
            viewer_id = f"{plugin_id}.{len(self._contributions.get(plugin_id, ())) + 1}"
        if host:
            self.require(plugin_id, host)  # 显示窗口由该扩展接口提供，缺了就注册失败
        api = open_api()
        if api is None:
            raise PluginError("程序没有提供查看器接口 viewer.open，无法注册查看器")
        viewer = api.add_viewer(
            plugin_id,
            viewer_id=viewer_id,
            name=name,
            extensions=tuple(clean),
            kind=kind,
            factory=factory,
            opener=opener,
            host=host,
            description=description,
            capabilities=tuple(capabilities),
        )
        return self.contribute(
            plugin_id,
            ExtensionPoint.VIEWER,
            viewer,
            key=viewer_id,
            order=order,
            description=description,
        )

    def add_editor(
        self,
        plugin_id: str,
        name: str,
        *,
        extensions: Iterable[str] = (),
        factory: EditorFactory | None = None,
        opener: EditorOpener | None = None,
        kind: str = "internal",
        description: str = "",
        capabilities: Sequence[str] = (),
        editor_id: str = "",
        host: str = "",
        order: int = 100,
    ) -> Contribution:
        """注册一个编辑器，并记下对应贡献。"""
        if factory is not None and not callable(factory):
            raise PluginError(f"编辑器「{name}」没有可用的控件工厂（插件需自行创建编辑器）")
        clean: list[str] = []
        for item in extensions:
            suffix = str(item).strip().lstrip(".").lower()
            if suffix and suffix not in clean:
                clean.append(suffix)
        if not clean:
            raise PluginError(f"编辑器「{name}」没有声明任何扩展名")
        if not editor_id:
            editor_id = f"{plugin_id}.{len(self._contributions.get(plugin_id, ())) + 1}"
        if host:
            self.require(plugin_id, host)  # 显示窗口由该扩展接口提供，缺了就注册失败
        api = editor_api()
        if api is None:
            raise PluginError("程序没有提供编辑器接口 editor.open，无法注册编辑器")
        editor = api.add_editor(
            plugin_id,
            editor_id=editor_id,
            name=name,
            extensions=tuple(clean),
            kind=kind,
            factory=factory,
            opener=opener,
            host=host,
            description=description,
            capabilities=tuple(capabilities),
        )
        return self.contribute(
            plugin_id,
            ExtensionPoint.EDITOR,
            editor,
            key=editor_id,
            order=order,
            description=description,
        )

    def add_page(
        self,
        plugin_id: str,
        key: str,
        title: str,
        factory: Callable[[], object],
        *,
        icon: str = "",
        bottom: bool = False,
        order: int = 100,
    ) -> Contribution:
        """往主窗口加一个页面（需要程序提供 `app.ui` 接口）。"""
        api = self._pages
        if api is None:
            raise PluginError("程序没有提供界面接口 app.ui，无法注册页面")
        spec = api.add_page(key, title, factory, icon=icon, bottom=bottom, plugin_id=plugin_id)
        return self.contribute(
            plugin_id,
            ExtensionPoint.PAGE,
            spec,
            key=spec.key,
            order=order,
            title=title,
        )

    def _resolve_library(self, plugin_id: str, module: str = "") -> object:
        if plugin_id not in self._plugins:
            raise SdkError(f"插件未载入，取不到它的库：{plugin_id}")
        try:
            return import_library(plugin_id, module)
        except PluginError as exc:
            raise SdkError(str(exc)) from exc

    def _dependency_lookup(self, plugin_id: str) -> bool:
        return plugin_id in self._plugins

    # ---------------------------------------------------------- 载入
    def load(self) -> int:
        """按依赖顺序载入所有启用的插件，返回注册的查看器数量。"""
        self.teardown_all()
        api = open_api()
        if api is not None:
            api.clear()
        editor = editor_api()
        if editor is not None:
            editor.clear()
        extension_registry.clear()
        self._provide_bootstrap()
        register_library_resolver(self._resolve_library)
        register_dependency_lookup(self._dependency_lookup)
        self._report = []
        self._loaded = []
        count = 0
        infos = self.discover()
        logger.info(
            "插件扫描完成：发现 {} 个（启用 {}、未启用 {}、清单有误 {}）",
            len(infos),
            sum(1 for info in infos if info.enabled and not info.error),
            sum(1 for info in infos if not info.enabled),
            sum(1 for info in infos if info.error),
        )
        for info in infos:
            if info.error or not info.enabled:
                continue
            if info.id in self._plugins:  # 同一轮里重复出现（清单 id 重复）由 core 拦下
                continue
            ok = self._load_one(info)
            if ok:
                self._loaded.append(info)
                count += len(self.viewers_of(info.id))
                self._note_unregistered_provides(info)
        self._sync_bootstrap()
        logger.info(self.loaded_summary())
        return count

    def load_viewers(self) -> int:
        """载入入口的别名（历史命名，界面与启动流程仍用它）。"""
        return self.load()

    def load_editors(self) -> int:
        """载入编辑器的别名（与载入整体插件等价）。"""
        return self.load()

    def _load_one(self, info: PluginInfo) -> bool:
        start = time.perf_counter()
        try:
            register_plugin_namespace(info)
        except PluginError as exc:
            return self._fail(info, PHASES[2], str(exc), start)
        try:
            module = import_entry(info)
        except PluginError as exc:
            return self._fail(info, PHASES[2], str(exc), start)
        except Exception:
            logger.exception("插件入口导入异常：{}", info.id)
            return self._fail(info, PHASES[2], "插件入口导入失败，详见日志", start)
        try:
            cls = plugin_class(info, module)
            plugin = cls()
            plugin.attach(
                id=info.id,
                name=info.name,
                version=info.version,
                description=info.description,
                author=info.author,
                path=Path(info.path) if info.path is not None else None,
                manifest=dict(info.manifest),
            )
            self._plugins[info.id] = plugin
        except PluginError as exc:
            return self._fail(info, PHASES[3], str(exc), start)
        except Exception:
            logger.exception("插件构造异常：{}", info.id)
            return self._fail(info, PHASES[3], "插件构造失败，详见日志", start)
        context = PluginContext(plugin, self)
        self._contexts[info.id] = context
        self._settings[info.id] = dict(info.settings)
        try:
            plugin.setup(context)
        except PluginError as exc:
            return self._fail(info, PHASES[4], str(exc), start)
        except Exception:
            logger.exception("插件 setup 异常：{}", info.id)
            return self._fail(info, PHASES[4], "插件初始化失败，详见日志", start)
        seconds = time.perf_counter() - start
        self._errors.pop(info.id, None)
        self._report.append((info.id, PHASES[4], True, "", seconds))
        logger.info("插件 {} 已载入", info.id)
        return True

    def _fail(self, info: PluginInfo, phase: str, message: str, start: float) -> bool:
        seconds = time.perf_counter() - start
        self._drop_plugin_state(info.id)
        self._errors[info.id] = (phase, message)
        self._report.append((info.id, phase, False, message, seconds))
        logger.warning("插件载入失败：{}（{}）", info.id, message)
        return False

    def _note_unregistered_provides(self, info: PluginInfo) -> None:
        """清单 `provides` 与实际注册对不上时记一条备注：只提示，不算载入失败。"""
        current = self._infos.get(info.id)
        existing = (current.note if current is not None else "") or ""
        missing = [name for name in info.provides if extension_registry.provider_plugin(name) != info.id]
        if missing:
            text = PROVIDES_NOTE_PREFIX + "、".join(missing)
            if existing == text:
                return
            self._save_state_for(info.id, note=text)
            if current is not None:
                self._infos[info.id] = current.clone(note=text)
            logger.info("插件 {} 声明了扩展接口 {}，但没有实际注册：已记入备注", info.id, "、".join(missing))
            return
        if existing.startswith(PROVIDES_NOTE_PREFIX):  # 提示过时了，只清掉自己写的那条
            self._save_state_for(info.id, note="")
            if current is not None:
                self._infos[info.id] = current.clone(note="")

    def _drop_plugin_state(self, plugin_id: str) -> None:
        """撤销该插件的一切运行期痕迹（贡献、接口、页面、模块）。"""
        self._contributions.pop(plugin_id, None)
        self._settings.pop(plugin_id, None)
        self._contexts.pop(plugin_id, None)
        self._plugins.pop(plugin_id, None)
        api = open_api()
        if api is not None:
            api.unregister_plugin(plugin_id)
        editor = editor_api()
        if editor is not None:
            editor.unregister_plugin(plugin_id)
        extension_registry.drop_plugin(plugin_id)
        for event, handlers in list(self._handlers.items()):
            kept = [(owner, fn) for owner, fn in handlers if owner != plugin_id]
            if kept:
                self._handlers[event] = kept
            else:
                self._handlers.pop(event, None)
        if self._pages is not None:
            for key in [item.key for item in self._pages.pages() if item.plugin_id == plugin_id]:
                self._pages.remove_page(key)
        prefix = f"{DM_PACKAGE}.{plugin_id}"
        for name in [name for name in sys.modules if name == prefix or name.startswith(prefix + ".")]:
            sys.modules.pop(name, None)

    def teardown(self, plugin_id: str) -> bool:
        """卸载一个插件（调用它的 `teardown()` 并撤销全部贡献）。"""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return False
        try:
            plugin.teardown()
        except Exception:
            logger.exception("插件退出异常：{}", plugin_id)
        self._drop_plugin_state(plugin_id)
        self._loaded = [info for info in self._loaded if info.id != plugin_id]
        return True

    def teardown_all(self) -> None:
        """卸载全部已载入的插件（倒序，先卸载依赖方）。"""
        for plugin_id in reversed(list(self._plugins)):
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                continue
            try:
                plugin.teardown()
            except Exception:
                logger.exception("插件退出异常：{}", plugin_id)
        for plugin_id in reversed(list(self._plugins)):
            self._drop_plugin_state(plugin_id)
        self._plugins.clear()
        self._contexts.clear()
        self._contributions.clear()
        self._handlers.clear()
        self._settings.clear()
        self._loaded = []
        reset_namespace()

    def loaded_plugins(self) -> list[PluginInfo]:
        """上一次载入成功的插件（按依赖顺序）。"""
        return list(self._loaded)

    def load_report(self) -> list[tuple[str, str, bool, str, float]]:
        """最近一次载入的分阶段报告：`(插件 id, 阶段, 是否成功, 说明, 用时秒)`。"""
        return list(self._report)

    def loaded_summary(self) -> str:
        """这次载入的汇总：总数与启用情况，再按库插件 / 功能插件分别列出。"""
        infos = list(self._infos.values())
        if not infos:
            return "本次没有发现任何插件"

        def part(items: list[PluginInfo]) -> str:
            enabled = sum(1 for info in items if info.enabled)
            return f"{len(items)} 个（已启用 {enabled}、未启用 {len(items) - enabled}）"

        libraries = [info for info in infos if info.libraries]
        features = [info for info in infos if not info.libraries]
        text = f"插件载入：共 {part(infos)}；库插件 {part(libraries)}、功能插件 {part(features)}"
        failures = sum(1 for info in infos if info.error)
        if failures:
            text += f"；{failures} 个插件载入失败（见插件页）"
        return text

    def viewers_of(self, plugin_id: str) -> list[ViewerInfo]:
        """该插件注册的查看器（未载入时先按需载入）。"""
        if not self._plugins and not self._loaded:
            self.load()
        return [item.value for item in self._contributions.get(plugin_id, ()) if item.point == ExtensionPoint.VIEWER]

    def editors_of(self, plugin_id: str) -> list[EditorInfo]:
        """该插件注册的编辑器（未载入时先按需载入）。"""
        if not self._plugins and not self._loaded:
            self.load()
        return [item.value for item in self._contributions.get(plugin_id, ()) if item.point == ExtensionPoint.EDITOR]

    def describe(self, plugin_id: str) -> list[tuple[str, str]]:
        """插件自报的运行期信息（键值行），未载入返回空列表。"""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return []
        try:
            return list(plugin.describe())
        except Exception:
            logger.exception("插件自检信息读取失败：{}", plugin_id)
            return []

    # ---------------------------------------------------------- 操作
    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        info = self.get(plugin_id)
        if info is None:
            return False
        if enabled and info.error:
            logger.warning("插件处于异常状态，无法启用：{}（{}）", plugin_id, info.error)
            return False
        self._save_state_for(plugin_id, enabled=bool(enabled))
        self.publish(Events.PLUGIN_ENABLED if enabled else Events.PLUGIN_DISABLED, plugin_id=plugin_id)
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
        self.load()
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
            self.load()
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
        if (root / MANIFEST_NAME).is_file():
            return root
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and (folder / MANIFEST_NAME).is_file():
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
    "CONTRIBUTION_FILTERS",
    "PLUGIN_ORDERS",
    "SOURCE_FILTERS",
    "STATE_FILTERS",
    "STATE_VERSION",
    "PluginError",
    "PluginHost",
    "PluginInfo",
    "PluginService",
    "SOURCE_BUILTIN",
    "SOURCE_EXTERNAL",
    "plugin_service",
]
