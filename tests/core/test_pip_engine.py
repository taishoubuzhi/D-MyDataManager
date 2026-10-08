"""pip 安装引擎单测：命令拼装、wheel 体检、流式执行、暂停 / 取消、失败路径。

**不碰网络**：真正的 pip 调用要么用假的 `python`（一个空文件）+ 把 `stream` 换掉，
要么跑几条本机的 `python -c`。只有 `create_venv` 那一条是真建虚拟环境。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core.pip import engine
from app.core.pip.engine import (
    Control,
    PipError,
    PipStopped,
    check_wheels,
    create_venv,
    dist_name,
    ensure,
    ensure_packages,
    package_versions,
    pip_command,
    stream,
    venv_python,
    wheel_dist_names,
    write_requirements,
)

VERSION = sys.version_info
PY_TAG = f"py{VERSION[0]}{VERSION[1]}"


class ToolTests(unittest.TestCase):
    """纯函数：命令形状、名字归一化、GitHub 直链与提示语。"""

    def test_pip_command_default_shape(self) -> None:
        command = pip_command(Path("py"), Path("req.txt"))
        self.assertEqual(command[:5], ["py", "-m", "pip", "install", "--progress-bar"])
        self.assertEqual(command[-2:], ["-r", "req.txt"])
        self.assertNotIn("--index-url", command)
        self.assertNotIn("--upgrade", command)

    def test_pip_command_with_sources_and_urls(self) -> None:
        command = pip_command(
            Path("py"),
            Path("req.txt"),
            index_url="https://mirror.example.com/simple",
            extra_index=("", "https://extra.example.com"),
            urls=("https://x.example.com/a.whl", ""),
            upgrade=True,
        )
        self.assertIn("--upgrade", command)
        self.assertEqual(command.count("--index-url"), 1)
        self.assertEqual(command[command.index("--index-url") + 1], "https://mirror.example.com/simple")
        self.assertEqual(command.count("--extra-index-url"), 1)
        self.assertIn("https://x.example.com/a.whl", command)
        self.assertNotIn("", command)

    def test_dist_name_normalizes(self) -> None:
        self.assertEqual(dist_name("llama_cpp_python>=0.3"), "llama-cpp-python")
        self.assertEqual(dist_name("  Pillow  "), "pillow")
        self.assertEqual(dist_name("numpy[extra]; python_version<'3.13'"), "numpy")

    def test_wheel_dist_names_accepts_paths(self) -> None:
        names = wheel_dist_names(
            [Path("torch-2.4.0-cp312-cp312-win_amd64.whl"), Path("llama_cpp_python-0.3.0-py3-none-any.whl")]
        )
        self.assertEqual(names, {"torch", "llama-cpp-python"})

    def test_write_requirements_empty_and_filled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            root = Path(raw)
            empty = write_requirements(root / "sub" / "a.txt", [])
            self.assertEqual(empty.read_text(encoding="utf-8"), "")
            filled = write_requirements(root / "b.txt", ("numpy>=2", " ", "torch"))
            self.assertEqual(filled.read_text(encoding="utf-8"), "numpy>=2\ntorch\n")

    def test_venv_python_is_platform_specific(self) -> None:
        path = venv_python(Path("env"))
        if sys.platform.startswith("win"):
            self.assertEqual(path, Path("env") / "Scripts" / "python.exe")
        else:
            self.assertEqual(path, Path("env") / "bin" / "python")

    def test_github_asset_urls_filters_and_dedupes(self) -> None:
        body = (
            "Downloading https://github.com/o/r/releases/download/v1/a.whl\n"
            "and (https://objects.githubusercontent.com/x/b.tar.gz),\n"
            "again https://github.com/o/r/releases/download/v1/a.whl\n"
            "https://example.com/not-github.whl\n"
        )
        found = engine.github_asset_urls(body)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0], "https://github.com/o/r/releases/download/v1/a.whl")
        self.assertEqual(found[1], "https://objects.githubusercontent.com/x/b.tar.gz")
        self.assertEqual(engine.github_asset_urls(body, limit=1), [found[0]])

    def test_mirror_github_urls_skips_blank_prefix(self) -> None:
        urls = ["https://github.com/o/r/releases/download/v1/a.whl"]
        mirrored = engine.mirror_github_urls(urls, ("", "https://ghfast.top/"))
        self.assertEqual(mirrored, ["https://ghfast.top/https://github.com/o/r/releases/download/v1/a.whl"])

    def test_github_retry_rebuilds_only_when_useful(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            log = Path(raw) / "pip.log"
            log.write_text("Requirement already satisfied\n", encoding="utf-8")
            self.assertIsNone(engine.github_retry(log, prefixes=["https://ghfast.top"], rebuild=lambda _u: []))
            log.write_text(
                "TimeoutError: https://github.com/o/r/releases/download/v1/a.whl\n", encoding="utf-8"
            )
            self.assertIsNone(engine.github_retry(log, prefixes=[], rebuild=lambda _u: []))
            seen: list[list[str]] = []
            built = engine.github_retry(
                log, prefixes=["https://ghfast.top"], rebuild=lambda urls: seen.append(urls) or ["ok"]
            )
            self.assertEqual(built, ["ok"])
            self.assertEqual(len(seen[0]), 1)
            self.assertTrue(seen[0][0].startswith("https://ghfast.top/"))

    def test_hints_are_keyed_on_log_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            log = Path(raw) / "pip.log"
            self.assertEqual(engine.network_hint(log), "")
            self.assertEqual(engine.long_path_hint(log), "")
            log.write_text("TimeoutError while fetching github.com/x\n", encoding="utf-8")
            self.assertIn("github.com", engine.network_hint(log))
            self.assertEqual(engine.long_path_hint(log), "")
            log.write_text("[Errno 2] No such file or directory: '.../site-packages/a.h'\n", encoding="utf-8")
            self.assertIn("260", engine.long_path_hint(log))


class WheelCheckTests(unittest.TestCase):
    """wheel 文件名体检：不存在 / 不是 whl / 名字怪 / python 与平台对不上。"""

    def _write(self, root: Path, name: str) -> Path:
        path = root / name
        path.write_bytes(b"")
        return path

    def test_reports_missing_and_wrong_suffix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            root = Path(raw)
            problems = check_wheels([str(root / "gone.whl"), str(self._write(root, "note.txt"))])
            self.assertEqual(len(problems), 2)
            self.assertIn("文件不存在", problems[0])
            self.assertIn("不是 .whl 文件", problems[1])

    def test_reports_odd_filename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            problems = check_wheels([str(self._write(Path(raw), "demo-1.0-any.whl"))])
            self.assertEqual(len(problems), 1)
            self.assertIn("文件名不像 wheel", problems[0])

    def test_current_interpreter_wheel_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            wheels = [
                str(self._write(Path(raw), f"demo-1.0-{PY_TAG}-none-any.whl")),
                str(self._write(Path(raw), f"demo-1.0-py{VERSION[0]}-none-any.whl")),
            ]
            self.assertEqual(check_wheels(wheels), [])

    def test_wrong_python_and_platform_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            wrong_py = self._write(Path(raw), "demo-1.0-py27-none-any.whl")
            wrong_platform = self._write(Path(raw), f"demo-1.0-{PY_TAG}-none-plan9.whl")
            problems = check_wheels([str(wrong_py), str(wrong_platform)])
            self.assertEqual(len(problems), 2)
            self.assertIn("Python py27", problems[0])
            self.assertIn("plan9", problems[1])

    def test_abi3_lower_minor_passes(self) -> None:
        if VERSION[1] < 1:
            self.skipTest("当前解释器太小，没有更低的 abi3 版本可试")
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            name = f"demo-1.0-cp{VERSION[0]}{VERSION[1] - 1}-abi3-any.whl"
            self.assertEqual(check_wheels([str(self._write(Path(raw), name))]), [])


class StreamTests(unittest.TestCase):
    """流式执行：真的起子进程，验输出落日志、退出码、暂停 / 取消能立刻收掉它。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dm_pip_")
        self.root = Path(self._tmp.name)
        self.log = self.root / "pip.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_captures_output_into_log(self) -> None:
        lines: list[str] = []
        code = stream(
            [sys.executable, "-c", "print('hello-pip')"],
            log=self.log,
            on_line=lines.append,
        )
        self.assertEqual(code, 0)
        body = self.log.read_text(encoding="utf-8")
        self.assertTrue(body.startswith("$ "))  # 命令本身留档
        self.assertIn("hello-pip", body)
        self.assertIn("hello-pip", lines)

    def test_returns_exit_code(self) -> None:
        code = stream([sys.executable, "-c", "import sys; sys.exit(3)"], log=self.log)
        self.assertEqual(code, 3)

    def test_stop_watch_thread_honours_cancel(self) -> None:
        flag = threading.Event()
        threading.Timer(0.3, flag.set).start()
        started = time.monotonic()
        with self.assertRaises(PipStopped) as caught:
            stream(
                [sys.executable, "-c", "import time; print('start'); time.sleep(30)"],
                log=self.log,
                should_cancel=flag.is_set,
            )
        self.assertFalse(caught.exception.paused)
        self.assertLess(time.monotonic() - started, 20.0)

    def test_control_cancel_stops_process(self) -> None:
        control = Control()
        threading.Timer(0.3, control.cancel).start()
        started = time.monotonic()
        with self.assertRaises(PipStopped) as caught:
            stream(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                log=self.log,
                control=control,
            )
        self.assertFalse(caught.exception.paused)
        self.assertLess(time.monotonic() - started, 20.0)

    def test_control_pause_reports_paused(self) -> None:
        control = Control()
        threading.Timer(0.3, control.pause).start()
        with self.assertRaises(PipStopped) as caught:
            stream(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                log=self.log,
                control=control,
            )
        self.assertTrue(caught.exception.paused)
        self.assertIn("暂停", str(caught.exception))

    def test_control_reset_clears_flags(self) -> None:
        control = Control()
        control.pause()
        self.assertTrue(control.should_pause())
        control.reset()
        self.assertFalse(control.should_pause())
        control.cancel()
        self.assertTrue(control.should_cancel())
        control.clear_pause()
        control.reset()
        self.assertFalse(control.should_cancel())

    def test_unlaunchable_command_raises_pip_error(self) -> None:
        with self.assertRaises(PipError) as caught:
            stream([str(self.root / "no-such-python.exe"), "-c", "pass"], log=self.log)
        self.assertIn("无法启动命令", str(caught.exception))


