"""内置 Markdown 查看器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class MarkdownViewerPlugin(ViewerPlugin):
    """渲染 Markdown 文档，超长文档只显示前 512 KiB。"""

    def create_view(self, path, parent=None):
        from .markdown_view import MarkdownViewer

        return MarkdownViewer(path, parent)
