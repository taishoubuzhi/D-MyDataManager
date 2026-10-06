"""SDK 异常类型：清单、依赖、版本与接口错误。"""

from __future__ import annotations

__all__ = [
    "DependencyError",
    "PluginError",
    "PluginManifestError",
    "SdkError",
    "VersionError",
]


class PluginError(Exception):
    """插件清单、依赖或载入过程中可以预期的错误。"""


class PluginManifestError(PluginError):
    """plugin.json 缺失、格式错误或字段不合法。

    注意与 `app.core.manifest.errors.ManifestError`（清单机制的通用错误）区分：
    这个只表示插件清单（`plugins/<id>/plugin.json`）本身有问题。
    """


class DependencyError(PluginError):
    """依赖缺失、版本不匹配或存在循环依赖。"""


class VersionError(PluginError):
    """版本号或版本范围不合法。"""


class SdkError(PluginError):
    """插件使用 SDK 的方式不正确（缺接口、依赖未声明等）。"""

