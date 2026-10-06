"""worker 子进程：入口脚本与启动命令。

worker 是「本地模型跑在插件专用 venv 里」的那条路：适配器 `adapters/worker.py` 用这里的
`build_command()` 拉起 `worker_main.py`，之后按 JSON-Lines 协议对话。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["WORKER_SCRIPT", "build_command"]

#: 子进程入口脚本（绝对路径）
WORKER_SCRIPT: Path = Path(__file__).resolve().parent / "worker_main.py"


def build_command(record: Any, settings: Any, python: Path) -> list[str]:
    """拼 worker 启动命令：`<python> -u worker_main.py --backend … --model … --device …`。

    命令行参数只是提示/兜底，真正要加载的模型随后由 `load` 请求的 payload 决定。
    """
    runtime = getattr(record, "runtime", None)
    runtime = runtime if isinstance(runtime, dict) else {}
    backend = str(runtime.get("backend") or getattr(record, "backend", "") or "")
    device = str(runtime.get("device") or getattr(settings, "device", "") or "auto")
    return [
        str(python),
        "-u",
        str(WORKER_SCRIPT),
        "--backend",
        backend,
        "--model",
        _model_path(record),
        "--device",
        device,
    ]


def _model_path(record: Any) -> str:
    """命令行的 `--model`：优先主文件，其次模型目录，都没有就空串。"""
    primary = getattr(record, "primary_file", None)
    if callable(primary):
        try:
            found = primary()
        except Exception:
            found = None
        if found is not None:
            return str(found)
    local = getattr(record, "local_path", None)
    if callable(local):
        try:
            folder = local()
        except Exception:
            folder = None
        if folder is not None:
            return str(folder)
    return ""
