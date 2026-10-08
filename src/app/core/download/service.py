"""程序本体共享的下载队列。

全程序只有一个 `DownloadManager`：下载管理页与插件（经 SDK 的 `download` 接口）拿到的是
同一个队列，用户在页面上暂停的任务不会在插件那边又跑起来。

队列索引是一份临时清单（`app.core.journals`，`kind="download"`，落
`.configs/journals/download/`）：进程异常退出后索引还在，下次启动 `restore()` 把没做完的
任务接回来，`.part` 里的实际字节数就是续传起点。所以「暂停 / 断点续传」不依赖界面还在，
重启之后照样接着下。

配置（并行数、顺序下载、代理、超时、重试、镜像规则）由 `app.core.download.settings`
从 QConfig 与清单里现取，`configure()` 用来把改完的设置热灌进正在跑的队列。
"""

from __future__ import annotations

import logging
import threading

from ..journals import JournalStore
from .engine import DOWNLOAD_KIND, DownloadManager, DownloadOptions

_logger = logging.getLogger(__name__)

_lock = threading.RLock()
_manager: DownloadManager | None = None


def manager() -> DownloadManager:
    """共享的下载队列（懒建）。

    第一次建的时候顺带 `restore()`：把上次没做完的任务、连同 `.part` 里的断点接回来，
    并按当前并行数自动接着下。
    """
    global _manager
    with _lock:
        if _manager is None:
            from .settings import options_from_config

            store = JournalStore(DOWNLOAD_KIND)
            _manager = DownloadManager(options_from_config(), index=store, name="download")
            try:
                restored = _manager.restore()
            except Exception:
                _logger.exception("恢复未完成的下载任务失败")
            else:
                if restored:
                    _logger.info("已接回 {} 个未完成的下载任务", restored)
        return _manager


def current_manager() -> DownloadManager | None:
    """已经建好的队列；没建过就返回 None。

    界面刷新用这个而不是 `manager()`：只是打开页面看一眼，不该凭空把队列（以及它的工作
    线程、索引文件）建起来，更不该顺手触发一次「接回未完成任务」。
    """
    with _lock:
        return _manager


def options() -> DownloadOptions:
    """当前队列正在用的设置（队列还没建起来时按配置现算一份）。"""
    with _lock:
        if _manager is not None:
            return _manager.options
    from .settings import options_from_config

    return options_from_config()


def configure() -> DownloadOptions:
    """把配置里的下载设置重新灌进正在跑的队列（立即生效），返回生效后的设置。

    队列还没建起来时什么都不做、只按配置现算一份返回：建的时候自然会用新配置，
    顺手也避免了「改一次设置就凭空起一批工作线程、再触发一次接回未完成任务」。
    """
    from .settings import apply_options, options_from_config

    with _lock:
        if _manager is None:
            return options_from_config()
        return apply_options(_manager)


def shutdown(wait: float = 3.0) -> None:
    """退出时收起队列：停下来的任务留在索引里，下次启动继续。"""
    global _manager
    with _lock:
        current = _manager
        _manager = None
    if current is None:
        return
    try:
        current.shutdown(wait=wait)
    except Exception:
        _logger.exception("收起下载队列失败")


def is_running() -> bool:
    """队列是否已经建起来（没建起来就不必为退出流程做任何事）。"""
    with _lock:
        return _manager is not None


__all__ = [
    "configure",
    "current_manager",
    "is_running",
    "manager",
    "options",
    "shutdown",
]
