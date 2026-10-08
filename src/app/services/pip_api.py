"""程序本体的 pip 接口实现：插件通过扩展接口 `pip.install` 共用 core 的通用安装器。

设计要点：

- **只做两件事**：替用户问一句（确认框）、把安装放进后台线程（`core.pip.tasks`）。真正
  怎么建 venv、怎么跑 pip、怎么在 GitHub 直链上重试，全在 `app.core.pip` 里，插件不必
  也不能自己抄一份 `pip install`；
- **不对用户开放**：程序本体没有 pip 安装页，界面由调用它的插件自己出（下载器那套
  `DownloadListView` 风格的视图工具在 UI 工具库里）；
- **红线**：`install()` 默认先弹确认框（装什么、装到哪个解释器都写清楚），用户同意才跑；
  `confirm=False` 只有调用方自己已经问过用户时才该用。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from ..core.pip import KIND_WHEELS, PipTask, PipTasks
from ..sdk import pip as pip_api

__all__ = ["PipApi", "api", "shutdown", "to_ref"]

#: 程序本体共用的那一个（界面 / 插件都走它，于是任务表只有一份）
_DEFAULT: "PipApi | None" = None


def api() -> "PipApi":
    """取程序本体共用的 pip 接口实例（没有就建一个）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PipApi()
    return _DEFAULT


def shutdown(wait: float | None = 3.0) -> None:
    """退出前收工：取消还在跑的安装（没建过就什么也不做）。"""
    if _DEFAULT is not None:
        _DEFAULT.shutdown(wait)


def _clean(values: Iterable[str] | str | None) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    items: list[str] = []
    for raw in values or ():
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text not in items:
            items.append(text)
    return tuple(items)


def to_ref(task: PipTask) -> pip_api.PipRef:
    """把 core 的安装任务转成插件能看的只读快照。"""
    return pip_api.PipRef(
        id=str(task.id),
        title=str(task.title or ""),
        kind=str(task.kind),
        state=str(task.state),
        state_label=str(task.state_label),
        error=str(task.error or ""),
        log=str(task.log or ""),
        python=str(task.python or ""),
        result=str(task.result or ""),
        tail=str(task.tail or ""),
    )


class PipApi:
    """`pip.install` 的实现。"""

    def __init__(self, *, on_change: Callable[[], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._tasks: PipTasks | None = None
        self._on_change = on_change

    # ------------------------------------------------------------------ 任务表
    def tasks(self) -> PipTasks:
        """安装任务表（没有就建一个）。"""
        with self._lock:
            if self._tasks is None:
                self._tasks = PipTasks(on_change=self._on_change, prefix="pip")
            return self._tasks

    def current_tasks(self) -> PipTasks | None:
        """已经建好的任务表；没建过返回 None（界面刷新用这个，别凭空建）。"""
        with self._lock:
            return self._tasks

    def is_running(self) -> bool:
        """安装任务表是否已经建起来（和下载队列的 `is_running()` 一个意思）。

        想判断「有没有任务在跑」用 `current_tasks().active()`：暂停中的任务算没在跑，但任务表
        还在那儿。
        """
        return self.current_tasks() is not None

    # ------------------------------------------------------------------ 安装
    def install(
        self,
        python: str | Path,
        *,
        log: str | Path,
        packages: Iterable[str] = (),
        kind: str = "packages",
        title: str = "",
        requirements: str | Path | None = None,
        wheels: Iterable[str] = (),
        venv: str | Path | None = None,
        base_python: str | Path | None = None,
        index_url: str = "",
        extra_index: Iterable[str] = (),
        urls: Iterable[str] = (),
        upgrade: bool = False,
        github_prefixes: Iterable[str] = (),
        on_line: Callable[[str], None] | None = None,
        confirm: bool = True,
        parent: Any = None,
        message: str = "",
    ) -> pip_api.PipRef | None:
        """发起一次安装（后台跑）；用户不同意返回 `None`。"""
        target = Path(python)
        items = _clean(packages)
        wheel_list = _clean(wheels)
        if confirm and not self._ask(
            target,
            items=items,
            wheels=wheel_list,
            kind=kind,
            title=title,
            message=message,
            parent=parent,
        ):
            return None
        task = self.tasks().submit(
            python=target,
            log=log,
            packages=items,
            kind=kind,
            title=title,
            requirements=requirements,
            wheels=wheel_list,
            venv=venv,
            base_python=base_python,
            index_url=index_url,
            extra_index=extra_index,
            urls=urls,
            upgrade=upgrade,
            github_prefixes=github_prefixes,
            on_line=on_line,
        )
        return to_ref(task)

    # ------------------------------------------------------------------ 查询
    def jobs(self) -> tuple[pip_api.PipRef, ...]:
        tasks = self.current_tasks()
        if tasks is None:
            return ()
        return tuple(to_ref(task) for task in tasks.jobs())

    def find(self, task_id: str) -> pip_api.PipRef | None:
        tasks = self.current_tasks()
        if tasks is None:
            return None
        task = tasks.find(str(task_id or ""))
        return to_ref(task) if task is not None else None

    def wait(self, task_id: str, timeout: float | None = None) -> bool:
        tasks = self.current_tasks()
        if tasks is None:
            return False
        return bool(tasks.wait(str(task_id or ""), timeout))

    # ------------------------------------------------------------------ 控制
    def pause(self, task_id: str) -> bool:
        tasks = self.current_tasks()
        return bool(tasks is not None and tasks.pause(str(task_id or "")))

    def resume(self, task_id: str) -> bool:
        tasks = self.current_tasks()
        return bool(tasks is not None and tasks.resume(str(task_id or "")))

    def cancel(self, task_id: str) -> bool:
        tasks = self.current_tasks()
        return bool(tasks is not None and tasks.cancel(str(task_id or "")))

    def forget(self, task_id: str) -> bool:
        tasks = self.current_tasks()
        return bool(tasks is not None and tasks.forget(str(task_id or "")))

    def clear_finished(self) -> int:
        tasks = self.current_tasks()
        return int(tasks.clear_finished()) if tasks is not None else 0

    def shutdown(self, wait: float | None = 3.0) -> None:
        """退出前收工：把还在跑的安装取消掉。"""
        tasks = self.current_tasks()
        if tasks is not None:
            tasks.shutdown(wait)

    # ------------------------------------------------------------------ 内部
    def _ask(
        self,
        python: Path,
        *,
        items: tuple[str, ...],
        wheels: tuple[str, ...],
        kind: str,
        title: str,
        message: str,
        parent: Any,
    ) -> bool:
        """问用户同不同意这一次安装。"""
        from ..ui.framework import confirm

        lines = [f"目标解释器：{python}"]
        if kind == KIND_WHEELS:
            lines.append("本地轮子：" + ("、".join(Path(item).name for item in wheels) if wheels else "（无）"))
            if items:
                lines.append("另外补装：" + "、".join(items))
        else:
            lines.append("要装的依赖：" + ("、".join(items) if items else "（无）"))
        body = str(message or "有插件请求安装依赖，是否继续？")
        return bool(confirm(parent, str(title or "安装依赖"), body + "\n\n" + "\n".join(lines)))
