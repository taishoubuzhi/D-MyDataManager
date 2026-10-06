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


def is_elevated() -> bool:
    """当前进程是否以管理员身份运行（提权时 Store 应用关联激活会被系统拒绝）。"""
    if not WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 查不到就当没提权
        logger.exception("检测提权状态失败")
        return False


def session_id() -> int:
    """当前进程所在的 Windows 会话号：交互桌面一般是 1，0 表示服务 / 非交互会话。"""
    if not WINDOWS:
        return 0
    try:
        import ctypes

        value = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(
            ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(value)
        )
        return int(value.value) if ok else 0
    except Exception:  # noqa: BLE001 - 查不到就不写
        logger.exception("检测会话号失败")
        return 0


def _process_hint() -> str:
    """排查用：提权与所在会话。提权时 Store 应用（AppX）关联打不开，非交互会话里
    连「交给资源管理器代开」也不会弹窗，日志里带上这两项才能一眼定位。"""
    return f"{'已提权' if is_elevated() else '未提权'}、会话 {session_id()}"


def open_default(path: Path) -> bool:
    """用系统默认关联程序打开文件（或文件夹）。

    只有确实交出去了才算成功：`os.startfile()` 成功，或退给资源管理器代开时它按约定退 1
    （`explorer.exe <路径>` 把请求交给已经在跑的 shell 之后固定退 1）。系统拒绝时返回 False，
    调用方据此退到程序内查看器之类的可用退路，而不是让用户点了没反应（用户 m08240）。
    """
    target = Path(path)
    if not target.exists():
        logger.warning("打开失败，文件不存在：{}", target)
        return False
    try:
        if WINDOWS:
            return _open_on_windows(target)
        if MACOS:
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        logger.error("打开文件失败：{}（{}）", target, exc)
        return False
    return True


def _open_on_windows(target: Path) -> bool:
    """Windows：先 `os.startfile()`，被系统拒绝时改让资源管理器代开。

    `os.startfile()` 走的是本进程里的 `ShellExecuteEx`：本机（Windows 11 + Store 版记事本
    这类 AppX/MSIX 关联）实测对**任何**路径都返回 `[WinError 5] 拒绝访问`。
    """
    try:
        os.startfile(str(target))  # noqa: S606
        return True
    except OSError as exc:
        logger.warning(
            "系统默认打开被拒绝，改由资源管理器代开：{}（{}；本进程{}）",
            target,
            exc,
            _process_hint(),
        )
    return _explorer_open(target)


#: `explorer.exe <路径>` 把请求交给已经在跑的 shell 之后固定退 1；别的退出码都算没接手。
EXPLORER_DELIVERED = 1

#: 等资源管理器退出的上限（秒）：起不来时几十毫秒就死，正常交出去也是立刻退。
EXPLORER_WAIT_SECONDS = 1.0


def _explorer_open(target: Path) -> bool:
    """让资源管理器代开，并按它的退出码确认有没有真的接手。

    本机实测：代开进程起来就死在 `0xC0000142`（DLL 初始化失败），或者干脆一直不退
    （自己另起了一个 shell 实例）——两种情况都说明请求没交出去，这里如实返回 False。
    """
    try:
        process = subprocess.Popen(["explorer", str(target)])
    except OSError as exc:
        logger.error("资源管理器代开失败：{}（{}）", target, exc)
        return False
    try:
        code = process.wait(timeout=EXPLORER_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("资源管理器没有接手（还在跑，像是另起了 shell 实例）：{}", target)
        return False
    if code == EXPLORER_DELIVERED:
        return True
    logger.warning("资源管理器代开没成功：{}（退出码 {}）", target, code)
    return False


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
