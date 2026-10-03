"""查看器扩展接口：程序本体只留一个扩展点与调度门面。

程序自己不实现任何查看行为，也不再保存查看器注册表：

1. 系统默认打开方式由 `open_system()` / `open_path()` 的退路直接调系统接口完成；
2. 「谁声明了哪些格式、规则怎么定、窗口长什么样」全部由查看器插件库
   `builtin.lib.viewer` 实现，并通过扩展接口 `viewer.open` 暴露成 `ViewerOpenApi`；
3. 这里的函数都是门面：有插件就转调插件，没有插件就退化为系统默认程序。

插件注册查看器仍然用 `ctx.add_viewer(...)`，由插件服务转交给 `viewer.open` 的实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from loguru import logger

#: 创建视图控件：`(path, parent) -> QWidget`，由插件自己延迟 import Qt
ViewerFactory = Callable[[Path, object], object]

#: 插件自带的打开函数：`(path, parent) -> (是否成功, 说明)`
ViewerOpener = Callable[..., "tuple[bool, str]"]


class ViewerInfo(Protocol):
    """查看器记录：程序本体只认这些字段，真正的记录类由查看器插件定义。"""

    id: str
    name: str
    extensions: tuple[str, ...]
    kind: str
    plugin_id: str
    host: str
    description: str
    capabilities: tuple[str, ...]

    def matches(self, suffix: str) -> bool: ...


#: 查看器调度接口（查看器插件库实现）：`open_path` / `open_viewer` / 规则读写 / 注册表
OPEN_EXTENSION = "viewer.open"


# --------------------------------------------------------------------- 调度
def open_api() -> object | None:
    """查看器插件提供的调度接口（`viewer.open`）；没启用查看器插件时为 None。"""
    from ..core.extensions import extension_registry

    return extension_registry.provider(OPEN_EXTENSION)


def _missing(path: Path | str) -> str:
    return f"文件不存在：{Path(path).name}"


def _fallback(path: Path | str) -> tuple[bool, str]:
    """没有查看器插件时的退路：交给系统默认程序。"""
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    from . import ui

    if ui.open_default(target):
        return True, "已交给系统默认程序打开"
    return False, "系统无法打开该文件"


def viewers_for(suffix: str | Path) -> tuple[ViewerInfo, ...]:
    """该扩展名可用的全部查看器（配置界面与右键菜单用）；没有插件时为空。"""
    api = open_api()
    if api is None:
        return ()
    try:
        return tuple(api.viewers_for(suffix))
    except Exception:
        logger.exception("读取查看器列表失败：{}", suffix)
        return ()


def open_path(path: Path | str, parent=None) -> tuple[bool, str]:
    """打开文件，返回 (是否成功, 说明)：由查看器插件决定内置查看还是系统程序。"""
    api = open_api()
    if api is None:
        return _fallback(path)
    try:
        return api.open_path(path, parent)
    except Exception as exc:
        logger.exception("查看器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def open_viewer_with(path: Path | str, viewer, parent=None) -> tuple[bool, str]:
    """点名用某个内置查看器打开。"""
    api = open_api()
    if api is None:
        return _fallback(path)
    try:
        return api.open_viewer(path, viewer, parent)
    except Exception as exc:
        logger.exception("查看器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def open_system(path: Path | str, ask: bool = False) -> tuple[bool, str]:
    """绕过内置查看器：`ask=False` 用系统默认程序，`ask=True` 弹出系统的选择框。"""
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    api = open_api()
    if api is not None:
        try:
            return api.open_external(target, ask=ask)
        except Exception as exc:
            logger.exception("查看器插件调用系统程序失败：{}", target)
            return False, f"打开失败：{exc}"
    from . import ui

    if ask and ui.ask_open_with(target):
        return True, "请在弹出的对话框里选择程序"
    if ui.open_default(target):
        return True, "已交给系统默认程序打开"
    return False, "系统无法打开该文件"


# --------------------------------------------------------------- 规则读写
def rules_available() -> bool:
    """查看器插件是否提供了规则（没有时配置界面隐藏相关操作）。"""
    return open_api() is not None


def current_viewer_id(suffix: str) -> str:
    api = open_api()
    return str(api.current_viewer_id(suffix)) if api is not None else ""


def set_viewer(suffix: str, viewer_id: str) -> bool:
    api = open_api()
    return bool(api.set_viewer(suffix, viewer_id)) if api is not None else False


def reset_viewer(viewer_id: str, suffixes=None) -> int:
    api = open_api()
    if api is None:
        return 0
    return int(api.reset_viewer(viewer_id, suffixes))


def use_viewer_for_all(viewer_id: str, suffixes=None) -> int:
    api = open_api()
    if api is None:
        return 0
    return int(api.use_viewer_for_all(viewer_id, suffixes))


def viewer_ids_for(plugin_id: str) -> tuple[str, ...]:
    api = open_api()
    if api is None:
        return ()
    return tuple(api.viewer_ids_for(plugin_id))


__all__ = [
    "OPEN_EXTENSION",
    "ViewerInfo",
    "ViewerFactory",
    "ViewerOpener",
    "current_viewer_id",
    "open_api",
    "open_path",
    "open_system",
    "open_viewer_with",
    "reset_viewer",
    "rules_available",
    "set_viewer",
    "use_viewer_for_all",
    "viewer_ids_for",
    "viewers_for",
]
