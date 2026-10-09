"""内置视频播放器：继承查看器基类，窗口由「内置弹窗工具库」承载。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class VideoViewerPlugin(ViewerPlugin):
    """播放视频文件：播放控制来自界面工具库的 PlayerPanel，探测 / 截图 / 字幕 /
    音轨 / 转码兜底来自程序本体的媒体接口（`app.sdk.media`）。"""

    def create_view(self, path, parent=None):
        from .video_view import VideoViewer

        return VideoViewer(
            path,
            parent,
            options={
                "volume": self.option("volume", 80),
                "muted": self.option("muted", False),
                "rate": self.option("rate", "1"),
                "loop": self.option("loop", False),
                "autoplay": self.option("autoplay", False),
                "aspect": self.option("aspect", "fit"),
                "subtitle": self.option("subtitle", True),
                "zoom_step": self.option("zoom_step", "1.25"),
                "zoom_hint": self.option("zoom_hint", 1000),
            },
            on_option=self.set_option,
        )