class EnsureTests(unittest.TestCase):
    """安装流程：真实的 pip 调用被替换掉，只验命令与状态机的接线。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dm_pip_")
        self.root = Path(self._tmp.name)
        self.log = self.root / "pip.log"
        self.fake_python = self.root / "python.exe"
        self.fake_python.write_bytes(b"")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _patch_stream(self, code: int = 0):
        calls: list[list[str]] = []

        def fake(command, **kwargs):
            calls.append([str(part) for part in command])
            return code

        return calls, mock.patch.object(engine, "stream", fake)

    def test_check_stop_before_any_work(self) -> None:
        with self.assertRaises(PipStopped):
            ensure(
                self.fake_python,
                packages=["numpy"],
                log=self.log,
                should_cancel=lambda: True,
            )
        self.assertFalse(self.log.exists())

    def test_missing_environment_without_venv_info(self) -> None:
        with self.assertRaises(PipError) as caught:
            ensure(self.root / "missing" / "python.exe", packages=["numpy"], log=self.log)
        self.assertIn("运行环境不存在", str(caught.exception))

    def test_empty_packages_writes_requirements_and_finishes(self) -> None:
        requirements = self.root / "requirements.txt"
        done: list[Path] = []
        calls, patch = self._patch_stream()
        with patch:
            result = ensure(
                self.fake_python,
                packages=[],
                log=self.log,
                requirements=requirements,
                on_finish=done.append,
            )
        self.assertEqual(result, self.fake_python)
        self.assertEqual(requirements.read_text(encoding="utf-8"), "")
        self.assertEqual(done, [self.fake_python])
        self.assertEqual(calls, [])
        self.assertFalse(self.log.exists())

    def test_install_runs_pip_with_configured_source(self) -> None:
        requirements = self.root / "requirements.txt"
        calls, patch = self._patch_stream()
        with patch:
            ensure_packages(
                self.fake_python,
                packages=["numpy>=2", " ", "torch"],
                log=self.log,
                requirements=requirements,
                index_url="https://mirror.example.com/simple",
            )
        self.assertEqual(requirements.read_text(encoding="utf-8"), "numpy>=2\ntorch\n")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:5], [str(self.fake_python), "-m", "pip", "install", "--progress-bar"])
        self.assertIn("https://mirror.example.com/simple", calls[0])
        self.assertEqual(calls[0][-2:], ["-r", str(requirements)])

    def test_ensure_calls_on_start_and_on_finish(self) -> None:
        requirements = self.root / "requirements.txt"
        started: list[tuple[str, ...]] = []
        done: list[Path] = []
        _calls, patch = self._patch_stream()
        with patch:
            ensure(
                self.fake_python,
                packages=["numpy>=2", " "],
                log=self.log,
                requirements=requirements,
                on_start=started.append,
                on_finish=done.append,
            )
        self.assertEqual(started, [("numpy>=2",)])
        self.assertEqual(done, [self.fake_python])

    def test_install_retries_with_github_mirror(self) -> None:
        requirements = self.root / "requirements.txt"
        github_line = "TimeoutError: https://github.com/o/r/releases/download/v1/a.whl"
        codes = iter([1, 0])
        calls: list[list[str]] = []

        def fake(command, **kwargs):
            calls.append([str(part) for part in command])
            if len(calls) == 1:
                self.log.write_text(github_line + "\n", encoding="utf-8")
            return next(codes)

        with mock.patch.object(engine, "stream", fake):
            ensure_packages(
                self.fake_python,
                packages=["llama-cpp-python"],
                log=self.log,
                requirements=requirements,
                github_prefixes=["https://ghfast.top"],
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("https://ghfast.top/https://github.com/o/r/releases/download/v1/a.whl", calls[1])

    def test_failed_install_raises_with_log_pointer(self) -> None:
        calls, patch = self._patch_stream(code=2)
        with patch, self.assertRaises(PipError) as caught:
            ensure_packages(self.fake_python, packages=["numpy"], log=self.log)
        self.assertIn("退出码 2", str(caught.exception))
        self.assertEqual(len(calls), 1)

    def test_install_wheels_puts_files_first_and_skips_covered_packages(self) -> None:
        wheel = self.root / "torch-2.4.0-cp312-cp312-win_amd64.whl"
        wheel.write_bytes(b"")
        calls, patch = self._patch_stream()
        with patch:
            result = engine.install_wheels(
                self.fake_python,
                [str(wheel)],
                packages=["Torch>=2.4", "nvidia-cuda-runtime-cu12"],
                log=self.log,
                index_url="https://mirror.example.com/simple",
            )
        self.assertEqual(result, self.fake_python)
        command = calls[0]
        self.assertEqual(command[:5], [str(self.fake_python), "-m", "pip", "install", "--progress-bar"])
        self.assertIn("https://mirror.example.com/simple", command)
        self.assertLess(command.index(str(wheel)), command.index("nvidia-cuda-runtime-cu12"))
        self.assertNotIn("Torch>=2.4", command)

    def test_install_wheels_rejects_empty_and_missing(self) -> None:
        with self.assertRaises(PipError) as caught:
            engine.install_wheels(self.fake_python, [], log=self.log)
        self.assertIn("没有选任何 whl 文件", str(caught.exception))
        with self.assertRaises(PipError) as caught:
            engine.install_wheels(self.fake_python, [str(self.root / "gone.whl")], log=self.log)
        self.assertIn("找不到这些文件", str(caught.exception))

    def test_package_versions_of_this_interpreter_has_pip(self) -> None:
        versions = package_versions(Path(sys.executable))
        self.assertIn("pip", {name.lower() for name in versions})

    def test_package_versions_of_bogus_interpreter_is_empty(self) -> None:
        self.assertEqual(package_versions(self.root / "no-such-python.exe"), {})


class VenvTests(unittest.TestCase):
    """真建一次虚拟环境（本组唯一慢的一条）：验 `create_venv` 的返回值与失败路径。"""

    def test_create_venv_failure_reports_pip_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            root = Path(raw)
            with self.assertRaises(PipError):
                create_venv(root / "no-such-python.exe", root / "env", log=root / "pip.log")

    def test_create_venv_makes_interpreter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dm_pip_") as raw:
            root = Path(raw)
            python = create_venv(Path(sys.executable), root / "env", log=root / "pip.log")
            self.assertEqual(python, venv_python(root / "env"))
            self.assertTrue(python.exists())
            # 命令行本身留档（「创建运行环境」那句是给 on_line 的，不落日志）
            self.assertIn("-m venv", (root / "pip.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
