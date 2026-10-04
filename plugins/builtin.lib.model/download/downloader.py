"""下载队列：断点续传、镜像回退、sha256 校验与原子落盘。

临时文件 `.part` 与断点信息 `.meta.json` 一律放 `paths.download_dir(model_id)`，
完成时才 `os.replace` 到目标路径——任何时刻中断都不会在目标位置留下半个「正式文件」。
下载线程只改 `DownloadJob` 的公开字段与私有标志位，界面通过 `on_change` 回调刷新。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.sdk.console import console_for

from app.sdk.data import human_size

from ..paths import download_dir
from ..settings import ModelSettings

_console = console_for("builtin.lib.model")

__all__ = [
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "build_opener",
    "drop_part",
    "host_of",
    "meta_path",
    "part_path",
    "prune_download_dir",
    "range_start",
    "read_meta",
    "remember_job",
    "retry_count",
    "sha256_file",
    "timeout_seconds",
    "to_int",
    "unique_target",
    "write_meta",
]

#: 下载线程的读取块大小（字节）
CHUNK_SIZE = 128 * 1024
#: 进度回调节奏（秒）
TICK_SECONDS = 0.2
#: 校验时的读取块大小（字节）
HASH_CHUNK_SIZE = 1024 * 1024
#: 单个地址默认重试次数
DEFAULT_RETRY = 2
#: 默认网络超时（秒）；按每次 socket 操作算，调小能让连不上的地址更快报错、暂停 / 取消更快生效
DEFAULT_TIMEOUT = 15.0
#: 请求标识，便于下载源侧统计
USER_AGENT = "MyDataManager-ModelDownloader/1.0"
#: 磁盘预检余量：目标盘可用空间必须大于总大小的这个倍数
DISK_MARGIN = 1.05

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

_STATE_LABELS = {
    STATE_QUEUED: "排队中",
    STATE_RUNNING: "下载中",
    STATE_PAUSED: "已暂停",
    STATE_DONE: "已完成",
    STATE_ERROR: "失败",
    STATE_CANCELLED: "已取消",
}
_FINAL_STATES = frozenset({STATE_DONE, STATE_ERROR, STATE_CANCELLED})
#: 这类 HTTP 状态是认证/权限问题，换镜像也没用，直接失败
_FORBIDDEN_CODES = frozenset({401, 403})


class DownloadError(RuntimeError):
    """下载失败：地址不可用、校验不过、磁盘不够、写盘失败等。"""


class _Halt(Exception):
    """内部信号：暂停 / 取消 / 关停，需要收尾退出。"""


class _Forbidden(Exception):
    """内部信号：认证类失败，不再回退镜像。"""


# --------------------------------------------------------------------- 小工具
def to_int(value: Any) -> int:
    """把响应头里的整数字符串转成正整数；拿不到或非正数返回 0。"""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def host_of(url: str) -> str:
    """从地址里取主机名，日志里不打印可能带令牌的完整地址。"""
    return urllib.parse.urlsplit(str(url)).netloc or str(url)


def range_start(value: str | None) -> int | None:
    """解析 `Content-Range: bytes 100-999/1000` 的起点；解析不出返回 None。"""
    text = str(value or "")
    if not text.lower().startswith("bytes "):
        return None
    head = text[6:].split("/", 1)[0].split("-", 1)[0].strip()
    return int(head) if head.isdigit() else None


def timeout_seconds(settings: Any, default: float = DEFAULT_TIMEOUT) -> float:
    """取设置里的网络超时；没配或非法就用默认值。"""
    download = getattr(settings, "download", None)
    raw = download.get("timeout") if isinstance(download, dict) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def retry_count(settings: Any, default: int = DEFAULT_RETRY) -> int:
    """取设置里的单地址重试次数，限制在 0~5。"""
    download = getattr(settings, "download", None)
    raw = download.get("chunk_retry") if isinstance(download, dict) else None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, min(5, value))


def build_opener(settings: Any = None) -> urllib.request.OpenerDirector:
    """按设置构造 opener：配了代理就走代理，否则用默认（含系统代理）。"""
    proxy = str(getattr(settings, "proxy", "") or "").strip()
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def describe_error(error: BaseException) -> str:
    """把异常转成可读中文原因，保留原始信息。"""
    if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
        return f"连接失败：{getattr(error, 'reason', error)}"
    text = str(error).strip()
    return text or error.__class__.__name__


def part_path(model_id: str, target: Path) -> Path:
    """`.part` 临时文件的位置：下载目录下按模型 id 分目录、按目标文件名命名。"""
    return download_dir(model_id) / f"{Path(target).name}.part"


def meta_path(part: Path) -> Path:
    """断点信息文件：`.part` 旁的 `<文件名>.meta.json`。"""
    name = Path(part).name
    return Path(part).with_name(f"{name}.meta.json")


def prune_download_dir(model_id: str) -> bool:
    """下载收尾：临时目录空了就删掉，空的 `download/` 也顺手清掉。

    只删空目录（`rmdir` 遇到非空目录会失败），所以用户往里放的东西一个都动不到。
    返回模型自己的临时目录是否已经不在了。
    """
    folder = download_dir(model_id)
    targets = [folder]
    if folder.parent.name == "download":
        targets.append(folder.parent)
    for path in targets:
        try:
            path.rmdir()
        except OSError:
            pass  # 目录不存在 / 里面还有东西：都不是错误
    return not folder.exists()


def drop_part(model_id: str, target: Path) -> bool:
    """取消收尾：`target` 对应的 `.part`、断点信息、空的临时目录一起清掉。

    取消就是不要了，半成品没有留着的理由；暂停走另一条路（只停下，全都留着续传）。
    返回临时目录是否已经不在了。
    """
    part = part_path(model_id, target)
    part.unlink(missing_ok=True)
    meta_path(part).unlink(missing_ok=True)
    return prune_download_dir(model_id)


def read_meta(path: Path) -> dict:
    """读断点信息；不存在或损坏时返回空字典。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_meta(path: Path, payload: dict) -> None:
    """写断点信息（etag / last_modified / total_bytes / url）；失败只记日志。"""
    try:
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        _console.warning(f"写断点信息失败 {Path(path).name}：{exc}")


