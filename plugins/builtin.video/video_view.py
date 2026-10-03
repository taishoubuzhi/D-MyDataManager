"""视频查看器：画面由 QVideoWidget 承载，播放控件用界面工具库的通用播放控件。"""

from __future__ import annotations

from dm_plugin.builtin.lib.ui.plugin import PlayerPanel


class VideoViewer(PlayerPanel):
    """视频播放页面（工具库负责播放 / 进度 / 音量，画面由 QVideoWidget 承载）。"""

    shows_video = True
