"""配置 ↔ 下载引擎之间的桥。

引擎（`app.core.download.engine`）只认 `DownloadOptions` 这一个「配置协议」，不认
QConfig；这里负责把 QConfig 的 `Download` 组和镜像规则清单拼成 `DownloadOptions`，
再塞回引擎。规则如下：

* 标量（下载路径 / 并行数 / 顺序下载 / 超时 / 重试 / 代理）来自 `app.core.config`；
* 镜像规则来自清单 `core.download_mirrors`（见 `app.core.download.mirror_store`）。

热切换：`apply_options()` 直接把新设置交给 `DownloadManager.update()`，引擎自己按新的
并行数重新分配额度（见 `DownloadManager._rebalance_locked`），不需要重启程序。
"""

from __future__ import annotations

from pathlib import Path

from ..config import config, download_dir
from .engine import (
    DEFAULT_CONCURRENT,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    MAX_CONCURRENT,
    DownloadManager,
    DownloadOptions,
)
from .mirror_store import load_rules


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def options_from_config() -> DownloadOptions:
    """按当前配置拼一份引擎设置。"""
    return DownloadOptions(
        proxy=str(config.downloadProxy.value or "").strip(),
        concurrent=_clamp(int(config.downloadConcurrent.value or DEFAULT_CONCURRENT), 1, MAX_CONCURRENT),
        sequential=bool(config.downloadSequential.value),
        timeout=float(config.downloadTimeout.value or DEFAULT_TIMEOUT),
        retries=_clamp(int(config.downloadRetries.value), 0, 5),
        mirrors=load_rules(),
    )


def apply_options(manager: DownloadManager) -> DownloadOptions:
    """把当前配置灌进引擎（立即生效），返回生效后的设置。"""
    options = options_from_config()
    manager.update(options)
    return options


def default_target(name: str) -> Path:
    """某个下载名的默认落点：下载设置里的下载路径 + 文件名。"""
    return download_dir() / Path(str(name)).name


__all__ = [
    "apply_options",
    "default_target",
    "options_from_config",
]
