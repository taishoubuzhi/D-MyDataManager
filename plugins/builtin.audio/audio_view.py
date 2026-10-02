"""音频查看器：播放控制直接用查看器工具库的 MediaViewer。"""

from __future__ import annotations

from dm_plugin.builtin.lib.viewer.plugin import MediaViewer


class AudioViewer(MediaViewer):
    """音频播放页面（无画面，工具库负责播放 / 进度 / 音量）。"""

    shows_video = False
