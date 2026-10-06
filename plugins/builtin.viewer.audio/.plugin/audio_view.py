"""音频查看器：播放控件用界面工具库的通用播放控件。"""

from __future__ import annotations

from dm_plugin.builtin.lib.ui.plugin import PlayerPanel


class AudioViewer(PlayerPanel):
    """音频播放页面（无画面，工具库负责播放 / 进度 / 音量）。"""

    shows_video = False
