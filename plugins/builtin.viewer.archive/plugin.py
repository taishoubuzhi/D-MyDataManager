"""内置压缩包查看器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class ArchiveViewerPlugin(ViewerPlugin):
    """列出压缩包内的条目与大小，并预览包内文本。"""

    def create_view(self, path, parent=None):
        from .archive_view import ArchiveViewer

        return ArchiveViewer(path, parent)
