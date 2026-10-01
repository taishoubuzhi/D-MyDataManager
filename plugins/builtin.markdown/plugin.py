"""内置 Markdown 查看器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认
该弹窗插件已经注册；viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里。
"""

from __future__ import annotations

from pathlib import Path

EXTENSIONS = ("md", "markdown")
PLUGIN_NAME = "Markdown 查看器"
VIEWER_KIND = "markdown"
CAPABILITIES = ("Markdown 渲染", "大文件截断")
DESCRIPTION = "渲染 Markdown 文档，超长文档只显示前 512 KiB。"


def _factory(path: Path, parent=None):
    from app.ui.viewers.markdown_view import MarkdownViewer

    return MarkdownViewer(path, parent)


def register(api) -> None:
    api.require("dialog")
    api.add_viewer(
        PLUGIN_NAME,
        extensions=EXTENSIONS,
        factory=_factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
