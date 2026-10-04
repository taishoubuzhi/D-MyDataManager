"""模型下载器：地址解析、断点续传、镜像回退、sha256 校验与原子落盘。

对外只暴露 `__all__` 里的名字；实现拆在 `downloader.py`（任务与队列）和
`hub.py`（HF 仓库探测与地址展开）里。
"""

from __future__ import annotations

from .downloader import DownloadError, DownloadJob, DownloadManager
from .hub import guess_total_size, hub_file_list, resolve_urls

__all__ = [
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "guess_total_size",
    "hub_file_list",
    "resolve_urls",
]
