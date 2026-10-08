"""core pip 的任务层：把一次安装放进后台线程，允许暂停 / 继续 / 取消 / 查询。

引擎（`engine`）是同步的；程序本体与插件都需要「发起一次安装、看着它跑、随时叫停」，
这一层补的就是这个。它不碰 Qt，也不猜运行环境放在哪儿 —— 解释器、venv、requirements、
日志路径一律由调用方给；问用户同不同意装，是 services 层的事。

约定：

* 一个任务 = 一次 `ensure`（装包）或 `install_wheels`（装本地轮子），在内存里，程序退出
  即丢。安装本身是幂等的（pip 会跳过已装好的版本），重来一次没有副作用；
* `pause()` 会真的把 pip 子进程停掉（已经下好的轮子留在 pip 缓存里），`resume()` 从头再跑
  一遍那次安装；`cancel()` 停掉且不再重跑；
* 暂停 / 取消靠 `Control` 与子进程说话，所以 pip 闷头下载、一行不输出时也停得下来。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .engine import Control, PipError, PipStopped, ensure, install_wheels

__all__ = [
    "FINAL_STATES",
    "KIND_PACKAGES",
    "KIND_WHEELS",
    "PipTask",
    "PipTasks",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_LABELS",
    "STATE_PAUSED",
    "STATE_PENDING",
    "STATE_RUNNING",
]

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

FINAL_STATES = frozenset({STATE_DONE, STATE_ERROR, STATE_CANCELLED})

STATE_LABELS = {
    STATE_PENDING: "排队中",
    STATE_RUNNING: "安装中",
    STATE_PAUSED: "已暂停",
    STATE_DONE: "已完成",
    STATE_ERROR: "失败",
    STATE_CANCELLED: "已取消",
}

KIND_PACKAGES = "packages"
KIND_WHEELS = "wheels"

#: 每个任务最多留多少行输出（给界面看最近发生了什么，不当作日志用 —— 完整日志在 log 文件里）
KEEP_LINES = 200


def _clean(values: Iterable[Any] | str | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    items: list[str] = []
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text not in items:
            items.append(text)
    return tuple(items)


@dataclass
class PipTask:
    """一次安装任务。字段可以直接读，改它没有意义（要改走 `PipTasks` 的方法）。"""

    id: str
    title: str = ""
    kind: str = KIND_PACKAGES
    state: str = STATE_PENDING
    error: str = ""
    log: Path | None = None
    python: Path | None = None
    result: Path | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    lines: list[str] = field(default_factory=list)
    control: Control = field(default_factory=Control)
    work: Callable[[Control, Callable[[str], None]], Path] | None = None
    thread: threading.Thread | None = None
    on_line: Callable[[str], None] | None = None

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def finished(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def paused(self) -> bool:
        return self.state == STATE_PAUSED

    @property
    def running(self) -> bool:
        return self.state == STATE_RUNNING

    @property
    def tail(self) -> str:
        """最近几行输出（换行拼好，给界面显示用）。"""
        return "\n".join(self.lines)

    def wait(self, timeout: float | None = None) -> bool:
        """等这个任务跑完；到点还没完返回 False。"""
        thread = self.thread
        if thread is None:
            return self.finished
        thread.join(timeout)
        return not thread.is_alive()


class PipTasks:
    """在内存里管一批安装任务（后台线程 + 暂停 / 取消）。"""

    def __init__(
        self,
        *,
        on_change: Callable[[], None] | None = None,
        prefix: str = "pip",
        keep_lines: int = KEEP_LINES,
    ) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._tasks: list[PipTask] = []
        self._on_change = on_change
        self._prefix = prefix
        self._keep_lines = max(0, int(keep_lines))
        self._closing = False

    # ---------------------------------------------------------------- 查询

    def jobs(self) -> tuple[PipTask, ...]:
        """按发起顺序返回全部任务。"""
        with self._lock:
            return tuple(self._tasks)

    def find(self, task_id: str) -> PipTask | None:
        key = str(task_id or "")
        if not key:
            return None
        with self._lock:
            for task in self._tasks:
                if task.id == key:
                    return task
        return None

    def active(self) -> tuple[PipTask, ...]:
        with self._lock:
            return tuple(task for task in self._tasks if not task.finished)

    def finished(self) -> tuple[PipTask, ...]:
        with self._lock:
            return tuple(task for task in self._tasks if task.finished)

    def wait(self, task_id: str, timeout: float | None = None) -> bool:
        """等某个任务进入终态；找不到返回 False。"""
        task = self.find(task_id)
        if task is None:
            return False
        if task.finished:
            return True
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cond:
            while not task.finished:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return task.finished
                self._cond.wait(remaining if remaining is not None else 0.2)
        return True

    # ---------------------------------------------------------------- 发起

    def submit(
        self,
        *,
        python: Path | str,
        log: Path | str,
        packages: Iterable[str] = (),
        kind: str = KIND_PACKAGES,
        title: str = "",
        requirements: Path | str | None = None,
        wheels: Iterable[str] = (),
        venv: Path | str | None = None,
        base_python: Path | str | None = None,
        index_url: str = "",
        extra_index: Iterable[str] = (),
        urls: Iterable[str] = (),
        upgrade: bool = False,
        github_prefixes: Iterable[str] = (),
        on_line: Callable[[str], None] | None = None,
    ) -> PipTask:
        """登记并立刻开跑一个安装任务（后台线程）。"""
        target = Path(python)
        log_path = Path(log)
        items = _clean(packages)
        wheel_list = _clean(wheels)
        extra = _clean(extra_index)
        prefixes = _clean(github_prefixes)
        direct = _clean(urls)
        requirements_path = Path(requirements) if requirements else None
        venv_path = Path(venv) if venv else None
        base = Path(base_python) if base_python else None

        def work(control: Control, emit: Callable[[str], None]) -> Path:
            if kind == KIND_WHEELS:
                return install_wheels(
                    target,
                    wheel_list,
                    packages=items,
                    log=log_path,
                    venv=venv_path,
                    base_python=base,
                    index_url=index_url,
                    extra_index=extra,
                    upgrade=upgrade,
                    github_prefixes=prefixes,
                    on_line=emit,
                    control=control,
                )
            return ensure(
                target,
                packages=items,
                log=log_path,
                requirements=requirements_path,
                venv=venv_path,
                base_python=base,
                index_url=index_url,
                extra_index=extra,
                urls=direct,
                upgrade=upgrade,
                github_prefixes=prefixes,
                on_line=emit,
                control=control,
            )

        task = PipTask(
            id=f"{self._prefix}-{uuid.uuid4().hex[:8]}",
            title=str(title or "").strip() or (items[0] if items else kind),
            kind=kind,
            log=log_path,
            python=target,
        )
        task.work = work
        task.on_line = on_line
        with self._lock:
            self._tasks.append(task)
            if not self._closing:
                self._spawn(task)
        self._notify()
        return task

    # ---------------------------------------------------------------- 控制

    def pause(self, task_id: str) -> bool:
        """暂停：真的把 pip 子进程停掉。只对正在跑的管用。"""
        with self._lock:
            task = self.find(task_id)
            if task is None or task.state != STATE_RUNNING:
                return False
            task.control.pause()
        return True

    def resume(self, task_id: str) -> bool:
        """继续：按原来的参数重跑那次安装。只对已暂停的管用。"""
        with self._lock:
            task = self.find(task_id)
            if task is None or task.state != STATE_PAUSED or self._closing:
                return False
            self._spawn(task)
        return True

    def cancel(self, task_id: str) -> bool:
        """取消：停掉子进程且不再重跑。已经结束的返回 False。"""
        with self._lock:
            task = self.find(task_id)
            if task is None or task.finished:
                return False
            task.control.cancel()
            if task.state in (STATE_PENDING, STATE_PAUSED):
                # 还没真正开跑（排队中）或已经停下（暂停中）的，当场结账
                task.state = STATE_CANCELLED
                task.finished_at = time.time()
                self._cond.notify_all()
                changed = True
            else:
                changed = False
        if changed:
            self._notify()
        return True

    def forget(self, task_id: str) -> bool:
        """从列表里删掉一个已经结束的任务。"""
        with self._lock:
            task = self.find(task_id)
            if task is None or not task.finished:
                return False
            self._tasks.remove(task)
        self._notify()
        return True

    def clear_finished(self) -> int:
        with self._lock:
            remaining = [task for task in self._tasks if not task.finished]
            removed = len(self._tasks) - len(remaining)
            self._tasks = remaining
        if removed:
            self._notify()
        return removed

    def shutdown(self, wait: float | None = 3.0) -> None:
        """退出前收工：把还在跑的全取消，最多等 `wait` 秒。"""
        with self._lock:
            self._closing = True
            pending = [task for task in self._tasks if not task.finished]
        for task in pending:
            self.cancel(task.id)
        deadline = None if wait is None else time.monotonic() + max(0.0, wait)
        for task in pending:
            thread = task.thread
            if thread is None:
                continue
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            thread.join(remaining)

    # ---------------------------------------------------------------- 内部

    def _spawn(self, task: PipTask) -> None:
        task.control.reset()
        task.error = ""
        task.result = None
        task.state = STATE_RUNNING
        task.started_at = time.time()
        task.finished_at = 0.0
        thread = threading.Thread(
            target=self._run,
            args=(task,),
            name=f"pip-task-{task.id}",
            daemon=True,
        )
        task.thread = thread
        thread.start()

    def _emit(self, task: PipTask, message: str) -> None:
        text = str(message)
        with self._lock:
            if self._keep_lines:
                task.lines.append(text)
                if len(task.lines) > self._keep_lines:
                    del task.lines[: len(task.lines) - self._keep_lines]
        handler = task.on_line
        if handler is not None:
            try:
                handler(text)
            except Exception:  # pragma: no cover - 回调自己炸了不该拖垮安装
                pass

    def _run(self, task: PipTask) -> None:
        work = task.work
        try:
            if work is None:
                raise PipError("这个安装任务没有可执行的内容")
            result = work(task.control, lambda text: self._emit(task, text))
        except PipStopped as stopped:
            with self._lock:
                if stopped.paused:
                    task.state = STATE_PAUSED
                else:
                    task.state = STATE_CANCELLED
                    task.error = str(stopped)
        except PipError as exc:
            with self._lock:
                task.state = STATE_ERROR
                task.error = str(exc)
        except Exception as exc:  # pragma: no cover - 兜底，别让线程静默死掉
            with self._lock:
                task.state = STATE_ERROR
                task.error = f"安装过程出错：{exc}"
        else:
            with self._lock:
                task.result = Path(result)
                task.state = STATE_DONE
        finally:
            with self._lock:
                task.finished_at = time.time()
                task.thread = threading.current_thread()
                self._cond.notify_all()
            self._notify()

    def _notify(self) -> None:
        handler = self._on_change
        if handler is None:
            return
        try:
            handler()
        except Exception:  # pragma: no cover - 通知失败不影响安装
            pass
