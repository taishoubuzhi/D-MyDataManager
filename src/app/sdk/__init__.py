"""插件 SDK：插件只依赖这里，不直接 import 程序内部模块（`app.core` / `app.services` / …）。

    from app.sdk import ExtensionPoint, Plugin

    class HelloPlugin(Plugin):
        def setup(self, ctx):
            ctx.log.info("hello from {}", ctx.plugin_id)
"""

from __future__ import annotations

from loguru import logger

from . import console, data, download, items, manifest, pip, storage, ui
from .context import ContextServices, PluginContext
from .errors import (
    DependencyError,
    PluginError,
    PluginManifestError,
    SdkError,
    VersionError,
)
from .library import library, register_dependency_lookup, register_library_resolver, requires
from .plugin import Plugin, collect_plugin_classes, plugin_class_names
from .points import Contribution, Events, ExtensionPoint, events
from .version import (
    SDK_VERSION,
    compare_versions,
    parse_range,
    parse_version,
    range_text,
    satisfies,
)

__all__ = [
    "Contribution",
    "ContextServices",
    "DependencyError",
    "Events",
    "ExtensionPoint",
    "PluginManifestError",
    "Plugin",
    "PluginContext",
    "PluginError",
    "SDK_VERSION",
    "SdkError",
    "VersionError",
    "collect_plugin_classes",
    "compare_versions",
    "console",
    "data",
    "download",
    "events",
    "items",
    "library",
    "manifest",
    "parse_range",
    "parse_version",
    "pip",
    "plugin_class_names",
    "range_text",
    "register_dependency_lookup",
    "register_library_resolver",
    "requires",
    "satisfies",
    "sdk_banner",
    "storage",
    "ui",
]


def sdk_banner() -> str:
    """启动横幅：SDK 载入时播报当前 SDK 版本。"""
    return f"SDK 已载入：版本 {SDK_VERSION}"


#: SDK 第一次被导入时播报一次：程序与插件启动时都能在控制台看到 SDK 版本。
logger.info(sdk_banner())
