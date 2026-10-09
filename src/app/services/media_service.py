"""媒体引擎（PyAV 进程内）：探测、抽帧 / 逐帧、字幕、转封装 / 转码、切片、抽音轨。

**为什么是 PyAV**：`av` 的 wheel 自带 FFmpeg 库（libavformat / libavcodec / libswscale…），
探测、解码、编码、封装全在 Python 进程里完成 —— 既没有"exe 找不到 / PATH 没配 / 杀软拦 exe"
这类外部依赖问题（「永不缺失」由 wheel 本身保证），探测拿到的又是结构化对象（不需要 ffprobe），
帧级与轨道级能力（逐帧、字幕、多轨）也齐全。

四条约定（详见 `docs/index/viewer-editor/media.md`）：

- **唯一实现**：插件不许自己 `import av`、也不许 `subprocess` 调 ffmpeg / ffprobe，
  一律经 `app.sdk.media` 走这里（`issue 2` 的"集成"就是这个意思）；
- **契约分明**：`probe()` / `frame()` / `iter_frames()` 这类"取不到就降级"的入口，
  失败返回 `None` / `""` / 空迭代，**不抛错**（视频封面抽帧失败不能拖垮导入）；
  `remux()` / `transcode()` / `clip()` / `extract_audio()` 这类用户显式发起的操作才抛
  `MediaError`，由调用方决定怎么提示；
- **临时文件**：产物一律落在 `paths.MEDIA_TMP_DIR`（`<根>/.tmp/media`），用 `temp_path()`
  起名、任务结束 / 窗口关闭时 `cleanup_temp()`；进程退出（atexit）与下次启动
  （`clear_stale()`）还有兜底清理；
- **线程**：这里的每个函数都是阻塞的，调用方要在工作线程里跑（UI 线程只接信号）。
  本模块自己不建线程、不碰界面，也没有全局可变状态。
"""

from __future__ import annotations

import atexit
import importlib.util
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from ..core.runtime import paths
from ..sdk.media import (
    DEFAULT_FRAME_SIZE,
    DecodedFrame,
    MediaCancelled,
    MediaError,
    MediaInfo,
    MediaStream,
    SubtitleCue,
)
from ..sdk.media import format_time as format_time  # noqa: PLC0414 - 服务层内部沿用旧名

__all__ = [
    "DEFAULT_AUDIO_CODEC",
    "DEFAULT_CRF",
    "DEFAULT_FRAME_SIZE",
    "DEFAULT_PRESET",
    "DEFAULT_VIDEO_CODEC",
    "DecodedFrame",
    "MediaCancelled",
    "MediaError",
    "MediaInfo",
    "MediaStream",
    "SubtitleCue",
    "available",
    "cleanup_temp",
    "clear_stale",
    "clear_temp_dir",
    "clip",
    "engine_version",
    "extract_audio",
    "format_time",
    "frame",
    "iter_frames",
    "probe",
    "remux",
    "subtitle",
    "temp_dir",
    "temp_path",
    "transcode",
]

#: 转码时给编码器的默认参数（“能播”优先，不追求画质 / 体积最优）
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"
DEFAULT_CRF = 23
DEFAULT_PRESET = "veryfast"

#: 启动时清理陈旧临时产物的默认时限（秒）：超过这个年龄的产物一定是上次进程遗留的
STALE_AGE_SECONDS = 24 * 3600

#: 目标扩展名 → 容器名（`extract_audio` 里按扩展名选容器）
AUDIO_CONTAINERS: dict[str, str] = {"m4a": "mp4", "oga": "ogg", "aif": "aiff", "aiff": "aiff"}

#: 容器能直接装下的音轨编码（`extract_audio(codec="copy")` 直接搬包的前提）
_AUDIO_COPYABLE: dict[str, frozenset[str]] = {
    "mp4": frozenset({"aac", "alac", "mp3", "ac3", "eac3", "opus"}),
    "mp3": frozenset({"mp3"}),
    "ogg": frozenset({"vorbis", "opus", "flac"}),
    "flac": frozenset({"flac"}),
    "wav": frozenset({"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8", "pcm_s16be"}),
    "aiff": frozenset({"pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_s16le"}),
    "adts": frozenset({"aac"}),
    "matroska": frozenset({"aac", "mp3", "vorbis", "opus", "flac", "ac3"}),
}

