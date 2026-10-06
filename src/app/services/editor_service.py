"""编辑器调度（程序侧）：走扩展接口 `editor.open`，没有插件时退化为系统默认程序。

与 `viewer_service.py` 同构：编辑器专属接口已经从 `app.sdk` 移走（TODO 1.3.2），
调度接口由编辑器工具库 `builtin.lib.editor` 通过扩展点 `editor.open` 提供；
这里放**程序自己**要用的调度与退路（插件不能 import `app.services`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from loguru import logger

#: 编辑器扩展点（编辑器工具库 `builtin.lib.editor` 在清单里声明并提供）
EDITOR_EXTENSION = "editor.open"

#: 创建编辑器控件：`(path, parent) -> QWidget`，由插件自己延迟 import Qt
EditorFactory = Callable[[Path, object], object]

#: 插件自带的编辑函数：`(path, parent) -> (是否成功, 说明)`
EditorOpener = Callable[..., "tuple[bool, str]"]


class EditorInfo(Protocol):
    """编辑器记录：程序只认这些字段，真正的记录类由编辑器插件定义。"""

    id: str
    name: str
    extensions: tuple[str, ...]
    kind: str
    plugin_id: str
    host: str
    description: str
    capabilities: tuple[str, ...]

    def matches(self, suffix: str) -> bool: ...


# --------------------------------------------------------------------- 调度
def editor_api() -> object | None:
    """编辑器插件提供的调度接口（`editor.open`）；没启用编辑器插件时为 None。"""
    from ..core.plugins.extensions import extension_registry

    return extension_registry.provider(EDITOR_EXTENSION)


def _missing(path: Path | str) -> str:
    return f"文件不存在：{Path(path).name}"


def _fallback(path: Path | str) -> tuple[bool, str]:
    """没有编辑器插件时的退路：交给系统默认程序。"""
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    from ..sdk import ui

    if ui.open_default(target):
        return True, "已交给系统默认编辑器"
    return False, "系统无法打开该文件"


def editors_for(suffix: str | Path) -> tuple[EditorInfo, ...]:
    """该扩展名可用的全部编辑器（配置界面与右键菜单用）；没有插件时为空。"""
    api = editor_api()
    if api is None:
        return ()
    try:
        return tuple(api.editors_for(suffix))
    except Exception:
        logger.exception("读取编辑器列表失败：{}", suffix)
        return ()


def edit_path(path: Path | str, parent=None) -> tuple[bool, str]:
    """编辑文件，返回 (是否成功, 说明)：由编辑器插件决定内置编辑还是系统程序。"""
    api = editor_api()
    if api is None:
        return _fallback(path)
    try:
        return api.edit_path(path, parent)
    except Exception as exc:
        logger.exception("编辑器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def edit_with(path: Path | str, editor, parent=None) -> tuple[bool, str]:
    """点名用某个内置编辑器编辑。"""
    api = editor_api()
    if api is None:
        return _fallback(path)
    try:
        return api.edit_with(path, editor, parent)
    except Exception as exc:
        logger.exception("编辑器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def open_system(path: Path | str, ask: bool = False) -> tuple[bool, str]:
    """绕过内置编辑器：`ask=False` 用系统默认程序，`ask=True` 弹出系统的选择框。"""
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    api = editor_api()
    if api is not None:
        try:
            return api.open_external(target, ask=ask)
        except Exception as exc:
            logger.exception("编辑器插件调用系统程序失败：{}", target)
            return False, f"打开失败：{exc}"
    from ..sdk import ui

    if ask and ui.ask_open_with(target):
        return True, "请在弹出的对话框里选择程序"
    if ui.open_default(target):
        return True, "已交给系统默认程序打开"
    return False, "系统无法打开该文件"


# --------------------------------------------------------------- 规则读写
def rules_available() -> bool:
    """编辑器插件是否提供了规则（没有时配置界面隐藏相关操作）。"""
    return editor_api() is not None


def current_editor_id(suffix: str) -> str:
    api = editor_api()
    return str(api.current_editor_id(suffix)) if api is not None else ""


def set_editor(suffix: str, editor_id: str) -> bool:
    api = editor_api()
    return bool(api.set_editor(suffix, editor_id)) if api is not None else False


def reset_editor(editor_id: str, suffixes=None) -> int:
    api = editor_api()
    if api is None:
        return 0
    return int(api.reset_editor(editor_id, suffixes))


def use_editor_for_all(editor_id: str, suffixes=None) -> int:
    api = editor_api()
    if api is None:
        return 0
    return int(api.use_editor_for_all(editor_id, suffixes))


def editor_ids_for(plugin_id: str) -> tuple[str, ...]:
    api = editor_api()
    if api is None:
        return ()
    return tuple(api.editor_ids_for(plugin_id))


__all__ = [
    "EDITOR_EXTENSION",
    "EditorFactory",
    "EditorInfo",
    "EditorOpener",
    "current_editor_id",
    "edit_path",
    "edit_with",
    "editor_api",
    "editor_ids_for",
    "editors_for",
    "open_system",
    "reset_editor",
    "rules_available",
    "set_editor",
    "use_editor_for_all",
]
