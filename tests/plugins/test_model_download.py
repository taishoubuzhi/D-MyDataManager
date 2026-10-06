"""模型下载器的用例：本地 http.server 验续传、sha256、镜像回退、暂停与磁盘预检。

用 `IsolatedCase` 把资源目录重定向到 `tests/.tmp/`，所以 `.part` 与断点信息都写在
临时目录里；服务器只提供内存里的字节，全程不联网。
"""

from __future__ import annotations

import hashlib
import http.server
import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path

from tests.harness import IsolatedCase


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.lib.model` 包。

    目录名本身带点，`import plugins.lib.model` 走不通，只能像
    `app.core.plugins.plugin_core._package` 那样手工注册命名空间包。
    """
    root = Path(__file__).resolve().parents[2] / "plugins" / "lib.model"
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.builtin.lib", None),
        ("dm_plugin.lib.model", root),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()

from dm_plugin.lib.model.download import (  # noqa: E402
    DownloadError,
    DownloadManager,
    guess_total_size,
    resolve_urls,
)
from dm_plugin.lib.model.download import downloader  # noqa: E402
from dm_plugin.lib.model.settings import ModelSettings  # noqa: E402

PART = downloader.part_path
META = downloader.meta_path


class _Server(http.server.ThreadingHTTPServer):
    """可控的下载源：内存字节、可设分块大小与发送间隔、可整体返回 404。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, payload: bytes, *, mode: str = "ok", chunk: int = 32768, delay: float = 0.0) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.payload = payload
        self.mode = mode
        self.chunk = max(1, int(chunk))
        self.delay = float(delay)
        self.requests: list[tuple[str, str | None]] = []

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/blob/model.gguf"

    @property
    def ranges(self) -> list[str | None]:
        return [header for _, header in self.requests]


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def do_HEAD(self) -> None:
        self._respond(head_only=True)

    def do_GET(self) -> None:
        self._respond(head_only=False)

    def _respond(self, *, head_only: bool) -> None:
        server: _Server = self.server  # type: ignore[assignment]
        server.requests.append((self.path, self.headers.get("Range")))
        if server.mode == "missing":
            self.send_error(404)
            return
        data = server.payload
        start = 0
        status = 200
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            head = header[len("bytes="):].split("-", 1)[0].strip()
            if head.isdigit():
                start = min(int(head), len(data))
                status = 206
        body = data[start:]
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{max(len(data) - 1, 0)}/{len(data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if head_only:
            return
        try:
            for offset in range(0, len(body), server.chunk):
                self.wfile.write(body[offset:offset + server.chunk])
                self.wfile.flush()
                if server.delay:
                    time.sleep(server.delay)
        except OSError:
            # 客户端暂停/取消会直接断开连接，这里静默收尾
            self.close_connection = True


def wait_for(predicate, timeout: float = 15.0) -> bool:
    """等到条件成立或超时，返回最终判定结果。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class ModelDownloadCase(IsolatedCase):
    """下载队列的行为。"""

    servers: list[_Server]
    managers: list[DownloadManager]

    def setUp(self) -> None:
        super().setUp()
        self.servers = []
        self.managers = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.shutdown()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        super().tearDown()

    # ------------------------------------------------------------- 工具
    def start_server(self, payload: bytes, **kwargs) -> _Server:
        server = _Server(payload, **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server

    def make_manager(self, on_change=None, **download) -> DownloadManager:
        settings = ModelSettings(download={"concurrent": 1, "chunk_retry": 0, "timeout": 10, **download})
        manager = DownloadManager(settings, on_change=on_change)
        self.managers.append(manager)
        return manager

    def target(self, name: str = "model.gguf") -> Path:
        return self.root / "targets" / name

    # ------------------------------------------------------------- 用例
    def test_enqueue_is_idempotent_for_the_same_target(self):
        """同一个文件重复入队要复用已有任务：两条线程抢同一个 .part 会撞 WinError 32（用户 m42407）。"""
        manager = self.make_manager()
        target = self.target("same.gguf")
        first = manager.enqueue("local/same", ["http://127.0.0.1:9/same.gguf"], target, total_bytes=8)
        second = manager.enqueue("local/same", ["http://127.0.0.1:9/same.gguf"], target, total_bytes=8)
        self.assertIs(second, first)
        self.assertEqual(len(manager.jobs()), 1)

    def test_download_and_verify_sha256(self):
        payload = bytes(range(256)) * 4096
        server = self.start_server(payload)
        target = self.target()
        changes: list[int] = []
        manager = self.make_manager(on_change=lambda: changes.append(1))

        job = manager.enqueue(
            "local/demo",
            [server.url],
            target,
            sha256=hashlib.sha256(payload).hexdigest(),
            total_bytes=len(payload),
            label="演示模型",
        )

        self.assertTrue(wait_for(lambda: job.state == "done"), job.error)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(job.done_bytes, len(payload))
        self.assertEqual(job.progress, 1.0)
        self.assertEqual(job.state_label, "已完成")
        self.assertEqual(job.target, target)
        self.assertTrue(changes, "状态变化应当触发 on_change")
        self.assertFalse(PART("local/demo", target).exists())
        self.assertFalse(META(PART("local/demo", target)).exists())
        self.assertEqual(manager.active(), 0)

    def test_github_urls_expand_by_download_source(self):
        """GitHub 直链按「GitHub 下载源」展开成镜像前缀地址，原地址留作兜底；别的地址不动。"""
        github = "https://github.com/a/b/releases/download/v1/x.whl"
        manager = self.make_manager(
            github_source="custom", github_custom="http://127.0.0.1:9", timeout=1, chunk_retry=0
        )
        job = manager.enqueue("local/gh", [github], self.target("gh.whl"), total_bytes=1)
        self.assertEqual(job._urls, [f"http://127.0.0.1:9/{github}", github])

        plain = manager.enqueue(
            "local/plain",
            ["http://127.0.0.1:9/plain.gguf"],
            self.target("plain.gguf"),
            total_bytes=1,
        )
        self.assertEqual(plain._urls, ["http://127.0.0.1:9/plain.gguf"])
        manager.shutdown()

    def test_range_resume_only_requests_tail(self):
        payload = bytes(range(256)) * 8192
        server = self.start_server(payload, chunk=65536)
        target = self.target("resume.gguf")
        part = PART("local/resume", target)
        head = len(payload) // 3
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(payload[:head])
        META(part).write_text(
            json.dumps({"url": server.url, "etag": "", "last_modified": "", "total_bytes": len(payload)}),
            encoding="utf-8",
        )
        manager = self.make_manager()

        job = manager.enqueue(
            "local/resume",
            [server.url],
            target,
            sha256=hashlib.sha256(payload).hexdigest(),
            total_bytes=len(payload),
        )

        self.assertTrue(wait_for(lambda: job.state == "done"), job.error)
        self.assertEqual(server.ranges, [f"bytes={head}-"])
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(part.exists())

    def test_sha256_mismatch_keeps_no_file(self):
        payload = b"payload-block" * 4096
        server = self.start_server(payload)
        target = self.target("bad.gguf")
        manager = self.make_manager()

        job = manager.enqueue(
            "local/bad",
            [server.url],
            target,
            sha256="0" * 64,
            total_bytes=len(payload),
        )

        self.assertTrue(wait_for(lambda: job.state == "error"), job.state)
        self.assertEqual(job.error, "校验失败")
        self.assertEqual(job.state_label, "失败")
        self.assertFalse(target.exists())
        self.assertFalse(PART("local/bad", target).exists())

    def test_resume_leftovers_after_crash(self):
        """程序被关掉后重开：盘上的 `.part` 接着下，而不是从头再来。"""
        payload = bytes(range(256)) * 4096
        server = self.start_server(payload, chunk=65536, delay=0.02)
        target = self.target("crash.gguf")
        first = self.make_manager()

        job = first.enqueue("local/crash", [server.url], target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: job.done_bytes > 0), job.error)
        job.pause()
        self.assertTrue(wait_for(lambda: job.state == "paused"), job.state)
        first.shutdown()  # 模拟程序被关掉：没人再跑这个任务了

        part = PART("local/crash", target)
        self.assertTrue(part.exists())
        meta = json.loads(META(part).read_text(encoding="utf-8"))
        self.assertEqual(meta["model_id"], "local/crash")
        self.assertEqual(meta["urls"], [server.url])
        self.assertEqual(meta["target"], str(target))
        head = part.stat().st_size

        second = self.make_manager()
        self.assertGreaterEqual(second.resume_leftovers(), 1)
        picked = [job for job in second.jobs() if job.target == target]
        self.assertEqual(len(picked), 1, "崩溃留下的 `.part` 该被排回队列，而且只排一次")
        picked = picked[0]
        self.assertEqual(picked.label, "crash.gguf")
        self.assertTrue(wait_for(lambda: picked.state == "done"), picked.error)
        self.assertEqual(target.read_bytes(), payload)
        self.assertIn(f"bytes={head}-", server.ranges)
        self.assertFalse(part.exists())
        second.resume_leftovers()
        self.assertEqual(
            len([job for job in second.jobs() if job.target == target]), 1, "已经在队列里的目标不该被排两次"
        )

    def test_resume_leftovers_skips_part_without_source(self):
        """没有来源信息的 `.part`（老版本留下的）不硬猜地址，原样留着。"""
        manager = self.make_manager()
        part = PART("local/stray", self.target("stray.gguf"))
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"half")

        manager.resume_leftovers()
        self.assertNotIn(part.with_name("stray.gguf"), [job.target for job in manager.jobs()])
        self.assertTrue(part.exists())

    def test_mirror_fallback_after_404_and_dead_host(self):
        payload = b"mirror-payload" * 2048
        broken = self.start_server(payload, mode="missing")
        good = self.start_server(payload)
        dead = "http://127.0.0.1:9/dead.gguf"
        target = self.target("fallback.gguf")
        manager = self.make_manager()

        job = manager.enqueue(
            "local/fallback",
            [broken.url, dead, good.url],
            target,
            sha256=hashlib.sha256(payload).hexdigest(),
            total_bytes=len(payload),
        )

        self.assertTrue(wait_for(lambda: job.state == "done"), job.error)
        self.assertEqual(target.read_bytes(), payload)
        self.assertTrue(broken.requests, "主站先被尝试过")
        self.assertTrue(good.requests, "镜像应当被用到")

    def test_pause_keeps_part_file(self):
        payload = b"x" * (1024 * 1024)
        server = self.start_server(payload, chunk=32768, delay=0.08)
        target = self.target("slow.gguf")
        manager = self.make_manager()

        job = manager.enqueue("local/slow", [server.url], target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: job.state == "running" and job.done_bytes > 0), job.state)

        job.pause()

        self.assertTrue(wait_for(lambda: job.state == "paused"), job.state)
        self.assertEqual(job.state_label, "已暂停")
        part = PART("local/slow", target)
        self.assertTrue(part.is_file())
        self.assertLess(part.stat().st_size, len(payload))
        self.assertFalse(target.exists())

    def test_disk_precheck_rejects_huge_size(self):
        manager = self.make_manager()
        with self.assertRaises(DownloadError) as caught:
            manager.enqueue("local/huge", ["http://127.0.0.1:9/huge.gguf"], self.target("huge.gguf"), total_bytes=10**15)
        self.assertIn("磁盘空间不足", str(caught.exception))

    def test_resolve_urls_expands_base_and_template(self):
        self.assertEqual(
            resolve_urls("https://huggingface.co/", ["https://hf-mirror.com"], "Qwen/Qwen2.5", "sub/model.gguf"),
            [
                "https://huggingface.co/Qwen/Qwen2.5/resolve/main/sub/model.gguf",
                "https://hf-mirror.com/Qwen/Qwen2.5/resolve/main/sub/model.gguf",
            ],
        )
        self.assertEqual(
            resolve_urls("https://modelscope.cn/{repo}/{revision}/{file}", (), "Qwen/Qwen2.5", "a.gguf", "master"),
            ["https://modelscope.cn/Qwen/Qwen2.5/master/a.gguf"],
        )

    def test_guess_total_size_from_head(self):
        payload = b"z" * 12345
        server = self.start_server(payload)
        port = server.server_address[1]
        settings = ModelSettings(download={"base_url": f"http://127.0.0.1:{port}"})
        self.assertEqual(guess_total_size("repo/model", "model.gguf", "main", settings), len(payload))


    def test_job_phase_and_note_are_visible(self):
        """阶段与「正在暂停 / 取消」要能直接显示：detail_label 就是界面拿的那一串。"""
        job = downloader.DownloadJob(
            id="probe",
            model_id="local/probe",
            label="演示模型",
            state="running",
            target=self.target("probe.gguf"),
        )
        self.assertEqual(job.phase, "")
        self.assertEqual(job.note, "")
        self.assertEqual(job.detail_label, "")
        job.phase = "接收数据"
        self.assertEqual(job.detail_label, "接收数据")
        job.note = "正在暂停…"
        self.assertEqual(job.detail_label, "正在暂停…")

    def test_phase_visible_through_real_download(self):
        """真跑一次慢下载：传输阶段出现在 phase 上，暂停收尾后阶段与提示都清干净。"""
        payload = b"y" * (1024 * 1024)
        server = self.start_server(payload, chunk=8192, delay=0.05)
        target = self.target("phase.gguf")
        manager = self.make_manager()

        job = manager.enqueue("local/phase", [server.url], target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: job.phase == "接收数据" and job.done_bytes > 0), job.phase)
        self.assertEqual(job.detail_label, "接收数据")

        job.pause()
        self.assertTrue(wait_for(lambda: job.state == "paused"), job.state)
        self.assertEqual(job.phase, "")
        self.assertEqual(job.note, "")

    def test_forget_and_clear_finished_records(self):
        """移除 / 清空只动记录不动磁盘文件：未结束的不给摘，终态才摘，摘了文件还在。"""
        payload = b"z" * (1024 * 1024)
        server = self.start_server(payload, chunk=8192, delay=0.05)
        target = self.target("forget.gguf")
        manager = self.make_manager()

        job = manager.enqueue("local/forget", [server.url], target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: job.state == "running"), job.state)
        self.assertFalse(manager.forget(job.id), "还在下载的记录不该被摘掉")
        self.assertFalse(manager.forget("no-such-job"), "不存在的记录摘不动")

        job.pause()
        self.assertTrue(wait_for(lambda: job.state == "paused"), job.state)
        part = PART("local/forget", target)
        self.assertTrue(part.is_file(), "暂停只是停下：`.part` 要留着续传")
        self.assertFalse(manager.forget(job.id), "暂停中的记录也得留着：还得靠它续传")

        done_target = self.target("forget-done.gguf")
        finished = manager.enqueue("local/forget-done", [server.url], done_target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: finished.state == "done"), finished.state)
        self.assertTrue(done_target.is_file())
        self.assertTrue(manager.forget(finished.id))
        self.assertIsNone(manager.find(finished.id))
        self.assertTrue(done_target.is_file(), "移除记录不该删掉下载好的模型文件")

        # 收尾：挂着的两条（暂停的那条、又开的一条）都取消掉，好清空记录
        job.cancel()
        self.assertEqual(job.state, "cancelled")
        second = manager.enqueue("local/forget2", [server.url], self.target("forget2.gguf"), total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: second.state == "running"), second.state)
        second.cancel()
        self.assertTrue(wait_for(lambda: second.state == "cancelled"), second.state)
        self.assertEqual(manager.clear_finished(), 2)
        self.assertEqual(list(manager.jobs()), [])
        self.assertEqual(manager.clear_finished(), 0)

    def test_cancel_cleans_part_and_temp_dir(self):
        """取消 = 不要了：`.part`、断点信息、空的临时目录一起清；暂停则一律保留。"""
        payload = b"c" * (1024 * 1024)
        server = self.start_server(payload, chunk=8192, delay=0.05)
        target = self.target("cleanup.gguf")
        manager = self.make_manager()

        job = manager.enqueue("local/cleanup", [server.url], target, total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: job.state == "running"), job.state)
        part = PART("local/cleanup", target)
        self.assertTrue(wait_for(lambda: part.is_file()), str(part))

        job.cancel()
        self.assertTrue(wait_for(lambda: job.state == "cancelled"), job.state)
        self.assertFalse(part.exists(), "取消后 `.part` 不该留着")
        self.assertFalse(META(part).exists(), "取消后断点信息不该留着")
        self.assertFalse(part.parent.exists(), "取消后空的临时目录也该删掉")

        paused = manager.enqueue("local/cleanup2", [server.url], self.target("cleanup2.gguf"), total_bytes=len(payload))
        self.assertTrue(wait_for(lambda: paused.state == "running"), paused.state)
        part2 = PART("local/cleanup2", paused.target)
        self.assertTrue(wait_for(lambda: part2.is_file()), str(part2))
        paused.pause()
        self.assertTrue(wait_for(lambda: paused.state == "paused"), paused.state)
        self.assertTrue(part2.is_file(), "暂停不该删半成品")
        self.assertTrue(part2.parent.is_dir(), "暂停的临时目录要留着续传")

        # 暂停之后再取消：没有下载线程替它收尾，也要清干净
        paused.cancel()
        self.assertEqual(paused.state, "cancelled")
        self.assertFalse(part2.exists(), "暂停后取消也要清掉 `.part`")
        self.assertFalse(META(part2).exists())
        self.assertFalse(part2.parent.exists(), "暂停后取消也要清掉空的临时目录")


if __name__ == "__main__":
    unittest.main()