#: 原编码装不下时改用的编码器（挑本机一定有、且各容器通用的那个）
_AUDIO_ENCODER: dict[str, str] = {
    "mp4": "aac",
    "mp3": "libmp3lame",
    "ogg": "vorbis",
    "flac": "flac",
    "wav": "pcm_s16le",
    "aiff": "pcm_s16be",
    "adts": "aac",
    "matroska": "libmp3lame",
}


# --------------------------------------------------------------------- 引擎探测

def available() -> bool:
    """本机有没有可用的媒体引擎（`av` 装了就有；**不 import**，启动路径可安全调用）。"""
    try:
        return importlib.util.find_spec("av") is not None
    except (ImportError, ValueError):  # 解释器被裁掉 / 路径异常时按"没有"处理
        return False


def engine_version() -> str:
    """引擎版本文案：`PyAV 19.0.1（FFmpeg libavcodec 63.1.102）`；没有引擎返回空串。"""
    if not available():
        return ""
    try:
        import av  # 延迟导入：重型库，只在真的要跑媒体操作时载入
    except Exception:  # noqa: BLE001 - 装了但导入失败（缺 DLL 等）也当作没有
        return ""
    version = str(getattr(av, "__version__", "") or "").strip()
    libs = getattr(av, "library_versions", {}) or {}
    libavcodec = libs.get("libavcodec")
    detail = ""
    if libavcodec:
        detail = "libavcodec " + ".".join(str(part) for part in tuple(libavcodec)[:3])
    if version and detail:
        return f"PyAV {version}（FFmpeg {detail}）"
    return f"PyAV {version}" if version else ""


def _av() -> Any:
    """取 `av` 模块；没有引擎时抛 `MediaError`（调用方按"无法操作"处理）。"""
    if not available():
        raise MediaError("没有可用的媒体引擎（缺少 av 依赖），请重新安装依赖后重试")
    try:
        import av
    except Exception as exc:  # noqa: BLE001 - 装了但导入失败（缺 DLL 等）
        raise MediaError(f"媒体引擎载入失败：{exc}") from exc
    _silence_logs(av)
    return av


_SILENCED = False


def _silence_logs(av: Any) -> None:
    """把 PyAV 自带 libav 的日志等级压到只报错（打开容器时的 `Input #0 …` 不再刷屏）。

    只做降噪：接口不存在或调用失败都只记 debug，不影响媒体功能。
    """
    global _SILENCED
    if _SILENCED:
        return
    _SILENCED = True
    logging_api = getattr(av, "logging", None)
    setter = getattr(logging_api, "set_libav_level", None)
    if not callable(setter):
        return
    try:
        setter(getattr(logging_api, "ERROR", 16))
    except Exception as exc:  # noqa: BLE001
        logger.debug("静音 PyAV 日志失败：{}", exc)


def _av_or_none() -> Any:
    """取 `av` 模块；没有引擎时返回 `None`（"取不到就降级"的入口用）。"""
    try:
        return _av()
    except MediaError as exc:
        logger.debug("媒体引擎不可用：{}", exc)
        return None


# --------------------------------------------------------------------- 探测


def probe(source: str | Path) -> MediaInfo | None:
    """读取媒体信息（容器 + 每条流）；读不到返回 `None`，**不抛错**。"""
    target = Path(source)
    if not target.is_file():
        return None
    av = _av_or_none()
    if av is None:
        return None
    try:
        with av.open(str(target), mode="r") as container:
            streams = tuple(_stream_of(stream) for stream in container.streams)
            return MediaInfo(
                path=str(target),
                container=str(getattr(container.format, "name", "") or ""),
                container_label=str(getattr(container.format, "long_name", "") or ""),
                duration=_container_seconds(container, av),
                bit_rate=int(getattr(container, "bit_rate", 0) or 0),
                streams=streams,
                metadata=dict(container.metadata or {}),
            )
    except Exception as exc:  # noqa: BLE001 - 损坏文件 / 不支持的容器都按"读不到"处理
        logger.debug("读取媒体信息失败：{}", exc)
        return None


def _seconds(value: Any, time_base: Any) -> float:
    """把（流时间基单位里的）数值换算成秒；取不到返回 0.0。"""
    if value is None or not time_base:
        return 0.0
    try:
        return max(0.0, float(value) * float(time_base))
    except (TypeError, ValueError):
        return 0.0


