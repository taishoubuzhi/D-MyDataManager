"""用系统程序打开文件、定位文件、按自定义程序打开：跨平台小封装。

只依赖标准库，方便单元测试直接断言拼出来的命令行。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from loguru import logger

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"


def split_args(text: str) -> list[str]:
    """把参数模板拆成参数列表；路径含空格时请写成 `"{path}"`。"""
    text = str(text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError as exc:  # 引号不配对等
        logger.warning("参数解析失败，按空格切分：{}（{}）", text, exc)
        return text.split()


def build_command(program: str, args: str, path: Path) -> list[str]:
    """把「程序 + 参数模板 + 文件路径」拼成命令行；模板里没有 `{path}` 时补在末尾。"""
    argv = [str(program)]
    replaced = False
    for part in split_args(args):
        if "{path}" in part:
            argv.append(part.replace("{path}", str(path)))
            replaced = True
        else:
            argv.append(part)
    if not replaced:
        argv.append(str(path))
    return argv


def open_default(path: Path) -> bool:
    """用系统默认关联程序打开文件。"""
    target = Path(path)
    if not target.exists():
        logger.warning("打开失败，文件不存在：{}", target)
        return False
    try:
        if WINDOWS:
            os.startfile(str(target))  # noqa: S606
        elif MACOS:
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        logger.error("打开文件失败：{}（{}）", target, exc)
        return False
    return True


def ask_open_with(path: Path) -> bool:
    """弹出系统「打开方式」选择框（其他平台退化为默认打开）。"""
    target = Path(path)
    if not target.exists():
        logger.warning("打开失败，文件不存在：{}", target)
        return False
    if not WINDOWS:
        return open_default(target)
    try:
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(target)])
    except OSError as exc:
        logger.error("打开「打开方式」对话框失败：{}（{}）", target, exc)
        return False
    return True


def open_with_program(program: str, args: str, path: Path) -> bool:
    """用自定义程序打开文件。"""
    executable = str(program or "").strip()
    if not executable:
        return False
    target = Path(path)
    if not target.exists():
        logger.warning("打开失败，文件不存在：{}", target)
        return False
    argv = build_command(executable, args, target)
    try:
        subprocess.Popen(argv, cwd=str(target.parent))
    except OSError as exc:
        logger.error("自定义程序打开失败：{} → {}（{}）", argv, target, exc)
        return False
    return True


def reveal(path: Path) -> bool:
    """在系统文件管理器里定位文件（文件不存在时打开所在目录）。"""
    target = Path(path)
    folder = target.parent if target.parent.exists() else Path.cwd()
    try:
        if WINDOWS:
            if target.exists() and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(folder)])
        elif MACOS:
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError as exc:
        logger.error("定位文件失败：{}（{}）", target, exc)
        return False
    return True
