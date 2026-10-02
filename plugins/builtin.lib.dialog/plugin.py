"""内置弹窗工具库：对外提供 dialog 扩展接口与弹窗外壳。

别的插件只允许从本模块取用（`dm_plugin.builtin.lib.dialog.plugin`）：

    from dm_plugin.builtin.lib.dialog.plugin import DialogApi, PopupWindow

不过正常情况下不需要 import：查看器插件通过 `ctx.require("dialog")` 拿扩展接口即可。
"""

from __future__ import annotations

from app.sdk import Plugin, PluginContext

from .dialog_host import DialogApi, PopupWindow

__all__ = ["DialogApi", "DialogPlugin", "PopupWindow"]


class DialogPlugin(Plugin):
    """把弹窗外壳注册成 dialog 扩展接口，供其他插件显示自己的页面。"""

    def setup(self, ctx: PluginContext) -> None:
        ctx.provide("dialog", DialogApi())
