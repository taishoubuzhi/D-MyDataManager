"""编辑器注册表：记录（Editor）与查询（EditorRegistry）。

程序本体不认识编辑器，只按 `editor.open` 接口拿注册表；本模块是编辑器工具库的内部结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.sdk.data import suffix_of

#: 创建编辑器控件：`(path, parent) -> QWidget`
EditorFactory = Callable[..., object]

#: 插件自带的编辑函数：`(path, parent) -> (是否成功, 说明)`
EditorOpener = Callable[..., "tuple[bool, str]"]

#: 内置编辑器：在程序窗口里用插件提供的控件编辑
KIND_INTERNAL = "internal"
#: 外部编辑器：交给系统默认程序（或用户自定义程序）编辑
KIND_EXTERNAL = "external"
KINDS = (KIND_INTERNAL, KIND_EXTERNAL)


def normalize_suffix(value: object) -> str:
    """统一成小写、不带点的扩展名。"""
    return suffix_of(value)


@dataclass(frozen=True)
class Editor:
    """一条编辑器记录：谁（plugin_id）能用哪些后缀、怎么打开。"""

    id: str
    name: str
    extensions: tuple[str, ...] = ()
    kind: str = KIND_INTERNAL
    plugin_id: str = ""
    factory: EditorFactory | None = None
    opener: EditorOpener | None = None
    host: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, suffix: str) -> bool:
        return normalize_suffix(suffix) in self.extensions


class EditorRegistry:
    """进程内的编辑器集合：注册、按后缀 / 插件查询。"""

    def __init__(self) -> None:
        self._items: dict[str, Editor] = {}

    def register(self, editor: Editor) -> Editor:
        self._items[editor.id] = editor
        return editor

    def unregister_plugin(self, plugin_id: str) -> int:
        removed = [key for key, item in self._items.items() if item.plugin_id == plugin_id]
        for key in removed:
            self._items.pop(key, None)
        return len(removed)

    def clear(self) -> None:
        self._items.clear()

    def all(self) -> tuple[Editor, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def by_id(self, editor_id: str) -> Editor | None:
        return self._items.get(str(editor_id or ""))

    def for_suffix(self, suffix: str) -> Editor | None:
        wanted = normalize_suffix(suffix)
        for editor in reversed(self.all()):
            if wanted in editor.extensions:
                return editor
        return None

    def all_for_suffix(self, suffix: str) -> tuple[Editor, ...]:
        wanted = normalize_suffix(suffix)
        return tuple(editor for editor in self.all() if wanted in editor.extensions)

    def by_plugin(self, plugin_id: str) -> tuple[Editor, ...]:
        return tuple(editor for editor in self.all() if editor.plugin_id == plugin_id)

    def extensions(self) -> tuple[str, ...]:
        seen: list[str] = []
        for editor in self.all():
            for suffix in editor.extensions:
                if suffix and suffix not in seen:
                    seen.append(suffix)
        return tuple(sorted(seen))

    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(sorted({editor.plugin_id for editor in self.all() if editor.plugin_id}))


#: 全局注册表：编辑器工具库提供接口时读写它。
editor_registry = EditorRegistry()


def reset() -> None:
    """清空注册表（编辑器工具库卸载时调用）。"""
    editor_registry.clear()


__all__ = [
    "Editor",
    "EditorFactory",
    "EditorOpener",
    "EditorRegistry",
    "KIND_EXTERNAL",
    "KIND_INTERNAL",
    "KINDS",
    "editor_registry",
    "normalize_suffix",
    "reset",
]
