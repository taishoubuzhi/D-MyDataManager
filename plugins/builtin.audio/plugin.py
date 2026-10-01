"""内置音频播放器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认
该弹窗插件已经注册；viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里。
"""

from __future__ import annotations

from pathlib import Path

from app.core.viewer_data import AUDIO_EXTENSIONS

PLUGIN_NAME = "音频播放器"
VIEWER_KIND = "audio"
CAPABILITIES = ("播放暂停", "进度拖动", "音量")
DESCRIPTION = "播放 mp3 / wav / flac 等音频，支持播放暂停、进度与音量。"


def _factory(path: Path, parent=None):
    from app.ui.viewers.media_view import AudioViewer

    return AudioViewer(path, parent)


def register(api) -> None:
    api.require("dialog")
    api.add_viewer(
        PLUGIN_NAME,
        extensions=AUDIO_EXTENSIONS,
        factory=_factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
