"""内置视频播放器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class VideoViewerPlugin(ViewerPlugin):
    """播放视频文件，播放控制由媒体控件自己实现。"""

    def create_view(self, path, parent=None):
        from .video_view import VideoViewer

        return VideoViewer(path, parent)
