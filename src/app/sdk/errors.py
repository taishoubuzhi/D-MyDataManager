"""SDK 异常类型：清单、依赖、版本与接口错误。"""

from __future__ import annotations

__all__ = [
    "DependencyError",
    "ManifestError",
    "ModelBusyError",
    "ModelError",
    "PluginError",
    "SdkError",
    "VersionError",
]


class PluginError(Exception):
    """插件清单、依赖或载入过程中可以预期的错误。"""


class ManifestError(PluginError):
    """plugin.json 缺失、格式错误或字段不合法。"""


class DependencyError(PluginError):
    """依赖缺失、版本不匹配或存在循环依赖。"""


class VersionError(PluginError):
    """版本号或版本范围不合法。"""


class SdkError(PluginError):
    """插件使用 SDK 的方式不正确（缺接口、依赖未声明等）。"""


class ModelError(Exception):
    """模型加载或调用失败（下载未完成、后端崩了、参数不对等）。"""


class ModelBusyError(ModelError):
    """模型忙或等不到：超时、被占用、常驻上限已满。"""
