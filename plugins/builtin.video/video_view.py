"""视频查看器：播放控制直接用查看器工具库的 MediaViewer。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import MediaViewer


class VideoViewer(MediaViewer):
    """视频播放页面（工具库负责播放 / 进度 / 音量，画面由 QVideoWidget 承载）。"""

    shows_video = True
