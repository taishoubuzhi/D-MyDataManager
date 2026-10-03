"""媒体查看器工具：基于 QtMultimedia 的播放控件（播放/暂停、进度、音量）。

查看器工具库（builtin.lib.viewer）对外提供的工具之一：音频 / 视频插件继承
`MediaViewer` 就能得到一个可用的播放器页面，不必自己写播放控制。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from app.sdk.ui import IconTextButton
try:  # 环境缺少 QtMultimedia 时库本身仍然可用，只有用到播放器时才会报错
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
except ImportError:  # pragma: no cover - 取决于运行环境
    QAudioOutput = QMediaPlayer = QVideoWidget = None
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, Slider, SubtitleLabel

from app.sdk.data import human_size
from app.sdk.ui import COMPACT_MARGINS


def format_time(ms: int) -> str:
    """毫秒 → `m:ss` / `h:mm:ss`。"""
    seconds = max(0, int(ms // 1000))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


class MediaViewer(QWidget):
    """音视频共用部分：播放器、进度条、音量与状态提示。"""

    shows_video = False

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if QMediaPlayer is None:
            raise RuntimeError("当前环境缺少 QtMultimedia，无法播放音视频")
        self._path = Path(path)
        size = 0
        try:
            size = self._path.stat().st_size
        except OSError:
            pass
        self.caption = f"{self._path.name} · {human_size(size)}"
        self._updating = False
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        self._surface: QWidget | None = None
        self._build_ui()
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        if self.shows_video:
            video = QVideoWidget(self)
            self._player.setVideoOutput(video)
            self._surface = video
            root.addWidget(video, 1)
        else:
            stage = QWidget(self)
            stage_layout = QVBoxLayout(stage)
            stage_layout.setContentsMargins(0, 0, 0, 0)
            stage_layout.setSpacing(6)
            stage_layout.addStretch(1)
            title = SubtitleLabel(self._path.stem, stage)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage_layout.addWidget(title)
            self._hint = CaptionLabel("音频播放中", stage)
            self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage_layout.addWidget(self._hint)
            stage_layout.addStretch(1)
            root.addWidget(stage, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._play_button = IconTextButton(FluentIcon.PLAY, "播放", self)
        self._play_button.clicked.connect(self._toggle)
        bar.addWidget(self._play_button)
        self._position_label = CaptionLabel("0:00", self)
        bar.addWidget(self._position_label)
        self._position_slider = Slider(Qt.Orientation.Horizontal, self)
        self._position_slider.setRange(0, 0)
        self._position_slider.valueChanged.connect(self._on_seek)
        bar.addWidget(self._position_slider, 1)
        self._duration_label = CaptionLabel("--:--", self)
        bar.addWidget(self._duration_label)
        bar.addWidget(CaptionLabel("音量", self))
        self._volume_slider = Slider(Qt.Orientation.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(120)
        self._volume_slider.valueChanged.connect(lambda value: self._audio.setVolume(value / 100))
        bar.addWidget(self._volume_slider)
        root.addLayout(bar)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------ 行为
    def _toggle(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_button.setText("暂停" if playing else "播放")

    def _on_position(self, position: int) -> None:
        self._updating = True
        self._position_slider.setValue(position)
        self._updating = False
        self._position_label.setText(format_time(position))

    def _on_duration(self, duration: int) -> None:
        self._position_slider.setRange(0, max(0, duration))
        self._duration_label.setText(format_time(duration))

    def _on_seek(self, value: int) -> None:
        if self._updating:
            return
        if self._player.duration() > 0 and abs(self._player.position() - value) > 800:
            self._player.setPosition(value)

    def _on_error(self, error, message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        text = message or "该格式无法播放"
        self._play_button.setEnabled(False)
        self.caption = f"{self._path.name} · 播放失败：{text}"
        if self._surface is None:
            self._hint.setText(f"无法播放：{text}（可在「打开方式」页改为继承系统默认程序）")

    def stop(self) -> None:
        self._player.stop()

__all__ = ["MediaViewer", "format_time"]
