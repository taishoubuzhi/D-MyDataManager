"""内置音频播放器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class AudioViewerPlugin(ViewerPlugin):
    """播放音频文件，播放控制由界面工具库的 PlayerPanel 提供。"""

    def create_view(self, path, parent=None):
        from .audio_view import AudioViewer

        return AudioViewer(path, parent)
