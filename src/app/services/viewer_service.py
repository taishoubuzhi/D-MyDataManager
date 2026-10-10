"""查看器调度（程序侧）：走扩展接口 `viewer.open`，没有插件时退化为系统默认程序。

协议 v2 之后 `app.sdk` 不再放查看器专属接口（TODO 1.3.2）：调度接口由查看器工具库
`builtin.lib.viewer` 通过扩展点 `viewer.open` 提供，插件要用就 `ctx.require("viewer.open")`
或从 `dm_plugin.builtin.lib.viewer.plugin` 导入基类；**程序自己**要用的那一份调度与退路
放在这里（插件不能 import `app.services`，所以两个方向互不越界）。

- 有查看器插件：转调它的 `viewer.open` 实现；
- 没有插件：`_fallback()` / `open_system()` 直接交给系统默认程序。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Callable, Protocol

from loguru import logger

#: 查看器扩展点（查看器工具库 `builtin.lib.viewer` 在清单里声明并提供）
VIEWER_EXTENSION = "viewer.open"

#: 创建视图控件：`(path, parent) -> QWidget`，由插件自己延迟 import Qt
ViewerFactory = Callable[[Path, object], object]

#: 插件自带的打开函数：`(path, parent, *, sources=()) -> (是否成功, 说明)`
#: `sources` 是调用方「当前看到的文件顺序」，查看器用它实现上一张 / 下一张；
#: 老实现不认这个关键字，所以只在非空时传（见 `_call_opener`）。
ViewerOpener = Callable[..., "tuple[bool, str]"]


class ViewerInfo(Protocol):
    """查看器记录：程序只认这些字段，真正的记录类由查看器插件定义。"""

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
def open_api() -> object | None:
    """查看器插件提供的调度接口（`viewer.open`）；没启用查看器插件时为 None。"""
    from ..core.plugins.extensions import extension_registry

    return extension_registry.provider(VIEWER_EXTENSION)


def _missing(path: Path | str) -> str:
    return f"文件不存在：{Path(path).name}"


def _fallback(path: Path | str) -> tuple[bool, str]:
    """没有查看器插件时的退路：交给系统默认程序。"""
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    from ..sdk import ui

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


def open_path(
    path: Path | str, parent=None, *, sources: Iterable[Path | str] = ()
) -> tuple[bool, str]:
    """打开文件，返回 (是否成功, 说明)：由查看器插件决定内置查看还是系统程序。

    `sources` 是调用方「当前列表里的文件顺序」（数据管理页正在显示的那一页），
    查看器的上一张 / 下一张按它走；不传时查看器自己决定（一般是同目录）。
    """
    api = open_api()
    if api is None:
        return _fallback(path)
    try:
        return _call_opener(api.open_path, path, parent, sources=sources)
    except Exception as exc:
        logger.exception("查看器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def open_viewer_with(
    path: Path | str, viewer, parent=None, *, sources: Iterable[Path | str] = ()
) -> tuple[bool, str]:
    """点名用某个内置查看器打开（`sources` 同上）。"""
    api = open_api()
    if api is None:
        return _fallback(path)
    try:
        return _call_opener(api.open_viewer, path, viewer, parent, sources=sources)
    except Exception as exc:
        logger.exception("查看器插件打开文件失败：{}", path)
        return False, f"打开失败：{exc}"


def _call_opener(call, *args, sources: Iterable[Path | str] = ()) -> tuple[bool, str]:
    """调用查看器插件的打开接口，顺带把「当前列表顺序」交过去。

    `sources` 为空时不传：老版本查看器插件没有这个关键字，传了反而报错。
    插件内部若因此抛 `TypeError`，这里退回老的两参数调用（再抛就交给上层兜底）。
    """
    items = tuple(sources)
    if not items:
        return call(*args)
    try:
        return call(*args, sources=items)
    except TypeError:
        logger.debug("查看器接口不认 sources 参数，按老签名重试")
        return call(*args)


def open_system(path: Path | str, ask: bool = False) -> tuple[bool, str]:
    """绕过内置查看器：`ask=False` 用系统默认程序，`ask=True` 弹出系统的选择框。

    系统把默认程序拒了（本机实测 `os.startfile()` 对任何路径都 `[WinError 5]`，Store 版记事本
    这类 AppX 关联连资源管理器代开都起不来）时不硬撑：有能打开这个格式的内置查看器就直接用它
    打开，并在说明里写清原因，别让用户点了没反应（用户 m08240）。
    """
    target = Path(path)
    if not target.exists():
        return False, _missing(target)
    ok, message = _system_once(target, ask)
    if ok:
        return True, message
    return _viewer_backup(target, message)


def _system_once(target: Path, ask: bool) -> tuple[bool, str]:
    """真正走「系统程序」那一步：有查看器插件就交给它，没有就程序自己调。"""
    api = open_api()
    if api is not None:
        try:
            return api.open_external(target, ask=ask)
        except Exception as exc:
            logger.exception("查看器插件调用系统程序失败：{}", target)
            return False, f"打开失败：{exc}"
    from ..sdk import ui

    if ask and ui.ask_open_with(target):
        return True, "请在弹出的对话框里选择程序"
    if ui.open_default(target):
        return True, "已交给系统默认程序打开"
    return False, "系统无法打开该文件"


def _viewer_backup(target: Path, reason: str) -> tuple[bool, str]:
    """系统打不开时的退路：用能打开这个格式的内置查看器打开（没有就用系统那句话）。"""
    views = viewers_for(target.suffix)
    if not views:
        return False, reason
    viewer = views[0]
    ok, message = open_viewer_with(target, viewer)
    if not ok:
        return False, message or reason
    return True, f"系统默认程序打不开，已改用内置查看器「{viewer.name}」"


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
    "VIEWER_EXTENSION",
    "ViewerFactory",
    "ViewerInfo",
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
