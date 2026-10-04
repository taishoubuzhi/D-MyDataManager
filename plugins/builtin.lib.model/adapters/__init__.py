"""适配器工厂：一条模型记录该用哪个适配器跑。

外部模型按 `api.adapter` 选 HTTP 后端；本地模型默认走 worker 子进程（独立 venv + JSON-Lines 协议），
`inprocess` 是高级选项（进程内推理，默认不用）。
"""

from __future__ import annotations

from .base import Adapter, AdapterError
from .http import LlamaServerAdapter, OllamaAdapter, OpenAICompatAdapter

__all__ = [
    "Adapter",
    "AdapterError",
    "HTTP_ADAPTERS",
    "InprocessAdapter",
    "LlamaServerAdapter",
    "OllamaAdapter",
    "OpenAICompatAdapter",
    "WorkerAdapter",
    "build_adapter",
]

#: 外部模型可选的 HTTP 适配器
HTTP_ADAPTERS = {
    "openai_compat": OpenAICompatAdapter,
    "ollama": OllamaAdapter,
    "llama_server": LlamaServerAdapter,
}


def build_adapter(record, settings) -> Adapter:
    """按记录造适配器；未知后端退回 OpenAI 兼容 / worker。"""
    if record.is_external:
        name = str(record.api.get("adapter") or "openai_compat")
        return HTTP_ADAPTERS.get(name, OpenAICompatAdapter)(record, settings)
    name = str(record.runtime.get("adapter") or "worker")
    if name == "inprocess":
        from .inprocess import InprocessAdapter

        return InprocessAdapter(record, settings)
    from .worker import WorkerAdapter

    return WorkerAdapter(record, settings)


def __getattr__(name: str):
    """惰性暴露子模块里的类，避免 `import adapters` 时拉起重依赖。"""
    if name == "WorkerAdapter":
        from .worker import WorkerAdapter

        return WorkerAdapter
    if name == "InprocessAdapter":
        from .inprocess import InprocessAdapter

        return InprocessAdapter
    raise AttributeError(name)
