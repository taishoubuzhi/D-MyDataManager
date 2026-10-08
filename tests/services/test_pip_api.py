"""pip 接口（`app.sdk.pip` + `app.services.pip_api`）的用例。

**不碰网络**：安装本体被换成假的（或者喂空包清单，引擎在这种情况下不跑 pip），重点看的是
接口层——任务快照、确认框的取舍、共用一份任务表、以及每条控制指令的返回值。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core.pip import KIND_WHEELS, PipTask, PipTasks
from app.core.pip import tasks as tasks_module
from app.core.pip.engine import PipStopped
from app.services import pip_api
from app.services.pip_api import PipApi, _clean, to_ref

from tests.harness import IsolatedCase


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class PureTests(unittest.TestCase):
    """不碰任务表的纯函数。"""

    def test_clean_removes_blank_and_duplicates(self) -> None:
        self.assertEqual(_clean([" a ", "b", "a", "", None]), ("a", "b"))
        self.assertEqual(_clean("numpy"), ("numpy",))
        self.assertEqual(_clean(None), ())

    def test_to_ref_maps_every_field(self) -> None:
        task = PipTask(
            id="pip-1",
            title="装 numpy",
            kind=KIND_WHEELS,
            log=Path("logs/pip.log"),
            python=Path("env/python.exe"),
            result=Path("env/python.exe"),
            lines=["第一行", "第二行"],
        )
        ref = to_ref(task)
        self.assertEqual(ref.id, "pip-1")
        self.assertEqual(ref.title, "装 numpy")
        self.assertEqual(ref.kind, KIND_WHEELS)
        self.assertEqual(ref.state, task.state)
        self.assertEqual(ref.state_label, task.state_label)
        self.assertEqual(ref.log, str(Path("logs/pip.log")))
        self.assertEqual(ref.python, str(Path("env/python.exe")))
        self.assertEqual(ref.result, str(Path("env/python.exe")))
        self.assertEqual(ref.tail, "第一行\n第二行")
        self.assertFalse(ref.finished)

    def test_module_singleton_is_stable(self) -> None:
        self.assertIs(pip_api.api(), pip_api.api())
        pip_api.shutdown(wait=0.0)  # 没建过任务表也要能收工


class ApiCase(IsolatedCase):
    """接口层：任务表懒建、安装、确认框、控制指令。"""

    def setUp(self) -> None:
        super().setUp()
        self.api = PipApi()
        self.log = self.log_dir() / "pip.log"

    def tearDown(self) -> None:
        self.api.shutdown(wait=2.0)
        super().tearDown()

    def log_dir(self) -> Path:
        path = Path(type(self).root) / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------- 懒建
    def test_nothing_is_built_until_it_is_used(self) -> None:
        self.assertIsNone(self.api.current_tasks())
        self.assertFalse(self.api.is_running())
        self.assertEqual(self.api.jobs(), ())
        self.assertIsNone(self.api.find("pip-1"))
        self.assertFalse(self.api.wait("pip-1", timeout=0.1))
        self.assertFalse(self.api.pause("pip-1"))
        self.assertFalse(self.api.resume("pip-1"))
        self.assertFalse(self.api.cancel("pip-1"))
        self.assertFalse(self.api.forget("pip-1"))
        self.assertEqual(self.api.clear_finished(), 0)
        self.assertIsNone(self.api.current_tasks())

    def test_tasks_is_built_once(self) -> None:
        first = self.api.tasks()
        self.assertIs(self.api.tasks(), first)
        self.assertIsInstance(first, PipTasks)
        self.assertIs(self.api.current_tasks(), first)

    # ------------------------------------------------------------- 安装
    def test_install_empty_packages_finishes_without_pip(self) -> None:
        ref = self.api.install(
            sys.executable,
            log=self.log,
            packages=(),
            confirm=False,
            title="自检",
        )
        self.assertIsNotNone(ref)
        self.assertEqual(ref.title, "自检")
        self.assertTrue(self.api.wait(ref.id, timeout=10.0))
        done = self.api.find(ref.id)
        self.assertEqual(done.state, "done")
        self.assertEqual(done.state_label, "已完成")
        self.assertEqual(done.python, str(Path(sys.executable)))

    def test_install_reports_failure(self) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("装不上")

        with mock.patch.object(tasks_module, "ensure", boom):
            ref = self.api.install(sys.executable, log=self.log, packages=("numpy",), confirm=False)
            self.assertTrue(self.api.wait(ref.id, timeout=10.0))
        failed = self.api.find(ref.id)
        self.assertEqual(failed.state, "error")
        self.assertIn("装不上", failed.error)

    def test_users_refusal_creates_no_task(self) -> None:
        with mock.patch.object(PipApi, "_ask", lambda *a, **k: False):
            self.assertIsNone(
                self.api.install(sys.executable, log=self.log, packages=("numpy",), confirm=True)
            )
        self.assertIsNone(self.api.current_tasks())

    def test_user_agreement_then_runs(self) -> None:
        with mock.patch.object(PipApi, "_ask", lambda *a, **k: True):
            with mock.patch.object(tasks_module, "ensure", lambda *a, **k: Path(sys.executable)):
                ref = self.api.install(sys.executable, log=self.log, packages=("numpy",), title="装 numpy")
        self.assertTrue(self.api.wait(ref.id, timeout=10.0))
        self.assertEqual(self.api.find(ref.id).state, "done")

    def test_ask_describes_what_and_where(self) -> None:
        seen: list[tuple] = []

        def fake_confirm(parent, title, content):
            seen.append((parent, title, content))
            return True

        with mock.patch("app.ui.framework.confirm", fake_confirm):
            with mock.patch.object(tasks_module, "ensure", lambda *a, **k: Path(sys.executable)):
                self.api.install(
                    sys.executable,
                    log=self.log,
                    packages=("numpy>=2", "pillow"),
                    title="装依赖",
                    message="插件要装东西",
                )
                self.api.install(
                    sys.executable,
                    log=self.log,
                    kind=KIND_WHEELS,
                    wheels=("C:/tmp/a-1.0-py3-none-any.whl",),
                    packages=("pillow",),
                )
        self.api.shutdown(wait=5.0)
        self.assertEqual(len(seen), 2)
        parent, title, content = seen[0]
        self.assertIsNone(parent)
        self.assertEqual(title, "装依赖")
        self.assertIn("插件要装东西", content)
        self.assertIn("目标解释器", content)
        self.assertIn("numpy>=2", content)
        self.assertIn("pillow", content)
        self.assertIn("a-1.0-py3-none-any.whl", seen[1][2])
        self.assertIn("另外补装：pillow", seen[1][2])

    # ------------------------------------------------------------- 控制
    def test_pause_resume_cancel_a_running_install(self) -> None:
        started = threading.Event()

        def slow(python, *, control=None, on_line=None, **kwargs):
            started.set()
            while not control.should_pause():
                if control.should_cancel():
                    raise PipStopped("已取消")
                time.sleep(0.01)
            raise PipStopped("安装已暂停", paused=True)

        with mock.patch.object(tasks_module, "ensure", slow):
            ref = self.api.install(sys.executable, log=self.log, packages=("numpy",), confirm=False)
            self.assertTrue(started.wait(5.0))
            self.assertEqual(self.api.find(ref.id).state, "running")
            self.assertTrue(self.api.pause(ref.id))
            self.assertTrue(wait_until(lambda: self.api.find(ref.id).state == "paused"))
            self.assertTrue(self.api.is_running())
            self.assertTrue(self.api.resume(ref.id))
            self.assertTrue(wait_until(lambda: self.api.current_tasks().active()))
            self.assertTrue(self.api.cancel(ref.id))
            self.assertTrue(wait_until(lambda: self.api.find(ref.id).state == "cancelled"))

    def test_forget_only_finished(self) -> None:
        ref = self.api.install(sys.executable, log=self.log, packages=(), confirm=False)
        self.assertTrue(self.api.wait(ref.id, timeout=10.0))
        self.assertTrue(self.api.forget(ref.id))
        self.assertIsNone(self.api.find(ref.id))
        self.assertEqual(self.api.jobs(), ())

    def test_clear_finished(self) -> None:
        first = self.api.install(sys.executable, log=self.log, packages=(), confirm=False)
        second = self.api.install(sys.executable, log=self.log, packages=(), confirm=False)
        self.assertTrue(self.api.wait(first.id, timeout=10.0))
        self.assertTrue(self.api.wait(second.id, timeout=10.0))
        self.assertEqual(len(self.api.jobs()), 2)
        self.assertEqual(self.api.clear_finished(), 2)
        self.assertEqual(self.api.jobs(), ())

    def test_unknown_ids_on_a_live_table(self) -> None:
        ref = self.api.install(sys.executable, log=self.log, packages=(), confirm=False)
        self.assertTrue(self.api.wait(ref.id, timeout=10.0))
        self.assertIsNone(self.api.find(""))
        self.assertIsNone(self.api.find("pip-没这个"))
        self.assertFalse(self.api.pause("pip-没这个"))
        self.assertFalse(self.api.resume("pip-没这个"))
        self.assertFalse(self.api.cancel("pip-没这个"))
        self.assertFalse(self.api.forget("pip-没这个"))
        self.assertFalse(self.api.wait("pip-没这个", timeout=0.1))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
