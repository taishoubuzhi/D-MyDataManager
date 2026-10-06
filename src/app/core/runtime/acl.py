"""资源文件夹的 ACL 锁定：用一条 Everyone 拒绝项挡住读取与遍历。

这里只提供两个动作：锁定一个对象（目录/文件）、放行一个对象，外加浅层锁整棵树的
`lock_tree()` / `unlock_tree()`。Windows 之外的平台一律视为不支持，由调用方
（`app.services.privacy_service`）降级为「不做保护」。

注意：ACL 无法区分「同一个用户的不同进程」，也就是说程序本身同样会被这条规则挡住，
所以锁定只适合在程序不运行的时候生效——运行期的放行策略见 `privacy_service` 的模块说明。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

#: Everyone 的众所周知 SID：用 SID 而不是名称，避免受系统语言影响
EVERYONE = "*S-1-1-0"
#: 深拒绝（目录，带继承标志）：这条 ACE 被子目录与文件继承，整棵子树都吃上它
DENY_PERMS = "(OI)(CI)(RX)"
#: 浅拒绝：不带继承标志，只挡对象自身（给资源根用，见 `lock_tree`）
SELF_PERMS = "(RX)"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
#: 单次 icacls 的等待上限（秒）：改 ACE 可能触发全子树传播，超时就放弃而不是挂住启动
_ICACLS_TIMEOUT = 60.0


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
    """调用一次 icacls；返回（是否成功，失败原因）。

    带超时：改一条 ACE 会让 Windows 把该对象的可继承项重新传播到整棵子树（`models/`
    十几万个文件），万一撞上就宁可放弃这次操作，也不能把调用方——启动路径上的
    `paths.release_locked_root()` ——无声无息地挂住。
    """
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
            timeout=_ICACLS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        reason = f"icacls 超时（{_ICACLS_TIMEOUT:.0f} 秒）"
        logger.warning("icacls 处理 {} 超时（{} 秒），放弃本次操作", target, _ICACLS_TIMEOUT)
        return False, reason
    except OSError as exc:  # icacls 不存在等
        logger.warning("执行 icacls 失败：{}：{}", target, exc)
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    lines = [line.strip() for line in f"{result.stdout}\n{result.stderr}".splitlines() if line.strip()]
    reason = lines[-1] if lines else f"icacls 退出码 {result.returncode}"
    logger.warning("icacls 处理 {} 失败：{}", target, reason)
    return False, reason


def _inherits(target: Path) -> bool:
    """目录当前是否已经在继承父级权限（`icacls` 列表里有 `(I)` 项）。

    已经在继承时 `/inheritance:e` 没有任何语义作用，却会让 Windows 把 ACE 重新传播到
    整棵子树：`.resources` 下有运行环境 venv，动辄十几万个文件，一次传播能卡住启动好几分钟。
    所以先问一次再决定要不要执行；读不到（目录不存在、icacls 报错）时按「不用做」处理。
    """
    if _missing(target):
        return True
    try:
        result = subprocess.run(
            ["icacls", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=_ICACLS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("读取 ACL 失败：{}：{}", target, exc)
        return False
    if result.returncode != 0:
        return False
    return "(I)" in f"{result.stdout}\n{result.stderr}"


def lock(path: str | Path, *, deep: bool = True) -> tuple[bool, str]:
    """给对象挂一条 Everyone 拒绝项（读 / 遍历）。

    `deep=True`（目录）时带 `(OI)(CI)`：拒绝项被子项继承，整棵子树都读不了；
    `deep=False` 只挡对象自身——给资源根用：根本身进不去、枚举不出来就够了，子项各自
    承担。挂一条**带继承**的拒绝项会让 Windows 把它物化到整棵子树（`.resources` 里光
    运行环境 venv 就有十几万个文件），那一步能拖住退出好几分钟，所以要躲开。

    这里刻意不像早先那样用 `/inheritance:r` 切断继承：显式拒绝项的优先级本来就在继承来
    的允许项之上，而放行只要 `/remove:d`，不必再触发一次继承传播。
    """
    target = Path(path)
    perms = DENY_PERMS if deep and target.is_dir() else SELF_PERMS
    return _icacls(target, "/deny", f"{EVERYONE}:{perms}")


def unlock(path: str | Path) -> tuple[bool, str]:
    """放行对象：移除 Everyone 的拒绝项，必要时恢复继承。幂等，可重复调用。

    `/inheritance:e` 只用来收拾早先用 `/inheritance:r` 留下的状态；它会让 Windows 把
    ACE 重新传播到整棵子树，所以先查一次（见 `_inherits`），已经在继承就跳过。
    """
    target = Path(path)
    ok, message = remove_deny(target)
    if not ok:
        return ok, message
    if _inherits(target):
        return True, ""
    return _icacls(target, "/inheritance:e")


def lock_tree(root: str | Path, *, skip: Iterable[str] = ()) -> tuple[int, str]:
    """浅层锁定一棵树：先深拒根下的每个直接子项，最后给根挂浅拒绝（`skip` 里的名字跳过）。

    子项的数量远小于整棵子树（`.resources` 去掉 `models/` 只剩几百个对象），所以 Windows
    的 ACE 传播范围很小，锁定几乎是瞬时的。

    顺序不能反过来：拒绝项连「遍历」权限一起挡，根一旦挂上它，**连程序自己都列不出根下的
    子项**（`_children` 直接 `WinError 5`），根下的目录就全漏在保护之外了。
    """
    target = Path(root)
    children = [child for child in _children(target) if child.name not in skip]
    done, message = 0, ""
    for child in children:
        ok, message = lock(child)
        done += 1 if ok else 0
    ok, message = lock(target, deep=False)
    done += 1 if ok else 0
    return done, message


def unlock_tree(root: str | Path, *, skip: Iterable[str] = ()) -> tuple[int, str]:
    """放行浅层锁过的树：先放行根本身（否则枚举不出子项），再摘掉各子项的拒绝项。

    子项只摘拒绝项、不碰继承：浅层锁从不切断继承，所以「恢复继承」既没必要，还会在子项
    上触发一次全子树传播——`models/` 被早先的做法 `/inheritance:r` 过，一旦恢复就是
    十几万个文件。

    `skip` 必须与 `lock_tree()` 用同一份名单：名单里的名字从来没被锁过，可它们的大小
    却可能是整棵树的全部（`models/` 十几万个对象，且早期留下的 `Everyone:(OI)(CI)(F)`
    允许项是可继承的），额外对它执行一次 icacls 会让 Windows 重写它的 DACL、顺带把 ACE
    重新传播进整棵子树，启动就这样卡住好几分钟。
    """
    target = Path(root)
    done, message = 0, ""
    ok, message = unlock(target)
    done += 1 if ok else 0
    for child in _children(target):
        if child.name in skip:
            continue
        ok, message = remove_deny(child)
        done += 1 if ok else 0
    return done, message


def remove_deny(target: str | Path) -> tuple[bool, str]:
    """只摘掉 Everyone 的拒绝项，不碰继承（浅层锁的子项用它放行）。

    对象本身没被拒绝过时是一次空操作；正因为不动继承，它不会像 `unlock()` 那样可能
    触发一次 `/inheritance:e` 的全子树 ACE 传播。
    """
    return _icacls(target, "/remove:d", EVERYONE)


def _children(root: Path) -> list[Path]:
    """目录的直接子项；读不到（不存在 / 正被锁着）时按空处理。"""
    try:
        return sorted(root.iterdir())
    except OSError as exc:
        logger.warning("枚举 {} 失败：{}", root, exc)
        return []
