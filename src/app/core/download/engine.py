"""core 通用下载引擎：任务队列、断点续传、镜像回退、并行/顺序热切换。

与模型工具库那套老下载器相比，这版有三处关键区别：

1. **不认具体插件**。配置走 `DownloadOptions`（纯数据），日志走 `logging`，落盘位置由
   调用方给出，所以程序本体页、别的插件都能直接用同一套引擎。
2. **续传依据是「下载任务索引」**，而不是扫描固定目录下的 `*.part`。用户可以在每次下载
   前自己选目标路径，散落在各处的临时文件扫不出来；索引（复用 `core.journals` 的临时
   清单）里记着每个任务的地址、目标、校验值与进度，进程重启后照着重排即可。
3. **并行数是热切换的**。`update()` 改完立刻生效：并行数下调时，超出并行数的那部分任务
   按列表顺序自动暂停（断点保留），正在跑的那几个会在下一个检查点停下来；上调时又按
   顺序自动接着开。为此暂停分成两种原因——`user`（用户按的，只有用户能恢复）与
   `policy`（调度压下来的，腾出额度就自动恢复），两者必须分得清，否则上调并行数会把
   用户手动暂停的任务也拉起来。

`<目标文件>.part` 就落在目标文件旁边：同目录才能保证 `os.replace` 是原子替换，也不会
跨卷失败。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..journals import (
    ITEM_ACTIVE,
    ITEM_CANCELLED,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_PENDING,
    JournalStore,
)
from ..runtime.sizes import human_size
from .mirrors import MirrorRules, default_rules

_logger = logging.getLogger(__name__)

__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_CONCURRENT",
    "DEFAULT_RETRY",
    "DEFAULT_TIMEOUT",
    "DOWNLOAD_KIND",
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "DownloadOptions",
    "FINAL_STATES",
    "MAX_CONCURRENT",
    "PAUSE_POLICY",
    "PAUSE_USER",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_LABELS",
    "STATE_PAUSED",
    "STATE_QUEUED",
    "STATE_RUNNING",
    "USER_AGENT",
    "build_opener",
    "describe_error",
    "free_space",
    "host_of",
    "part_path",
    "sha256_file",
    "to_int",
    "unique_target",
]

CHUNK_SIZE = 128 * 1024
TICK_SECONDS = 0.2
HASH_CHUNK_SIZE = 1024 * 1024
DEFAULT_RETRY = 2
DEFAULT_TIMEOUT = 15.0
DEFAULT_CONCURRENT = 2
MAX_CONCURRENT = 16
USER_AGENT = "MyDataManager-Downloader/1.0"
DISK_MARGIN = 1.05
FORBIDDEN_CODES = frozenset({401, 403})
PART_SUFFIX = ".part"
#: 下载任务索引的临时清单类别（`.configs/journals/download/`）
DOWNLOAD_KIND = "download"

#: 任务状态
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"
STATE_LABELS = {
    STATE_QUEUED: "排队中",
    STATE_RUNNING: "下载中",
    STATE_PAUSED: "已暂停",
    STATE_DONE: "已完成",
    STATE_ERROR: "失败",
    STATE_CANCELLED: "已取消",
}
FINAL_STATES = frozenset({STATE_DONE, STATE_ERROR, STATE_CANCELLED})

#: 暂停原因：用户按的 vs 调度压下来的
PAUSE_USER = "user"
PAUSE_POLICY = "policy"

#: 任务状态 → 临时清单的单项状态
_ITEM_STATUS = {
    STATE_QUEUED: ITEM_PENDING,
    STATE_RUNNING: ITEM_ACTIVE,
    STATE_PAUSED: ITEM_PENDING,
    STATE_DONE: ITEM_DONE,
    STATE_ERROR: ITEM_FAILED,
    STATE_CANCELLED: ITEM_CANCELLED,
}


class DownloadError(RuntimeError):
    """下载相关的可读错误（调度方直接拿去给用户看）。"""


class _Halt(Exception):
    """内部信号：暂停 / 取消 / 退出，用于从读取循环里跳出来。"""


class _Forbidden(Exception):
    """内部信号：401 / 403——换镜像也没用，直接失败。"""


# ---------------------------------------------------------------- 小工具
def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def host_of(url: str) -> str:
    """取地址里的主机名（小写）；取不到就返回原串。"""
    return (urllib.parse.urlsplit(str(url or "")).netloc or str(url or "")).lower()


def range_start(value: str | None) -> int:
    """从 `Content-Range: bytes 100-200/300` 里取出续传起点。"""
    text = str(value or "")
    if "bytes" in text:
        text = text.split("bytes", 1)[1]
    return to_int(text.strip().split("-", 1)[0].strip(), 0)


def describe_error(exc: BaseException) -> str:
    """异常 → 给用户看的一句话。"""
    text = str(exc).strip()
    return text or exc.__class__.__name__


def build_opener(proxy: str = ""):
    """按代理设置建一个 urlopen 用的 opener。

    给了代理就用它替掉默认的代理处理器（只认 http/https 两种）；没给就保持
    `urlopen` 的默认行为，也就是照环境变量走。
    """
    text = str(proxy or "").strip()
    if not text:
        return urllib.request.build_opener()
    handler = urllib.request.ProxyHandler({"http": text, "https": text})
    return urllib.request.build_opener(handler)


def part_path(target: str | Path) -> Path:
    """临时文件路径：`<目标文件>.part`（必须同目录，`os.replace` 才原子）。"""
    path = Path(target)
    return path.with_name(path.name + PART_SUFFIX)


def sha256_file(path: str | Path, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """算文件 sha256（按块读，别把大文件整个吞进内存）。"""
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def unique_target(target: str | Path) -> Path:
    """目标已存在时换个名字：`a.bin` → `a_1.bin` → `a_2.bin`。"""
    path = Path(target)
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for serial in range(1, 1000):
        candidate = path.with_name(f"{stem}_{serial}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{uuid.uuid4().hex[:6]}{suffix}")


def free_space(probe: str | Path) -> int:
    """目标所在卷的剩余空间；问不出来返回 -1（调用方据此跳过检查）。"""
    path = Path(probe)
    while not path.exists():
        parent = path.parent
        if parent == path:
            return -1
        path = parent
    try:
        return int(_disk_free(path))
    except OSError:  # pragma: no cover - 平台差异
        return -1


def _disk_free(path: Path) -> int:
    import shutil

    return shutil.disk_usage(str(path)).free


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _as_urls(urls: Any) -> list[str]:
    if isinstance(urls, str):
        items = [urls]
    elif isinstance(urls, Iterable):
        items = list(urls)
    else:
        items = [urls]
    return [str(item or "").strip() for item in items if str(item or "").strip()]


def _item_of(job: DownloadJob) -> dict[str, Any]:
    return {
        "status": _ITEM_STATUS.get(job.state, ITEM_PENDING),
        "urls": list(job.urls),
        "target": str(job.target),
        "label": job.label,
        "sha256": job.sha256,
        "total_bytes": int(job.total_bytes),
        "done_bytes": int(job.done_bytes),
        "detail": job.error or job.note,
    }


# ---------------------------------------------------------------- 配置
@dataclass(frozen=True)
class DownloadOptions:
    """引擎要的全部设置项（纯数据，改一份新的交给 `DownloadManager.update`）。"""

    proxy: str = ""
    concurrent: int = DEFAULT_CONCURRENT
    #: 顺序下载：任何时刻只跑一个任务（并行数设置保留着，切回来就恢复）
    sequential: bool = False
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRY
    mirrors: MirrorRules = field(default_factory=default_rules)

    @property
    def limit(self) -> int:
        """当前允许同时跑几个任务。"""
        if self.sequential:
            return 1
        return max(1, min(to_int(self.concurrent, DEFAULT_CONCURRENT), MAX_CONCURRENT))

    def updated(self, **changes: Any) -> DownloadOptions:
        """改几项、其余照旧（界面上的设置项一改就产出一份新的）。"""
        return replace(self, **changes)


# ---------------------------------------------------------------- 任务
class DownloadJob:
    """一个下载任务。

    属性都可以给界面直接读；`state` 等运行状态只由 `DownloadManager` 改写，界面别自己动。
    """

    def __init__(
        self,
        job_id: str,
        urls: Iterable[str],
        target: str | Path,
        *,
        sha256: str = "",
        total_bytes: int = 0,
        label: str = "",
        done_bytes: int = 0,
        error: str = "",
        state: str = STATE_QUEUED,
    ) -> None:
        self.id = job_id
        #: 展开后的候选地址（顺序就是尝试顺序）
        self.urls: tuple[str, ...] = tuple(urls)
        self.target = Path(target)
        #: 临时文件（`<目标>.part`）
        self.staging = part_path(self.target)
        self.sha256 = str(sha256 or "")
        self.label = str(label or "")
        self.state = state
        self.total_bytes = int(total_bytes or 0)
        self.done_bytes = int(done_bytes or 0)
        self.error = str(error or "")
        self.speed = 0.0
        self.eta = 0.0
        self.phase = ""
        self.note = ""
        #: 上次成功连上的地址，重试 / 续传时提到最前面
        self.last_url = ""
        # 内部调度标记
        self._halt = False
        self._cancel = False
        self._pause_reason = ""

    # ------------------------------------------------------------ 展示
    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def progress(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done_bytes / self.total_bytes))

    @property
    def detail_label(self) -> str:
        return self.note or self.error or self.phase

    @property
    def paused_by_policy(self) -> bool:
        return self._pause_reason == PAUSE_POLICY

    @property
    def finished(self) -> bool:
        return self.state in FINAL_STATES

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<DownloadJob {self.id} {self.state} {self.target.name}>"


def _job_of(item: Mapping[str, Any]) -> DownloadJob | None:
    """从索引项还原任务；信息不全（缺目标或缺地址）的项直接丢掉。"""
    target = str(item.get("target") or "")
    urls = _as_urls(item.get("urls") or ())
    if not target or not urls:
        return None
    return DownloadJob(
        str(item.get("key") or _new_id()),
        urls,
        target,
        sha256=str(item.get("sha256") or ""),
        total_bytes=to_int(item.get("total_bytes")),
        label=str(item.get("label") or ""),
        done_bytes=to_int(item.get("done_bytes")),
    )


# ---------------------------------------------------------------- 引擎
class DownloadManager:
    """下载队列：排队、跑、暂停、续传、取消、重试、清理，并行数随时可改。

    `index` 传一个 `JournalStore` 就把任务索引落到盘上（进程重启后 `restore()` 能接着
    做）；传 `None` 就是纯内存队列，单测与一次性下载用这个。

    工作线程只增不减：上限取「用户设置过的最大并行数」，多出来的线程没事干就睡在条件
    变量上，不占什么资源。真正的并发上限由 `_take_locked()` 在取任务时把住，所以并行数
    下调也是立刻生效的。
    """

    def __init__(
        self,
        options: DownloadOptions | None = None,
        *,
        index: JournalStore | None = None,
        on_change: Any = None,
        name: str = "download",
    ) -> None:
        self._options = options or DownloadOptions()
        self._index = index
        self._on_change = on_change
        self._name = str(name or "download")
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[str, DownloadJob] = {}
        self._workers: list[threading.Thread] = []
        self._spawned = 0
        self._closing = False
        self._journal: Any = None

    # ------------------------------------------------------------ 设置
    @property
    def options(self) -> DownloadOptions:
        return self._options

    @property
    def limit(self) -> int:
        return self._options.limit

    def update(self, options: DownloadOptions) -> None:
        """换一套设置并立刻生效（并行数、顺序模式、代理、镜像规则都走这里）。"""
        with self._cond:
            self._options = options or DownloadOptions()
            self._rebalance_locked()
            self._sync_workers_locked()
            self._cond.notify_all()
        self._notify()

    # ------------------------------------------------------------ 查询
    def jobs(self) -> list[DownloadJob]:
        """全部任务，按加入顺序（列表里的先后就是调度顺序）。"""
        with self._lock:
            return list(self._jobs.values())

    def find(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(str(job_id))

    def active(self) -> list[DownloadJob]:
        """在跑的（排队中 / 下载中）。"""
        with self._lock:
            return [job for job in self._jobs.values() if job.state in (STATE_QUEUED, STATE_RUNNING)]

    def finished(self) -> list[DownloadJob]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state in FINAL_STATES]

    # ------------------------------------------------------------ 加入队列
    def expand(self, urls: Any) -> list[str]:
        """把用户给的地址按镜像规则展开成候选列表（顺序即尝试顺序）。"""
        out: list[str] = []
        for url in _as_urls(urls):
            scheme = url.split(":", 1)[0].lower()
            if scheme not in ("http", "https"):
                raise DownloadError(f"不支持的下载地址（只认 http/https）：{url}")
            for candidate in self._options.mirrors.candidates(url):
                if candidate and candidate not in out:
                    out.append(candidate)
        return out

    def enqueue(
        self,
        urls: Any,
        target: str | Path,
        *,
        sha256: str = "",
        total_bytes: int = 0,
        label: str = "",
    ) -> DownloadJob:
        """加一个下载任务。

        `urls` 可以是一个地址，也可以是一串（第一个之后的都当额外的备用地址）；
        镜像规则会在这一步就展开好，所以列表上看到的就是真正会去试的地址。
        同一个目标已经有没结束的任务时，直接返回那个任务，不重复排队。
        """
        candidates = self.expand(urls)
        if not candidates:
            raise DownloadError("没有可用的下载地址：请检查地址与镜像设置")
        path = Path(target).expanduser()
        if not path.name:
            raise DownloadError("下载目标不合法：缺少文件名")
        if not path.is_absolute():
            path = Path.cwd() / path
        # 磁盘检查放在持锁之前：它要 stat 磁盘，别占着队列的锁
        self._check_disk(path.parent, total_bytes)
        with self._cond:
            for existing in self._jobs.values():
                if existing.target == path and existing.state not in FINAL_STATES:
                    return existing
            job = DownloadJob(
                _new_id(),
                candidates,
                path,
                sha256=sha256,
                total_bytes=total_bytes,
                label=label,
            )
            job.done_bytes = _part_size(job)
            self._jobs[job.id] = job
            self._ensure_journal_locked()
            self._touch_locked(job, force=True)
            self._rebalance_locked()
            self._sync_workers_locked()
            self._cond.notify_all()
        self._notify()
        return job

    # ------------------------------------------------------------ 单个操作
    def pause(self, job_id: str) -> bool:
        """暂停一个任务（排队中的当场变暂停，跑着的在下一个检查点停）。"""
        job = self.find(job_id)
        if job is None or job.finished:
            return False
        with self._cond:
            job._halt = True
            job._pause_reason = PAUSE_USER
            if job.state == STATE_QUEUED:
                job.state = STATE_PAUSED
                job.note = "已暂停"
            elif job.state == STATE_RUNNING:
                job.note = "正在暂停…"
            else:
                job.note = job.note or "已暂停"
            self._touch_locked(job, force=True)
            self._cond.notify_all()
        self._notify()
        return True

    def resume(self, job_id: str) -> bool:
        """继续一个暂停的任务；额度不够时它会继续排队等。"""
        job = self.find(job_id)
        if job is None or job.finished:
            return False
        with self._cond:
            if job.state != STATE_PAUSED and job._pause_reason != PAUSE_POLICY:
                return False
            job._halt = False
            job._pause_reason = ""
            job.note = ""
            job.error = ""
            if job.state == STATE_PAUSED:
                job.state = STATE_QUEUED
            job.done_bytes = max(job.done_bytes, _part_size(job))
            self._touch_locked(job, force=True)
            self._rebalance_locked()
            self._sync_workers_locked()
            self._cond.notify_all()
        self._notify()
        return True

    def cancel(self, job_id: str) -> bool:
        """取消一个任务（临时文件一起删掉）。"""
        job = self.find(job_id)
        if job is None or job.finished:
            return False
        with self._cond:
            job._cancel = True
            job._halt = True
            if job.state in (STATE_QUEUED, STATE_PAUSED):
                # 同 `_stop`：清干净再置状态，别让观察者看到「已取消 + 还有 .part」
                job.done_bytes = 0
                _drop_part(job)
                job.state = STATE_CANCELLED
                job.error = ""
                job.note = "已取消"
                job.speed = 0.0
                job.eta = 0.0
                job.phase = ""
            else:
                job.note = "正在取消…"
            self._touch_locked(job, force=True)
            self._settle_if_done_locked()
            self._cond.notify_all()
        self._notify()
        return True

    def retry(self, job_id: str) -> bool:
        """失败 / 已取消的任务重新排队（断点还在就从断点接着来）。"""
        job = self.find(job_id)
        if job is None or job.state not in (STATE_ERROR, STATE_CANCELLED):
            return False
        with self._cond:
            job._cancel = False
            job._halt = False
            job._pause_reason = ""
            job.state = STATE_QUEUED
            job.error = ""
            job.phase = ""
            job.note = ""
            job.speed = 0.0
            job.eta = 0.0
            job.done_bytes = _part_size(job)
            self._touch_locked(job, force=True)
            self._rebalance_locked()
            self._sync_workers_locked()
            self._cond.notify_all()
        self._notify()
        return True

    def forget(self, job_id: str) -> bool:
        """从列表里移掉一个任务（只有终态能移）。"""
        job = self.find(job_id)
        if job is None or job.state not in FINAL_STATES:
            return False
        with self._cond:
            self._jobs.pop(job.id, None)
            self._forget_item_locked(job.id)
            self._settle_if_done_locked()
            self._cond.notify_all()
        self._notify()
        return True

    # ------------------------------------------------------------ 批量操作
    def pause_all(self) -> int:
        """全部暂停（用户按的暂停）。"""
        count = 0
        with self._cond:
            for job in self._jobs.values():
                if job.finished:
                    continue
                job._halt = True
                job._pause_reason = PAUSE_USER
                if job.state == STATE_QUEUED:
                    job.state = STATE_PAUSED
                    job.note = "已暂停"
                elif job.state == STATE_RUNNING:
                    job.note = "正在暂停…"
                else:
                    job.note = job.note or "已暂停"
                self._touch_locked(job)
                count += 1
            self._cond.notify_all()
        if count:
            self._notify()
        return count

    def resume_all(self) -> int:
        """全部继续；超出并行数的那些仍然按并行数排队。"""
        count = 0
        with self._cond:
            for job in self._jobs.values():
                if job.finished:
                    continue
                if job.state != STATE_PAUSED and job._pause_reason != PAUSE_POLICY:
                    continue
                job._halt = False
                job._pause_reason = ""
                job.note = ""
                job.error = ""
                if job.state == STATE_PAUSED:
                    job.state = STATE_QUEUED
                job.done_bytes = max(job.done_bytes, _part_size(job))
                self._touch_locked(job)
                count += 1
            self._rebalance_locked()
            self._sync_workers_locked()
            self._cond.notify_all()
        if count:
            self._notify()
        return count

    def cancel_all(self) -> int:
        """全部取消（临时文件一起删）。"""
        count = 0
        with self._cond:
            for job in self._jobs.values():
                if job.finished:
                    continue
                job._cancel = True
                job._halt = True
                if job.state in (STATE_QUEUED, STATE_PAUSED):
                    job.state = STATE_CANCELLED
                    job.done_bytes = 0
                    job.error = ""
                    job.phase = ""
                    job.note = "已取消"
                    job.speed = 0.0
                    job.eta = 0.0
                    _drop_part(job)
                else:
                    job.note = "正在取消…"
                self._touch_locked(job)
                count += 1
            self._settle_if_done_locked()
            self._cond.notify_all()
        if count:
            self._notify()
        return count

    def clear_finished(self) -> int:
        """清掉所有已结束的任务（列表上的「已结束清理」）。"""
        with self._cond:
            done = [job for job in self._jobs.values() if job.state in FINAL_STATES]
            for job in done:
                self._jobs.pop(job.id, None)
                self._forget_item_locked(job.id)
            self._settle_if_done_locked()
        if done:
            self._notify()
        return len(done)

    # ------------------------------------------------------------ 恢复
    def restore(self) -> list[DownloadJob]:
        """从下载任务索引里恢复上次没做完的任务（进程重启后调用）。

        索引是唯一的失据来源（`.part` 文件本身不带地址信息），恢复出来的任务一律回到
        「排队中」，按当前并行数自动接着开——和模型工具库原来的行为一致，用户想让它先
        停着就再点暂停。
        """
        if self._index is None:
            return []
        store = self._index
        store.clear_settled()
        restored: list[DownloadJob] = []
        with self._cond:
            stale = []
            for journal in store.scan():
                if journal.terminal():
                    continue
                journal.recover()  # 「进行中」的项退回「待处理」
                for item in journal.open_items():
                    job = _job_of(item)
                    if job is None or job.id in self._jobs:
                        continue
                    job.done_bytes = max(job.done_bytes, _part_size(job))
                    self._jobs[job.id] = job
                    restored.append(job)
                stale.append(journal)
            # 旧索引里的内容已经全部搬进内存，合并成一份新索引，别留一堆碎片文件
            for journal in stale:
                store.remove(journal)
            self._journal = None
            if restored:
                self._ensure_journal_locked()
                for job in restored:
                    self._touch_locked(job, force=True)
                self._rebalance_locked()
                self._sync_workers_locked()
            self._cond.notify_all()
        if restored:
            self._notify()
        return restored

    # ------------------------------------------------------------ 收尾
    def shutdown(self, wait: float = 3.0) -> None:
        """停掉引擎：先全部暂停（断点留着），再让工作线程退出。"""
        self.pause_all()
        with self._cond:
            self._closing = True
            self._cond.notify_all()
            workers = list(self._workers)
            self._workers.clear()
        deadline = time.monotonic() + max(0.0, float(wait))
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        self.flush()

    def flush(self) -> None:
        """把任务索引立刻写出去（退出前调用）。"""
        if self._index is None:
            return
        with self._lock:
            journal = self._journal
        if journal is not None:
            self._index.flush(journal)

    # ------------------------------------------------------------ 内部：索引
    def _ensure_journal_locked(self) -> Any:
        journal = self._journal
        if journal is not None or self._index is None:
            return journal
        items = [
            {"key": job.id, **_item_of(job)}
            for job in self._jobs.values()
            if job.state not in FINAL_STATES
        ]
        self._journal = self._index.create(
            options={"name": self._name},
            items=items,
            title="下载任务",
            description="下载器正在进行的任务；待处理项为空时自动删除",
        )
        return self._journal

    def _touch_locked(self, job: DownloadJob, *, force: bool = False) -> None:
        journal = self._journal
        if journal is None or self._index is None:
            return
        item = journal.find(job.id)
        fields = _item_of(job)
        if item is None:
            journal.add_item(job.id, **fields)
        else:
            item.update(fields)
        self._index.save(journal, force=force)

    def _forget_item_locked(self, job_id: str) -> None:
        journal = self._journal
        if journal is None:
            return
        journal.items = [item for item in journal.items if str(item.get("key")) != str(job_id)]

    def _settle_if_done_locked(self) -> None:
        journal = self._journal
        if journal is None or self._index is None:
            return
        if journal.terminal():
            self._index.settle(journal)
            self._journal = None

    # ------------------------------------------------------------ 内部：调度
    def _take_locked(self) -> DownloadJob | None:
        """取下一个能跑的任务；本函数就是并发上限的把关处。"""
        if self._closing:
            return None
        if self._running_count_locked() >= self._options.limit:
            return None
        for job in self._jobs.values():
            if job.state == STATE_QUEUED and not job._halt and not job._cancel:
                job.state = STATE_RUNNING
                job.speed = 0.0
                job.eta = 0.0
                job.error = ""
                job.note = ""
                job.phase = ""
                self._touch_locked(job, force=True)
                return job
        return None

    def _running_count_locked(self) -> int:
        return sum(1 for job in self._jobs.values() if job.state == STATE_RUNNING)

    def _rebalance_locked(self) -> None:
        """按当前并行数把队列调整到该有的样子。

        列表顺序就是优先级：前 `limit` 个「想跑的任务」拿到额度，其后的全部按调度暂停；
        之前被调度暂停的任务，一旦重新排进前 `limit` 个就自动恢复队列。
        """
        limit = self._options.limit
        slots = 0
        changed = False
        for job in self._jobs.values():
            if job.finished or job._cancel:
                continue
            if job._pause_reason == PAUSE_USER:
                continue  # 用户暂停的不参与调度
            if slots < limit:
                slots += 1
                if job._pause_reason == PAUSE_POLICY:
                    job._pause_reason = ""
                    job._halt = False
                    job.note = ""
                    if job.state == STATE_PAUSED:
                        job.state = STATE_QUEUED
                        job.done_bytes = max(job.done_bytes, _part_size(job))
                    self._touch_locked(job)
                    changed = True
                continue
            if job._pause_reason != PAUSE_POLICY:
                job._pause_reason = PAUSE_POLICY
                job._halt = True
                if job.state == STATE_QUEUED:
                    job.state = STATE_PAUSED
                    job.note = "超出并行数，已自动暂停"
                else:
                    job.note = "超出并行数，即将暂停"
                self._touch_locked(job)
                changed = True
        if changed:
            self._cond.notify_all()

    def _sync_workers_locked(self) -> None:
        """工作线程不够就补上；多了不管（多出来的睡在条件变量上，不占额度）。"""
        self._workers = [worker for worker in self._workers if worker.is_alive()]
        while len(self._workers) < self._options.limit:
            self._spawned += 1
            worker = threading.Thread(
                target=self._worker,
                name=f"{self._name}-download-{self._spawned}",
                daemon=True,
            )
            self._workers.append(worker)
            worker.start()

    # ------------------------------------------------------------ 内部：线程
    def _worker(self) -> None:
        while True:
            with self._cond:
                if self._closing:
                    return
                job = self._take_locked()
                if job is None:
                    self._cond.wait(0.25)
                    continue
            try:
                self._download(job)
            except _Halt:
                self._stop(job)
            except Exception as exc:  # 兜底：线程绝不能因为一个任务静默死掉
                _logger.exception("下载任务异常：%s", job.id)
                self._fail(job, describe_error(exc))
            finally:
                with self._cond:
                    self._settle_if_done_locked()
                    self._rebalance_locked()
                    self._sync_workers_locked()
                    self._cond.notify_all()
                self._notify()

    def _stopped(self, job: DownloadJob) -> bool:
        """要不要停下来（暂停 / 取消 / 引擎退出）。判断不加锁，读两个布尔值足够。"""
        return bool(job._halt or job._cancel or self._closing)

    def _stop(self, job: DownloadJob) -> None:
        """从读取循环里退出来，落到暂停 / 取消状态。"""
        with self._cond:
            job.speed = 0.0
            job.eta = 0.0
            job.phase = ""
            job.error = ""
            if job._cancel:
                # 先把半成品清掉再广播「已取消」：外面一看到终态，残留物就已经没了
                # （反过来写会让观察者抢在 unlink 之前看到 CANCELLED，测试与界面都会闪一下）。
                _drop_part(job)
                job.state = STATE_CANCELLED
                job.note = "已取消"
                job.done_bytes = 0
            else:
                # 退出时是「停在这里」而不是「取消」：任务索引里这一项留在待处理，
                # 下次启动 restore() 接着做。
                job.state = STATE_PAUSED
                job.done_bytes = max(job.done_bytes, _part_size(job))
                if self._closing:
                    job.note = "已退出，下次启动继续"
                elif job._pause_reason == PAUSE_POLICY:
                    job.note = "超出并行数，已自动暂停"
                else:
                    job.note = "已暂停"
            self._touch_locked(job, force=True)
            self._settle_if_done_locked()
            self._cond.notify_all()
        self._notify()

    def _fail(self, job: DownloadJob, message: str) -> None:
        with self._cond:
            job.state = STATE_ERROR
            job.error = str(message)
            job.note = ""
            job.phase = ""
            job.speed = 0.0
            job.eta = 0.0
            job.done_bytes = max(job.done_bytes, _part_size(job))
            self._touch_locked(job, force=True)
            self._settle_if_done_locked()
            self._cond.notify_all()
        self._notify()

    # ------------------------------------------------------------ 内部：下载
    def _download(self, job: DownloadJob) -> None:
        """一个任务的完整过程：候选地址轮着试，每个地址重试若干次。"""
        part = job.staging
        try:
            part.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._fail(job, f"建不了下载目录：{describe_error(exc)}")
            return
        urls = list(job.urls)
        if job.last_url and job.last_url in urls:  # 上次成功的地址先试
            urls.remove(job.last_url)
            urls.insert(0, job.last_url)
        last_error = ""
        for url in urls:
            ok = False
            for attempt in range(max(0, to_int(self._options.retries, DEFAULT_RETRY)) + 1):
                if self._stopped(job):
                    self._stop(job)
                    return
                try:
                    self._fetch(job, url, part)
                except _Halt:
                    self._stop(job)
                    return
                except _Forbidden as exc:
                    self._fail(job, describe_error(exc))  # 没权限，换镜像也没用
                    return
                except Exception as exc:
                    last_error = describe_error(exc)
                    _logger.warning("下载失败（%s，第 %s 次）：%s", host_of(url), attempt + 1, last_error)
                    with self._cond:
                        job.error = f"{last_error}（第 {attempt + 1} 次失败，继续重试）"
                        job.phase = "重试中"
                        self._touch_locked(job)
                        self._cond.notify_all()
                    self._notify()
                    if self._backoff(job, attempt):
                        self._stop(job)
                        return
                    continue
                ok = True
                break
            if ok:
                break
        else:
            self._fail(job, last_error or "下载失败")
            return
        if self._stopped(job):
            self._stop(job)
            return
        self._finalize(job, part)

    def _fetch(self, job: DownloadJob, url: str, part: Path) -> None:
        """按一个地址拉一次数据；已完成的那部分靠 `Range` 续上。"""
        done = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if done > 0:
            headers["Range"] = f"bytes={done}-"
        request = urllib.request.Request(url, headers=headers)
        with self._cond:
            job.phase = "连接中"
            self._cond.notify_all()
        self._notify()
        opener = build_opener(self._options.proxy)
        try:
            response = opener.open(request, timeout=float(self._options.timeout or DEFAULT_TIMEOUT))
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:  # pragma: no cover - 关不掉就算了
                pass
            if exc.code in FORBIDDEN_CODES:
                raise _Forbidden(f"没有下载权限（HTTP {exc.code}）：{host_of(url)}") from exc
            if exc.code == 416 and job.total_bytes and done >= job.total_bytes:
                with self._cond:
                    job.done_bytes = done
                return
            raise DownloadError(f"HTTP {exc.code}：{host_of(url)}") from exc
        except urllib.error.URLError as exc:
            raise DownloadError(
                f"连接失败：{getattr(exc, 'reason', exc)}（{host_of(url)}）"
            ) from exc
        with response:
            status = int(getattr(response, "status", 200) or 200)
            mode = "wb"
            if status == 206:
                start = range_start(response.headers.get("Content-Range"))
                if start > done:
                    raise DownloadError(f"服务器返回的续传起点不匹配：{host_of(url)}")
                if start < done:  # 服务器起点靠前，把多出来的截掉
                    with part.open("r+b") as handle:
                        handle.truncate(start)
                    done = start
                mode = "ab"
            else:
                done = 0  # 服务器不认续传，整份重来
            length = to_int(response.headers.get("Content-Length"))
            with self._cond:
                if length and length > 0:
                    job.total_bytes = done + length
                job.phase = "接收数据"
                job.last_url = url
                job.error = ""
                job.done_bytes = done
                self._touch_locked(job)
                self._cond.notify_all()
            received = self._read_body(job, response, part, mode, done)
        if job.total_bytes and received < job.total_bytes:
            raise DownloadError(f"连接提前结束：{host_of(url)}")

    def _read_body(self, job: DownloadJob, response: Any, part: Path, mode: str, done: int) -> int:
        """把响应体写进临时文件，顺手更新速度与剩余时间。"""
        received = done
        window_bytes = done
        window_start = time.monotonic()
        handle = part.open(mode)
        try:
            while True:
                if self._stopped(job):
                    raise _Halt()
                block = response.read(CHUNK_SIZE)
                if not block:
                    break
                handle.write(block)
                received += len(block)
                now = time.monotonic()
                if now - window_start >= TICK_SECONDS:
                    speed = (received - window_bytes) / max(1e-6, now - window_start)
                    with self._cond:
                        job.done_bytes = received
                        job.speed = speed
                        job.eta = (
                            (job.total_bytes - received) / speed
                            if job.total_bytes and speed > 0
                            else 0.0
                        )
                        self._touch_locked(job)
                        self._cond.notify_all()
                    window_bytes = received
                    window_start = now
        finally:
            handle.close()
        with self._cond:
            job.done_bytes = received
            self._touch_locked(job)
            self._cond.notify_all()
        return received

    def _finalize(self, job: DownloadJob, part: Path) -> None:
        """校验、改名、落到目标位置。"""
        if self._stopped(job):
            self._stop(job)
            return
        with self._cond:
            job.phase = "校验中"
            self._cond.notify_all()
        self._notify()
        if job.sha256:
            actual = sha256_file(part)
            if actual.lower() != job.sha256.strip().lower():
                try:
                    part.unlink()
                except OSError:
                    pass
                self._fail(job, "校验失败：文件内容与预期的 sha256 不一致")
                return
        target = unique_target(job.target)
        last: BaseException | None = None
        for _ in range(3):  # Windows 上偶发瞬时占用，重试几次
            try:
                os.replace(part, target)
                last = None
                break
            except OSError as exc:
                last = exc
                time.sleep(0.3)
        if last is not None:
            self._fail(job, f"写入目标文件失败：{describe_error(last)}")
            return
        with self._cond:
            job.target = target
            job.state = STATE_DONE
            job.error = ""
            job.phase = ""
            job.note = ""
            job.speed = 0.0
            job.eta = 0.0
            try:
                job.done_bytes = target.stat().st_size
            except OSError:  # pragma: no cover - 刚写完一般都在
                job.done_bytes = max(job.done_bytes, 0)
            if not job.total_bytes:
                job.total_bytes = job.done_bytes
            self._touch_locked(job, force=True)
            self._settle_if_done_locked()
            self._cond.notify_all()
        self._notify()

    def _backoff(self, job: DownloadJob, attempt: int) -> bool:
        """失败后等一小会儿再试；期间被打断（暂停 / 取消 / 退出）返回 True。"""
        deadline = time.monotonic() + min(1.0, 0.2 * (attempt + 1))
        while time.monotonic() < deadline:
            if self._stopped(job):
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------ 内部：杂项
    def _check_disk(self, folder: Path, total_bytes: int) -> None:
        if to_int(total_bytes) <= 0:
            return
        required = int(to_int(total_bytes) * DISK_MARGIN)
        free = free_space(folder)
        if 0 <= free < required:
            raise DownloadError(
                f"磁盘空间不足：需要 {human_size(required)}，可用 {human_size(free)}"
            )

    def _notify(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception:  # 回调是界面的事，出错不能影响下载
            _logger.exception("下载状态回调失败")


def _part_size(job: DownloadJob) -> int:
    """临时文件当前有多大（断点就是它）。"""
    try:
        return job.staging.stat().st_size if job.staging.is_file() else 0
    except OSError:  # pragma: no cover - 平台差异
        return 0


def _drop_part(job: DownloadJob) -> None:
    try:
        job.staging.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - 平台差异
        _logger.warning("临时文件删不掉：%s：%s", job.staging, exc)
