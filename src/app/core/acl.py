"""资源文件夹的 ACL 锁定：对 Everyone 关闭继承并拒绝读/遍历。

这里只提供两个动作：锁定一个目录、放行一个目录。Windows 之外的平台一律视为不支持，
由调用方（`app.services.privacy_service`）降级为「不做保护」。

注意：ACL 无法区分「同一个用户的不同进程」，也就是说程序本身同样会被这条规则挡住，
所以锁定只适合在程序不运行的时候生效——运行期的放行策略见 `privacy_service` 的模块说明。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from loguru import logger

#: Everyone 的众所周知 SID：用 SID 而不是名称，避免受系统语言影响
EVERYONE = "*S-1-1-0"
#: 拒绝「遍历/执行」与「读取」，并让子目录与文件继承这条 ACE
DENY_PERMS = "(OI)(CI)(RX)"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_supported() -> bool:
    """当前平台是否支持 ACL 锁定（只有 Windows 带 icacls）。"""
    return os.name == "nt"


def _missing(target: Path) -> bool:
    """路径是否真的不存在。

    被 ACL 拒绝的目录 `os.stat` 也会失败，但那是「存在但进不去」，不能当成不存在，
    否则永远解不开自己锁上的目录。
    """
    try:
        os.stat(target)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _icacls(path: str | Path, *args: str) -> tuple[bool, str]:
    """调用一次 icacls；返回（是否成功，失败原因）。"""
    target = Path(path)
    if _missing(target):
        # 目录还没建出来（例如首次启动）：没有可锁的东西，也不算失败
        return True, "路径不存在，跳过"
    try:
        result = subprocess.run(
            ["icacls", str(target), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:  # icacls 不存在等
        logger.warning("执行 icacls 失败：{}：{}", target, exc)
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    lines = [line.strip() for line in f"{result.stdout}\n{result.stderr}".splitlines() if line.strip()]
    reason = lines[-1] if lines else f"icacls 退出码 {result.returncode}"
    logger.warning("icacls 处理 {} 失败：{}", target, reason)
    return False, reason


def lock(path: str | Path) -> tuple[bool, str]:
    """锁定目录：关闭继承并拒绝 Everyone 读取/遍历。"""
    return _icacls(path, "/inheritance:r", "/deny", f"{EVERYONE}:{DENY_PERMS}")


def unlock(path: str | Path) -> tuple[bool, str]:
    """放行目录：移除 Everyone 的拒绝项并恢复继承。幂等，可重复调用。"""
    ok, message = _icacls(path, "/remove:d", EVERYONE)
    if not ok:
        return ok, message
    return _icacls(path, "/inheritance:e")
