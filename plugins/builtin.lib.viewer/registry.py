"""查看器注册表：扩展名 → 查看器，住在查看器插件库里。

程序本体不再保存任何查看器信息（`app.sdk.viewers` 只剩调度门面），注册表随
`viewer.open` 接口一起由本插件提供：插件通过 `ViewerOpenApi.add_viewer()` 登记，
禁用插件时整组注销，重新载入时重新登记。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.sdk.data import suffix_of

#: 创建视图控件：`(path, parent) -> QWidget`
ViewerFactory = Callable[[Path, object], object]

#: 插件自带的打开函数：`(path, parent) -> (是否成功, 说明)`
ViewerOpener = Callable[..., "tuple[bool, str]"]


def normalize_suffix(value: str | Path) -> str:
    """取小写、不带点的扩展名：`A.TXT` → `txt`，`a.tar.gz` → `gz`。"""
    return suffix_of(value)


@dataclass(frozen=True)
class Viewer:
    """一个查看器：`id` 全局唯一，`extensions` 为小写、不带点的扩展名。"""

    id: str
    name: str
    extensions: tuple[str, ...]
    kind: str = "text"
    plugin_id: str = ""
    factory: ViewerFactory | None = None
    #: 插件自带的打开函数（有它时界面不再替它找宿主窗口）
    opener: ViewerOpener | None = None
    #: 负责显示该查看器的扩展接口名（例如 "dialog"）；空值表示由调度方自己决定
    host: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, suffix: str | Path) -> bool:
        return normalize_suffix(suffix) in self.extensions


class ViewerRegistry:
    """扩展名 → 查看器；禁用插件时整组注销，启用时重新注册。"""

    def __init__(self) -> None:
        self._viewers: dict[str, Viewer] = {}

    def register(self, viewer: Viewer) -> Viewer:
        self._viewers[viewer.id] = viewer
        return viewer

    def unregister_plugin(self, plugin_id: str) -> int:
        """注销某个插件的全部查看器，返回注销数量。"""
        keys = [key for key, viewer in self._viewers.items() if viewer.plugin_id == plugin_id]
        for key in keys:
            del self._viewers[key]
        return len(keys)

    def clear(self) -> None:
        self._viewers.clear()

    def all(self) -> list[Viewer]:
        return sorted(self._viewers.values(), key=lambda viewer: viewer.id)

    def by_id(self, viewer_id: str) -> Viewer | None:
        return self._viewers.get(viewer_id)

    def for_suffix(self, suffix: str | Path) -> Viewer | None:
        """按扩展名找查看器；同一扩展名被多个插件声明时以 id 靠后者为准。"""
        name = normalize_suffix(suffix)
        if not name:
            return None
        for viewer in reversed(self.all()):
            if name in viewer.extensions:
                return viewer
        return None

    def all_for_suffix(self, suffix: str | Path) -> tuple[Viewer, ...]:
        """按扩展名找出所有匹配的查看器（按 id 排序），供配置界面选择具体插件。"""
        name = normalize_suffix(suffix)
        if not name:
            return ()
        return tuple(viewer for viewer in self.all() if name in viewer.extensions)

    def by_plugin(self, plugin_id: str) -> tuple[Viewer, ...]:
        """某个插件注册的全部查看器。"""
        return tuple(viewer for viewer in self.all() if viewer.plugin_id == plugin_id)

    def extensions(self) -> list[str]:
        """所有已注册的扩展名（去重排序）。"""
        found = {extension for viewer in self._viewers.values() for extension in viewer.extensions}
        return sorted(found)

    def plugin_ids(self) -> list[str]:
        return sorted({viewer.plugin_id for viewer in self._viewers.values() if viewer.plugin_id})


#: 全局注册表：查看器插件库持有，插件服务通过 `viewer.open` 接口写入
viewer_registry = ViewerRegistry()


def reset() -> None:
    """清空注册表（查看器插件库卸载时调用）。"""
    viewer_registry.clear()


__all__ = [
    "Viewer",
    "ViewerFactory",
    "ViewerOpener",
    "ViewerRegistry",
    "normalize_suffix",
    "reset",
    "viewer_registry",
]
