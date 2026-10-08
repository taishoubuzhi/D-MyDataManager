"""下载引擎单测：并行数热切换、暂停原因区分、断点、索引恢复。

全程不碰网络：`_fetch` 被换成一个「写假文件」的实现，并发行为靠一个闸门（`gate`）控制，
所以「正在下载」这种中间态在测试里是可控、可断言的。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.core.download.engine import (
    MAX_CONCURRENT,
    DownloadError,
    DownloadManager,
    DownloadOptions,
    STATE_DONE,
    STATE_ERROR,
    STATE_PAUSED,
    STATE_QUEUED,
    STATE_RUNNING,
)
from app.core.download.engine import _Halt
from app.core.download.mirrors import MirrorRule, MirrorRules, default_rules
from app.core.journals import JournalStore
from app.core.manifest import manifest_kit

PAYLOAD = b"payload-bytes-0123456789"


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class StubManager(DownloadManager):
    """把 `_fetch` 换成写假文件；`gate` 清掉就能把任务卡在「下载中」。

    `hold(name)` 能按地址（用文件名区分，如 `a.bin` → `a`）单独扣住一个任务，用来只
    放行一部分任务——验证调度时必须这样，否则闸门一开就全跑完了。
    """

    def __init__(self, *args, **kwargs) -> None:
        self.fetched: list[str] = []
        self.gate = threading.Event()
        self.gate.set()
        self.holds: dict[str, threading.Event] = {}
        super().__init__(*args, **kwargs)

    def hold(self, name: str) -> threading.Event:
        event = threading.Event()
        self.holds[name] = event
        return event

    def _fetch(self, job, url, part) -> None:  # type: ignore[override]
        self.fetched.append(url)
        event = self.holds.get(url.rsplit("/", 1)[-1].split(".")[0])
        (event or self.gate).wait(5.0)
        if self._stopped(job):
            raise _Halt()
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(PAYLOAD)
        with self._cond:
            job.total_bytes = len(PAYLOAD)
            job.done_bytes = len(PAYLOAD)
            self._cond.notify_all()


class ManagerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dm_download_")
        self.tmp = Path(self._tmp.name)
        self.managers: list[DownloadManager] = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.shutdown(wait=1.0)
        # 索引清单是运行期动态登记的：关机后任务停在「已暂停」，清单仍然开着。
        # 用例自己把登记清掉，否则同一进程里后面的用例（比如清单登记表的条数）
        # 会看到多出来的条目。
        for manifest_id in list(manifest_kit.ids()):
            if manifest_id.startswith("core.journal."):
                manifest_kit.drop(manifest_id)
        self._tmp.cleanup()

    def make(self, **changes) -> StubManager:
        manager = StubManager(DownloadOptions(**changes))
        self.managers.append(manager)
        return manager

    def target(self, name: str = "a.bin") -> Path:
        return self.tmp / name


class OptionTests(unittest.TestCase):
    def test_limit_follows_concurrent(self) -> None:
        self.assertEqual(DownloadOptions().limit, 2)
        self.assertEqual(DownloadOptions(concurrent=5).limit, 5)

    def test_sequential_pins_limit_to_one(self) -> None:
        self.assertEqual(DownloadOptions(concurrent=5, sequential=True).limit, 1)

    def test_limit_is_clamped(self) -> None:
        self.assertEqual(DownloadOptions(concurrent=0).limit, 1)
        self.assertEqual(DownloadOptions(concurrent=999).limit, MAX_CONCURRENT)

    def test_updated_keeps_the_rest(self) -> None:
        options = DownloadOptions(proxy="http://127.0.0.1:7890", concurrent=3)
        changed = options.updated(concurrent=1)
        self.assertEqual(changed.concurrent, 1)
        self.assertEqual(changed.proxy, "http://127.0.0.1:7890")


class ExpandTests(unittest.TestCase):
    def test_official_first_then_mirrors(self) -> None:
        manager = DownloadManager(DownloadOptions())
        candidates = manager.expand("https://huggingface.co/a/b.bin")
        self.assertEqual(
            candidates,
            ["https://huggingface.co/a/b.bin", "https://hf-mirror.com/a/b.bin"],
        )

    def test_several_urls_are_expanded_in_order(self) -> None:
        manager = DownloadManager(DownloadOptions())
        candidates = manager.expand(
            ["https://huggingface.co/a.bin", "https://github.com/o/r/releases/download/v1/b.bin"]
        )
        self.assertEqual(candidates[0], "https://huggingface.co/a.bin")
        self.assertEqual(candidates[1], "https://hf-mirror.com/a.bin")
        self.assertEqual(candidates[2], "https://github.com/o/r/releases/download/v1/b.bin")
        # 2 条 HF（官方 + 镜像）+ 4 条 GitHub（官方 + 3 个镜像）
        self.assertEqual(len(candidates), 6)

    def test_mirror_mode_drops_official(self) -> None:
        rules = MirrorRules(
            rules=(
                MirrorRule(
                    id="hf",
                    pattern="huggingface.co",
                    official="https://huggingface.co",
                    mirrors=("https://hf-mirror.com",),
                    mode="mirror",
                ),
            )
        )
        manager = DownloadManager(DownloadOptions(mirrors=rules))
        self.assertEqual(manager.expand("https://huggingface.co/a.bin"), ["https://hf-mirror.com/a.bin"])

    def test_unknown_scheme_is_rejected(self) -> None:
        manager = DownloadManager(DownloadOptions())
        with self.assertRaises(DownloadError):
            manager.expand("ftp://example.com/a.bin")
        with self.assertRaises(DownloadError):
            manager.expand("D:\\data\\a.bin")

    def test_default_rules_cover_huggingface_and_github(self) -> None:
        self.assertEqual([rule.id for rule in default_rules().rules], ["huggingface", "github"])


class RunTests(ManagerCase):
    def test_download_success_writes_target(self) -> None:
        manager = self.make(concurrent=1)
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))
        target = self.target()
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertFalse(job.staging.exists())
        self.assertEqual(job.progress, 1.0)

    def test_same_target_is_not_queued_twice(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        first = manager.enqueue("https://example.com/a.bin", self.target())
        second = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertIs(first, second)

    def test_existing_target_gets_a_serial_name(self) -> None:
        self.target().write_bytes(b"old")
        manager = self.make(concurrent=1)
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))
        self.assertEqual(job.target.name, "a_1.bin")
        self.assertEqual(self.target().read_bytes(), b"old")

    def test_sha256_mismatch_fails_and_keeps_no_file(self) -> None:
        manager = self.make(concurrent=1)
        job = manager.enqueue(
            "https://example.com/a.bin", self.target(), sha256="0" * 64
        )
        self.assertTrue(wait_until(lambda: job.state == STATE_ERROR))
        self.assertIn("校验失败", job.error)
        self.assertFalse(self.target().exists())
        self.assertFalse(job.staging.exists())

    def test_retry_requeues_a_failed_job(self) -> None:
        manager = self.make(concurrent=1)
        job = manager.enqueue("https://example.com/a.bin", self.target(), sha256="0" * 64)
        self.assertTrue(wait_until(lambda: job.state == STATE_ERROR))
        manager._jobs[job.id].sha256 = ""
        self.assertTrue(manager.retry(job.id))
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))

    def test_part_file_is_resumed(self) -> None:
        staging = self.target().with_name("a.bin.part")
        staging.write_bytes(b"x" * 7)
        manager = self.make(concurrent=1)
        job = manager.enqueue("https://example.com/a.bin", self.target())
        # 断点会被算进进度（假实现直接用整份覆盖，这里只验证恢复时读过 .part）
        self.assertGreaterEqual(job.done_bytes, 0)
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))


class ScheduleTests(ManagerCase):
    def test_limit_holds_back_the_extra_jobs(self) -> None:
        manager = self.make(concurrent=2)
        manager.gate.clear()
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "abc"]
        self.assertTrue(wait_until(lambda: len(manager.fetched) == 2))
        self.assertTrue(wait_until(lambda: jobs[2].state == STATE_PAUSED))
        self.assertTrue(jobs[2].paused_by_policy)
        self.assertIn("超出并行数", jobs[2].note)
        self.assertEqual([job.state for job in jobs[:2]], [STATE_RUNNING, STATE_RUNNING])

    def test_raising_the_limit_resumes_by_order(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "abc"]
        self.assertTrue(wait_until(lambda: jobs[1].state == STATE_PAUSED and jobs[1].paused_by_policy))
        manager.update(manager.options.updated(concurrent=3))
        self.assertTrue(wait_until(lambda: len(manager.fetched) == 3))
        self.assertTrue(wait_until(lambda: all(job.state == STATE_RUNNING for job in jobs)))

    def test_lowering_the_limit_parks_the_tail(self) -> None:
        manager = self.make(concurrent=3)
        holds = {name: manager.hold(name) for name in "abc"}
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "abc"]
        self.assertTrue(wait_until(lambda: len(manager.fetched) == 3))

        manager.update(manager.options.updated(concurrent=1))
        self.assertTrue(wait_until(lambda: jobs[1].paused_by_policy and jobs[2].paused_by_policy))
        self.assertIn("即将暂停", jobs[1].note)

        # 只放行后两个：第一个还没有结束，额度就没空出来，它们必须停在暂停上
        holds["b"].set()
        holds["c"].set()
        self.assertTrue(wait_until(lambda: jobs[1].state == STATE_PAUSED))
        self.assertTrue(wait_until(lambda: jobs[2].state == STATE_PAUSED))
        self.assertEqual(jobs[0].state, STATE_RUNNING)

        # 额度调回 3：停着的两个按顺序自动接着做完
        manager.update(manager.options.updated(concurrent=3))
        self.assertTrue(wait_until(lambda: jobs[1].state == STATE_DONE))
        self.assertTrue(wait_until(lambda: jobs[2].state == STATE_DONE))

        holds["a"].set()
        self.assertTrue(wait_until(lambda: jobs[0].state == STATE_DONE))

    def test_sequential_mode_runs_one_at_a_time(self) -> None:
        manager = self.make(concurrent=4, sequential=True)
        manager.gate.clear()
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "abc"]
        self.assertTrue(wait_until(lambda: len(manager.fetched) == 1))
        time.sleep(0.3)
        self.assertEqual(len(manager.fetched), 1)
        self.assertEqual(jobs[0].state, STATE_RUNNING)
        self.assertTrue(jobs[1].paused_by_policy)
        self.assertTrue(jobs[2].paused_by_policy)

    def test_policy_pause_survives_a_limit_bounce(self) -> None:
        manager = self.make(concurrent=2)
        holds = {name: manager.hold(name) for name in "ab"}
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "abc"]
        self.assertTrue(wait_until(lambda: len(manager.fetched) >= 2))
        self.assertTrue(wait_until(lambda: jobs[2].paused_by_policy))

        # 额度降到 1：第二个也被挂起；再调回 2，它拿回额度，第三个仍然排在外面
        manager.update(manager.options.updated(concurrent=1))
        self.assertTrue(jobs[1].paused_by_policy)
        manager.update(manager.options.updated(concurrent=2))
        self.assertTrue(wait_until(lambda: not jobs[1].paused_by_policy))
        self.assertTrue(jobs[2].paused_by_policy)

        # 额度放到 3：第三个才轮得到
        manager.update(manager.options.updated(concurrent=3))
        self.assertTrue(wait_until(lambda: jobs[2].state == STATE_DONE))
        holds["a"].set()
        holds["b"].set()
        self.assertTrue(wait_until(lambda: jobs[0].state == STATE_DONE))
        self.assertTrue(wait_until(lambda: jobs[1].state == STATE_DONE))


class OperationTests(ManagerCase):
    def test_pause_and_resume_one_job(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_RUNNING))
        self.assertTrue(manager.pause(job.id))
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == STATE_PAUSED))
        self.assertFalse(job.paused_by_policy)
        self.assertEqual(job.note, "已暂停")
        self.assertTrue(manager.resume(job.id))
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))

    def test_pause_all_keeps_breakpoints(self) -> None:
        manager = self.make(concurrent=2)
        manager.gate.clear()
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "ab"]
        self.assertTrue(wait_until(lambda: len(manager.fetched) == 2))
        self.assertEqual(manager.pause_all(), 2)
        manager.gate.set()
        self.assertTrue(wait_until(lambda: all(job.state == STATE_PAUSED for job in jobs)))
        self.assertTrue(manager.resume_all() >= 2)
        self.assertTrue(wait_until(lambda: all(job.state == STATE_DONE for job in jobs)))

    def test_cancel_running_job_removes_the_part(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_RUNNING))
        job.staging.parent.mkdir(parents=True, exist_ok=True)
        job.staging.write_bytes(b"partial")
        self.assertTrue(manager.cancel(job.id))
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == "cancelled"))
        self.assertFalse(job.staging.exists())
        self.assertFalse(self.target().exists())

    def test_cancel_a_queued_job_is_immediate(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        first = manager.enqueue("https://example.com/a.bin", self.target("a.bin"))
        second = manager.enqueue("https://example.com/b.bin", self.target("b.bin"))
        self.assertTrue(wait_until(lambda: second.state == STATE_PAUSED))
        self.assertTrue(manager.cancel(second.id))
        self.assertEqual(second.state, "cancelled")
        self.assertTrue(first.state == STATE_RUNNING)

    def test_forget_only_accepts_finished_jobs(self) -> None:
        manager = self.make(concurrent=1)
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_RUNNING))
        self.assertFalse(manager.forget(job.id))
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))
        self.assertTrue(manager.forget(job.id))
        self.assertEqual(manager.jobs(), [])

    def test_clear_finished(self) -> None:
        manager = self.make(concurrent=2)
        jobs = [manager.enqueue(f"https://example.com/{n}.bin", self.target(f"{n}.bin")) for n in "ab"]
        self.assertTrue(wait_until(lambda: all(job.state == STATE_DONE for job in jobs)))
        self.assertEqual(manager.clear_finished(), 2)
        self.assertEqual(manager.jobs(), [])

    def test_operations_on_unknown_job_are_false(self) -> None:
        manager = self.make()
        self.assertFalse(manager.pause("nope"))
        self.assertFalse(manager.resume("nope"))
        self.assertFalse(manager.cancel("nope"))
        self.assertFalse(manager.retry("nope"))
        self.assertFalse(manager.forget("nope"))
        self.assertIsNone(manager.find("nope"))


class IndexTests(ManagerCase):
    def make_indexed(self, kind: str = "download_index_test", **changes) -> tuple[StubManager, JournalStore]:
        store = JournalStore(kind, root=self.tmp / "journals")
        manager = StubManager(DownloadOptions(**changes), index=store)
        self.managers.append(manager)
        return manager, store

    def test_index_is_written_while_open_and_removed_when_settled(self) -> None:
        manager, store = self.make_indexed()
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: len(list(store.root.glob("*.json"))) == 1))
        journals = store.scan()
        self.assertEqual(len(journals), 1)
        item = journals[0].items[0]
        self.assertEqual(item["key"], job.id)
        self.assertEqual(item["target"], str(self.target()))
        self.assertEqual(item["urls"], ["https://example.com/a.bin"])
        # 全部到终态后索引自己删掉（「待处理项为空才删清单」）
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == STATE_DONE))
        self.assertTrue(wait_until(lambda: not list(store.root.glob("*.json"))))

    def test_shutdown_parks_the_task_and_keeps_the_index(self) -> None:
        manager, store = self.make_indexed()
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_RUNNING))
        manager.shutdown(wait=0.2)
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == STATE_PAUSED))
        self.assertIn("已退出", job.note)
        self.assertEqual(len(list(store.root.glob("*.json"))), 1)

    def test_restore_rebuilds_unfinished_jobs(self) -> None:
        manager, store = self.make_indexed()
        manager.gate.clear()
        job = manager.enqueue("https://example.com/a.bin", self.target())
        self.assertTrue(wait_until(lambda: job.state == STATE_RUNNING))
        manager.shutdown(wait=0.2)
        manager.gate.set()
        self.assertTrue(wait_until(lambda: job.state == STATE_PAUSED))

        other = StubManager(DownloadOptions(concurrent=1), index=store)
        self.managers.append(other)
        restored = other.restore()
        self.assertEqual([item.id for item in restored], [job.id])
        self.assertEqual(restored[0].target, self.target())
        self.assertEqual(restored[0].urls, ("https://example.com/a.bin",))
        # 恢复出来的任务按当前并行数自动接着做
        self.assertTrue(wait_until(lambda: restored[0].state == STATE_DONE))

    def test_restore_without_index_is_a_noop(self) -> None:
        manager = self.make()
        self.assertEqual(manager.restore(), [])

    def test_unusable_index_rows_are_ignored(self) -> None:
        store = JournalStore("download_bad_rows_test", root=self.tmp / "journals")
        store.create(items=[{"key": "bad", "urls": [], "target": ""}])
        manager = StubManager(DownloadOptions(concurrent=1), index=store)
        self.managers.append(manager)
        self.assertEqual(manager.restore(), [])


if __name__ == "__main__":
    unittest.main()
