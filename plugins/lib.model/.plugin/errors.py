"""模型工具库自己的错误类型（协议 v2：插件专属异常由拥有它的插件提供）。

`ModelError` 是所有模型调用失败的基类，`ModelBusyError` 表示忙 / 超时 / 常驻上限已满。
其它插件在 `depends` 里声明 `lib.model` 后直接 `from dm_plugin.lib.model.errors import ModelError`。
"""

from __future__ import annotations

__all__ = ["ModelBusyError", "ModelError"]


class ModelError(Exception):
    """模型加载或调用失败（下载未完成、后端崩了、参数不对等）。"""


class ModelBusyError(ModelError):
    """模型忙或等不到：超时、被占用、常驻上限已满。"""
