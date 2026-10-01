"""按「打开方式」规则打开文件的公共流程。

数据管理页双击、右键「打开」、右键「打开方式」以及打开方式页的「测试打开」都走这里，
四种出口（内置查看 / 继承系统默认 / 自定义程序 / 交给系统选择）只保留一份逻辑。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QWidget

from ...core import shell
from ...core.viewers import Viewer, normalize_suffix
from ...services.open_with_service import (
    MODE_ASK,
    MODE_INHERIT,
    OpenDecision,
    open_with_service,
)
from .window import open_viewer


def open_path(path: Path | str, parent: QWidget | None = None) -> tuple[bool, str]:
    """打开文件，返回 (是否成功, 说明)；内置查看器不可用时退化为系统默认程序。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    decision = open_with_service.resolve(target)
    if decision.is_builtin and decision.viewer is not None:
        ok, message = open_viewer(decision.viewer, target, parent)
        if ok:
            return True, message
        logger.warning("内置查看器打开失败，改交系统默认程序：{}", target)
        if shell.open_default(target):
            return True, "已交给系统默认程序打开"
        return False, message
    if decision.needs_ask:
        if shell.ask_open_with(target):
            return True, "请在弹出的「打开方式」对话框中选择程序"
        return False, "已取消打开"
    ok, message = open_with_service.open_external(decision, target)
    return ok, message or decision.describe()


def open_viewer_with(path: Path | str, viewer: Viewer, parent: QWidget | None = None) -> tuple[bool, str]:
    """用指定的内置查看器打开（右键「打开方式」里点名某个插件时用）。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    return open_viewer(viewer, target, parent)


def open_system(path: Path | str, mode: str = MODE_INHERIT) -> tuple[bool, str]:
    """绕过内置查看器按系统方式打开：`inherit` 用系统默认程序，`ask` 交给系统选择。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    decision = OpenDecision(mode if mode in (MODE_INHERIT, MODE_ASK) else MODE_INHERIT, normalize_suffix(target))
    ok, message = open_with_service.open_external(decision, target)
    return ok, message or decision.describe()


__all__ = ["open_path", "open_system", "open_viewer_with"]
