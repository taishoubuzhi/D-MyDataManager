"""内置文本查看器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class TextViewerPlugin(ViewerPlugin):
    """按扩展名打开纯文本与代码文件，编码探测与截断逻辑都在控件里。"""

    def create_view(self, path, parent=None):
        from .text_view import TextViewer

        return TextViewer(path, parent)
