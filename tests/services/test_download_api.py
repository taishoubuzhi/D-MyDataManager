"""下载接口（`app.sdk.download` + `app.services.download_api`）的用例。

**不碰网络**：真队列只在内存里调度，网络那一步（`DownloadManager._fetch`）被换成写一段固定
内容；重点看的是接口层——快照映射、默认目录、确认框的取舍、以及每条控制指令的返回值。
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import download_dir
from app.core.download.engine import STATE_DONE, DownloadJob, DownloadManager
from app.core.download import service as download_service
from app.sdk.errors import SdkError
from app.services import download_api
from app.services.download_api import DownloadApi, _name_of, _urls, to_ref

from tests.harness import IsolatedCase

PAYLOAD = b"download-api-payload"


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def fake_fetch(self, job, url, part: Path) -> None:
    """把「网络」换成直接落盘（签名与 `DownloadManager._fetch` 一致）。"""
    part.write_bytes(PAYLOAD)
    job.done_bytes = len(PAYLOAD)
    job.total_bytes = len(PAYLOAD)


class PureTests(unittest.TestCase):
    """不碰队列的纯函数。"""

    def test_urls_cleans_and_keeps_order(self) -> None:
        self.assertEqual(_urls([" a ", "b", "a", "", "  "]), ["a", "b"])
        self.assertEqual(_urls("https://x/y"), ["https://x/y"])
        self.assertEqual(_urls(None), [])
        self.assertEqual(_urls(()), [])

    def test_name_of_guesses_from_url(self) -> None:
        self.assertEqual(_name_of(["https://x/y/model.bin?token=1#frag"]), "model.bin")
        self.assertEqual(_name_of(["https://x/dir/"]), "dir")
        self.assertEqual(_name_of(["https://x/dir/", "https://x/real.bin"]), "dir")

    def test_to_ref_maps_every_field(self) -> None:
        job = DownloadJob(
            "job-1",
            ["https://x/a.bin", "https://mirror/a.bin"],
            Path("out/a.bin"),
            sha256="abc",
            total_bytes=100,
            label="模型",
            done_bytes=25,
        )
        ref = to_ref(job)
        self.assertEqual(ref.id, "job-1")
        self.assertEqual(ref.urls, ("https://x/a.bin", "https://mirror/a.bin"))
        self.assertEqual(ref.target, str(Path("out/a.bin")))
        self.assertEqual(ref.label, "模型")
        self.assertEqual(ref.state, job.state)
        self.assertEqual(ref.state_label, job.state_label)
        self.assertEqual(ref.done_bytes, 25)
        self.assertEqual(ref.total_bytes, 100)
        self.assertAlmostEqual(ref.progress, 0.25)
        self.assertEqual(ref.sha256, "abc")
        self.assertEqual(ref.error, "")
        self.assertFalse(ref.finished)

    def test_ref_name_and_finished(self) -> None:
        job = DownloadJob("job-2", ["https://x/a.bin"], Path("out/a.bin"), state=STATE_DONE)
        ref = to_ref(job)
        self.assertEqual(ref.name, "a.bin")
        self.assertTrue(ref.finished)


class ApiCase(IsolatedCase):
    """接口层：默认目录、入队、确认框、控制指令。"""

    def setUp(self) -> None:
        super().setUp()
        self.api: DownloadApi = download_api.api()
        self.out = Path(type(self).root) / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self._patch = mock.patch.object(DownloadManager, "_fetch", fake_fetch)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        # 没跑完的任务会留在索引里，下一个用例的队列会把它接回来（这正是设计），所以收尾
        # 先全部取消掉：终态才会让索引收摊，用例之间才是真的隔离。
        try:
            self.api.cancel_all()
        except Exception:  # pragma: no cover - 收尾尽力而为
            pass
        download_service.shutdown(wait=2.0)
        super().tearDown()

    # ------------------------------------------------------------- 目录 / 查询
    def test_default_dir_follows_config(self) -> None:
        self.assertEqual(self.api.default_dir(), str(download_dir()))
        self.assertTrue(Path(self.api.default_dir()).name)

    def test_jobs_and_find_on_empty_queue(self) -> None:
        self.assertEqual(self.api.jobs(), ())
        self.assertIsNone(self.api.find("没这个"))
        self.assertFalse(self.api.pause("没这个"))
        self.assertFalse(self.api.resume("没这个"))
        self.assertFalse(self.api.cancel("没这个"))
        self.assertFalse(self.api.retry("没这个"))
        self.assertFalse(self.api.forget("没这个"))
        self.assertEqual(self.api.pause_all(), 0)
        self.assertEqual(self.api.resume_all(), 0)
        self.assertEqual(self.api.cancel_all(), 0)
        self.assertEqual(self.api.clear_finished(), 0)

    # ------------------------------------------------------------- 入队
    def test_enqueue_rejects_empty_urls(self) -> None:
        with self.assertRaises(SdkError):
            self.api.enqueue([], self.out / "a.bin")
        with self.assertRaises(SdkError):
            self.api.enqueue(["  ", ""], self.out / "a.bin")

    def test_enqueue_runs_and_reports_done(self) -> None:
        target = self.out / "a.bin"
        job_id = self.api.enqueue(["https://example.com/a.bin"], target, label="自检")
        self.assertTrue(job_id)
        self.assertTrue(wait_until(lambda: (ref := self.api.find(job_id)) and ref.finished))
        ref = self.api.find(job_id)
        self.assertEqual(ref.state, STATE_DONE)
        self.assertEqual(ref.state_label, "已完成")
        self.assertEqual(ref.label, "自检")
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertIn(job_id, [item.id for item in self.api.jobs()])

    def test_enqueue_shares_the_one_queue(self) -> None:
        self.api.enqueue(["https://example.com/a.bin"], self.out / "a.bin")
        self.assertIs(self.api.manager(), download_service.manager())
        self.assertEqual(len(self.api.jobs()), 1)

    def test_forget_and_clear_finished(self) -> None:
        target = self.out / "b.bin"
        job_id = self.api.enqueue(["https://example.com/b.bin"], target)
        self.assertTrue(wait_until(lambda: (ref := self.api.find(job_id)) and ref.finished))
        self.assertTrue(self.api.forget(job_id))
        self.assertIsNone(self.api.find(job_id))
        self.assertEqual(self.api.clear_finished(), 0)

    # ------------------------------------------------------------- 确认框
    def test_request_without_confirm_enqueues_directly(self) -> None:
        target = self.out / "c.bin"
        job_id = self.api.request(
            ["https://example.com/c.bin"], target=target, confirm=False, label="直连"
        )
        self.assertTrue(job_id)
        self.assertEqual(self.api.find(job_id).target, str(target))

    def test_request_returns_none_on_empty_urls(self) -> None:
        with mock.patch.object(DownloadApi, "_confirm", lambda *a, **k: self.fail("不该弹框")):
            self.assertIsNone(self.api.request([], confirm=True))

    def test_request_returns_none_when_user_cancels(self) -> None:
        with mock.patch.object(DownloadApi, "_confirm", lambda *a, **k: None):
            self.assertIsNone(self.api.request(["https://example.com/d.bin"], confirm=True))
        self.assertEqual(self.api.jobs(), ())

    def test_request_uses_what_the_user_picked(self) -> None:
        picked = self.out / "picked.bin"
        with mock.patch.object(
            DownloadApi, "_confirm", lambda *a, **k: (["https://example.com/e.bin"], picked)
        ):
            job_id = self.api.request(["https://example.com/e.bin"], confirm=True, name="e.bin")
        self.assertTrue(job_id)
        self.assertEqual(self.api.find(job_id).target, str(picked))

    def test_request_needs_a_target(self) -> None:
        # 地址里没有文件名、也没给文件名 → 找不到落点，不能再往下走
        with mock.patch.object(
            DownloadApi, "_confirm", lambda *a, **k: (["https://example.com/"], None)
        ):
            self.assertIsNone(self.api.request(["https://example.com/"], confirm=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
