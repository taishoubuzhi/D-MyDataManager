"""Qt 多媒体后端的日志降噪。

QtMultimedia 自带的 ffmpeg 后端在打开音视频文件时，会把 libavformat 的
`Input #0 …` 容器信息、MediaFoundation 编解码器探测消息（`[h264_mf] …`、
`[hevc_mf] …`）这类 info 级日志直接写进控制台。它们不是错误，但会淹没程序
自己的日志，用户看到会以为播放出故障了。

Qt 用的那份 FFmpeg 是随 PyQt6 分发的独立 DLL（`PyQt6/Qt6/bin/avcodec-*.dll`
等），和 PyAV 自带的 `av.libs` 各是一份，互不影响。这里用 ctypes 按完整路径
把 Qt 的 libavutil 载入进来（Windows 按加载路径复用同一个模块实例，改的就是
Qt 正在用的那份全局状态），再把全局日志等级压到 `AV_LOG_PANIC`：libav 这一路
的日志整个关掉。

真正的播放错误不会因此丢失：Qt 自己会通过 `QMediaPlayer.errorOccurred` 交给
程序显示（视频查看器据此提示并询问是否转码）。剩下 Qt 自己那行
`qt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version …` 属于 Qt 日志，
是一次性的版本播报，保留。

PyAV 那一份由 `app.services.media_service` 自己压（它 import av 时就地处理）。
找不到 DLL 或加载失败只返回 False，调用方不必当错误处理：这只是降噪。
"""

from __future__ import annotations

import ctypes
from pathlib import Path

from loguru import logger

#: libavutil 的日志等级（见 avutil/log.h）：0 什么都不要，16 只留错误
AV_LOG_PANIC = 0
AV_LOG_ERROR = 16
#: Qt 的 FFmpeg 运行库文件名（带版本号，随 Qt 升级会变）
_AVUTIL_GLOB = "avutil-*.dll"
#: PyQt6 包里放 Qt 运行库的目录
_QT_BIN_DIR = Path("Qt6") / "bin"


def _avutil_path() -> Path | None:
    """Qt 自带的 libavutil 完整路径；找不到返回 None。"""
    try:
        import PyQt6
    except ImportError:  # 没有 PyQt6 就没什么可降噪的
        return None
    root = Path(PyQt6.__file__).resolve().parent
    for candidate in sorted((root / _QT_BIN_DIR).glob(_AVUTIL_GLOB)):
        return candidate
    return None


def _apply_level(path: Path, level: int) -> bool:
    """把某个 libavutil 的全局日志等级设成 level，成功返回 True。"""
    try:
        library = ctypes.WinDLL(str(path))
        setter = library.av_log_set_level
    except (AttributeError, OSError) as exc:
        logger.debug("静音 Qt 的 FFmpeg 日志失败（{}）：{}", path.name, exc)
        return False
    setter.argtypes = [ctypes.c_int]
    setter.restype = None
    setter(level)
    return True


def silence_ffmpeg_logs(level: int = AV_LOG_PANIC) -> bool:
    """把 Qt 自带 FFmpeg 的日志等级压到 level，成功返回 True。

    默认压到 `AV_LOG_PANIC`：libav 不再往控制台写任何东西（打开容器的
    `Input #0 …`、探测硬件编解码器的 `[h264_mf]` / `[hevc_mf]` 都会被丢掉）。
    排障时想留错误可以传 `AV_LOG_ERROR`。

    典型失败原因是找不到 Qt 的 libavutil（非 Windows、精简安装、Qt 大版本换了
    文件名），此时返回 False，播放行为不受影响。
    """
    path = _avutil_path()
    if path is None:
        logger.debug("没有找到 Qt 自带的 libavutil，跳过 FFmpeg 日志降噪")
        return False
    if not _apply_level(path, level):
        return False
    logger.debug("已把 Qt 自带 FFmpeg 的日志等级压到 {}（{}）", level, path.name)
    return True


__all__ = ["AV_LOG_ERROR", "AV_LOG_PANIC", "silence_ffmpeg_logs"]