def remember_job(path: Path, job: DownloadJob) -> None:
    """把「这个 `.part` 是谁的、从哪下、落到哪」写进断点信息。

    正常跑的时候用不上；程序被强杀（关窗口 / 断电）后盘上只剩 `.part` 与这份记录，
    `DownloadManager.resume_leftovers()` 靠它把任务原样排回队列接着下。
    """
    payload = read_meta(path)
    payload.update(
        {
            "model_id": job.model_id,
            "urls": list(job._urls),
            "sha256": job._sha256,
            "label": job.label,
            "target": str(job.target),
            "total_bytes": job.total_bytes,
        }
    )
    write_meta(path, payload)


def sha256_file(path: Path, chunk: int = HASH_CHUNK_SIZE) -> str:
    """流式算文件的 sha256（十六进制小写）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def unique_target(target: Path) -> Path:
    """同目录同名冲突时加 `_1`、`_2`……避免覆盖已有文件。"""
    target = Path(target)
    if not target.exists():
        return target
    for index in range(1, 1000):
        candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{target.stem}_{uuid.uuid4().hex[:6]}{target.suffix}")


def _free_space(probe: Path) -> int:
    """目标所在盘的可用空间；探测点不存在时往上找到第一个存在的祖先。"""
    point = Path(probe)
    while not point.exists() and point != point.parent:
        point = point.parent
    try:
        return int(shutil.disk_usage(point).free)
    except OSError as exc:
        _console.warning(f"读磁盘剩余空间失败 {point}：{exc}")
        return -1


# --------------------------------------------------------------------- 任务
@dataclass
class DownloadJob:
    """一个下载任务：状态、进度与暂停 / 取消入口。"""

    id: str
    model_id: str
    label: str
    state: str
    target: Path
    total_bytes: int = 0
    done_bytes: int = 0
    error: str = ""
    speed: float = 0.0
    eta: float = 0.0
    #: 当前阶段（连接中 / 接收数据 / 校验中），界面用它说明「下载中」到底在干什么
    phase: str = ""
    #: 控制动作提示（正在暂停… / 正在取消…）
    note: str = ""
    _urls: list[str] = field(default_factory=list, repr=False, compare=False)
    _sha256: str = field(default="", repr=False, compare=False)
    _pause: bool = field(default=False, repr=False, compare=False)
    _cancel: bool = field(default=False, repr=False, compare=False)
    _notify: Callable[[], None] | None = field(default=None, repr=False, compare=False)
    _wake: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------- 控制
    def pause(self) -> None:
        """请求暂停：运行中的由下载线程收尾，排队的立刻转已暂停。"""
        if self.state in _FINAL_STATES:
            return
        self._pause = True
        if self.state == STATE_QUEUED:
            self.state = STATE_PAUSED
            self.speed = 0.0
            self.eta = 0.0
            self.note = ""
        else:
            # 运行中的由下载线程收尾：先给一句「正在暂停…」，不然点了像没反应
            self.note = "正在暂停…"
        self._changed()

    def resume(self) -> None:
        """继续下载：把任务重新排进队列，`.part` 里已有的字节会被续传。"""
        self._pause = False
        if self.state != STATE_PAUSED:
            return
        self.state = STATE_QUEUED
        self.error = ""
        self.note = ""
        self.phase = ""
        if self._wake is not None:
            self._wake()
        self._changed()

    def cancel(self) -> None:
        """取消下载：临时文件清掉，任务不再进队列。"""
        if self.state in _FINAL_STATES:
            return
        self._cancel = True
        self._pause = False
        if self.state in (STATE_QUEUED, STATE_PAUSED):
            # 排队中 / 已暂停的没有下载线程替它收尾，这里自己清
            drop_part(self.model_id, self.target)
            self.state = STATE_CANCELLED
            self.speed = 0.0
            self.eta = 0.0
            self.note = ""
        else:
            self.note = "正在取消…"
        self._changed()

    # ------------------------------------------------------------- 展示
    @property
    def progress(self) -> float:
        """完成比例 0~1；总大小未知时返回 0.0。"""
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done_bytes / self.total_bytes))

    @property
    def state_label(self) -> str:
        """状态的中文名。"""
        return _STATE_LABELS.get(self.state, self.state)

    @property
    def detail_label(self) -> str:
        """状态之外的一句话补充：连接 / 校验阶段，或「正在暂停…」。"""
        return self.note or self.phase

    def _changed(self) -> None:
        if self._notify is not None:
            self._notify()


# --------------------------------------------------------------------- 队列
class DownloadManager:
    """下载队列：固定几个线程按入队顺序干活，逐个任务续传 / 回退 / 校验。"""

    def __init__(
        self,
        settings: ModelSettings,
        on_change: Callable[[], None] | None = None,
        max_workers: int | None = None,
    ) -> None:
        self._settings = settings
        self._on_change = on_change
        self._retries = retry_count(settings)
        self._timeout = timeout_seconds(settings)
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[str, DownloadJob] = {}
        self._pending: deque[str] = deque()
        self._closing = False
        workers = max_workers if max_workers and max_workers > 0 else settings.concurrent
        self._workers = [
            threading.Thread(target=self._worker, name=f"model-download-{index + 1}", daemon=True)
            for index in range(max(1, int(workers)))
        ]
        for thread in self._workers:
            thread.start()

    # ------------------------------------------------------------- 对外
    def enqueue(
        self,
        model_id: str,
        urls: list[str],
        target: Path,
        *,
        sha256: str = "",
        total_bytes: int = 0,
        label: str = "",
    ) -> DownloadJob:
        """排一个下载任务；`urls` 已按「主站 + 镜像」顺序给好，逐个回退。

        地址里凡是 GitHub 上的资源（github.com / codeload / raw 等），再按「GitHub 下载源」
        设置展开成镜像前缀地址，排在原地址之前。
        """
        clean: list[str] = []
        expand = getattr(self._settings, "github_urls", None)
        for url in urls or ():
            text = str(url).strip()
            if not text:
                continue
            for item in expand(text) if callable(expand) else (text,):
                if item and item not in clean:
                    clean.append(str(item))
        if not clean:
            raise DownloadError("没有可用的下载地址")
        path = Path(target)
        if not path.name:
            raise DownloadError("下载目标不合法：缺少文件名")
        size = max(0, int(total_bytes or 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        self._check_disk(path.parent, size)
        job = DownloadJob(
            id=uuid.uuid4().hex[:8],
            model_id=str(model_id or ""),
            label=str(label or path.name),
            state=STATE_QUEUED,
            target=path,
            total_bytes=size,
            _urls=clean,
            _sha256=str(sha256 or "").strip().lower(),
            _notify=self._notify,
        )
        job._wake = lambda job=job: self._requeue(job)
        with self._cond:
            self._jobs[job.id] = job
            self._pending.append(job.id)
            self._cond.notify()
        self._notify()
        return job

    def jobs(self) -> tuple[DownloadJob, ...]:
        """全部任务，按入队顺序。"""
        with self._lock:
            return tuple(self._jobs.values())

    def find(self, job_id: str) -> DownloadJob | None:
        """按 id 找任务。"""
        with self._lock:
            return self._jobs.get(str(job_id or ""))

    def active(self) -> int:
        """未结束（排队 / 下载中）的任务数。"""
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.state in (STATE_QUEUED, STATE_RUNNING))

    def cancel_all(self) -> None:
        """取消所有未结束的任务；`.part` 一律保留。"""
        with self._lock:
            jobs = list(self._jobs.values())
            self._pending.clear()
        for job in jobs:
            job.cancel()

    def pause_all(self) -> int:
        """把所有未结束的任务转成暂停（排队中的当场转，下载中的由下载线程收尾）。

        关程序走这条：`.part` 与断点信息留在盘上，下次进场 `resume_leftovers()` 接着下——
        关掉程序不等于放弃下载。返回处理了多少条。
        """
        with self._lock:
            jobs = list(self._jobs.values())
            self._pending.clear()
        count = 0
        for job in jobs:
            if job.state in _FINAL_STATES:
                continue
            job.pause()
            count += 1
        return count

    def resume_leftovers(self) -> int:
        """上次异常退出留下的 `.part`：按断点信息排回队列，接着下。

        进场时才调用一次：程序被强杀（关窗口 / 断电）后盘上只剩 `.part` 与
        `remember_job()` 写的来源记录，这里把它们还原成任务——几个 G 的进度不用重来。
        没有来源信息的（老版本留下的）跳过；已经有同名任务的也不重复排。
        返回排回去的任务数。
        """
        base = download_dir()
        if not base.is_dir():
            return 0
        with self._lock:
            busy = {str(job.target) for job in self._jobs.values()}
        resumed = 0
        for part in sorted(base.rglob("*.part")):
            meta = read_meta(meta_path(part))
            urls = [str(url).strip() for url in meta.get("urls") or () if str(url).strip()]
            if not urls:
                previous = str(meta.get("url") or "").strip()
                urls = [previous] if previous else []
            if not urls:
                continue
            name = part.name[: -len(".part")]
            target_text = str(meta.get("target") or "")
            target = Path(target_text) if target_text else part.with_name(name)
            if not target.name or str(target) in busy:
                continue
            try:
                size = part.stat().st_size
            except OSError:
                size = 0
            try:
                self.enqueue(
                    str(meta.get("model_id") or part.parent.name),
                    urls,
                    target,
                    sha256=str(meta.get("sha256") or ""),
                    total_bytes=to_int(meta.get("total_bytes")) or size,
                    label=str(meta.get("label") or target.name),
                )
            except DownloadError as exc:
                _console.warning(f"恢复下载失败 {target.name}：{exc}")
                continue
            busy.add(str(target))
            resumed += 1
            _console.info(f"接上次没下完的下载：{target.name}（已下 {human_size(size)}）")
        if resumed:
            _console.info(f"共恢复 {resumed} 个没下完的下载任务")
        return resumed

    def forget(self, job_id: str) -> bool:
        """从列表里摘掉一条已结束的记录（`.part` 保留，之后还能续传）。"""
        key = str(job_id or "")
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.state not in _FINAL_STATES:
                return False
            del self._jobs[key]
            try:
                self._pending.remove(key)
            except ValueError:
                pass
        self._notify()
        return True

    def clear_finished(self) -> int:
        """清掉所有已结束的记录，返回条数；`.part` 一律保留。"""
        with self._lock:
            keys = [key for key, job in self._jobs.items() if job.state in _FINAL_STATES]
            for key in keys:
                del self._jobs[key]
                try:
                    self._pending.remove(key)
                except ValueError:
                    pass
        if keys:
            self._notify()
        return len(keys)

    def shutdown(self, wait: float = 3.0) -> None:
        """停掉下载线程，供插件 teardown 调用；未完成的任务按**暂停**收尾。

        关程序不等于放弃下载：`.part` 与断点信息留在盘上，下次进场
        `resume_leftovers()` 接着下（取消才会删 `.part`）。
        """
        self.pause_all()
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        deadline = time.monotonic() + max(0.0, float(wait))
        for thread in self._workers:
            thread.join(max(0.0, deadline - time.monotonic()))

    # ------------------------------------------------------------- 线程
    def _worker(self) -> None:
        while True:
            with self._cond:
                job = None
                while job is None:
                    if self._closing:
                        return
                    job = self._take_locked()
                    if job is None:
                        self._cond.wait(0.5)
                job.state = STATE_RUNNING
                job.speed = 0.0
                job.eta = 0.0
            self._notify()
            try:
                self._download(job)
            except Exception as exc:  # 线程兜底：任何漏网异常都不能让下载线程悄悄死掉
                _console.exception(f"下载任务异常 {job.id}")
                self._fail(job, describe_error(exc))

    def _take_locked(self) -> DownloadJob | None:
        while self._pending:
            job = self._jobs.get(self._pending.popleft())
            if job is None or job.state in _FINAL_STATES or job.state == STATE_PAUSED:
                continue
            if job._cancel:
                job.state = STATE_CANCELLED
                continue
            return job
        return None

    def _requeue(self, job: DownloadJob) -> None:
        with self._cond:
            if self._closing:
                return
            self._pending.append(job.id)
            self._cond.notify()

    # ------------------------------------------------------------- 下载
    def _download(self, job: DownloadJob) -> None:
        part = part_path(job.model_id, job.target)
        meta = meta_path(part)
        part.parent.mkdir(parents=True, exist_ok=True)
        remember_job(meta, job)
        urls = self._ordered_urls(job, meta)
        last_error = ""
        succeeded = False
        for url in urls:
            for attempt in range(self._retries + 1):
                if self._stopped(job):
                    self._stop(job)
                    return
                try:
                    self._fetch(job, url, part, meta)
                except _Halt:
                    self._stop(job)
                    return
                except _Forbidden as exc:
                    self._fail(job, str(exc))
                    return
                except Exception as exc:
                    last_error = describe_error(exc)
                    _console.warning(f"下载失败（{host_of(url)}，第 {attempt + 1} 次）：{last_error}")
                    if attempt < self._retries:
                        # 让界面马上看到「失败原因 + 正在重试」，而不是一直显示「下载中」等日志
                        job.error = f"{last_error}（第 {attempt + 1} 次失败，继续重试）"
                        job.phase = "重试中"
                        self._notify()
                        self._backoff(attempt)
                    continue
                succeeded = True
                break
            if succeeded:
                break
        if not succeeded:
            self._fail(job, last_error or "下载失败")
            return
        self._finalize(job, part, meta)

    def _ordered_urls(self, job: DownloadJob, meta: Path) -> list[str]:
        """上次用的地址优先，续传时尽量接着同一个源。"""
        urls = list(job._urls)
        previous = str(read_meta(meta).get("url") or "")
        if previous in urls and urls[0] != previous:
            urls.insert(0, urls.pop(urls.index(previous)))
        return urls

    def _fetch(self, job: DownloadJob, url: str, part: Path, meta: Path) -> None:
        """请求一个地址并写入 `.part`；成功返回，失败抛异常由上层决定重试 / 回退。"""
        done = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": USER_AGENT}
        if done:
            headers["Range"] = f"bytes={done}-"
        request = urllib.request.Request(url, headers=headers)
        job.phase = "连接中"
        job.note = ""
        self._notify()
        try:
            response = build_opener(self._settings).open(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            exc.close()  # 关掉响应体，避免连接悬挂
            if exc.code in _FORBIDDEN_CODES:
                raise _Forbidden(f"没有下载权限（HTTP {exc.code}）：{host_of(url)}") from exc
            if exc.code == 416 and done and job.total_bytes and done >= job.total_bytes:
                job.done_bytes = done
                return
            raise DownloadError(f"HTTP {exc.code}：{host_of(url)}") from exc
        except urllib.error.URLError as exc:
            raise DownloadError(f"连接失败：{getattr(exc, 'reason', exc)}（{host_of(url)}）") from exc
        with response:
            job.phase = "接收数据"
            self._notify()
            status = int(getattr(response, "status", 200) or 200)
            mode = "ab"
            if status != 206:
                mode = "wb"
                done = 0
            else:
                start = range_start(response.headers.get("Content-Range"))
                if start is not None and start != done:
                    if start > done:
                        raise DownloadError(f"服务器返回的续传起点不匹配：{host_of(url)}")
                    with part.open("r+b") as handle:
                        handle.truncate(start)
                    done = start
            length = to_int(response.headers.get("Content-Length"))
            if length:
                job.total_bytes = done + length
            write_meta(
                meta,
                {
                    **read_meta(meta),
                    "url": url,
                    "etag": response.headers.get("ETag") or "",
                    "last_modified": response.headers.get("Last-Modified") or "",
                    "total_bytes": job.total_bytes,
                },
            )
            self._read_body(job, response, part, mode, done)
        if job.total_bytes and job.done_bytes < job.total_bytes:
            raise DownloadError(f"连接提前结束：{host_of(url)}")

    def _read_body(self, job: DownloadJob, response: Any, part: Path, mode: str, done: int) -> None:
        """把响应体写进 `.part`，期间按 TICK_SECONDS 更新速度 / ETA 并回调。"""
        received = done
        window_bytes = done
        window_start = time.monotonic()
        last_tick = window_start
        with part.open(mode) as handle:
            job.done_bytes = done
            while True:
                if self._stopped(job):
                    raise _Halt()
                block = response.read(CHUNK_SIZE)
                if not block:
                    break
                handle.write(block)
                received += len(block)
                job.done_bytes = received
                now = time.monotonic()
                if now - last_tick >= TICK_SECONDS:
                    job.speed = (received - window_bytes) / max(1e-6, now - window_start)
                    window_bytes = received
                    window_start = now
                    last_tick = now
                    remaining = job.total_bytes - received
                    job.eta = remaining / job.speed if remaining > 0 and job.speed > 0 else 0.0
                    self._notify()

    def _finalize(self, job: DownloadJob, part: Path, meta: Path) -> None:
        """校验 sha256，然后原子改名到目标路径，最后回调通知。"""
        if self._stopped(job):
            self._stop(job)
            return
        job.phase = "校验中"
        job.note = ""
        self._notify()
        expected = job._sha256
        if expected and sha256_file(part) != expected:
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            prune_download_dir(job.model_id)
            job.done_bytes = 0
            job.speed = 0.0
            job.eta = 0.0
            self._fail(job, "校验失败")
            return
        target = unique_target(job.target)
        try:
            os.replace(part, target)
        except OSError as exc:
            self._fail(job, f"写入目标文件失败：{exc}")
            return
        meta.unlink(missing_ok=True)
        prune_download_dir(job.model_id)
        job.target = target
        job.state = STATE_DONE
        job.error = ""
        job.phase = ""
        job.note = ""
        job.done_bytes = target.stat().st_size
        job.total_bytes = job.total_bytes or job.done_bytes
        job.speed = 0.0
        job.eta = 0.0
        self._notify()

    # ------------------------------------------------------------- 辅助
    def _check_disk(self, folder: Path, total_bytes: int) -> None:
        if total_bytes <= 0:
            return
        required = int(total_bytes * DISK_MARGIN)
        free = _free_space(folder)
        if free >= 0 and free < required:
            raise DownloadError(f"磁盘空间不足：需要 {human_size(required)}，可用 {human_size(free)}")

    def _stopped(self, job: DownloadJob) -> bool:
        return job._cancel or job._pause or self._closing

    def _stop(self, job: DownloadJob) -> None:
        job.speed = 0.0
        job.eta = 0.0
        job.phase = ""
        job.note = ""
        job.error = ""
        if job._cancel:
            # 取消就是不要了：先清临时文件再改状态，界面看到「已取消」时文件已经没了。
            # （关停不走这里：那是暂停语义，留着半成品下次续传）
            drop_part(job.model_id, job.target)
        if job._cancel or self._closing:
            job.state = STATE_CANCELLED
        else:
            job.state = STATE_PAUSED
        self._notify()

    def _fail(self, job: DownloadJob, message: str) -> None:
        job.state = STATE_ERROR
        job.error = str(message) or "下载失败"
        job.speed = 0.0
        job.eta = 0.0
        job.phase = ""
        job.note = ""
        self._notify()

    def _backoff(self, attempt: int) -> None:
        """退避等待，但关停时立刻结束。"""
        deadline = time.monotonic() + min(1.0, 0.2 * (attempt + 1))
        while time.monotonic() < deadline and not self._closing:
            time.sleep(0.05)

    def _notify(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback()
        except Exception:
            _console.exception("下载状态回调失败")
