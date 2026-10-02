"""取库助手：`library()` 是方案 B 的兜底入口，`requires()` 用来断言依赖已声明。

内置插件统一用方案 A 的静态导入：

    from dm_plugin.builtin.lib.viewer.viewer_kind import ViewerPlugin

核心按依赖顺序加载插件，并在加载每个插件前把它注册成 `dm_plugin.<id>` 命名空间包，
所以只要在 `depends` 里声明过，上面的导入必然成功。
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from .errors import SdkError

__all__ = ["library", "register_dependency_lookup", "register_library_resolver", "requires"]

_RESOLVER: Callable[[str, str], Any] | None = None
_DEPENDENCIES: Callable[[str], bool] | None = None


def register_library_resolver(resolver: Callable[[str, str], Any] | None) -> None:
    """核心注入取库函数：`resolver(plugin_id, module)` 返回该插件暴露的库模块。"""
    global _RESOLVER
    _RESOLVER = resolver


def register_dependency_lookup(lookup: Callable[[str], bool] | None) -> None:
    """核心注入依赖查询：`lookup(plugin_id)` 判断当前插件是否依赖了它。"""
    global _DEPENDENCIES
    _DEPENDENCIES = lookup


def library(plugin_id: str, module: str = "") -> Any:
    """取插件 `plugin_id` 暴露的库模块；`module` 为空时返回插件包本身。"""
    target = f"{plugin_id}.{module}" if module else str(plugin_id)
    if _RESOLVER is not None:
        try:
            return _RESOLVER(plugin_id, module)
        except Exception as exc:
            raise SdkError(f"取不到插件库：{target}（{exc}）") from exc
    try:
        return importlib.import_module(f"dm_plugin.{target}")
    except Exception as exc:
        raise SdkError(f"取不到插件库：{target}（{exc}）") from exc


def requires(plugin_id: str) -> None:
    """断言当前插件确实依赖了 `plugin_id`（没写进 depends 就报错，避免隐式耦合）。"""
    if _DEPENDENCIES is None:
        raise SdkError(f"无法校验依赖：{plugin_id}（核心未注入依赖查询）")
    if not _DEPENDENCIES(plugin_id):
        raise SdkError(f"插件未在 depends 里声明依赖：{plugin_id}")
