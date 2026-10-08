"""core 通用下载器：任务队列、断点续传、镜像规则与运行期持久化。

这一层只认「配置协议 + 目录提供者」，不认任何具体插件（模型工具库等）的设置对象与
目录布局，所以程序本体与其他插件都能直接用。

分层与延迟导入：

* `engine` 是纯标准库实现的下载引擎，可以随包一起导入；
* `mirrors` 是纯标准库实现的镜像规则（官方网址 + 多个镜像网址 + 启用模式）；
* `mirror_store` 负责把规则存进清单 `core.download_mirrors`，能独立导入；
* **`settings` 会拉起 QConfig（进而拉起 Qt），所以这里绝不预导入它** ——
  需要「配置 ↔ 引擎」的桥时，各自 `from app.core.download import settings` 即可。
"""

from __future__ import annotations

from .engine import (
    DEFAULT_CONCURRENT,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    DOWNLOAD_KIND,
    FINAL_STATES,
    MAX_CONCURRENT,
    PAUSE_POLICY,
    PAUSE_USER,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_ERROR,
    STATE_LABELS,
    STATE_PAUSED,
    STATE_QUEUED,
    STATE_RUNNING,
    USER_AGENT,
    DownloadError,
    DownloadJob,
    DownloadManager,
    DownloadOptions,
    build_opener,
    describe_error,
    part_path,
    to_int,
)
from .mirrors import (
    DEFAULT_MANIFEST_ID,
    DEFAULT_RULE_ID,
    MODE_LABELS,
    MODE_MIRROR,
    MODE_SEQUENTIAL,
    MODES,
    MirrorRule,
    MirrorRules,
    URL_PLACEHOLDER,
    default_rules,
    host_of,
)

__all__ = [
    "DEFAULT_CONCURRENT",
    "DEFAULT_MANIFEST_ID",
    "DEFAULT_RETRY",
    "DEFAULT_RULE_ID",
    "DEFAULT_TIMEOUT",
    "DOWNLOAD_KIND",
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "DownloadOptions",
    "FINAL_STATES",
    "MAX_CONCURRENT",
    "MODE_LABELS",
    "MODE_MIRROR",
    "MODE_SEQUENTIAL",
    "MODES",
    "MirrorRule",
    "MirrorRules",
    "PAUSE_POLICY",
    "PAUSE_USER",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_LABELS",
    "STATE_PAUSED",
    "STATE_QUEUED",
    "STATE_RUNNING",
    "URL_PLACEHOLDER",
    "USER_AGENT",
    "build_opener",
    "default_rules",
    "describe_error",
    "host_of",
    "part_path",
    "to_int",
]
