"""模型下载器：程序本体共享下载队列的薄壳（引擎在 core）。

下载引擎——断点续传、镜像回退、sha256 校验、原子落盘、并行与顺序调度——已经搬进程序
本体（`app.sdk.download` 背后的 core 引擎）。插件这一层只做三件事：

* 把插件的设置翻译成引擎要的东西（网络超时、代理、请求头）；
* 拿**程序本体那一个队列**（`app.sdk.download.manager()`）。于是模型下载会出现在
  「下载管理」页，统一受下载设置（并行数、镜像规则、代理）约束，用户在页面上暂停的
  任务也不会在这里又跑起来；
* 补上插件要的形状：`DownloadJob` 在 core 的任务对象外面套一层只读视图，多了
  `.model_id` 与 `.pause()/.resume()/.cancel()`，`.target` 是 `Path`。

`.part` 的规矩跟着 core：**贴着目标文件**（同卷才能原子 `os.replace`），断点信息由 core
的「下载任务」索引清单承担——旧版「每个模型一个临时目录 + `.meta.json` 边车」没有了。

插件的「GitHub 下载源」设置仍然决定候选地址，但每个候选地址最后都要过一遍全局镜像
规则（「下载管理 → 下载设置」里的规则表）：命中规则时以规则为准。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from app.sdk import download as _sdk

__all__ = [
    "DEFAULT_RETRY",
    "DEFAULT_TIMEOUT",
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "FINAL_STATES",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_PAUSED",
    "STATE_QUEUED",
    "STATE_RUNNING",
    "USER_AGENT",
    "build_opener",
    "describe_error",
    "part_path",
    "timeout_seconds",
    "to_int",
]

_logger = logging.getLogger(__name__)

# ---- core 的名字原样转出来（hub.py 与界面都按这些名字用） ----
USER_AGENT = _sdk.USER_AGENT
DownloadError = _sdk.DownloadError
STATE_QUEUED = _sdk.STATE_QUEUED
STATE_RUNNING = _sdk.STATE_RUNNING
STATE_PAUSED = _sdk.STATE_PAUSED
STATE_DONE = _sdk.STATE_DONE
STATE_ERROR = _sdk.STATE_ERROR
STATE_CANCELLED = _sdk.STATE_CANCELLED
FINAL_STATES = _sdk.FINAL_STATES
to_int = _sdk.to_int
describe_error = _sdk.describe_error

#: 设置里没配时的默认值（取自引擎）
DEFAULT_TIMEOUT = float(_sdk.DownloadOptions().timeout)
DEFAULT_RETRY = int(_sdk.DownloadOptions().retries)


def part_path(target: str | Path) -> Path:
    """半成品的位置：`<目标文件>.part`（贴着目标文件，core 的规矩）。"""
    return _sdk.part_path(target)


def timeout_seconds(settings: Any, default: float = DEFAULT_TIMEOUT) -> float:
    """取插件设置里的网络超时；没配或非法就用默认值。"""
    download = getattr(settings, "download", None)
    raw = download.get("timeout") if isinstance(download, dict) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def build_opener(settings: Any = None):
    """按插件设置构造 opener：配了代理就走代理，否则用默认（含系统代理）。"""
    proxy = str(getattr(settings, "proxy", "") or "").strip()
    return _sdk.build_opener(proxy)


class DownloadJob:
    """core 任务对象的只读视图。

    `page.py` 只读它（状态、进度、速度、目标、标签），按钮则调 `.pause()` 这几个方法；
    其余属性一律转发给 core 的任务对象。
    """

    def __init__(self, job: Any, manager: "DownloadManager", model_id: str = "") -> None:
        self._job = job
        self._manager = manager
        self.model_id = str(model_id or "")

    # ---- 常用的几个显式写出来，方便读代码与 IDE ----
    @property
    def id(self) -> str:
        return str(self._job.id)

    @property
    def target(self) -> Path:
        return Path(self._job.target)

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(self._job.urls)

    @property
    def sha256(self) -> str:
        return str(getattr(self._job, "sha256", "") or "")

    # ---- 按钮：都转成对共享队列的一次操作 ----
    def pause(self) -> bool:
        return self._manager.pause(self.id)

    def resume(self) -> bool:
        return self._manager.resume(self.id)

    def cancel(self) -> bool:
        return self._manager.cancel(self.id)

    def retry(self) -> bool:
        return self._manager.retry(self.id)

    def __getattr__(self, name: str) -> Any:
        # 只有正常属性找不到才会走到这里：剩下的都问 core 的任务对象
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._job, name)

    def __repr__(self) -> str:  # pragma: no cover - 只为调试好看
        return f"<DownloadJob {self.id} {self._job.state} {self.target.name}>"


class DownloadManager:
    """插件门面：拿程序本体的共享队列，替插件记「这条任务属于哪个模型」。

    `model_id` 不进 core 的索引项（引擎只认地址、目标、标签、sha256），所以插件自己存
    一份并落盘到 `local_root()/downloads.json`——崩溃重开后任务从索引里回来，插件还能
    按 `job.model_id` 把盘上的权重同步进模型记录。
    """

    def __init__(
        self,
        settings: Any = None,
        *,
        on_change: Any = None,
        max_workers: int | None = None,
        queue: Any = None,
    ) -> None:
        # `settings` / `on_change` / `max_workers` 是旧接口留下的位置：并行数、超时、代理
        # 现在由「下载管理 → 下载设置」统管，队列也是共享的，所以这里只收下不解释。
        self._settings = settings
        self._on_change = on_change
        self._max_workers = max_workers
        self._queue = queue if queue is not None else _sdk.manager()
        self._models: dict[str, str] = {}
        self._views: dict[str, DownloadJob] = {}
        self._load_models()

    # ---- 查询 ----
    def jobs(self) -> tuple[DownloadJob, ...]:
        return tuple(self._view(job) for job in self._queue.jobs())

    def find(self, job_id: str) -> DownloadJob | None:
        job = self._queue.find(str(job_id or ""))
        return self._view(job) if job is not None else None

    def active(self) -> int:
        """还在跑的任务**条数**（core 那边返回的是列表，界面要的是条数）。"""
        return len(self._queue.active())

    def finished(self) -> tuple[DownloadJob, ...]:
        return tuple(self._view(job) for job in self._queue.finished())

    # ---- 排队 ----
    def enqueue(
        self,
        model_id: str,
        urls: Iterable[str],
        target: str | Path,
        *,
        sha256: str = "",
        total_bytes: int = 0,
        label: str = "",
    ) -> DownloadJob:
        """排一条模型下载；地址展开与去重由引擎按全局镜像规则做。"""
        job = self._queue.enqueue(
            urls,
            target,
            sha256=sha256,
            total_bytes=total_bytes,
            # 没给标签就用文件名：队列页与崩溃恢复出来的任务都靠它显示
            label=str(label or "") or Path(target).name,
        )
        self._remember(job.id, model_id)
        return self._view(job)

    # ---- 单任务 ----
    def pause(self, job_id: str) -> bool:
        return bool(self._queue.pause(str(job_id or "")))

    def resume(self, job_id: str) -> bool:
        return bool(self._queue.resume(str(job_id or "")))

    def cancel(self, job_id: str) -> bool:
        return bool(self._queue.cancel(str(job_id or "")))

    def retry(self, job_id: str) -> bool:
        return bool(self._queue.retry(str(job_id or "")))

    def forget(self, job_id: str) -> bool:
        if not self._queue.forget(str(job_id or "")):
            return False
        self._forget_models(str(job_id))
        return True

    # ---- 批量 ----
    def pause_all(self) -> int:
        return int(self._queue.pause_all())

    def resume_all(self) -> int:
        return int(self._queue.resume_all())

    def cancel_all(self) -> int:
        return int(self._queue.cancel_all())

    def clear_finished(self) -> int:
        return int(self._queue.clear_finished())

    # ---- 断点与退场 ----
    def resume_leftovers(self) -> int:
        """把上次没做完的任务接回队列；断点由引擎读自己的任务索引清单。

        程序本体启动时已经接过一次，这里是给「插件页面晚一步打开」兜底：重复调用不会
        把同一个任务排两遍（引擎按 id 认）。
        """
        jobs = self._queue.restore()
        self._prune_models()
        return len(jobs)

    def shutdown(self, wait: float = 3.0) -> None:
        """插件退场：只让队列落盘，**不关**共享队列（那会把别人的下载也停掉）。"""
        flush = getattr(self._queue, "flush", None)
        if flush is None:
            return
        try:
            flush()
        except Exception as exc:  # noqa: BLE001 - 退场尽力而为
            _logger.debug("下载队列落盘失败：%s", exc)

    # ---- 内部：视图缓存与 model_id 记录 ----
    def _view(self, job: Any) -> DownloadJob:
        key = str(job.id)
        view = self._views.get(key)
        if view is None:
            view = DownloadJob(job, self, self._models.get(key, ""))
            self._views[key] = view
        return view

    def _remember(self, job_id: str, model_id: str) -> None:
        key, text = str(job_id), str(model_id or "")
        if self._models.get(key) == text:
            return
        self._models[key] = text
        view = self._views.get(key)
        if view is not None:
            view.model_id = text
        self._save_models()

    def _forget_models(self, job_id: str) -> None:
        self._views.pop(str(job_id), None)
        if self._models.pop(str(job_id), None) is not None:
            self._save_models()

    @staticmethod
    def _models_file() -> Path | None:
        try:
            from ..paths import local_root

            return Path(local_root()) / "downloads.json"
        except Exception:  # noqa: BLE001 - 落盘不了就当没有这份记录
            return None

    def _load_models(self) -> None:
        path = self._models_file()
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _logger.debug("下载记录读不出来：%s", exc)
            return
        if isinstance(data, dict):
            self._models = {str(key): str(value) for key, value in data.items()}

    def _save_models(self) -> None:
        path = self._models_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._models, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            _logger.debug("下载记录落盘失败：%s", exc)

    def _prune_models(self) -> None:
        """索引里已经没有的任务不再占位（在 `resume_leftovers()` 之后清理最安全）。"""
        alive = {str(job.id) for job in self._queue.jobs()}
        stale = [key for key in self._models if key not in alive]
        if not stale:
            return
        for key in stale:
            self._models.pop(key, None)
        self._save_models()
