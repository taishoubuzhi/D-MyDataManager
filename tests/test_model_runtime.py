"""模型插件的运行环境与 worker 子进程后端测试。

覆盖：

- `runtime` 的路径推导、profile 解析（含 ctx 与文件缺失两种来源）、安装前取消；
- `worker.build_command` 的命令行形状；
- `WorkerAdapter` 用**假 worker 脚本**跑通 start → invoke → stop，并验证 stop 后没有残留进程、
  超时抛 `AdapterError`、流式逐段产出；
- `worker_main.py` 本身（纯 stdlib）的 ping / info / 未知后端错误帧；
- 推理设备：`auto` / `cuda:N` / `mps` 的解析与退路，以及 `runtime/probe.py` 的设备与依赖探测。

不碰任何真实依赖（torch / llama-cpp-python 都不需要）。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import ROOT, IsolatedCase

PLUGIN_DIR = ROOT / "plugins" / "lib.model"
PACKAGE = "dm_plugin.lib.model"


def _register_namespace() -> None:
    """把插件目录挂成 `dm_plugin.lib.model` 包（和 plugin_core 的机制一致）。"""
    names = ["dm_plugin", "dm_plugin.builtin", "dm_plugin.builtin.lib", PACKAGE]
    for index, name in enumerate(names):
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__path__ = [str(PLUGIN_DIR)] if name == PACKAGE else []
            sys.modules[name] = module
            if index:
                setattr(sys.modules[names[index - 1]], name.rsplit(".", 1)[1], module)


_register_namespace()

runtime = importlib.import_module(PACKAGE + ".runtime")
worker_pkg = importlib.import_module(PACKAGE + ".worker")
worker_adapters = importlib.import_module(PACKAGE + ".adapters.worker")

WORKER_SCRIPT: Path = worker_pkg.WORKER_SCRIPT

_FAKE_WORKER = '''
import json
import sys
import time

for line in sys.stdin:
    text = line.strip()
    if not text:
        continue
    try:
        req = json.loads(text)
    except ValueError:
        continue
    rid = req.get("id")
    op = req.get("op")
    payload = req.get("payload") or {}

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    if op == "ping":
        send({"id": rid, "ok": True, "result": {"pong": True, "python": sys.version, "backend": ""}})
    elif op == "load":
        send({"id": rid, "ok": True, "result": {"loaded": True, "backend": payload.get("backend")}})
    elif op == "invoke":
        params = payload.get("params") or {}
        delay = params.get("sleep")
        if delay:
            time.sleep(float(delay))
        if payload.get("stream"):
            for piece in ("he", "llo"):
                send({"id": rid, "stream": "delta", "text": piece})
            send({"id": rid, "ok": True, "result": {"text": "hello"}})
        else:
            send({"id": rid, "ok": True, "result": {"text": "echo:%s" % payload.get("input")}})
    elif op == "unload":
        send({"id": rid, "ok": True, "result": {"loaded": False}})
    else:
        send({"id": rid, "ok": False, "error": "未知操作 %s" % op})
'''


class _Record:
    """测试用的极简模型记录，只提供 worker 适配器需要的字段。"""

    def __init__(self, target: Path) -> None:
        self.id = "demo-model"
        self.kind = "local"
        self.runtime = {"adapter": "worker", "backend": "fake", "profile": "", "params": {}}
        self._target = Path(target)

    @property
    def backend(self) -> str:
        return str(self.runtime.get("backend") or "")

    def primary_file(self) -> Path:
        return self._target

    def local_path(self) -> Path:
        return self._target.parent


class _Settings:
    """最小的 settings 替身。"""

    def __init__(self, **kwargs) -> None:
        self.heartbeat_sec = float(kwargs.get("heartbeat_sec", 1))
        self.allow_system_env = bool(kwargs.get("allow_system_env", True))
        self.device = str(kwargs.get("device", "auto"))


class _Ctx:
    """最小的 PluginContext 替身，只实现 `data()`。"""

    def __init__(self, payload) -> None:
        self.payload = payload

    def data(self, key, default=None):
        return self.payload


class ModelRuntimeCase(IsolatedCase):
    """runtime 的路径与 profile 解析。"""

    def test_paths(self) -> None:
        venv = runtime.venv_dir("gpu")
        self.assertEqual(venv.name, "venv")
        self.assertEqual(venv.parent.name, "gpu")
        self.assertEqual(venv.parent.parent, runtime.runtime_root())
        python = runtime.python_path("gpu")
        self.assertTrue(python.name.startswith("python"))
        self.assertEqual(runtime.requirements_path("gpu").name, "requirements.txt")
        self.assertEqual(runtime.log_file("gpu").name, "runtime-gpu.log")
        self.assertFalse(runtime.installed("gpu"))

    def test_profiles_from_ctx(self) -> None:
        ctx = _Ctx(
            {
                "profiles": [
                    {
                        "id": "gpu",
                        "name": "GPU 运行环境",
                        "note": "CUDA 版",
                        "packages": ["torch", "llama-cpp-python>=0.3"],
                        "index_url": "https://example.invalid/simple",
                        "mirrors": ["https://mirror.invalid/simple"],
                        "python": "",
                        "size_hint": "约 3 GB",
                        "backends": ["llama_cpp", "transformers"],
                    },
                    {"name": "没有 id 的记录"},
                ]
            }
        )
        items = runtime.profiles(ctx)
        self.assertEqual(len(items), 1)
        profile = items[0]
        self.assertEqual(profile.id, "gpu")
        self.assertEqual(profile.description, "CUDA 版")
        self.assertEqual(profile.packages, ("torch", "llama-cpp-python>=0.3"))
        self.assertEqual(profile.extra_index, ("https://mirror.invalid/simple",))
        self.assertEqual(profile.backends, ("llama_cpp", "transformers"))
        self.assertEqual(runtime.profile_of("gpu", ctx), profile)
        self.assertIsNone(runtime.profile_of("missing", ctx))

    def test_profiles_from_file_and_missing(self) -> None:
        with mock.patch.object(runtime, "_DATA_FILE", self.root / "no-such-file.json"):
            self.assertEqual(runtime.profiles(), ())
            self.assertIsNone(runtime.profile_of("gpu"))

        payload = ROOT / "plugins" / "lib.model" / "data" / "runtime_profiles.json"
        if payload.exists():
            # 插件里已经带了清单：确保读得到、能按 id 取回
            items = runtime.profiles()
            self.assertTrue(items)
            self.assertEqual(runtime.profile_of(items[0].id), items[0])
        else:
            # 清单还没建（当前仓库状态）：文件不存在时必须返回空元组而不是抛错
            self.assertEqual(runtime.profiles(), ())

    def test_ensure_cancel_before_install(self) -> None:
        profile = runtime.RuntimeProfile(id="cancel-me", packages=("torch",))
        with self.assertRaises(runtime.RuntimeError_):
            runtime.ensure(profile, should_cancel=lambda: True)
        self.assertFalse(runtime.venv_dir("cancel-me").exists())

    def test_uninstall_missing(self) -> None:
        self.assertTrue(runtime.uninstall("never-installed"))

    def test_pending_marker_wraps_running_install(self) -> None:
        """装的过程中盘上有「正在安装」标记，装完抹掉——强杀留下的半成品靠它认出来。"""
        profile = runtime.RuntimeProfile(id="pend", packages=("torch",))
        python = runtime.python_path("pend")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        seen: list[bool] = []

        def fake(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            seen.append(runtime.interrupted("pend"))
            payload = json.loads(runtime.pending_path("pend").read_text(encoding="utf-8"))
            seen.append(bool(payload.get("packages")) and int(payload.get("pid") or 0) > 0)
            return 0

        with mock.patch.object(runtime, "_stream", fake):
            runtime.ensure(profile)

        self.assertEqual(seen, [True, True])
        self.assertFalse(runtime.pending_path("pend").exists())
        self.assertTrue(runtime.marker_path("pend").exists())
        self.assertFalse(runtime.interrupted("pend"))

    def test_pending_marker_survives_broken_install(self) -> None:
        """pip 半路没了（异常退出 / 断电）：标记留着、完成标记没写，页面据此显示「未完成」。"""
        profile = runtime.RuntimeProfile(id="half", packages=("torch",))
        python = runtime.python_path("half")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")

        def fake(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            return 1

        with mock.patch.object(runtime, "_stream", fake):
            with self.assertRaises(runtime.RuntimeError_):
                runtime.ensure(profile)

        self.assertTrue(runtime.interrupted("half"))
        self.assertFalse(runtime.marker_path("half").exists())
        self.assertFalse(runtime.installed("half"))

    def test_pending_marker_cleared_by_cancel_and_uninstall(self) -> None:
        """取消安装（清半成品）与卸载都要把中断标记一起抹掉，别留下「未完成」的假象。"""
        pending = runtime.pending_path("gone")
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text("{}", encoding="utf-8")
        self.assertTrue(runtime.interrupted("gone"))

        runtime.clear_pending("gone")
        self.assertFalse(pending.exists())
        self.assertFalse(runtime.interrupted("gone"))

        runtime.pending_path("venv-gone").parent.mkdir(parents=True, exist_ok=True)
        runtime.pending_path("venv-gone").write_text("{}", encoding="utf-8")
        self.assertTrue(runtime.interrupted("venv-gone"))
        runtime.uninstall("venv-gone")
        self.assertFalse(runtime.interrupted("venv-gone"))

        system = runtime.pending_path("both", system=True)
        system.parent.mkdir(parents=True, exist_ok=True)
        system.write_text("{}", encoding="utf-8")
        runtime.clear_pending("both")
        self.assertFalse(runtime.interrupted("both", system=True))


class SystemEnvCase(IsolatedCase):
    """P4 程序环境安装模式：`ensure_system()` 的命令、留档与失败处理。"""

    def _capture(self, code: int = 0):
        """替换 `_stream`，记录命令与日志路径，不真的跑 pip。"""
        calls: list[dict] = []

        def fake(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            calls.append({"command": list(command), "log": Path(log)})
            if on_line is not None:
                on_line("fake output")
            return code

        return calls, fake

    def test_ensure_system_targets_program_python(self) -> None:
        profile = runtime.RuntimeProfile(
            id="sys-env",
            packages=("torch", "onnxruntime"),
            index_url="https://example.invalid/simple",
            extra_index=("https://mirror.invalid/simple",),
        )
        calls, fake = self._capture()
        lines: list[str] = []
        with mock.patch.object(runtime, "_stream", fake):
            target = runtime.ensure_system(profile, on_line=lines.append)

        self.assertEqual(target, runtime.system_python())
        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertEqual(command[0], str(sys.executable))  # 装进程序自己的解释器，不建 venv
        self.assertEqual(command[1:4], ["-m", "pip", "install"])
        self.assertIn("--progress-bar", command)
        self.assertIn("--index-url", command)
        self.assertIn("--extra-index-url", command)
        self.assertEqual(calls[0]["log"], runtime.system_log_file("sys-env"))
        requirements = runtime.system_requirements_path("sys-env")
        self.assertIn(str(requirements), command)
        self.assertEqual(requirements.read_text(encoding="utf-8").split(), ["torch", "onnxruntime"])
        self.assertIn("安装进程序环境：torch、onnxruntime", lines)
        self.assertFalse(runtime.venv_dir("sys-env").exists())  # 不建 venv

    def test_ensure_system_explicit_python_and_empty_list(self) -> None:
        fake_python = self.root / "fake-python.exe"
        fake_python.write_text("", encoding="utf-8")
        profile = runtime.RuntimeProfile(id="sys-empty", packages=())
        calls, fake = self._capture()
        with mock.patch.object(runtime, "_stream", fake):
            target = runtime.ensure_system(profile, python=fake_python)
        self.assertEqual(target, fake_python)
        self.assertEqual(calls, [])  # 清单为空就什么都不装

    def test_ensure_system_cancel_before_install(self) -> None:
        profile = runtime.RuntimeProfile(id="sys-cancel", packages=("torch",))
        calls, fake = self._capture()
        with mock.patch.object(runtime, "_stream", fake):
            with self.assertRaises(runtime.RuntimeError_):
                runtime.ensure_system(profile, should_cancel=lambda: True)
        self.assertEqual(calls, [])
        self.assertFalse(runtime.system_requirements_path("sys-cancel").exists())

    def test_uninstall_system_targets_program_python(self) -> None:
        profile = runtime.RuntimeProfile(id="sys-off", packages=("torch", "onnxruntime"))
        calls, fake = self._capture()
        lines: list[str] = []
        with mock.patch.object(runtime, "_stream", fake):
            target = runtime.uninstall_system(profile, on_line=lines.append)

        self.assertEqual(target, runtime.system_python())
        self.assertEqual(len(calls), 1)
        command = calls[0]["command"]
        self.assertEqual(command[0], str(sys.executable))
        self.assertEqual(command[1:4], ["-m", "pip", "uninstall"])
        self.assertEqual(command[4], "-y")
        self.assertEqual(command[5:], ["torch", "onnxruntime"])
        self.assertEqual(calls[0]["log"], runtime.system_log_file("sys-off"))
        self.assertIn("从程序环境卸载：torch、onnxruntime", lines)

    def test_uninstall_system_empty_and_missing_python(self) -> None:
        fake_python = self.root / "fake-python.exe"
        fake_python.write_text("", encoding="utf-8")
        calls, fake = self._capture()
        with mock.patch.object(runtime, "_stream", fake):
            target = runtime.uninstall_system(runtime.RuntimeProfile(id="sys-off-empty", packages=()), python=fake_python)
        self.assertEqual(target, fake_python)
        self.assertEqual(calls, [])  # 清单为空就不跑 pip

        with mock.patch.object(runtime, "_stream", fake):
            with self.assertRaises(runtime.RuntimeError_):
                runtime.uninstall_system(runtime.RuntimeProfile(id="sys-off-missing", packages=("torch",)), python=self.root / "no-such-python.exe")

    def test_ensure_system_failure_reported(self) -> None:
        profile = runtime.RuntimeProfile(id="sys-fail", packages=("torch",))
        _calls, fake = self._capture(code=1)
        with mock.patch.object(runtime, "_stream", fake):
            with self.assertRaises(runtime.RuntimeError_) as caught:
                runtime.ensure_system(profile)
        self.assertIn("装进程序环境失败", str(caught.exception))
        self.assertTrue(runtime.interrupted("sys-fail", system=True))

    def test_ensure_system_pending_marker_round_trip(self) -> None:
        """装进程序环境也一样：装的过程中留标记，装完抹掉。"""
        fake_python = self.root / "fake-python.exe"
        fake_python.write_text("", encoding="utf-8")
        profile = runtime.RuntimeProfile(id="sys-pend", packages=("torch",))
        seen: list[bool] = []

        def working(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            seen.append(runtime.interrupted("sys-pend", system=True))
            return 0

        with mock.patch.object(runtime, "_stream", working):
            runtime.ensure_system(profile, python=fake_python)
        self.assertEqual(seen, [True])
        self.assertFalse(runtime.interrupted("sys-pend", system=True))

    def test_uninstall_system_clears_pending(self) -> None:
        """从程序环境卸载之后，「上次安装中断」的提示也要消失。"""
        fake_python = self.root / "fake-python.exe"
        fake_python.write_text("", encoding="utf-8")
        profile = runtime.RuntimeProfile(id="sys-pend-off", packages=("torch",))
        pending = runtime.pending_path("sys-pend-off", system=True)
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text("{}", encoding="utf-8")
        self.assertTrue(runtime.interrupted("sys-pend-off", system=True))

        _calls, fake = self._capture()
        with mock.patch.object(runtime, "_stream", fake):
            runtime.uninstall_system(profile, python=fake_python)

        self.assertFalse(pending.exists())
        self.assertFalse(runtime.interrupted("sys-pend-off", system=True))

    def test_system_paths_shape(self) -> None:
        self.assertEqual(runtime.system_python(), Path(sys.executable))
        self.assertEqual(runtime.system_log_file("gpu").name, "runtime-system-gpu.log")
        self.assertEqual(runtime.system_requirements_path("gpu").name, "requirements-gpu.txt")
        self.assertEqual(runtime.system_requirements_path("gpu").parent.name, "system")


class RuntimeControlCase(IsolatedCase):
    """`Control` 直接收子进程（不再靠 0.2 秒轮询）、`clear_logs()` 与本地 whl 安装。"""

    class _FakeProcess:
        """只记调用次数的假子进程：`_terminate` 会先 terminate 再 wait。"""

        def __init__(self) -> None:
            self.terminated = 0
            self.waited = 0
            self.killed = 0
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated += 1
            self.returncode = 0

        def wait(self, timeout=None):
            self.waited += 1
            return self.returncode

        def kill(self) -> None:
            self.killed += 1
            self.returncode = -9

    def test_cancel_and_pause_stop_bound_process(self) -> None:
        control = runtime.Control()
        process = self._FakeProcess()
        control.bind(process)
        self.assertFalse(control.should_cancel())
        control.pause()
        self.assertTrue(control.should_pause())
        self.assertFalse(control.should_cancel())
        self.assertEqual(process.terminated, 1)
        control.clear_pause()
        self.assertFalse(control.should_pause())
        control.cancel()
        self.assertTrue(control.should_cancel())
        self.assertFalse(control.should_pause())  # 取消要把暂停位一起清掉

    def test_bind_after_stop_kills_immediately_and_unbind_stops_watching(self) -> None:
        control = runtime.Control()
        control.cancel()
        late = self._FakeProcess()
        control.bind(late)  # 已经喊停：刚起的进程当场收掉
        self.assertEqual(late.terminated, 1)
        control.unbind(late)
        again = self._FakeProcess()
        control.bind(again)
        control.reset()
        self.assertFalse(control.should_cancel())
        control.pause()
        self.assertEqual(again.terminated, 1)

    def test_stream_with_control_skips_polling_thread(self) -> None:
        """传了 `Control` 就不该再开那个 0.2 秒轮询线程。"""
        log = self.root / "control.log"
        control = runtime.Control()
        with mock.patch.object(runtime, "_watch_stop") as watch:
            code = runtime._stream(
                [sys.executable, "-c", "print('hi')"],
                log=log,
                control=control,
            )
        self.assertEqual(code, 0)
        watch.assert_not_called()
        self.assertIn("hi", log.read_text(encoding="utf-8"))

    def test_stream_without_control_still_watches(self) -> None:
        """老式回调路径保留轮询线程（旧调用方与测试还靠它）。"""
        log = self.root / "watch.log"
        with mock.patch.object(runtime, "_watch_stop", wraps=runtime._watch_stop) as watch:
            runtime._stream(
                [sys.executable, "-c", "print('hi')"],
                log=log,
                should_cancel=lambda: False,
            )
        watch.assert_called_once()

    def test_clear_logs_removes_profile_and_system_logs(self) -> None:
        profile_log = runtime.log_file("logs-gone")
        system_log = runtime.system_log_file("logs-gone")
        for path in (profile_log, system_log):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        runtime.clear_logs("logs-gone", system=True)
        self.assertFalse(profile_log.exists())
        self.assertFalse(system_log.exists())

        profile_log.parent.mkdir(parents=True, exist_ok=True)
        profile_log.write_text("x", encoding="utf-8")
        system_log.parent.mkdir(parents=True, exist_ok=True)
        system_log.write_text("x", encoding="utf-8")
        runtime.discard("logs-gone")  # 清半成品也顺手清日志
        self.assertFalse(profile_log.exists())
        self.assertFalse(system_log.exists())

    def test_check_wheels_reports_look_wrong(self) -> None:
        major, minor = sys.version_info[0], sys.version_info[1]
        other_python = "cp%d%d" % (major, minor + 1 if minor < 20 else minor - 1)
        for name in ("notes.txt", "badname.whl", f"demo-1.0-{other_python}-{other_python}-any.whl",
                     "demo-1.0-cp311-cp311-linux_x86_64.whl"):
            (self.root / name).write_text("", encoding="utf-8")
        problems = runtime.check_wheels(
            [
                self.root / "no-such-file.whl",
                self.root / "notes.txt",
                self.root / "badname.whl",
                self.root / f"demo-1.0-{other_python}-{other_python}-any.whl",
                self.root / "demo-1.0-cp311-cp311-linux_x86_64.whl",
            ]
        )
        joined = "；".join(problems)
        self.assertIn("文件不存在", joined)
        self.assertIn("不是 .whl 文件", joined)
        self.assertIn("文件名不像 wheel", joined)
        self.assertIn("Python", joined)
        self.assertIn("平台", joined)

    def test_check_wheels_accepts_this_machine(self) -> None:
        wheel = self.root / "demo-1.0-py3-none-any.whl"
        wheel.write_text("", encoding="utf-8")
        self.assertEqual(runtime.check_wheels([wheel]), [])

    def test_install_wheels_runs_pip_and_fills_leftovers(self) -> None:
        profile = runtime.RuntimeProfile(
            id="whl-env",
            packages=("torch", "demo>=1.0"),
            index_url="https://example.invalid/simple",
        )
        python = runtime.python_path("whl-env")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        wheel = self.root / "demo-1.0-py3-none-any.whl"
        wheel.write_text("", encoding="utf-8")
        calls: list[list[str]] = []

        def fake(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            calls.append(list(command))
            if on_line is not None:
                on_line("fake output")
            return 0

        with mock.patch.object(runtime, "_stream", fake):
            target = runtime.install_wheels(profile, [str(wheel)], on_line=lambda _line: None)

        self.assertEqual(target, python)
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(command[1:4], ["-m", "pip", "install"])
        self.assertIn("--index-url", command)
        self.assertEqual(command[command.index("--index-url") + 1], "https://example.invalid/simple")
        self.assertIn(str(wheel), command)
        # whl 覆盖到的清单项不重复装，剩下的（比如 -gpu 环境的 nvidia-*-cu12）一起装
        self.assertEqual(command[-1], "torch")
        self.assertNotIn("demo>=1.0", command)
        self.assertTrue(runtime.marker_path("whl-env").exists())
        self.assertFalse(runtime.interrupted("whl-env"))

    def test_network_hint_mentions_local_wheels(self) -> None:
        go = self.root / "go.log"
        go.write_text("ConnectTimeoutError(\"Connection to github.com timed out.\")\n", encoding="utf-8")
        self.assertIn("本地 whl", runtime._network_hint(go))
        ok = self.root / "ok.log"
        ok.write_text("Successfully installed demo-1.0\n", encoding="utf-8")
        self.assertEqual(runtime._network_hint(ok), "")
        self.assertEqual(runtime._network_hint(self.root / "missing.log"), "")

    def test_dist_name_normalises(self) -> None:
        self.assertEqual(runtime._dist_name("llama_cpp_python>=0.3.2"), "llama-cpp-python")
        self.assertEqual(runtime._dist_name("Llama-CPP.Python"), "llama-cpp-python")
        self.assertEqual(runtime._dist_name("torch ; extra == \"x\""), "torch")

    def test_install_wheels_rejects_empty_and_missing(self) -> None:
        profile = runtime.RuntimeProfile(id="whl-empty", packages=())
        with self.assertRaises(runtime.RuntimeError_):
            runtime.install_wheels(profile, [])
        with self.assertRaises(runtime.RuntimeError_):
            runtime.install_wheels(profile, [str(self.root / "no-such.whl")])


class GitHubSourceCase(IsolatedCase):
    """GitHub 下载源：从 pip 日志里认出 GitHub 直链、换成镜像前缀并重试一次。"""

    def test_github_asset_urls_picks_assets(self) -> None:
        text = (
            "  Downloading https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.36/"
            "llama_cpp_python-0.3.36-py3-none-win_amd64.whl (511.4 MB)\n"
            "also https://github.com/a/b/archive/v1.zip,\n"
            "skip https://files.pythonhosted.org/x/demo-1.0-py3-none-any.whl\n"
        )
        urls = runtime.github_asset_urls(text)
        self.assertEqual(
            urls[0],
            "https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.36/"
            "llama_cpp_python-0.3.36-py3-none-win_amd64.whl",
        )
        self.assertEqual(urls[1], "https://github.com/a/b/archive/v1.zip")  # 尾逗号要去掉
        self.assertEqual(runtime.github_asset_urls("nothing here"), [])

    def test_mirror_github_urls_skips_official(self) -> None:
        url = "https://github.com/a/b/releases/download/v1/x.whl"
        self.assertEqual(runtime.mirror_github_urls([url], ("",)), [])  # 官方不算重试
        self.assertEqual(
            runtime.mirror_github_urls([url], ("https://ghproxy.net/", "")),
            [f"https://ghproxy.net/{url}"],
        )
        self.assertEqual(runtime.mirror_github_urls([], ("https://ghproxy.net",)), [])

    def test_ensure_retries_with_mirror_after_github_failure(self) -> None:
        profile = runtime.RuntimeProfile(id="gh-env", packages=("torch",))
        python = runtime.python_path("gh-env")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        url = "https://github.com/a/b/releases/download/v1/x.whl"
        calls: list[list[str]] = []

        def fake(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
            calls.append(list(command))
            Path(log).write_text(
                f"ERROR: ConnectTimeoutError: {url}\n"
                if len(calls) == 1
                else "Successfully installed torch\n",
                encoding="utf-8",
            )
            if on_line is not None:
                on_line(f"fake {len(calls)}")
            return 1 if len(calls) == 1 else 0

        lines: list[str] = []
        with mock.patch.object(runtime, "_stream", fake):
            runtime.ensure(profile, on_line=lines.append, github_prefixes=("https://ghproxy.net",))

        self.assertEqual(len(calls), 2)
        self.assertNotIn(url, calls[0])
        # urls 插在 `-r requirements` 之前（`_pip_command` 的形状）
        self.assertIn(f"https://ghproxy.net/{url}", calls[1])
        self.assertTrue(any("GitHub 下载源重试" in line for line in lines))
        self.assertTrue(runtime.marker_path("gh-env").exists())


class LlamaKwargsCase(IsolatedCase):
    """llama.cpp 构造参数：CPU 全走 CPU；选了 GPU 又没写层数时默认全部上卡。"""

    def test_device_defaults_layers(self) -> None:
        wm = importlib.import_module(PACKAGE + ".worker.worker_main")
        cpu = wm._llama_kwargs({"n_ctx": 4096, "bogus": 1}, "cpu")
        self.assertEqual(cpu["n_gpu_layers"], 0)
        self.assertEqual(cpu["n_ctx"], 4096)
        self.assertNotIn("bogus", cpu)
        self.assertFalse(cpu["verbose"])
        cuda = wm._llama_kwargs({}, "cuda:0")
        self.assertEqual(cuda["n_gpu_layers"], -1)
        self.assertEqual(cuda["main_gpu"], 0)
        metal = wm._llama_kwargs({}, "mps")
        self.assertEqual(metal["n_gpu_layers"], -1)
        given = wm._llama_kwargs({"n_gpu_layers": 8, "main_gpu": 1}, "cuda")
        self.assertEqual(given["n_gpu_layers"], 8)
        self.assertEqual(given["main_gpu"], 1)


class RuntimeTwinCase(IsolatedCase):
    """CPU / GPU 双胞胎运行环境：模型声明 CPU 版、只装了 GPU 版时也要能跑。"""

    def test_twin_ids_and_resolve(self) -> None:
        self.assertEqual(runtime.twin_ids("llama-cpp"), ("llama-cpp-gpu",))
        self.assertEqual(runtime.twin_ids("llama-cpp-gpu"), ("llama-cpp",))
        self.assertEqual(runtime.twin_ids(""), ())
        self.assertEqual(runtime.resolve_id(""), "")
        self.assertEqual(runtime.resolve_id("nope"), "nope")

        python = runtime.python_path("llama-cpp-gpu")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        try:
            self.assertEqual(runtime.resolve_id("llama-cpp"), "llama-cpp-gpu")
            self.assertEqual(runtime.resolve_id("llama-cpp-gpu"), "llama-cpp-gpu")
            self.assertIn("llama-cpp-gpu", runtime.installed_ids())
        finally:
            shutil.rmtree(runtime.venv_dir("llama-cpp-gpu").parent, ignore_errors=True)

    def test_pick_python_accepts_twin_and_lists_installed(self) -> None:
        record = _Record(self.root / "weights" / "model.gguf")
        record.runtime = {"adapter": "worker", "backend": "fake", "profile": "llama-cpp", "params": {}}
        python = runtime.python_path("llama-cpp-gpu")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        try:
            adapter = worker_adapters.WorkerAdapter(record, _Settings(allow_system_env=False))
            self.assertEqual(adapter._pick_python(), python)
        finally:
            shutil.rmtree(runtime.venv_dir("llama-cpp-gpu").parent, ignore_errors=True)

        adapter = worker_adapters.WorkerAdapter(record, _Settings(allow_system_env=False))
        with self.assertRaises(worker_adapters.AdapterError) as caught:
            adapter._pick_python()
        self.assertIn("尚未安装", str(caught.exception))

    def test_pick_python_prefers_program_env_when_allowed(self) -> None:
        record = _Record(self.root / "weights" / "model.gguf")
        record.runtime = {"adapter": "worker", "backend": "fake", "profile": "llama-cpp", "params": {}}
        python = runtime.python_path("llama-cpp-gpu")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        try:
            adapter = worker_adapters.WorkerAdapter(record, _Settings(allow_system_env=True))
            self.assertEqual(adapter._pick_python(), Path(sys.executable))
        finally:
            shutil.rmtree(runtime.venv_dir("llama-cpp-gpu").parent, ignore_errors=True)

    def test_slug_dir_name_keeps_empty(self) -> None:
        from dm_plugin.lib.model.paths import local_dir, local_root, slug_dir_name

        self.assertEqual(slug_dir_name(""), "")
        self.assertEqual(slug_dir_name(None), "")
        self.assertEqual(slug_dir_name("local/demo"), "demo")
        self.assertEqual(local_dir(""), local_root())
        self.assertFalse((local_root() / "model").exists())


class WorkerCommandCase(IsolatedCase):
    """`build_command` 的命令行形状。"""

    def test_build_command_shape(self) -> None:
        record = _Record(self.root / "weights" / "model.gguf")
        command = worker_pkg.build_command(record, _Settings(), Path(sys.executable))
        self.assertEqual(command[0], str(sys.executable))
        self.assertEqual(command[1], "-u")
        self.assertEqual(command[2], str(WORKER_SCRIPT))
        self.assertIn("--backend", command)
        self.assertEqual(command[command.index("--backend") + 1], "fake")
        self.assertEqual(command[command.index("--model") + 1], str(record.primary_file()))
        self.assertEqual(command[command.index("--device") + 1], "auto")
        self.assertTrue(WORKER_SCRIPT.exists())


class WorkerAdapterCase(IsolatedCase):
    """假 worker 脚本上的适配器生命周期。"""

    def setUp(self) -> None:
        super().setUp()
        self.fake_script = self.root / "fake_worker.py"
        self.fake_script.write_text(_FAKE_WORKER, encoding="utf-8")
        self.record = _Record(self.root / "weights" / "model.gguf")
        self.patcher = mock.patch.object(
            worker_adapters,
            "build_command",
            lambda record, settings, python: [str(sys.executable), "-u", str(self.fake_script)],
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _adapter(self, **kwargs):
        adapter = worker_adapters.WorkerAdapter(self.record, _Settings(**kwargs))
        self.addCleanup(adapter.stop)
        return adapter

    def test_lifecycle_and_no_leftover_process(self) -> None:
        adapter = self._adapter()
        adapter.start()
        self.assertTrue(adapter.loaded)
        self.assertTrue(adapter.available())
        info = adapter.info()
        self.assertEqual(info["adapter"], "worker")
        self.assertEqual(info["backend"], "fake")
        self.assertTrue(info["pid"])
        self.assertTrue(info["loaded"])

        self.assertEqual(adapter.invoke("completion", {"input": "hi"}), "echo:hi")

        proc = adapter._proc
        adapter.stop()
        self.assertFalse(adapter.loaded)
        self.assertIsNotNone(proc.poll())
        self.assertFalse(adapter.info()["pid"])
        # 幂等：再停一次不报错
        adapter.stop()

    def test_invoke_stream(self) -> None:
        adapter = self._adapter()
        adapter.start()
        pieces = list(adapter.invoke("completion", {"input": "hi"}, stream=True))
        self.assertEqual(pieces, ["he", "llo"])
        self.assertEqual("".join(pieces), "hello")
        adapter.stop()

    def test_invoke_timeout(self) -> None:
        adapter = self._adapter()
        adapter.start()
        with self.assertRaises(worker_adapters.AdapterError):
            adapter.invoke("completion", {"input": "hi", "sleep": 5}, timeout=0.5)
        adapter.stop()

    def test_invoke_before_start(self) -> None:
        adapter = self._adapter()
        with self.assertRaises(worker_adapters.AdapterError):
            adapter.invoke("completion", {"input": "hi"})

    def test_pick_python_requires_env_or_system(self) -> None:
        record = _Record(self.root / "weights" / "model.gguf")
        record.runtime = {"adapter": "worker", "backend": "fake", "profile": "not-installed", "params": {}}
        adapter = worker_adapters.WorkerAdapter(record, _Settings(allow_system_env=False))
        self.assertFalse(adapter.available())
        with self.assertRaises(worker_adapters.AdapterError):
            adapter.start()

    def test_worker_exit_reported(self) -> None:
        adapter = self._adapter()
        adapter.start()
        adapter._proc.kill()
        adapter._proc.wait(timeout=5)
        with self.assertRaises(worker_adapters.AdapterError):
            adapter.invoke("completion", {"input": "hi"}, timeout=5)
        adapter.stop()


class WorkerScriptCase(IsolatedCase):
    """直接跑 `worker_main.py`：它只用标准库，因此可以真跑。"""

    def _run_frames(self, frames: list[dict]) -> list[dict]:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(WORKER_SCRIPT), "--backend", "", "--model", "", "--device", "auto"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        payload = "".join(json.dumps(frame) + "\n" for frame in frames)
        stdout, _stderr = proc.communicate(payload, timeout=60)
        return [json.loads(line) for line in stdout.splitlines() if line.strip()]

    def test_ping_and_info(self) -> None:
        frames = self._run_frames(
            [
                {"id": 1, "op": "ping", "payload": {}},
                {"id": 2, "op": "info", "payload": {}},
            ]
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"])
        self.assertTrue(by_id[1]["result"]["pong"])
        self.assertIn("python", by_id[1]["result"])
        self.assertTrue(by_id[2]["ok"])
        self.assertFalse(by_id[2]["result"]["loaded"])

    def test_unknown_backend(self) -> None:
        frames = self._run_frames([{"id": 1, "op": "load", "payload": {"backend": "nope"}}])
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0]["ok"])
        self.assertIn("未知后端", frames[0]["error"])
        self.assertIn("当前 worker 支持", frames[0]["error"])

    def test_missing_dependency_message(self) -> None:
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "transformers", "model_path": str(self.root)}},
                {"id": 2, "op": "invoke", "payload": {"task": "completion", "input": "hi"}},
                {"id": 3, "op": "bogus", "payload": {}},
            ]
        )
        self.assertFalse(frames[0]["ok"])
        self.assertTrue(frames[0]["error"])
        try:
            importlib.import_module("transformers")
        except ImportError:
            # 目标解释器里没装 transformers：必须给出「去运行环境页安装」的提示
            self.assertIn("缺少依赖", frames[0]["error"])
        # 没 load 成功就 invoke，要报错而不是崩
        self.assertFalse(frames[1]["ok"])
        self.assertTrue(frames[1]["error"])
        self.assertFalse(frames[2]["ok"])
        self.assertIn("未知操作", frames[2]["error"])


_FAKES = {
    "faster_whisper.py": '''
class _Segment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _Info:
    language = "zh"
    duration = 1.5


class WhisperModel:
    def __init__(self, model_size_or_path, device="auto", compute_type="default", **kwargs):
        self.model = model_size_or_path
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, audio, **options):
        self.audio = audio
        self.options = options
        return [_Segment(0.0, 1.0, "你好"), _Segment(1.0, 1.5, "世界")], _Info()
''',
    "piper/__init__.py": '''
from .voice import PiperVoice

__all__ = ["PiperVoice"]
''',
    "piper/voice.py": '''
class PiperVoice:
    def __init__(self, path):
        self.path = path

    @classmethod
    def load(cls, path, **kwargs):
        return cls(path)

    def synthesize(self, text, wav_file, **kwargs):
        self.last_text = text
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\\x00\\x00" * 16)
''',
    "diffusers.py": '''
class _Image:
    def save(self, path):
        with open(path, "wb") as handle:
            handle.write(b"\\x89PNG\\r\\n")


class _Result:
    def __init__(self):
        self.images = [_Image()]


class _Pipeline:
    def __init__(self, source):
        self.source = source
        self.device = ""

    @classmethod
    def from_pretrained(cls, source, **kwargs):
        return cls(source)

    def to(self, device):
        self.device = device
        return self

    def __call__(self, prompt="", **kwargs):
        self.prompt = prompt
        self.options = kwargs
        return _Result()


class AutoPipelineForText2Image(_Pipeline):
    pass
''',
    "rapidocr.py": '''
class _Output:
    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class RapidOCR:
    def __init__(self, config_path=None, params=None):
        # 3.x 的参数是 "Global.text_score" 这种点号键
        for key in (params or {}):
            if "." not in key:
                raise ValueError(f"params 必须是点号键：{key}")
        self.config_path = config_path
        self.params = params

    def __call__(self, img_content, use_det=None, use_cls=None, use_rec=None, text_score=None):
        self.called_with = (img_content, use_det, use_cls, use_rec, text_score)
        return _Output([[[0, 0], [10, 0], [10, 5], [0, 5]]], ["你好"], [0.98])
''',
    "rapidocr_onnxruntime.py": '''
class RapidOCR:
    def __init__(self, **kwargs):
        self.options = kwargs

    def __call__(self, image, **kwargs):
        self.image = image
        return [[[[0, 0], [10, 0], [10, 5], [0, 5]], "你好", 0.98]], 0.02
''',
}


class WorkerBackendCase(IsolatedCase):
    """用假模块把 worker 新增的四个后端（语音 / 合成 / 图像 / OCR）真跑一遍。

    真跑的是 `worker_main.py` + 假依赖：验证后端名分派、参数整形、结果整形与卸载，
    不需要装 torch / faster-whisper 这些重包。
    """

    #: 让 rapidocr 两个候选模块都 import 失败，用来验证「缺少依赖」提示（不依赖宿主装了什么）
    _BROKEN_RAPIDOCR = 'raise ImportError("rapidocr 未安装")'

    def _fake_dir(self, *, rapidocr: str = "v1") -> Path:
        folder = self.root / ("fakes-" + rapidocr)
        if rapidocr == "missing":
            for name in ("rapidocr.py", "rapidocr_onnxruntime.py"):
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self._BROKEN_RAPIDOCR + "\n", encoding="utf-8")
            return folder
        skip = "rapidocr_onnxruntime.py" if rapidocr == "v3" else "rapidocr.py"
        for name, source in _FAKES.items():
            if name == skip:
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.strip() + "\n", encoding="utf-8")
        return folder

    def _run_frames(self, frames: list[dict], *, with_fakes: bool = True, rapidocr: str = "v1") -> list[dict]:
        env = {**os.environ, "PYTHONUTF8": "1"}
        if with_fakes:
            env["PYTHONPATH"] = str(self._fake_dir(rapidocr=rapidocr))
        proc = subprocess.Popen(
            [sys.executable, "-u", str(WORKER_SCRIPT), "--backend", "", "--model", "", "--device", "auto"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        payload = "".join(json.dumps(frame) + "\n" for frame in frames)
        stdout, _stderr = proc.communicate(payload, timeout=60)
        return [json.loads(line) for line in stdout.splitlines() if line.strip()]

    def test_faster_whisper_asr(self) -> None:
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "faster_whisper", "model_path": "small"}},
                {"id": 2, "op": "invoke", "payload": {"task": "asr", "input": "audio.wav", "params": {"language": "zh"}}},
                {"id": 3, "op": "invoke", "payload": {"task": "chat", "input": "hi"}},
                {"id": 4, "op": "unload", "payload": {}},
            ]
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"], by_id[1])
        self.assertTrue(by_id[2]["ok"], by_id[2])
        self.assertEqual(by_id[2]["result"]["text"], "你好世界")
        self.assertEqual([row["text"] for row in by_id[2]["result"]["segments"]], ["你好", "世界"])
        self.assertEqual(by_id[2]["result"]["language"], "zh")
        self.assertEqual(by_id[2]["result"]["duration"], 1.5)
        self.assertIn("不支持任务", by_id[3]["error"])
        self.assertFalse(by_id[4]["result"]["loaded"])

    def test_piper_tts(self) -> None:
        output = self.root / "out.wav"
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "piper", "model_path": "voice.onnx"}},
                {"id": 2, "op": "invoke", "payload": {"task": "tts", "input": "你好", "params": {"output": str(output)}}},
                {"id": 3, "op": "invoke", "payload": {"task": "chat", "input": "hi"}},
            ]
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"], by_id[1])
        self.assertTrue(by_id[2]["ok"], by_id[2])
        self.assertEqual(by_id[2]["result"]["file"], str(output))
        self.assertGreater(by_id[2]["result"]["size"], 0)
        self.assertTrue(output.read_bytes().startswith(b"RIFF"))
        self.assertIn("不支持任务", by_id[3]["error"])

    def test_piper_needs_model_path(self) -> None:
        frames = self._run_frames([{"id": 1, "op": "load", "payload": {"backend": "piper", "model_path": ""}}])
        self.assertFalse(frames[0]["ok"])
        self.assertIn("需要 .onnx", frames[0]["error"])

    def test_diffusers_image(self) -> None:
        output = self.root / "out.png"
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "diffusers", "model_path": "sdxl-turbo"}},
                {"id": 2, "op": "invoke", "payload": {"task": "image", "input": "一只猫", "params": {"output": str(output), "num_inference_steps": 2}}},
            ]
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"], by_id[1])
        self.assertTrue(by_id[2]["ok"], by_id[2])
        self.assertEqual(by_id[2]["result"]["count"], 1)
        self.assertEqual(by_id[2]["result"]["file"], str(output))
        self.assertTrue(output.exists())

    def test_rapidocr_ocr(self) -> None:
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "rapidocr", "model_path": ""}},
                {"id": 2, "op": "invoke", "payload": {"task": "ocr", "input": "page.png"}},
                {"id": 3, "op": "invoke", "payload": {"task": "chat", "input": "hi"}},
            ]
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"], by_id[1])
        self.assertTrue(by_id[2]["ok"], by_id[2])
        self.assertEqual(by_id[2]["result"]["text"], "你好")
        self.assertEqual(by_id[2]["result"]["lines"][0]["score"], 0.98)
        self.assertIn("不支持任务", by_id[3]["error"])

    def test_rapidocr_v3_api(self) -> None:
        """rapidocr 3.x：用 params 字典构造，返回 boxes/txts/scores 对象。"""
        frames = self._run_frames(
            [
                {"id": 1, "op": "load", "payload": {"backend": "rapidocr", "model_path": "", "params": {"text_score": 0.6}}},
                {"id": 2, "op": "invoke", "payload": {"task": "ocr", "input": "page.png", "params": {"text_score": 0.7}}},
                {"id": 3, "op": "unload", "payload": {}},
            ],
            rapidocr="v3",
        )
        by_id = {frame["id"]: frame for frame in frames}
        self.assertTrue(by_id[1]["ok"], by_id[1])
        self.assertTrue(by_id[2]["ok"], by_id[2])
        self.assertEqual(by_id[2]["result"]["text"], "你好")
        self.assertEqual(by_id[2]["result"]["lines"][0]["score"], 0.98)
        self.assertEqual(by_id[2]["result"]["lines"][0]["box"][0], [0, 0])
        self.assertFalse(by_id[3]["result"]["loaded"])

    def test_new_backend_dependency_hint(self) -> None:
        frames = self._run_frames(
            [{"id": 1, "op": "load", "payload": {"backend": "rapidocr", "model_path": ""}}],
            rapidocr="missing",
        )
        self.assertFalse(frames[0]["ok"])
        self.assertIn("缺少依赖", frames[0]["error"])


class _FakeTorch:
    """只带 `_torch_device` / `_mps_ok` 会问到的属性。"""

    def __init__(self, *, cuda: bool, mps: bool) -> None:
        self.cuda = types.SimpleNamespace(
            is_available=lambda: cuda,
            device_count=lambda: 3,
            get_device_name=lambda index: f"GPU {index}",
        )
        self.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))


class WorkerRequestShapeCase(unittest.TestCase):
    """请求形状：`messages` / `prompt` 这类请求字段不能漏进 params 再被后端展开一次。

    真实事故：界面按 `{"messages": [...]}` 传对话，适配器把整个负载塞进 params，worker 又
    `create_chat_completion(messages=..., **options)` ⇒ `TypeError: got multiple values for
    keyword argument 'messages'`（模型刚加载完就报错卸载）。
    """

    @staticmethod
    def _worker_module():
        """就地加载 `worker_main.py`：它 import 时会把 sys.stdout 换成 stderr，用完还原。"""
        import importlib.util

        saved = sys.stdout
        try:
            spec = importlib.util.spec_from_file_location("model_worker_main_request_shape", WORKER_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.stdout = saved
        return module

    def test_model_class_prefers_declared_architecture(self) -> None:
        """transformers 5.18 没有 AutoModelForVision2Seq：要用配置里写明的生成式类，别退成 AutoModel。

        真事故：回退成 `AutoModel` 后 BLIP 变成没有 `generate` 的 `BlipModel`（用户 m42466）。
        """
        worker = self._worker_module()

        class AutoModelCls: ...

        class BlipGen: ...

        class NewName: ...

        fake = types.SimpleNamespace(AutoModel=AutoModelCls, BlipForConditionalGeneration=BlipGen)
        self.assertIs(
            worker._model_class(fake, "vision2seq", ["BlipForConditionalGeneration"]), BlipGen
        )
        self.assertIs(worker._model_class(fake, "vision2seq", []), AutoModelCls)
        newer = types.SimpleNamespace(AutoModel=AutoModelCls, AutoModelForImageTextToText=NewName)
        self.assertIs(worker._model_class(newer, "vision2seq", []), NewName)
        self.assertIs(worker._model_class(fake, "base", []), AutoModelCls)

    def test_adapter_keeps_messages_out_of_params(self) -> None:
        adapter = importlib.import_module(PACKAGE + ".adapters.worker")
        messages = [{"role": "user", "content": "你好"}]
        body = adapter.WorkerAdapter._body(
            None, "chat", {"messages": messages, "temperature": 0.3}, False
        )
        self.assertEqual(body["input"], messages)  # 对话内容即请求内容
        self.assertEqual(body["params"], {"temperature": 0.3})  # 其余顶层键才是调用参数
        self.assertNotIn("messages", body["params"])

    def test_worker_options_drop_request_keys(self) -> None:
        worker = self._worker_module()
        options = worker._options(
            {
                "task": "chat",
                "input": "x",
                "stream": False,
                "params": {"messages": [{"role": "user"}], "prompt": "hi", "temperature": 0.2},
            }
        )
        self.assertEqual(options, {"temperature": 0.2})

    def test_worker_clean_params_still_filters_unknown_keys(self) -> None:
        worker = self._worker_module()
        cleaned = worker._clean_params({"a": 1, "b": None, "c": 3}, {"a", "b"})
        self.assertEqual(cleaned, {"a": 1})


class DeviceChoiceCase(IsolatedCase):
    """推理设备：设置里的设备串怎么拆、`auto` 怎么挑（纯函数，不需要真显卡）。"""

    @staticmethod
    def _worker_module():
        """就地加载 `worker_main.py`：它 import 时会把 sys.stdout 换成 stderr，用完还原。"""
        import importlib.util

        saved = sys.stdout
        try:
            spec = importlib.util.spec_from_file_location("model_worker_main_under_test", WORKER_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.stdout = saved
        return module

    def test_device_parts(self) -> None:
        worker = self._worker_module()
        self.assertEqual(worker._device_parts("cuda:3"), ("cuda", 3))
        self.assertEqual(worker._device_parts(" CUDA:1 "), ("cuda", 1))
        self.assertEqual(worker._device_parts("cuda"), ("cuda", None))
        self.assertEqual(worker._device_parts("cpu"), ("cpu", None))
        self.assertEqual(worker._device_parts(""), ("auto", None))
        self.assertEqual(worker._device_parts("weird"), ("weird", None))

    def test_torch_device(self) -> None:
        worker = self._worker_module()
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=True, mps=False), "auto"), "cuda")
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=True, mps=False), "cuda:2"), "cuda:2")
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=False, mps=True), "auto"), "mps")
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=False, mps=False), "auto"), "cpu")
        # 选了个这台机器没有的设备 → 退回 CPU，而不是报错
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=False, mps=False), "cuda"), "cpu")
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=False, mps=False), "mps"), "cpu")
        self.assertEqual(worker._torch_device(_FakeTorch(cuda=False, mps=False), "weird"), "cpu")


class TransformersCaptionCase(unittest.TestCase):
    """图像描述不走 `pipeline()` 的任务名。

    真事故：新版 transformers（5.18）已经没有 `image-to-text` 这个任务（只有
    `image-text-to-text`），BLIP 的对齐请求一发起就抛
    `KeyError: Unknown task image-to-text`。现在 `caption` / `ocr` 直接用手上的
    `AutoProcessor` + 生成模型跑。
    """

    @staticmethod
    def _worker_module():
        """就地加载 `worker_main.py`：它 import 时会把 sys.stdout 换成 stderr，用完还原。"""
        import importlib.util

        saved = sys.stdout
        try:
            spec = importlib.util.spec_from_file_location("model_worker_main_caption", WORKER_SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.stdout = saved
        return module

    def test_caption_and_ocr_are_not_pipeline_tasks(self) -> None:
        backend = self._worker_module().TransformersBackend()
        self.assertNotIn("caption", backend.PIPELINES)
        self.assertNotIn("ocr", backend.PIPELINES)
        self.assertIn("asr", backend.PIPELINES)

    def test_caption_generates_text_with_processor(self) -> None:
        worker = self._worker_module()

        class _NoGrad:
            def __enter__(self):
                return None

            def __exit__(self, *args):
                return False

        class _Image:
            def convert(self, mode):
                return self

        class _ImageModule:
            Image = _Image

            @staticmethod
            def open(path):
                return _Image()

        class _Inputs(dict):
            def to(self, device):
                return self

        class _Processor:
            def __call__(self, **kwargs):
                return _Inputs()

            def batch_decode(self, output, skip_special_tokens=True):
                return ["一只橘猫"]

        class _Model:
            device = "cpu"

            def __init__(self):
                self.kwargs = {}

            def generate(self, **kwargs):
                self.kwargs = kwargs
                return [[1, 2]]

        model = _Model()
        backend = worker.TransformersBackend()
        backend._tokenizer = _Processor()
        backend._model = model
        backend._torch = types.SimpleNamespace(no_grad=_NoGrad)
        with mock.patch.object(worker, "_require", return_value=_ImageModule):
            result = backend._caption("C:/pic/x.png", {"max_new_tokens": 10, "temperature": 0.8})
        self.assertEqual(result, {"result": "一只橘猫"})
        self.assertEqual(model.kwargs["max_new_tokens"], 10)

    def test_caption_without_model_raises(self) -> None:
        backend = self._worker_module().TransformersBackend()
        with self.assertRaises(ValueError):
            backend._caption("C:/pic/x.png", {})


class DeviceProbeCase(IsolatedCase):
    """`runtime/probe.py`：设备与依赖探测都起子进程，结果解析要稳、起不来要有退路。"""

    @staticmethod
    def _probe_module():
        return importlib.import_module(PACKAGE + ".runtime.probe")

    def test_import_name(self) -> None:
        probe = self._probe_module()
        self.assertEqual(probe.import_name("faster-whisper>=1.0"), "faster_whisper")
        self.assertEqual(probe.import_name("rapidocr-onnxruntime"), "rapidocr_onnxruntime")
        self.assertEqual(probe.import_name("Pillow"), "PIL")
        self.assertEqual(probe.import_name("some-pkg[extra]>=2"), "some_pkg")
        self.assertEqual(probe.import_name("onnxruntime-gpu"), "onnxruntime")
        self.assertEqual(probe.import_name(""), "")

    def test_detect_devices_always_has_cpu(self) -> None:
        probe = self._probe_module()
        found = probe.detect_devices([Path(sys.executable)])
        self.assertIn("cpu", [item.get("id") for item in found], found)

    def test_detect_devices_broken_python(self) -> None:
        probe = self._probe_module()
        found = probe.detect_devices([self.root / "no-such-python.exe"])
        self.assertEqual([item.get("id") for item in found], ["cpu"])

    def test_detect_hardware_reports_names(self) -> None:
        probe = self._probe_module()
        payload = probe.detect_hardware([Path(sys.executable)])
        self.assertIsInstance(payload, dict, payload)
        devices = payload.get("devices") or ()
        self.assertTrue(devices, payload)
        for item in devices:
            self.assertTrue(item.get("id"), item)
            self.assertTrue(str(item.get("name") or "").strip(), item)
            self.assertIn("usable", item)
        cpu = [item for item in devices if item.get("id") == "cpu"]
        self.assertEqual(len(cpu), 1, devices)
        self.assertIn("处理器", cpu[0]["name"])
        self.assertIsInstance(payload.get("others"), tuple, payload)

    def test_detect_hardware_broken_python(self) -> None:
        probe = self._probe_module()
        payload = probe.detect_hardware([self.root / "no-such-python.exe"])
        self.assertEqual([item.get("id") for item in payload["devices"]], ["cpu"])
        self.assertEqual(tuple(payload["others"]), ())

    def test_missing_program_packages(self) -> None:
        probe = self._probe_module()
        missing = probe.missing_program_packages(["sys", "definitely-not-a-real-pkg-xyz"])
        self.assertEqual(tuple(missing or ()), ("definitely-not-a-real-pkg-xyz",))

    def test_missing_program_packages_broken_python(self) -> None:
        probe = self._probe_module()
        self.assertIsNone(probe.missing_program_packages(["sys"], python=self.root / "no-such-python.exe"))

    def test_missing_program_group(self) -> None:
        """一套运行环境一次批量问完：返回每组缺的原始声明，空组不进报告。"""
        probe = self._probe_module()
        report = probe.missing_program_group(
            {
                "ok": ["sys"],
                "bad": ["definitely-not-a-real-pkg-xyz>=1.0"],
                "empty": [],
            }
        )
        self.assertEqual(report, {"ok": (), "bad": ("definitely-not-a-real-pkg-xyz>=1.0",)})
        self.assertEqual(probe.missing_program_group({}), {})

    def test_distribution_without_module_counts_as_installed(self) -> None:
        """装了轮子但没有同名顶层模块（`nvidia-*` / `onnxruntime-gpu` 这类）也算装上了。"""
        probe = self._probe_module()
        site = self.root / "fake_site"
        dist = site / "fake_dist_only-1.0.0.dist-info"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: fake-dist-only\nVersion: 1.0.0\n", encoding="utf-8"
        )
        saved = dict(probe._PROBE_ENV)
        probe._PROBE_ENV["PYTHONPATH"] = str(site)
        try:
            missing = probe.missing_program_packages(["fake-dist-only>=1.0"])
        finally:
            probe._PROBE_ENV.clear()
            probe._PROBE_ENV.update(saved)
        self.assertEqual(tuple(missing or ()), ())

    def test_missing_program_group_broken_python(self) -> None:
        probe = self._probe_module()
        self.assertIsNone(probe.missing_program_group({"x": ["sys"]}, python=self.root / "no-such-python.exe"))


if __name__ == "__main__":
    unittest.main()