def _container_seconds(container: Any, av: Any) -> float:
    """容器总时长（秒）：`container.duration` 用的是 `av.time_base` 单位（微秒）。"""
    return _ratio(getattr(container, "duration", None), getattr(av, "time_base", 0))


def _packet_seconds(packet: Any) -> float | None:
    """包的时间戳（秒）；包上没有时间戳的（冲刷包）返回 `None`。"""
    pts = getattr(packet, "pts", None)
    time_base = getattr(packet, "time_base", None)
    if pts is None or not time_base:
        return None
    try:
        return float(pts) * float(time_base)
    except (TypeError, ValueError):
        return None


def _ratio(value: Any, base: Any) -> float:
    """`value / base`（base 为 0 / 取不到时返回 0.0）。"""
    if value is None or not base:
        return 0.0
    try:
        return max(0.0, float(value) / float(base))
    except (TypeError, ValueError):
        return 0.0


def _codec_label(name: str) -> str:
    """编码名的常见中文 / 通用叫法（拿不到就用原名）。"""
    return {
        "h264": "H.264",
        "hevc": "H.265",
        "mpeg4": "MPEG-4",
        "mpeg2video": "MPEG-2",
        "vp8": "VP8",
        "vp9": "VP9",
        "av1": "AV1",
        "aac": "AAC",
        "mp3": "MP3",
        "opus": "Opus",
        "vorbis": "Vorbis",
        "flac": "FLAC",
        "ac3": "AC-3",
    }.get(name, name)


def _stream_of(stream: Any) -> MediaStream:
    """把 PyAV 的流对象转成只读快照。"""
    context = getattr(stream, "codec_context", None)
    codec = str(getattr(context, "name", "") or getattr(stream, "codec_context", "") or "")
    metadata = dict(getattr(stream, "metadata", {}) or {})
    disposition = getattr(stream, "disposition", None)
    rate = getattr(stream, "average_rate", None) or getattr(stream, "base_rate", None)
    try:
        fps = float(rate) if rate else 0.0
    except (TypeError, ValueError):
        fps = 0.0
    channels = _channels_of(context)
    rotation = 0
    for key in ("rotate", "rotation"):
        raw = metadata.get(key)
        if raw:
            try:
                rotation = int(float(str(raw)))
            except (TypeError, ValueError):
                rotation = 0
            break
    duration = _seconds(
        getattr(stream, "duration", None),
        getattr(stream, "time_base", None),
    )
    if not duration:
        duration = _seconds(getattr(context, "duration", None), getattr(stream, "time_base", None))
    return MediaStream(
        index=int(getattr(stream, "index", 0) or 0),
        kind=str(getattr(stream, "type", "") or ""),
        codec=codec,
        codec_label=_codec_label(codec),
        width=int(getattr(context, "width", 0) or 0),
        height=int(getattr(context, "height", 0) or 0),
        fps=fps,
        pix_fmt=str(getattr(context, "pix_fmt", "") or ""),
        sample_rate=int(getattr(context, "sample_rate", 0) or 0),
        channels=channels,
        language=str(metadata.get("language", "") or ""),
        title=str(metadata.get("title", "") or ""),
        default=bool(getattr(disposition, "default", False)),
        forced=bool(getattr(disposition, "forced", False)),
        frames=int(getattr(stream, "frames", 0) or 0),
        duration=duration,
        bit_rate=int(getattr(stream, "bit_rate", 0) or getattr(context, "bit_rate", 0) or 0),
        rotation=rotation,
    )


def _channels_of(context: Any) -> int:
    """声道数（不同 PyAV 版本字段名不同，逐个兜底）。"""
    for attr in ("channels",):
        value = getattr(context, attr, None)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    layout = getattr(context, "layout", None)
    channels = getattr(layout, "channels", None)
    if channels:
        try:
            return len(channels)
        except TypeError:
            return 0
    return 0


# --------------------------------------------------------------------- 抽帧 / 逐帧


