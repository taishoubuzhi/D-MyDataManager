"""worker 适配器：把本地模型放进「运行环境 venv」里的子进程运行，通过 JSON-Lines 对话。

和 HTTP 适配器的区别：模型跑在本机子进程里，因此适配器要负责进程生命周期、心跳探活、
流式分段转发，以及进程意外退出后的错误上报（下一次 `invoke` 抛 `AdapterError`，由
`ModelManager` 决定是否重新 `start()`）。

协议见 MODEL_PLUGIN.md §4.1 与 `worker/worker_main.py`。
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from app.sdk.console import console_for

from .base import Adapter, AdapterError
from ..paths import clear_model_logs, model_log_file
from ..runtime import installed_ids, python_path, resolve_id
from ..worker import WORKER_SCRIPT, build_command

__all__ = ["WorkerAdapter"]

#: worker 子进程没有程序 SDK，只能往 stderr 说；这里替它播报到控制台
_console = console_for("builtin.lib.model.worker")
#: worker 自己发的行（`worker_main._log` 统一带这个前缀）
_WORKER_LINE = re.compile(r"^\[worker\]")
#: 看着像报错的行：从这里开始的后继行（含整段栈）都按 error 播
_ERROR_WORDS = re.compile(r"Traceback|Error|Exception|error:|failed|失败|异常")

#: Windows 上不弹控制台窗口（照 `src/app/core/acl.py:22`）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: 加载大模型可能很慢，给足时间
_LOAD_TIMEOUT = 1800.0
#: 心跳探测的超时（比 interval 略长，别把慢响应误判成僵死）
_PING_TIMEOUT = 10.0


class WorkerAdapter(Adapter):
    """子进程 worker 适配器。"""

    name = "worker"

    def __init__(self, record: Any, settings: Any) -> None:
        super().__init__(record, settings)
        self._proc: subprocess.Popen[str] | None = None
        self._log_path: Path | None = None
        self._loaded = False
        self._dead = False
        self._next_id = 0
        self._busy = 0
        #: req_id → 该请求的响应队列（delta / frame / exit）
        self._pending: dict[int, "queue.Queue[tuple[str, Any]]"] = {}
        self._write_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_flag = threading.Event()
        self._cancel_flag = threading.Event()
        self._threads: list[threading.Thread] = []
        self._restarts = 0

    # ------------------------------------------------------------ 基类接口

    @property
    def loaded(self) -> bool:
        """进程活着且 `load` 成功过。"""
        return bool(self._loaded and self._alive())

    def available(self) -> bool:
        """解释器能不能选出来（不代表依赖已装）。"""
        try:
            self._pick_python()
        except AdapterError:
            return False
        return True

    def info(self) -> dict[str, Any]:
        proc = self._proc
        return {
            "adapter": "worker",
            "backend": self._backend_name(),
            "pid": int(proc.pid) if proc is not None and proc.poll() is None else 0,
            "loaded": self.loaded,
            "profile": self._profile_id(),
            "log": str(self._log_path) if self._log_path else "",
        }

    def start(self) -> None:
        """选解释器 → 拉起 worker → 发送 `load`。已运行则直接返回。"""
        with self._state_lock:
            if self._alive():
                return
            if self._proc is not None:
                self._restarts += 1
            python = self._pick_python()
            command = build_command(self.record, self.settings, python)
            self._log_path = self._make_log_path()
            self._write_log_header()
            try:
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=_CREATE_NO_WINDOW,
                    cwd=str(WORKER_SCRIPT.parent),
                )
            except OSError as exc:
                raise AdapterError(f"无法启动 worker：{exc}") from exc

            self._proc = proc
            self._pending.clear()
            self._dead = False
            self._loaded = False
            self._stop_flag.clear()
            self._cancel_flag.clear()
            self._threads = [
                self._spawn(self._pump_stdout, proc),
                self._spawn(self._pump_stderr, proc, self._log_path),
            ]

        try:
            result = self._request("load", self._load_payload(), timeout=_LOAD_TIMEOUT)
        except AdapterError:
            self._loaded = False
            raise
        self._loaded = True
        with self._state_lock:
            self._threads.append(self._spawn(self._heartbeat))
        _ = result

    def invoke(self, task: str, payload: Any = None, *, stream: bool = False, timeout: float | None = None) -> Any:
        """执行任务；`stream=True` 时返回逐段 yield 文本的生成器。"""
        body = self._body(task, payload, stream)
        if not self._alive():
            raise AdapterError("worker 进程未运行（已退出或尚未 start），请重新加载模型")
        if stream:
            return self._iterate("invoke", body, timeout)
        result = self._request("invoke", body, timeout=timeout)
        return self._extract(task, result)

    def stop(self) -> None:
        """terminate → wait(5) → kill；可重复调用。"""
        self._stop_flag.set()
        proc = self._proc
        if proc is None:
            self._loaded = False
            return
        # 关掉 stdin，让 worker 的主循环自然结束（比直接 terminate 干净）
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况
                    pass
        for stream_obj in (proc.stdout, proc.stderr):
            try:
                if stream_obj is not None:
                    stream_obj.close()
            except (OSError, ValueError):
                pass
        for thread in list(self._threads):
            if thread.is_alive():
                thread.join(timeout=2)
        self._threads = []
        self._loaded = False
        self._dead = True
        for waiter in list(self._pending.values()):
            waiter.put(("exit", proc.poll()))

    def cancel(self) -> None:
        """请求取消正在跑的推理：置本地标志 + 给 worker 发一帧 `cancel`（不等回复）。"""
        self._cancel_flag.set()
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return
        with self._write_lock:
            self._next_id += 1
            frame = {"id": self._next_id, "op": "cancel", "payload": {}}
            try:
                proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------ 选择解释器

    def _profile_id(self) -> str:
        runtime = getattr(self.record, "runtime", None)
        runtime = runtime if isinstance(runtime, dict) else {}
        return str(runtime.get("profile") or "").strip()

    def _backend_name(self) -> str:
        runtime = getattr(self.record, "runtime", None)
        runtime = runtime if isinstance(runtime, dict) else {}
        return str(runtime.get("backend") or getattr(self.record, "backend", "") or "")

    def _pick_python(self) -> Path:
        """解释器：勾了「允许装进程序环境」就用程序解释器，否则用 profile 的 venv（认 CPU/GPU 双胞胎）。"""
        if getattr(self.settings, "allow_system_env", False):
            return Path(sys.executable)
        profile_id = self._profile_id()
        if profile_id:
            # CPU / GPU 版是同一批包的两套环境：模型写 `llama-cpp`、只装了 `llama-cpp-gpu` 也用
            resolved = resolve_id(profile_id)
            candidate = python_path(resolved)
            if candidate.exists():
                return candidate
            have = "、".join(installed_ids()) or "一个都没装"
            raise AdapterError(
                f"运行环境 {profile_id} 尚未安装（已经装的：{have}），请到「运行环境」页安装后重试"
            )
        raise AdapterError("没有可用的运行环境：请先安装运行环境，或允许使用程序自带解释器")

    def _make_log_path(self) -> Path:
        """一条模型一个日志文件（不再按时间戳攒）。

        顺手清掉老版本留下的时间戳日志，否则「一个模型只留最新一份」做不到。
        """
        model_id = str(getattr(self.record, "id", "worker"))
        clear_model_logs(model_id)
        return model_log_file(model_id)

    def _write_log_header(self) -> None:
        """日志只留最新：每次启动把文件重写成一行表头。"""
        path = self._log_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            name = str(getattr(self.record, "name", "") or "")
            path.write_text(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 {name}\n", encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ 请求/响应

    def _body(self, task: str, payload: Any, stream: bool) -> dict[str, Any]:
        data = dict(payload) if isinstance(payload, dict) else {"input": payload}
        # 对话内容调用方按 `{"messages": [...]}` 传（和 HTTP 适配器一个写法）：它属于请求内容，
        # 不能漏进 params —— 后端会显式传 messages=，params 里再来一份就成了重复关键字参数。
        content = data.get("input")
        if content is None and "messages" in data:
            content = data["messages"]
        body = {
            "task": str(task),
            "input": content,
            "params": dict(data.get("params") or {}),
            "stream": bool(stream),
        }
        for key, value in data.items():
            if key not in ("input", "messages", "params", "task", "stream"):
                body["params"].setdefault(key, value)
        return body

    def _load_payload(self) -> dict[str, Any]:
        runtime = getattr(self.record, "runtime", None)
        runtime = runtime if isinstance(runtime, dict) else {}
        return {
            "backend": self._backend_name(),
            "model_path": self._model_path(),
            "params": dict(runtime.get("params") or {}),
            "device": str(runtime.get("device") or getattr(self.settings, "device", "") or "auto"),
        }

    def _model_path(self) -> str:
        for name in ("primary_file", "local_path"):
            getter = getattr(self.record, name, None)
            if not callable(getter):
                continue
            try:
                found = getter()
            except Exception:
                found = None
            if found is not None:
                return str(found)
        return ""

    def _alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _open(self, op: str, payload: dict[str, Any]) -> tuple[int, "queue.Queue[tuple[str, Any]]"]:
        """登记一个请求并写出请求帧，返回 (req_id, 响应队列)。"""
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise AdapterError("worker 进程未运行")
        waiter: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        with self._write_lock:
            self._next_id += 1
            req_id = self._next_id
            self._pending[req_id] = waiter
            self._busy += 1
            try:
                proc.stdin.write(json.dumps({"id": req_id, "op": op, "payload": payload}, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._close(req_id)
                raise AdapterError(f"worker 写入失败：{exc}") from exc
        return req_id, waiter

    def _close(self, req_id: int) -> None:
        if self._pending.pop(req_id, None) is not None:
            self._busy = max(0, self._busy - 1)

    def _wait(self, waiter: "queue.Queue[tuple[str, Any]]", deadline: float | None, op: str) -> dict[str, Any]:
        """等最终帧；delta 帧直接丢弃（非流式调用不该出现）。"""
        while True:
            kind, data = self._take(waiter, deadline, op)
            if kind == "delta":
                continue
            if kind == "exit":
                raise AdapterError(f"worker 进程已退出（退出码 {data}）")
            frame: dict[str, Any] = data if isinstance(data, dict) else {}
            if frame.get("ok"):
                result = frame.get("result")
                return result if isinstance(result, dict) else {"result": result}
            raise AdapterError(str(frame.get("error") or "worker 调用失败"))

    def _take(self, waiter: "queue.Queue[tuple[str, Any]]", deadline: float | None, op: str) -> tuple[str, Any]:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise AdapterError(f"worker 调用超时（{op}）")
        try:
            return waiter.get(timeout=remaining)
        except queue.Empty:
            raise AdapterError(f"worker 调用超时（{op}）") from None

    def _request(self, op: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        req_id, waiter = self._open(op, payload)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        try:
            return self._wait(waiter, deadline, op)
        finally:
            self._close(req_id)

    def _iterate(self, op: str, payload: dict[str, Any], timeout: float | None) -> Iterator[str]:
        """流式调用：逐段 yield 文本；最终帧的完整文本只补发前面没产出的尾巴。"""
        req_id, waiter = self._open(op, payload)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        emitted = 0
        try:
            while True:
                kind, data = self._take(waiter, deadline, op)
                if kind == "delta":
                    text = str(data or "")
                    if text and not self._cancel_flag.is_set():
                        emitted += len(text)
                        yield text
                    continue
                if kind == "exit":
                    raise AdapterError(f"worker 进程已退出（退出码 {data}）")
                frame: dict[str, Any] = data if isinstance(data, dict) else {}
                if not frame.get("ok"):
                    raise AdapterError(str(frame.get("error") or "worker 调用失败"))
                result = frame.get("result")
                result = result if isinstance(result, dict) else {}
                full = result.get("text")
                if isinstance(full, str) and len(full) > emitted:
                    tail = full[emitted:]
                    emitted += len(tail)
                    yield tail
                return
        finally:
            self._close(req_id)

    def _extract(self, task: str, result: dict[str, Any]) -> Any:
        """对齐 HTTP 适配器的返回风格：chat/completion 给文本，embedding 给向量。"""
        if task == "embedding":
            if "embedding" in result:
                return result.get("embedding")
            vectors = result.get("embeddings")
            if isinstance(vectors, list) and len(vectors) == 1:
                return vectors[0]
            if vectors is not None:
                return vectors
        if "text" in result:
            return result.get("text")
        return result

    # ------------------------------------------------------------ 后台线程

    def _spawn(self, target: Any, *args: Any) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def _pump_stdout(self, proc: "subprocess.Popen[str]") -> None:
        """读 worker 的 stdout，按 id 把帧投递给对应等待者。"""
        stream = proc.stdout
        if stream is not None:
            for line in stream:
                text = line.strip()
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(frame, dict):
                    continue
                waiter = self._pending.get(frame.get("id"))
                if waiter is None:
                    continue
                if "stream" in frame:
                    waiter.put(("delta", frame.get("text") or ""))
                else:
                    waiter.put(("frame", frame))
        code = proc.poll()
        with self._state_lock:
            self._dead = True
            self._loaded = False
        for waiter in list(self._pending.values()):
            waiter.put(("exit", code))

    def _pump_stderr(self, proc: "subprocess.Popen[str]", path: Path) -> None:
        """worker 的 stderr 落进日志文件，同时把有意义的行播报到控制台。

        worker 自己的行（`[worker] …`）按 info 播；报错那几行连同后面的栈按 error 播；
        其余（依赖库的进度条、警告）压到 debug，免得刷屏。
        """
        stream = proc.stderr
        if stream is None or path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        in_error = False
        try:
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                for line in stream:
                    handle.write(line)
                    handle.flush()
                    text = str(line or "").strip()
                    if not text:
                        in_error = False
                        continue
                    if _ERROR_WORDS.search(text):
                        in_error = True
                    if in_error:
                        _console.error(text)
                    elif _WORKER_LINE.match(text):
                        _console.info(text)
                    else:
                        _console.debug(text)
        except OSError:
            pass

    def _heartbeat(self) -> None:
        """每 `heartbeat_sec` 发一次 ping；连续 2 次无响应判僵死并杀进程。"""
        interval = float(getattr(self.settings, "heartbeat_sec", 0) or 5)
        interval = max(1.0, interval)
        misses = 0
        while not self._stop_flag.wait(interval):
            if not self._alive():
                return
            if self._busy > 0:
                # 有调用在飞（含流式），别用 ping 去抢 worker 的队列
                misses = 0
                continue
            try:
                self._request("ping", {}, timeout=_PING_TIMEOUT)
                misses = 0
            except AdapterError:
                misses += 1
                if misses >= 2:
                    self._loaded = False
                    self._kill()
                    for waiter in list(self._pending.values()):
                        waiter.put(("exit", self._proc.poll() if self._proc else None))
                    return

    def _kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
