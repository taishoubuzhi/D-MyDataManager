"""内置表格查看器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class SheetViewerPlugin(ViewerPlugin):
    """打开 xlsx / csv 等表格，工作表切换与预览行数都在控件里。"""

    def create_view(self, path, parent=None):
        from .sheet_view import SheetViewer

        return SheetViewer(path, parent)
