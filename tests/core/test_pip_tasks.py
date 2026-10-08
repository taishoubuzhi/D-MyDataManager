"""core pip 任务层（`app.core.pip.tasks`）的用例。

**不碰网络**：安装本体的 `ensure` / `install_wheels` 全被换成假的，重点看的是任务层自己的
行为——后台线程、状态流转、暂停 / 继续 / 取消 / 清理，以及「回调炸了不许拖垮安装」。
只有一条用例真的调了 `ensure`，而且喂的是空包清单（引擎在这种情况下不跑 pip）。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core.pip import tasks as tasks_module
from app.core.pip.engine import PipError, PipStopped
from app.core.pip.tasks import (
    FINAL_STATES,
    KIND_PACKAGES,
    KIND_WHEELS,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_ERROR,
    STATE_PAUSED,
    STATE_PENDING,
    STATE_RUNNING,
    STATE_LABELS,
    PipTask,
    PipTasks,
)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    """轮询等一个条件成立（任务跑在别的线程里，只能这样等）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class TaskCase(unittest.TestCase):
    """任务层的公共脚手架：临时日志目录 + 收尾时逐个收工。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dm_pip_tasks_")
        self.tmp = Path(self._tmp.name)
        self.log = self.tmp / "pip.log"
        self.managers: list[PipTasks] = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.shutdown(wait=2.0)
        self._tmp.cleanup()

    def tasks(self, **kwargs) -> PipTasks:
        of = PipTasks(**kwargs)
        self.managers.append(of)
        return of

    def submit(self, manager: PipTasks, **overrides):
        options = {
            "python": Path(sys.executable),
            "log": self.log,
            "packages": (),
        }
        options.update(overrides)
        return manager.submit(**options)


class SubmitTests(TaskCase):
    """发起、等待与状态标签。"""

    def test_empty_packages_finish_without_running_pip(self) -> None:
        manager = self.tasks()
        task = self.submit(manager, title="自检")
        self.assertTrue(task.wait(10.0), f"任务没跑完：{task.state}")
        self.assertEqual(task.state, STATE_DONE)
        self.assertEqual(task.result, Path(sys.executable))
        self.assertTrue(task.finished)
        self.assertEqual(task.title, "自检")
        self.assertEqual(task.state_label, "已完成")

    def test_title_defaults_to_first_package_then_kind(self) -> None:
        manager = self.tasks()
        with mock.patch.object(tasks_module, "ensure", lambda *a, **k: Path(sys.executable)):
            first = self.submit(manager, packages=("numpy>=2", "pillow"), title="")
            second = self.submit(manager, packages=(), title="", kind=KIND_WHEELS)
        first.wait(5.0)
        second.wait(5.0)
        self.assertEqual(first.title, "numpy>=2")
        self.assertEqual(second.title, KIND_WHEELS)
        self.assertEqual(second.kind, KIND_WHEELS)

    def test_wait_returns_false_while_running(self) -> None:
        manager = self.tasks()
        release = threading.Event()
        started = threading.Event()

        def slow(*args, **kwargs):
            started.set()
            release.wait(5.0)
            return Path(sys.executable)

        with mock.patch.object(tasks_module, "ensure", slow):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(started.wait(5.0))
            self.assertFalse(manager.wait(task.id, timeout=0.1))
            self.assertEqual(task.state, STATE_RUNNING)
            release.set()
            self.assertTrue(manager.wait(task.id, timeout=5.0))

    def test_wait_and_find_unknown(self) -> None:
        manager = self.tasks()
        self.assertIsNone(manager.find(""))
        self.assertIsNone(manager.find("没这个"))
        self.assertFalse(manager.wait("没这个", timeout=0.1))
        self.assertEqual(manager.jobs(), ())
        self.assertEqual(manager.active(), ())
        self.assertEqual(manager.finished(), ())

    def test_state_labels_cover_every_state(self) -> None:
        for state in (STATE_PENDING, STATE_RUNNING, STATE_PAUSED, *FINAL_STATES):
            self.assertIn(state, STATE_LABELS)
        self.assertEqual(PipTask(id="x", state="别的").state_label, "别的")


class ControlTests(TaskCase):
    """暂停 / 继续 / 取消。"""

    def test_pause_then_resume_runs_again(self) -> None:
        manager = self.tasks()
        runs: list[int] = []
        started = threading.Event()

        def work(python, *, control=None, on_line=None, **kwargs):
            runs.append(1)
            started.set()
            while not control.should_pause():
                if control.should_cancel():
                    raise PipStopped("已取消")
                time.sleep(0.01)
            raise PipStopped("安装已暂停", paused=True)

        with mock.patch.object(tasks_module, "ensure", work):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(started.wait(5.0))
            self.assertTrue(manager.pause(task.id))
            self.assertTrue(wait_until(lambda: task.state == STATE_PAUSED), f"状态是 {task.state}")
            self.assertTrue(task.paused)
            self.assertFalse(task.finished)
            # 只有暂停中的任务能「继续」，重复暂停无效
            self.assertFalse(manager.pause(task.id))
            self.assertTrue(manager.resume(task.id))
            self.assertTrue(wait_until(lambda: len(runs) == 2), f"只跑了 {len(runs)} 次")
            self.assertTrue(wait_until(lambda: task.state == STATE_RUNNING))
            self.assertEqual(len(runs), 2)
            # 第二次又跑起来了，收尾时取消掉
            self.assertTrue(manager.cancel(task.id))
            self.assertTrue(wait_until(lambda: task.state == STATE_CANCELLED))

    def test_cancel_running_task(self) -> None:
        manager = self.tasks()
        started = threading.Event()

        def work(python, *, control=None, on_line=None, **kwargs):
            started.set()
            while not control.should_cancel():
                time.sleep(0.01)
            raise PipStopped("已取消")

        with mock.patch.object(tasks_module, "ensure", work):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(started.wait(5.0))
            self.assertTrue(manager.cancel(task.id))
            self.assertTrue(wait_until(lambda: task.state == STATE_CANCELLED), f"状态是 {task.state}")
            self.assertEqual(task.error, "已取消")

    def test_cancel_pending_task_settles_immediately(self) -> None:
        manager = self.tasks()
        manager.shutdown(wait=0.0)  # `_closing` 之后 submit 不再开线程，任务停在排队中
        task = self.submit(manager, packages=("numpy",))
        self.assertEqual(task.state, STATE_PENDING)
        self.assertTrue(manager.cancel(task.id))
        self.assertEqual(task.state, STATE_CANCELLED)
        self.assertTrue(task.finished)
        self.assertFalse(manager.cancel(task.id))

    def test_control_on_unknown_or_finished(self) -> None:
        manager = self.tasks()
        task = self.submit(manager, title="自检")
        task.wait(10.0)
        self.assertFalse(manager.pause(task.id))
        self.assertFalse(manager.resume(task.id))
        self.assertFalse(manager.cancel(task.id))
        self.assertFalse(manager.pause("没这个"))
        self.assertFalse(manager.resume("没这个"))
        self.assertFalse(manager.cancel("没这个"))
        self.assertFalse(manager.forget("没这个"))


class FailureTests(TaskCase):
    """失败路径：别让线程静默死掉，也别把意外异常当成正常结束。"""

    def test_pip_error_lands_in_error_state(self) -> None:
        manager = self.tasks()

        def boom(*args, **kwargs):
            raise PipError("装不上")

        with mock.patch.object(tasks_module, "ensure", boom):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(task.wait(5.0))
        self.assertEqual(task.state, STATE_ERROR)
        self.assertEqual(task.error, "装不上")
        self.assertIn(task.state, FINAL_STATES)

    def test_unexpected_exception_is_contained(self) -> None:
        manager = self.tasks()

        def boom(*args, **kwargs):
            raise ValueError("谁也没想到")

        with mock.patch.object(tasks_module, "ensure", boom):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(task.wait(5.0))
        self.assertEqual(task.state, STATE_ERROR)
        self.assertIn("谁也没想到", task.error)

    def test_missing_work_is_reported(self) -> None:
        manager = self.tasks()
        task = PipTask(id="pip-x")
        manager._tasks.append(task)
        manager._run(task)
        self.assertEqual(task.state, STATE_ERROR)
        self.assertIn("没有可执行的内容", task.error)


class OutputTests(TaskCase):
    """输出留档、回调、清理与退出。"""

    def test_lines_are_kept_and_capped(self) -> None:
        manager = self.tasks(keep_lines=2)
        seen: list[str] = []

        def work(python, *, on_line=None, **kwargs):
            for index in range(4):
                on_line(f"第 {index} 行")
            return Path(sys.executable)

        with mock.patch.object(tasks_module, "ensure", work):
            task = self.submit(
                manager,
                packages=("numpy",),
                on_line=seen.append,
            )
            self.assertTrue(task.wait(5.0))
        self.assertEqual(task.state, STATE_DONE)
        self.assertEqual(seen, ["第 0 行", "第 1 行", "第 2 行", "第 3 行"])
        self.assertEqual(task.lines, ["第 2 行", "第 3 行"])
        self.assertEqual(task.tail, "第 2 行\n第 3 行")

    def test_broken_on_line_callback_does_not_kill_the_install(self) -> None:
        manager = self.tasks()

        def work(python, *, on_line=None, **kwargs):
            on_line("一行输出")
            return Path(sys.executable)

        def boom(text):
            raise RuntimeError("回调炸了")

        with mock.patch.object(tasks_module, "ensure", work):
            task = self.submit(manager, packages=("numpy",), on_line=boom)
            self.assertTrue(task.wait(5.0))
        self.assertEqual(task.state, STATE_DONE)
        self.assertEqual(task.lines, ["一行输出"])

    def test_on_change_is_called_and_never_breaks_the_task(self) -> None:
        calls: list[int] = []

        def broken() -> None:
            calls.append(1)
            raise RuntimeError("通知炸了")

        manager = self.tasks(on_change=broken)
        task = self.submit(manager, title="自检")
        self.assertTrue(task.wait(10.0))
        self.assertEqual(task.state, STATE_DONE)
        self.assertGreaterEqual(len(calls), 2)

    def test_forget_and_clear_finished(self) -> None:
        manager = self.tasks()
        with mock.patch.object(tasks_module, "ensure", lambda *a, **k: Path(sys.executable)):
            first = self.submit(manager, packages=("a",))
            second = self.submit(manager, packages=("b",))
        first.wait(5.0)
        second.wait(5.0)
        self.assertEqual(len(manager.finished()), 2)
        self.assertTrue(manager.forget(first.id))
        self.assertIsNone(manager.find(first.id))
        self.assertEqual(manager.clear_finished(), 1)
        self.assertEqual(manager.jobs(), ())

    def test_shutdown_cancels_what_is_still_running(self) -> None:
        manager = self.tasks()
        started = threading.Event()

        def work(python, *, control=None, on_line=None, **kwargs):
            started.set()
            while not control.should_cancel():
                time.sleep(0.01)
            raise PipStopped("已取消")

        with mock.patch.object(tasks_module, "ensure", work):
            task = self.submit(manager, packages=("numpy",))
            self.assertTrue(started.wait(5.0))
            manager.shutdown(wait=5.0)
        self.assertEqual(task.state, STATE_CANCELLED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