def frame(
    source: str | Path,
    target: str | Path,
    *,
    at: float | None = None,
    size: int | None = DEFAULT_FRAME_SIZE,
) -> str:
    """抽一帧存成图片，返回图片路径；失败返回空串（**不抛错**）。

    `at` 给秒数时先 seek 再解一帧，不给就取第一帧；`size` 是长边上限（`None` 表示原尺寸）。
    """
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        return ""
    av = _av_or_none()
    if av is None:
        return ""
    try:
        with av.open(str(source_path), mode="r") as container:
            stream = _first_video(container)
            if stream is None:
                return ""
            _tune(stream)
            if at is not None:
                _seek(container, stream, float(at))
            for decoded in container.decode(stream):
                image = _scaled_image(decoded.to_image(), size)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(target_path)
                return str(target_path)
    except Exception as exc:  # noqa: BLE001 - 抽帧失败按"没有帧"处理
        logger.debug("抽取视频帧失败：{}", exc)
        return ""
    return ""


def iter_frames(
    source: str | Path,
    *,
    start: float | None = None,
    count: int | None = None,
    step: int = 1,
    size: int | None = None,
) -> Iterator[DecodedFrame]:
    """按顺序解码逐帧（生成器）：`start` 秒起、每 `step` 帧取一帧、最多 `count` 帧。

    供「抽帧导出」与需要按帧取图的调用方用（`iter_frames(start=…)` 取单帧）；解不出来就结束迭代（**不抛错**）。
    `images` 是 Pillow 的 `Image`，`size` 给长边上限时才缩放（逐帧播放别缩放，交给界面）。
    """
    source_path = Path(source)
    if not source_path.is_file():
        return
    av = _av_or_none()
    if av is None:
        return
    step = max(1, int(step or 1))
    emitted = 0
    index = 0
    try:
        with av.open(str(source_path), mode="r") as container:
            stream = _first_video(container)
            if stream is None:
                return
            _tune(stream)
            if start:
                _seek(container, stream, float(start))
            for decoded in container.decode(stream):
                if index % step == 0:
                    image = decoded.to_image()
                    if size:
                        image = _scaled_image(image, size)
                    emit = DecodedFrame(index=index, time=float(decoded.time or 0.0), image=image)
                    yield emit
                    emitted += 1
                    if count is not None and emitted >= int(count):
                        return
                index += 1
    except Exception as exc:  # noqa: BLE001 - 解到一半失败就到这里为止
        logger.debug("逐帧解码中断：{}", exc)


def _first_video(container: Any) -> Any:
    """容器里的第一条视频流（没有返回 `None`）。"""
    streams = getattr(container, "streams", None)
    videos = list(getattr(streams, "video", []) or [])
    return videos[0] if videos else None


def _tune(stream: Any) -> None:
    """让解码器用多线程（解码速度差别很大，失败无所谓）。"""
    try:
        stream.thread_type = "AUTO"
    except Exception:  # noqa: BLE001
        pass


def _scaled_image(image: Any, size: int | None) -> Any:
    """按长边上限等比缩小（`size` 为空 / 不超限时原样返回）。"""
    if not size:
        return image
    limit = max(1, int(size))
    if max(image.size) <= limit:
        return image
    copy = image.copy()
    copy.thumbnail((limit, limit))
    return copy


def _seek(container: Any, stream: Any, seconds: float) -> None:
    """跳到某个时刻（尽量往前靠），失败就当作从头开始。"""
    try:
        time_base = float(getattr(stream, "time_base", 0) or 0)
        if time_base <= 0:
            return
        offset = max(0, int(round(float(seconds) / time_base)))
        container.seek(offset, backward=True, any_frame=False, stream=stream)
    except Exception as exc:  # noqa: BLE001 - 有些容器不支持 seek
        logger.debug("跳转到 {} 秒失败：{}", seconds, exc)


# --------------------------------------------------------------------- 转封装 / 转码 / 切片


def _select_streams(
    streams: list[Any],
    *,
    keep_audio: int | None = None,
    keep_subtitle: int | None = None,
) -> list[Any]:
    """转封装时的流筛选：音频 / 字幕按**流的索引**只保留指定的一条（None 表示全留）。

    「换音轨 / 换字幕轨」就靠它：把指定轨单独搬进临时文件，再交给播放器。
    """
    picked: list[Any] = []
    for stream in streams:
        kind = str(getattr(stream, "type", "") or "")
        if kind == "audio" and keep_audio is not None and int(stream.index) != int(keep_audio):
            continue
        if kind == "subtitle" and keep_subtitle is not None and int(stream.index) != int(keep_subtitle):
            continue
        picked.append(stream)
    return picked


