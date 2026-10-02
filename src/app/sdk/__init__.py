"""插件 SDK：插件只依赖这里，不直接 import 程序内部模块（`app.core` / `app.services` / …）。

    from app.sdk import ExtensionPoint, Plugin

    class HelloPlugin(Plugin):
        def setup(self, ctx):
            ctx.log.info("hello from {}", ctx.plugin_id)
"""

from __future__ import annotations

from . import data, ui
from .context import ContextServices, PluginContext
from .errors import DependencyError, ManifestError, PluginError, SdkError, VersionError
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
    "ManifestError",
    "Plugin",
    "PluginContext",
    "PluginError",
    "SDK_VERSION",
    "SdkError",
    "VersionError",
    "collect_plugin_classes",
    "compare_versions",
    "data",
    "events",
    "library",
    "parse_range",
    "parse_version",
    "plugin_class_names",
    "range_text",
    "register_dependency_lookup",
    "register_library_resolver",
    "requires",
    "satisfies",
    "ui",
]
