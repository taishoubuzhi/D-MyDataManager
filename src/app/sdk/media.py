"""媒体接口：探测、抽帧 / 逐帧、字幕、转封装 / 转码、切片、抽音轨。

插件侧统一用这里，**不要自己 import `av`、也不要 `subprocess` 调 ffmpeg / ffprobe**：

    from app.sdk import media

    info = media.probe(path)                  # 读不到返回 None
    if info:
        print(info.summary_text)              # 时长 · 分辨率 · 编码
    cover = media.frame(path, target)         # 抽一帧；失败返回空串
    for frame in media.iter_frames(path, start=5.0, count=10):
        print(frame.index, frame.time, frame.image.size)

为什么必须走这里（用户 m00416 / issue 2）：引擎只有程序本体一处实现
（`app.services.media_service`，PyAV 进程内自带 FFmpeg 库），插件各自调用会立刻分叉成
"有的系统能跑、有的缺 ffmpeg"，探测结果、临时文件、转码参数也会各写一套。
程序本体在 `src/main.py` 启动时注册扩展点 `media.open`，这里只做转发。

**契约**（与 `app.sdk.download` 同构）：

- 读操作（`probe` / `frame` / `iter_frames` / `subtitle`）：取不到就降级——
  返回 `None` / 空串 / 空迭代，**不抛错**（视频封面抽帧失败不能拖垮导入）；
- 写操作（`remux` / `transcode` / `clip` / `extract_audio`）：失败抛 `MediaError`
  （中文文案），调用方决定怎么提示；
- 临时产物用 `temp_path()` 起名（落 `<根>/.tmp/media`），用完 `cleanup_temp()`；
- 每个函数都是**阻塞**的：调用方要在工作线程里跑，界面线程只接信号；
- 程序没有提供实现时抛 `SdkError`（例如在只装了 SDK 的脚本里跑）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SdkError

__all__ = [
    "DEFAULT_FRAME_SIZE",
    "MEDIA_EXTENSION",
    "DecodedFrame",
    "MediaCancelled",
    "MediaError",
    "MediaInfo",
    "MediaStream",
    "SubtitleCue",
    "available",
    "cleanup_temp",
    "clip",
    "engine_version",
    "extract_audio",
    "frame",
    "format_time",
    "iter_frames",
    "probe",
    "provider",
    "remux",
    "subtitle",
    "temp_dir",
    "temp_path",
    "transcode",
]

#: 媒体接口的扩展点名字：程序本体启动时以它注册实现（见 `src/main.py`）。
MEDIA_EXTENSION = "media.open"

#: 抽帧的默认长边上限（等比缩放后的最大边）
DEFAULT_FRAME_SIZE = 512


class MediaError(RuntimeError):
    """媒体操作失败（写操作抛它，消息是给人看的中文）。"""


class MediaCancelled(MediaError):
    """调用方通过 `cancel` 回调取消了操作（也属于 `MediaError`，插件可据此区分「取消」和「失败」）。"""


@dataclass(frozen=True)
class MediaStream:
    """一条流的只读快照（视频 / 音频 / 字幕 / 数据）。"""

    index: int = 0
    kind: str = ""  # video / audio / subtitle / data / attachment
    codec: str = ""
    codec_label: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    pix_fmt: str = ""
    sample_rate: int = 0
    channels: int = 0
    language: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False
    frames: int = 0
    duration: float = 0.0
    bit_rate: int = 0
    rotation: int = 0

    @property
    def kind_label(self) -> str:
        """流类型的中文名（界面文案用）。"""
        return {"video": "视频", "audio": "音频", "subtitle": "字幕"}.get(self.kind, self.kind or "未知")

    @property
    def resolution_text(self) -> str:
        """`1920x1080` 或空串。"""
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return ""

    @property
    def track_text(self) -> str:
        """轨道描述：`中文 · 默认` 这类。"""
        parts: list[str] = []
        if self.language:
            parts.append(self.language)
        if self.title:
            parts.append(self.title)
        if self.default:
            parts.append("默认")
        if self.forced:
            parts.append("强制")
        return " · ".join(parts)

    def describe(self) -> str:
        """一行摘要：`视频 · H.264 · 1920x1080 · 30fps`。"""
        parts = [self.kind_label]
        if self.codec:
            parts.append(self.codec_label or self.codec)
        if self.resolution_text:
            parts.append(self.resolution_text)
        elif self.sample_rate:
            parts.append(f"{self.sample_rate} Hz")
        if self.fps:
            parts.append(f"{self.fps:g}fps")
        if self.channels:
            parts.append(f"{self.channels} 声道")
        track = self.track_text
        if track:
            parts.append(track)
        return " · ".join(part for part in parts if part)


@dataclass(frozen=True)
class MediaInfo:
    """一个媒体文件的只读快照（容器 + 各条流）。"""

    path: str = ""
    container: str = ""
    container_label: str = ""
    duration: float = 0.0
    bit_rate: int = 0
    streams: tuple[MediaStream, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def videos(self) -> tuple[MediaStream, ...]:
        return tuple(item for item in self.streams if item.kind == "video")

    @property
    def audios(self) -> tuple[MediaStream, ...]:
        return tuple(item for item in self.streams if item.kind == "audio")

    @property
    def subtitles(self) -> tuple[MediaStream, ...]:
        return tuple(item for item in self.streams if item.kind == "subtitle")

    @property
    def has_video(self) -> bool:
        return bool(self.videos)

    @property
    def has_audio(self) -> bool:
        return bool(self.audios)

    @property
    def width_height(self) -> tuple[int, int]:
        """第一路视频的分辨率（没有视频流时是 `(0, 0)`）。"""
        video = self.videos[0] if self.videos else None
        return (video.width, video.height) if video else (0, 0)

    @property
    def duration_text(self) -> str:
        """`01:23:45` / `03:07` 这类时长文案（取不到返回空串）。"""
        return format_time(self.duration)

    @property
    def summary_text(self) -> str:
        """一行摘要：`时长 · 分辨率 · 编码`（界面 caption 用）。"""
        parts: list[str] = []
        if self.duration_text:
            parts.append(self.duration_text)
        width, height = self.width_height
        if width and height:
            parts.append(f"{width}x{height}")
        stream = self.videos[0] if self.videos else (self.audios[0] if self.audios else None)
        if stream is not None and stream.codec:
            parts.append(stream.codec_label or stream.codec)
        if not parts and self.container_label:
            parts.append(self.container_label)
        return " · ".join(parts)

    def describe_streams(self) -> str:
        """各条流的多行描述（详细信息面板 / 日志用）。"""
        return "\n".join(stream.describe() for stream in self.streams)


@dataclass(frozen=True)
class SubtitleCue:
    """一条字幕（内嵌轨解出来的，单位秒）。"""

    index: int = 0
    start: float = 0.0
    end: float = 0.0
    text: str = ""

    def is_active(self, position: float) -> bool:
        """某一时刻是否显示这条字幕。"""
        return self.start <= position <= self.end


@dataclass(frozen=True)
class DecodedFrame:
    """顺序解出来的一帧（`image` 是 Pillow 的 `Image`，用完即弃）。"""

    index: int = 0
    time: float = 0.0
    image: Any = None


def format_time(seconds: float | int | None) -> str:
    """把秒数格式化成 `MM:SS` / `HH:MM:SS`（无效值返回空串）。"""
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def provider() -> Any:
    """取程序本体注册的媒体实现；没有返回 `None`。"""
    try:
        from ..core.plugins.extensions import extension_registry
    except Exception:  # noqa: BLE001 - 只装了 SDK 的脚本环境
        return None
    return extension_registry.provider(MEDIA_EXTENSION)


def available() -> bool:
    """程序有没有提供媒体接口（脚本环境里可能是假）。"""
    return provider() is not None


def _require() -> Any:
    """取实现，没有就抛 `SdkError`。"""
    impl = provider()
    if impl is None:
        raise SdkError("程序没有提供媒体接口 media.open")
    return impl


def probe(path: str | Path) -> MediaInfo | None:
    """读媒体信息（容器 + 每条流）；读不到返回 `None`，不抛错。"""
    impl = provider()
    if impl is None:
        return None
    return impl.probe(path)


def engine_version() -> str:
    """媒体引擎版本文案（没有引擎返回空串）。"""
    impl = provider()
    if impl is None:
        return ""
    return impl.engine_version()


def frame(
    source: str | Path,
    target: str | Path,
    *,
    at: float | None = None,
    size: int | None = DEFAULT_FRAME_SIZE,
) -> str:
    """抽一帧存成图片，返回图片路径；失败返回空串，不抛错。

    `at` 给秒数时先跳到那个位置再解一帧，不给就取第一帧；`size` 是长边上限。
    """
    impl = provider()
    if impl is None:
        return ""
    return impl.frame(source, target, at=at, size=size)


def iter_frames(
    source: str | Path,
    *,
    start: float | None = None,
    count: int | None = None,
    step: int = 1,
    size: int | None = None,
) -> Iterator[DecodedFrame]:
    """顺序解码逐帧（生成器）：`start` 秒起、每 `step` 帧取一帧、最多 `count` 帧。

    没有实现或解不出来时是空迭代（不抛错）。这是**阻塞**调用：逐帧步进请放工作线程。
    """
    impl = provider()
    if impl is None:
        return iter(())
    return iter(impl.iter_frames(source, start=start, count=count, step=step, size=size))


def subtitle(source: str | Path, *, stream: int | None = None) -> list[SubtitleCue]:
    """解出内嵌字幕轨；解不出来返回空列表，不抛错。

    外部 `.srt` / `.vtt` 是纯文本，插件自己读文本解析即可，不必走这里。
    """
    impl = provider()
    if impl is None:
        return []
    return list(impl.subtitle(source, stream=stream))


def remux(
    source: str | Path,
    target: str | Path,
    *,
    container: str = "mp4",
    keep_audio: int | None = None,
    keep_subtitle: int | None = None,
    on_progress: Any = None,
    cancel: Any = None,
) -> str:
    """只换容器（不解码、不改画质），返回产物路径；失败抛 `MediaError`。

    `keep_audio` / `keep_subtitle` 给流索引时只保留那一条轨（换音轨 / 换字幕轨）。
    `on_progress` 收到 0~1 的浮点；`cancel()` 返回真时抛 `MediaCancelled` 并删掉半成品。
    """
    return _require().remux(
        source,
        target,
        container=container,
        keep_audio=keep_audio,
        keep_subtitle=keep_subtitle,
        on_progress=on_progress,
        cancel=cancel,
    )


def transcode(
    source: str | Path,
    target: str | Path,
    *,
    vcodec: str = "libx264",
    acodec: str = "aac",
    scale: int | None = None,
    on_progress: Any = None,
    cancel: Any = None,
) -> str:
    """转码到新文件，返回产物路径；失败抛 `MediaError`。

    参数是**白名单项**（不接受任意命令行参数）：`scale` 是长边上限，不给就保持原分辨率。
    """
    return _require().transcode(
        source,
        target,
        vcodec=vcodec,
        acodec=acodec,
        scale=scale,
        on_progress=on_progress,
        cancel=cancel,
    )


def clip(
    source: str | Path,
    target: str | Path,
    *,
    start: float = 0.0,
    duration: float | None = None,
    container: str = "mp4",
) -> str:
    """按区间导出片段（转封装式，不解码）；失败抛 `MediaError`。"""
    return _require().clip(source, target, start=start, duration=duration, container=container)


def extract_audio(
    source: str | Path,
    target: str | Path,
    *,
    codec: str = "copy",
    on_progress: Any = None,
    cancel: Any = None,
) -> str:
    """抽出音轨另存；失败抛 `MediaError`。"""
    return _require().extract_audio(source, target, codec=codec, on_progress=on_progress, cancel=cancel)


def temp_dir() -> Path:
    """媒体临时目录（`<根>/.tmp/media`）。"""
    impl = provider()
    if impl is None:
        raise SdkError("程序没有提供媒体接口 media.open")
    return Path(impl.temp_dir())


def temp_path(suffix: str = "") -> Path:
    """在媒体临时目录里起一个唯一产物名（`suffix` 带不带点都行）。"""
    impl = provider()
    if impl is None:
        raise SdkError("程序没有提供媒体接口 media.open")
    return Path(impl.temp_path(suffix))


def cleanup_temp(target: str | Path | None) -> bool:
    """删掉一个临时产物（只认媒体临时目录里的路径；目录外返回假）。"""
    impl = provider()
    if impl is None or not target:
        return False
    return bool(impl.cleanup_temp(target))
