"""插件上下文：插件与程序打交道的唯一门面。

核心为每个插件构造一个 :class:`PluginContext` 并交给 `Plugin.setup(ctx)`；
插件只调用这里的方法，不 import 程序内部模块。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .console import console_for
from .plugin import Plugin
from .points import Contribution

__all__ = ["ContextServices", "PluginContext"]


@runtime_checkable
class ContextServices(Protocol):
    """核心必须提供的服务（插件不实现，只使用）。

    方法都带 `plugin_id` 首参：核心据此记录「谁贡献了什么」，卸载时自动撤销。
    """

    host: Any

    def option(self, plugin_id: str, key: str, default: Any = None) -> Any: ...

    def options(self, plugin_id: str) -> dict[str, Any]: ...

    def set_option(self, plugin_id: str, key: str, value: Any) -> Any: ...

    def provide(self, plugin_id: str, name: str, obj: Any) -> None: ...

    def require(self, plugin_id: str, name: str) -> Any: ...

    def has(self, plugin_id: str, name: str) -> bool: ...

    def contribute(
        self,
        plugin_id: str,
        point: str,
        value: Any,
        *,
        key: str = "",
        order: int = 100,
        description: str = "",
        **extra: Any,
    ) -> Contribution: ...

    def contributions(self, plugin_id: str = "", point: str = "") -> tuple[Contribution, ...]: ...

    def on(self, plugin_id: str, event: str, handler: Callable) -> None: ...

    def emit(self, event: str, payload: dict[str, Any]) -> None: ...

    def add_viewer(
        self,
        plugin_id: str,
        name: str,
        *,
        extensions: Any = (),
        factory: Callable | None = None,
        opener: Callable | None = None,
        kind: str = "text",
        description: str = "",
        capabilities: Any = (),
        viewer_id: str = "",
        host: str = "",
        order: int = 100,
    ) -> Contribution: ...

    def add_editor(
        self,
        plugin_id: str,
        name: str,
        *,
        extensions: Any = (),
        factory: Callable | None = None,
        opener: Callable | None = None,
        kind: str = "internal",
        description: str = "",
        capabilities: Any = (),
        editor_id: str = "",
        host: str = "",
        order: int = 100,
    ) -> Contribution: ...

    def add_page(
        self,
        plugin_id: str,
        key: str,
        title: str,
        factory: Callable,
        *,
        icon: str = "",
        bottom: bool = False,
        order: int = 100,
    ) -> Contribution: ...
class PluginContext:
    """插件上下文：读配置、取数据、注册贡献、订阅事件、取依赖的接口。"""

    def __init__(self, plugin: Plugin, services: ContextServices) -> None:
        self._plugin = plugin
        self._services = services
        self._console = console_for(plugin.id)

    # ---- 基本信息 ---------------------------------------------------

    @property
    def plugin_id(self) -> str:
        return self._plugin.id

    @property
    def plugin_name(self) -> str:
        return self._plugin.name

    @property
    def manifest(self) -> Mapping[str, Any]:
        return self._plugin.manifest

    @property
    def log(self):
        return self._plugin.log

    @property
    def console(self):
        """控制台输出：`ctx.console.stage("下载", "开始")`，来源自动带上插件 id。"""
        return self._console

    def data_path(self, key: str):
        return self._plugin.data_path(key)

    def data(self, key: str, default: Any = None) -> Any:
        return self._plugin.data(key, default)

    # ---- 选项（清单 options 与用户设置） -----------------------------

    def option(self, key: str, default: Any = None) -> Any:
        return self._services.option(self.plugin_id, key, default)

    def options(self) -> dict[str, Any]:
        return dict(self._services.options(self.plugin_id))

    def set_option(self, key: str, value: Any) -> Any:
        """写入一个选项：与插件页「插件设置」写的是同一份，改完立即生效。"""
        return self._services.set_option(self.plugin_id, key, value)

    # ---- 运行期接口 -------------------------------------------------

    def provide(self, name: str, obj: Any) -> None:
        self._services.provide(self.plugin_id, name, obj)

    def require(self, name: str) -> Any:
        return self._services.require(self.plugin_id, name)

    def has(self, name: str) -> bool:
        return self._services.has(self.plugin_id, name)

    # ---- 扩展点贡献 -------------------------------------------------

    def contribute(
        self,
        point: str,
        value: Any,
        *,
        key: str = "",
        order: int = 100,
        description: str = "",
        **extra: Any,
    ) -> Contribution:
        """往扩展点放东西；返回的贡献会随插件卸载自动撤销。"""
        return self._services.contribute(
            self.plugin_id,
            point,
            value,
            key=key,
            order=order,
            description=description,
            **extra,
        )

    def contributions(self, point: str = "") -> tuple[Contribution, ...]:
        return tuple(self._services.contributions(self.plugin_id, point))

    # ---- 事件 -------------------------------------------------------

    def on(self, event: str, handler: Callable) -> None:
        self._services.on(self.plugin_id, event, handler)

    def emit(self, event: str, **payload: Any) -> None:
        self._services.emit(event, dict(payload))

    # ---- 程序扩展点的便捷封装 ---------------------------------------

    def add_viewer(self, name: str, **fields: Any) -> Contribution:
        """注册一个查看器。"""
        return self._services.add_viewer(self.plugin_id, name, **fields)

    def add_editor(self, name: str, **fields: Any) -> Contribution:
        """注册一个编辑器。"""
        return self._services.add_editor(self.plugin_id, name, **fields)

    def add_page(self, key: str, title: str, factory: Callable, **fields: Any) -> Contribution:
        """往主窗口加一个页面。"""
        return self._services.add_page(self.plugin_id, key, title, factory, **fields)

    # ---- 程序服务 ---------------------------------------------------

    @property
    def host(self) -> Any:
        """程序服务句柄（只读白名单：查看器列表、打开路径、当前用户、提示等）。"""
        return self._services.host
