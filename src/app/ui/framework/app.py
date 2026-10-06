"""应用级操作：重启进程、打开路径。"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QProcess, QTimer

from ...core.runtime.shell import open_default


def restart_application(delay_ms: int = 400) -> None:
    """稍后重启当前程序（先让界面把提示画出来）。"""
    QTimer.singleShot(delay_ms, _start_detached)


def _start_detached() -> None:
    arguments = [str(argument) for argument in sys.argv[1:]]
    if getattr(sys, "frozen", False):
        program, arguments = sys.executable, [str(argument) for argument in sys.argv[1:]]
    else:
        program, arguments = sys.executable, [str(sys.argv[0]), *arguments]
    QProcess.startDetached(program, arguments)


def open_path(path) -> bool:
    """用系统默认程序打开路径（文件或文件夹），失败返回 False。

    与「数据管理」里的打开走同一条路（`app.core.runtime.shell.open_default`）：
    系统拒绝 `os.startfile()` 时会自动改由资源管理器代开。
    """
    return open_default(path)


__all__ = ["open_path", "restart_application"]
