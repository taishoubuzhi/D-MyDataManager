"""适配器基类：一个模型记录怎么被真正跑起来。

管理器只认这一套接口：`start()` 加载（或探活）、`invoke()` 调用、`stop()` 卸载。
新增后端（worker 子进程、HTTP 服务、进程内推理）都实现本类，再由 `adapters.build_adapter()` 分派。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = ["Adapter", "AdapterError"]


class AdapterError(RuntimeError):
    """适配器自己报的错：加载失败、后端不可用、协议错乱。"""


class Adapter(ABC):
    """模型适配器：`start/invoke/stop` 三段式，管理器负责引用计数与淘汰。"""

    #: 适配器标识，与 `ModelRecord.adapter` 对应
    name: str = "base"

    def __init__(self, record, settings) -> None:
        self.record = record
        self.settings = settings
        self._loaded = False

    # -------------------------------------------------------------- 能力
    @property
    def loaded(self) -> bool:
        return self._loaded

    def available(self) -> bool:
        """环境是否支持这个后端（例如缺 torch / 缺可执行文件）。"""
        return True

    def info(self) -> dict:
        """给页面看的运行信息。"""
        return {"adapter": self.name, "backend": getattr(self.record, "backend", ""), "loaded": self.loaded}

    # -------------------------------------------------------------- 生命周期
    @abstractmethod
    def start(self) -> None:
        """加载模型或连接服务；失败抛 `AdapterError`。"""

    @abstractmethod
    def invoke(
        self,
        task: str,
        payload: dict | None = None,
        *,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """执行一次任务；`stream=True` 时返回逐段产出（迭代器 / 生成器）。"""

    def stop(self) -> None:
        """卸载并释放资源；必须可重复调用。"""
        self._loaded = False

    def cancel(self) -> None:
        """取消正在进行的调用（能取消就取消，不支持则忽略）。"""
