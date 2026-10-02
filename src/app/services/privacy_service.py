"""资源文件夹与隐藏目录的隐私保护（静态保护模型）。

保护模型（方案 2）
------------------
ACL 拒绝项一旦生效，**连程序自己**也读不了被锁的目录，而 `data.db`、`-wal`、`-shm`、
封面、正文文件都在 `.resources` 里。所以保护不能采用「平时锁着、用的时候临时放行」的
做法——那条路上任何一处漏了放行窗口（新建数据库连接、WAL 共享内存、目录枚举……）
程序就会以 `unable to open database file` / `disk I/O error` 崩掉，异常退出后还会
把「锁定」留给下一次启动，直接起不来。

因此改为静态保护：

1. **运行期整场放行**：程序启动第一步就 `begin_session()` 放行资源文件夹，之后运行
   期间不再翻转 ACL。数据库连接、封面读取、查看器、导入导出都不会被自己的保护挡住，
   也就没有「放行窗口漏了哪一步」的问题。
2. **退出时锁定**：退出流程先关掉数据库引擎，再 `end_session()` 按设置锁定
   `.resources`（或各分类的 `.hiddens`）。于是「程序没有在跑」的时间窗里，
   文件夹对其它本地程序不可读、不可进入。
3. **异常退出可自愈**：崩溃、任务管理器强杀、断电留下的多半是「放行」态，下次启动照常；
   万一留下的是「锁定」态，`begin_session()` 的幂等放行也会先解开它。放行确实失败时，
   `main` 会关掉保护开关重试一次（宁可少一层保护，也不能让程序打不开），
   并留下 `--unlock` 应急命令与会话标记便于排查。

开关语义：开启开关只是记下设置（退出后生效），关闭开关会立刻放行。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from loguru import logger

from ..core import acl, paths
from ..core.config import config, library_root, resources_root
from ..core.paths import HIDDEN_DIR_NAME


class PrivacyService:
    """按设置锁定/放行资源文件夹；见模块说明。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hidden_cache: list[Path] | None = None
        self._session = False

    # ------------------------------------------------------------------ 状态
    def supported(self) -> bool:
        return acl.is_supported()

    def enabled(self) -> bool:
        """是否有任一保护开关处于开启状态。"""
        return bool(config.resourceProtected.value or config.hiddenProtected.value)

    def targets(self) -> list[Path]:
        """当前设置下需要加锁的目录。

        资源文件夹保护覆盖它内部的一切（含各分类的 `.hiddens`），所以两个开关同开时
        只按资源根处理，不再单独列举隐藏目录。
        """
        if config.resourceProtected.value:
            return [resources_root()]
        if config.hiddenProtected.value:
            return self.hidden_dirs()
        return []

    def hidden_dirs(self) -> list[Path]:
        """库里所有已存在的 `.hiddens` 目录（带缓存）。

        被锁住的 `.hiddens` 自身 `is_dir()` 会返回 False，但它一定出现在父目录的
        子目录名里，所以按名字判断；进入被锁目录会报错，交给 `onerror` 忽略。
        """
        with self._lock:
            if self._hidden_cache is not None:
                return list(self._hidden_cache)
        found: list[Path] = []
        root = library_root()
        try:
            for current, dirnames, _files in os.walk(root, onerror=lambda _exc: None):
                if HIDDEN_DIR_NAME in dirnames:
                    found.append(Path(current) / HIDDEN_DIR_NAME)
                dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        except OSError as exc:  # 库文件夹被锁或不存在：当作没有隐藏目录
            logger.warning("枚举隐藏目录失败：{}", exc)
            return []
        with self._lock:
            self._hidden_cache = list(found)
        return found

    def invalidate(self) -> None:
        """丢弃隐藏目录缓存（库结构变化后调用）。"""
        with self._lock:
            self._hidden_cache = None

    def state_text(self) -> str:
        """给界面/自检用的一句话状态描述。"""
        if not self.enabled():
            return "未开启保护"
        if not self.supported():
            return "当前系统不支持 ACL 保护，设置不会生效"
        scope = "资源文件夹" if config.resourceProtected.value else "隐藏目录"
        return f"{scope}保护已开启：程序运行期间保持可访问，退出后锁定"

    # ------------------------------------------------------------------ 锁定
    def lock(self) -> tuple[int, str]:
        """按当前设置锁定；没有开启保护时不做任何事。"""
        return self._apply(acl.lock, self.targets())

    def unlock(self) -> tuple[int, str]:
        """立刻放行（幂等，不看设置）。

        关闭保护开关时必须真的把 ACL 拆掉，所以放行用「所有可能被锁过的目录」，而不是
        当前设置对应的目录：用户关掉开关时设置已经是关的，按 `targets()` 走就会漏掉
        这次放行，目录要一直锁到下次开程序。
        """
        return self._apply(acl.unlock, self._all_targets())

    def _all_targets(self) -> list[Path]:
        """所有可能被锁过的目录：资源根、数据目录、库根 + 当前存在的隐藏目录。"""
        roots = (resources_root(), paths.DATA_DIR, paths.DEFAULT_RESOURCE_DIR)
        return list(dict.fromkeys(roots)) + self.hidden_dirs()

    def _apply(self, action, targets: list[Path]) -> tuple[int, str]:
        if not self.supported():
            return 0, "当前系统不支持 ACL 锁定"
        if not targets:
            return 0, "没有开启保护"
        done, message = 0, ""
        for target in targets:
            ok, message = action(target)
            done += 1 if ok else 0
        if action is acl.unlock:
            # 放行之后目录结构才可能重新枚举到（被锁的 .hiddens 之前是读不到的）
            self.invalidate()
        return done, message

    def force_unlock(self) -> tuple[int, str]:
        """无视开关强制放行资源根与隐藏目录（启动自愈、`--unlock` 应急用）。"""
        return self._apply(acl.unlock, self._all_targets())

    # ------------------------------------------------------------------ 会话
    def begin_session(self) -> tuple[int, str]:
        """程序启动时调用：幂等放行，保证上次留下的锁不会挡住本次启动。"""
        with self._lock:
            self._session = True
        return self.force_unlock()

    def end_session(self) -> tuple[int, str]:
        """程序退出时调用（已在数据库引擎关闭之后）：按设置锁定。"""
        with self._lock:
            self._session = False
        return self.lock()

    def in_session(self) -> bool:
        return self._session

    # ------------------------------------------------------------------ 语义标记
    @contextmanager
    def guard(self):
        """「这段代码会读写资源文件夹」的语义标记。

        静态保护下运行期整场放行，这里不再翻转 ACL；保留它是为了让这些访问点显式可见，
        将来若要改回运行期保护，只需要改这一个地方。
        """
        yield


def guarded(func):
    """把函数体标记为「会访问资源文件夹」（见 `PrivacyService.guard`）。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        with privacy.guard():
            return func(*args, **kwargs)

    return wrapper


privacy = PrivacyService()