def remux(
    source: str | Path,
    target: str | Path,
    *,
    container: str = "mp4",
    keep_audio: int | None = None,
    keep_subtitle: int | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """只换容器（**不解码、不改画质**）：把每条流原样搬进新文件，返回产物路径。

    这是"系统解码器播不了"的第一道兜底（mkv / flv / avi → mp4）；`keep_audio` /
    `keep_subtitle` 给流索引时只保留那一条（换音轨 / 换字幕轨）；失败抛 `MediaError`，
    调用方再决定要不要退回 `transcode()`。
    """
    av = _av()
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise MediaError(f"文件不存在：{source_path.name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0.0
    try:
        with av.open(str(source_path), mode="r") as in_container:
            total = _container_seconds(in_container, av)
            streams = _select_streams(
                list(in_container.streams),
                keep_audio=keep_audio,
                keep_subtitle=keep_subtitle,
            )
            if not streams:
                raise MediaError("这个文件里没有可用的媒体流")
            with av.open(str(target_path), mode="w", format=container) as out_container:
                mapping = [(stream, out_container.add_stream_from_template(stream)) for stream in streams]
                position = 0.0
                for packet in in_container.demux(*[item[0] for item in mapping]):
                    if cancel is not None and cancel():
                        raise MediaCancelled("转封装已取消")
                    source_stream, out_stream = _pair_of(mapping, packet.stream)
                    moment = _packet_seconds(packet)
                    if moment is not None:
                        position = max(position, moment)
                    packet.stream = out_stream
                    out_container.mux(packet)
                    if on_progress is not None and total:
                        on_progress(min(1.0, position / total))
        if on_progress is not None:
            on_progress(1.0)
        return str(target_path)
    except MediaError:
        _remove(target_path)
        raise
    except Exception as exc:  # noqa: BLE001 - 统一成中文文案
        _remove(target_path)
        raise MediaError(f"转封装失败：{exc}") from exc


def transcode(
    source: str | Path,
    target: str | Path,
    *,
    vcodec: str = DEFAULT_VIDEO_CODEC,
    acodec: str = DEFAULT_AUDIO_CODEC,
    scale: int | None = None,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    on_progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """转码（解码 → 编码 → 封装），返回产物路径；失败抛 `MediaError`。

    **不接受任意 argv**：参数只有 `vcodec` / `acodec` / `scale` / `crf` / `preset` 这几个白名单项。
    `scale` 是长边上限（等比缩放，不给就保持原分辨率）；`on_progress` 回调 0~1；
    `cancel()` 返回真时抛 `MediaCancelled` 并删掉半成品。
    """
    av = _av()
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise MediaError(f"文件不存在：{source_path.name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(source_path), mode="r") as in_container:
            in_video = _first_video(in_container)
            in_audio = next(iter(in_container.streams.audio), None)
            if in_video is None and in_audio is None:
                raise MediaError("这个文件里没有可转码的音视频流")
            total = _container_seconds(in_container, av)
            with av.open(str(target_path), mode="w") as out_container:
                out_video = _add_video_stream(out_container, in_video, vcodec, crf, preset, scale)
                out_audio = _add_audio_stream(out_container, in_audio, acodec)
                written = 0
                decoded_total = 0
                position = 0.0
                for packet in in_container.demux(*[item for item in (in_video, in_audio) if item is not None]):
                    if cancel is not None and cancel():
                        raise MediaCancelled("转码已取消")
                    for decoded in packet.decode():
                        if cancel is not None and cancel():
                            raise MediaCancelled("转码已取消")
                        decoded_total += 1
                        if _encode(decoded, out_video, out_audio, out_container):
                            written += 1
                        if decoded.time is not None:
                            position = max(position, float(decoded.time))
                        if on_progress is not None and total:
                            on_progress(min(0.99, position / total))
                written += _flush(out_video, out_container)
                written += _flush(out_audio, out_container)
        if on_progress is not None:
            on_progress(1.0)
        if not written and decoded_total:
            raise MediaError("转码没有产出任何数据")
        return str(target_path)
    except MediaError:
        _remove(target_path)
        raise
    except Exception as exc:  # noqa: BLE001 - 统一成中文文案
        _remove(target_path)
        raise MediaError(f"转码失败：{exc}") from exc


def _add_video_stream(
    out_container: Any,
    in_stream: Any,
    vcodec: str,
    crf: int,
    preset: str,
    scale: int | None,
) -> Any:
    """按输入流参数建输出视频流（没有输入视频流时返回 `None`）。"""
    if in_stream is None:
        return None
    context = in_stream.codec_context
    options = {"crf": str(int(crf)), "preset": str(preset)}
    stream = out_container.add_stream(str(vcodec), rate=getattr(in_stream, "average_rate", None) or 25)
    width = int(getattr(context, "width", 0) or 0)
    height = int(getattr(context, "height", 0) or 0)
    if scale and width and height:
        limit = max(1, int(scale))
        if max(width, height) > limit:
            ratio = limit / max(width, height)
            width = max(2, int(width * ratio) // 2 * 2)
            height = max(2, int(height * ratio) // 2 * 2)
    stream.width = width or 640
    stream.height = height or 480
    stream.pix_fmt = "yuv420p"  # 兼容性最好（浏览器 / 系统播放器都认）
    stream.options = options
    return stream


def _add_audio_stream(out_container: Any, in_stream: Any, acodec: str) -> Any:
    """按输入流参数建输出音频流（没有输入音频流时返回 `None`）。"""
    if in_stream is None:
        return None
    context = in_stream.codec_context
    rate = int(getattr(context, "sample_rate", 0) or 44100)
    stream = out_container.add_stream(str(acodec), rate=rate)
    return stream


def _encode(decoded: Any, out_video: Any, out_audio: Any, out_container: Any) -> bool:
    """把一帧交给对应的编码器并写出包；返回是否真的写了东西。"""
    is_video = getattr(decoded, "width", 0) or type(decoded).__name__ == "VideoFrame"
    stream = out_video if is_video else out_audio
    if stream is None:
        return False
    decoded.pts = None  # 交给编码器按输出流的帧率 / 采样率重新打时间戳
    written = False
    for packet in stream.encode(decoded):
        out_container.mux(packet)
        written = True
    return written


def _flush(stream: Any, out_container: Any) -> int:
    """冲刷编码器（写出缓存的包）；返回写出的包数。

    短片段（只有几帧）在 libx264 这类带前瞻的编码器里可能一个包都不吐，直到冲刷才输出，
    因此「有没有产出」必须把冲刷算进去。
    """
    if stream is None:
        return 0
    written = 0
    try:
        for packet in stream.encode(None):
            out_container.mux(packet)
            written += 1
    except Exception as exc:  # noqa: BLE001 - 冲刷失败不掩盖已经写好的内容
        logger.debug("冲刷编码器失败：{}", exc)
    return written


def clip(
    source: str | Path,
    target: str | Path,
    *,
    start: float = 0.0,
    duration: float | None = None,
    container: str = "mp4",
) -> str:
    """按区间导出片段（转封装式，不解码）；返回产物路径，失败抛 `MediaError`。

    `start` 是起始秒数，`duration` 是时长（不给就到结尾）；时间戳会平移到 0，导出即可直接播。
    """
    av = _av()
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise MediaError(f"文件不存在：{source_path.name}")
    begin = max(0.0, float(start or 0.0))
    end = None if duration is None else begin + max(0.0, float(duration))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(source_path), mode="r") as in_container:
            streams = list(in_container.streams)
            if not streams:
                raise MediaError("这个文件里没有可用的媒体流")
            with av.open(str(target_path), mode="w", format=container) as out_container:
                mapping = [(stream, out_container.add_stream_from_template(stream)) for stream in streams]
                for packet in in_container.demux(*[item[0] for item in mapping]):
                    moment = _packet_seconds(packet)
                    if moment is None:
                        continue
                    if moment < begin:
                        continue
                    if end is not None and moment > end:
                        break
                    source_stream, out_stream = _pair_of(mapping, packet.stream)
                    offset = int(round(begin / float(packet.time_base))) if packet.time_base else 0
                    if packet.pts is not None:
                        packet.pts = max(0, packet.pts - offset)
                    if packet.dts is not None:
                        packet.dts = max(0, packet.dts - offset)
                    packet.stream = out_stream
                    out_container.mux(packet)
        return str(target_path)
    except MediaError:
        _remove(target_path)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove(target_path)
        raise MediaError(f"导出片段失败：{exc}") from exc


def _pair_of(mapping: list[tuple[Any, Any]], stream: Any) -> tuple[Any, Any]:
    """按输入流找对应的（输入流, 输出流）对。"""
    for source_stream, out_stream in mapping:
        if source_stream is stream or getattr(source_stream, "index", None) == getattr(stream, "index", None):
            return source_stream, out_stream
    raise MediaError("找不到对应的流")


def extract_audio(
    source: str | Path,
    target: str | Path,
    *,
    codec: str = "copy",
    on_progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """抽出音轨另存（`codec="copy"` 直接搬，否则转码）；返回产物路径，失败抛 `MediaError`。

    `copy` 跟着目标扩展名选容器（`.m4a` / `.mp3` / `.ogg`…）；原编码装不下时自动改走转码
    （例如 wav 的 PCM 要存成 mp3），不会写出一个打不开的文件。
    """
    av = _av()
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise MediaError(f"文件不存在：{source_path.name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = target_path.suffix.lower().lstrip(".")
    container = AUDIO_CONTAINERS.get(suffix, suffix or "mp3")
    try:
        with av.open(str(source_path), mode="r") as in_container:
            in_audio = next(iter(in_container.streams.audio), None)
            if in_audio is None:
                raise MediaError("这个文件里没有音轨")
            total = _container_seconds(in_container, av)
            in_codec = str(getattr(in_audio.codec_context, "name", "") or "")
            wanted = codec
            if codec == "copy" and in_codec not in _AUDIO_COPYABLE.get(container, frozenset()):
                wanted = _AUDIO_ENCODER.get(container, "aac")
                logger.debug("音轨编码 {} 装不进 {}，改用 {}", in_codec or "未知", container, wanted)
            copying = wanted == "copy"
            with av.open(str(target_path), mode="w", format=container) as out_container:
                out_stream = (
                    out_container.add_stream_from_template(in_audio)
                    if copying
                    else _add_audio_stream(out_container, in_audio, wanted)
                )
                position = 0.0
                written = 0
                for packet in in_container.demux(in_audio):
                    if cancel is not None and cancel():
                        raise MediaCancelled("抽出音轨已取消")
                    if copying:
                        if packet.dts is None:
                            continue  # 冲刷包
                        moment = _packet_seconds(packet)
                        if moment is not None:
                            position = max(position, moment)
                        packet.stream = out_stream
                        out_container.mux(packet)
                        written += 1
                    else:
                        for decoded in packet.decode():
                            if cancel is not None and cancel():
                                raise MediaCancelled("抽出音轨已取消")
                            decoded.pts = None  # 交给编码器按输出采样率重新打时间戳
                            for out_packet in out_stream.encode(decoded):
                                out_container.mux(out_packet)
                                written += 1
                            if decoded.time is not None:
                                position = max(position, float(decoded.time))
                    if on_progress is not None and total:
                        on_progress(min(0.99, position / total))
                if not copying:  # 转封装路径没有编码器缓存，不需要冲刷
                    written += _flush(out_stream, out_container)
                if not written:
                    raise MediaError("抽出音轨没有产出任何数据")
        if on_progress is not None:
            on_progress(1.0)
        return str(target_path)
    except MediaError:
        _remove(target_path)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove(target_path)
        raise MediaError(f"抽出音轨失败：{exc}") from exc


# --------------------------------------------------------------------- 字幕


def subtitle(source: str | Path, *, stream: int | None = None) -> list[SubtitleCue]:
    """解出内嵌字幕轨（`stream` 给流序号时只看那一条）；解不出来返回空列表，**不抛错**。

    没有结束时间的轨用下一条的开头当结束（最后一条给 3 秒），保证界面能按时序叠加显示。
    外部 `.srt` / `.vtt` 文件由查看器自己读文本解析（那是纯文本，不需要引擎）。
    """
    source_path = Path(source)
    if not source_path.is_file():
        return []
    av = _av_or_none()
    if av is None:
        return []
    marks: list[tuple[float, str]] = []
    try:
        with av.open(str(source_path), mode="r") as container:
            candidates = list(container.streams.subtitle or [])
            if stream is not None:
                candidates = [item for item in candidates if int(item.index) == int(stream)]
            for item in candidates:
                for decoded in container.decode(item):
                    text = _cue_text(decoded)
                    if not text:
                        continue
                    start = _seconds(getattr(decoded, "pts", None), getattr(decoded, "time_base", None))
                    marks.append((start, text))
    except Exception as exc:  # noqa: BLE001 - 字幕解不出来不影响播放
        logger.debug("解码字幕失败：{}", exc)
    if not marks:
        return []
    marks.sort(key=lambda pair: pair[0])
    cues: list[SubtitleCue] = []
    for index, (start, text) in enumerate(marks):
        if index + 1 < len(marks):
            end = marks[index + 1][0]
        else:
            end = start + 3.0
        cues.append(SubtitleCue(index=index, start=start, end=max(end, start), text=text))
    return cues


def _cue_text(decoded: Any) -> str:
    """把一条解码出来的字幕转成纯文本（去掉 ASS 的花括号标签）。"""
    parts: list[str] = []
    for rect in getattr(decoded, "rects", []) or []:
        raw = str(getattr(rect, "text", "") or "")
        if not raw:
            continue
        cleaned = _plain_subtitle_text(raw)
        if cleaned:
            parts.append(cleaned)
    return "\n".join(parts).strip()


def _plain_subtitle_text(raw: str) -> str:
    """尽量把字幕文本里的排版标签清掉（`{\\an8}` 这类 ASS 标签、`<i>` 这类 HTML 标签）。"""
    text = raw.replace("\\N", "\n").replace("\\n", "\n")
    out: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
        index += 1
    cleaned = "".join(out)
    for tag in ("<i>", "</i>", "<b>", "</b>", "<u>", "</u>", "<font", "</font>"):
        cleaned = cleaned.replace(tag, "")
    return cleaned.strip()


# --------------------------------------------------------------------- 临时产物


def temp_dir() -> Path:
    """媒体临时目录（`<根>/.tmp/media`），保证存在。"""
    paths.make_dir(paths.MEDIA_TMP_DIR)
    return paths.MEDIA_TMP_DIR


def temp_path(suffix: str = "", *, prefix: str = "media-") -> Path:
    """在媒体临时目录里起一个唯一产物名（并发任务互不踩）。"""
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    return temp_dir() / f"{prefix}{uuid.uuid4().hex[:12]}{suffix}"


def cleanup_temp(target: str | Path | None) -> bool:
    """删掉本模块产出的临时文件（只删临时目录里的，目录外的路径直接忽略）。"""
    if not target:
        return False
    path = Path(target)
    try:
        root = paths.MEDIA_TMP_DIR.resolve()
        if root not in path.resolve().parents:
            return False
    except OSError:
        return False
    return _remove(path)


def clear_temp_dir() -> int:
    """清空媒体临时目录（返回删掉的条目数）；进程退出与启动时兜底用。"""
    root = paths.MEDIA_TMP_DIR
    if not root.is_dir():
        return 0
    removed = 0
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.debug("清理媒体临时文件失败：{}（{}）", child, exc)
    return removed


def clear_stale(max_age_seconds: int = STALE_AGE_SECONDS) -> int:
    """启动时清掉上次进程遗留的陈旧产物（返回删掉的条目数）。"""
    root = paths.MEDIA_TMP_DIR
    if not root.is_dir():
        return 0
    deadline = time.time() - max(0, int(max_age_seconds))
    removed = 0
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        try:
            if child.stat().st_mtime >= deadline:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.debug("清理陈旧媒体文件失败：{}（{}）", child, exc)
    if removed:
        logger.info("已清理上次遗留的媒体临时文件 {} 个", removed)
    return removed


def _remove(target: str | Path | None) -> bool:
    """删一个文件（幂等，失败只记日志）。"""
    if not target:
        return False
    try:
        Path(target).unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.debug("删除文件失败：{}（{}）", target, exc)
        return False


def _exit_cleanup() -> None:
    """进程退出兜底：把临时目录清空（正常退出不该留东西）。"""
    try:
        clear_temp_dir()
    except Exception as exc:  # noqa: BLE001 - 退出路径绝不能再抛
        logger.debug("退出清理媒体临时目录失败：{}", exc)


atexit.register(_exit_cleanup)
