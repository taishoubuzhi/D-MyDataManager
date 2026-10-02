"""查看器插件的模型与注册表。

查看器只声明「哪些扩展名由谁打开」以及如何创建视图控件；控件本身延迟创建，
所以注册表不依赖 Qt，无界面环境（单元测试、自检脚本）也能使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: 创建视图控件：`(path, parent) -> QWidget`，由插件自己延迟 import Qt
ViewerFactory = Callable[[Path, object], object]

#: 插件自带的打开函数：`(path) -> (是否成功, 说明)`，由查看器插件在弹窗里打开自己的页面
ViewerOpener = Callable[[Path], "tuple[bool, str]"]

#: 内置插件的 id 前缀，便于界面上区分「内置」与「外部」
BUILTIN_PREFIX = "builtin:"

#: 查看器分类：界面上的筛选与图标都按它走
KINDS: tuple[tuple[str, str], ...] = (
    ("image", "图片"),
    ("video", "视频"),
    ("audio", "音频"),
    ("archive", "压缩包"),
    ("text", "文本"),
    ("code", "代码"),
    ("markdown", "Markdown"),
    ("spreadsheet", "表格"),
)
KIND_LABELS = dict(KINDS)


def normalize_suffix(value: str | Path) -> str:
    """取小写、不带点的扩展名：`A.TXT` → `txt`，`a.tar.gz` → `gz`。"""
    from ..sdk.data import suffix_of

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
    #: 负责显示该查看器的扩展接口名（例如 "dialog"）；空值表示由界面自带窗口显示
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
        """按扩展名找出所有匹配的查看器（按 id 排序），供「打开方式」页选择具体插件。"""
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


#: 全局注册表：插件服务在启动时把启用插件的查看器注册进来
viewer_registry = ViewerRegistry()
