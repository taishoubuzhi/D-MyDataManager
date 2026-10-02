"""应用级操作：重启进程、打开路径。"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QProcess, QTimer


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
    """用系统默认程序打开路径，失败返回 False。"""
    target = str(path)
    try:
        os.startfile(target)  # noqa: S606
    except Exception:  # noqa: BLE001
        return False
    return True


__all__ = ["open_path", "restart_application"]
