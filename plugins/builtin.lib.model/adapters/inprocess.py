"""进程内推理（高级选项）：把模型直接加载到程序进程里。

这个后端**默认不用**：它会把权重与显存占用挂在程序本体上，退出前难以彻底释放，
而且重依赖一旦装进程序环境就会污染主程序。因此本文件只保留接口位置——
真正要用时应当改用 worker 运行环境（独立进程 + 独立 venv），而不是在这里堆代码。
"""

from __future__ import annotations

from .base import Adapter, AdapterError

__all__ = ["InprocessAdapter"]


class InprocessAdapter(Adapter):
    """占位实现：明确拒绝，而不是静默失败或把主程序环境搞乱。"""

    name = "inprocess"

    def available(self) -> bool:
        return False

    def _refuse(self):
        raise AdapterError(
            "进程内推理未启用：会把模型挂进程序进程，难以释放。"
            "请把该模型的运行环境选成 worker（独立虚拟环境），或在设置里改用 llama-server / ollama。"
        )

    def start(self) -> None:
        self._refuse()

    def invoke(self, task, payload=None, *, stream=False, timeout=None):
        self._refuse()
