"""内置插件包：随程序分发的插件（目前都是打开方式插件）。

外部插件放在运行期的 `plugins/` 目录（见 `app.core.paths.PLUGIN_DIR`），
由 `app.services.PluginService` 扫描、校验与加载。
"""

from __future__ import annotations

from .builtin_viewers import builtin_plugins

__all__ = ["builtin_plugins"]
