"""媒体接口实现：把 `media_service`（PyAV 进程内引擎）以扩展点形式交给插件。

程序本体在 `src/main.py` 启动时注册：

    plugin_service.bootstrap(MEDIA_EXTENSION, media_api.api())

插件侧**不直接 import 本模块**，而是走门面 `app.sdk.media`（`sdk.media.probe(...)`）。

为什么不把 `media_service` 直接当实现给插件：

- 插件只能依赖 `app.sdk`，`app.services` 是程序内部层次（`AGENTS.md` 的分层约定）；
- 以后要加线程池 / 进度播报 / 限额（同时只跑几个转码）时，改这里就够了，
  `app.sdk.media` 的函数签名不用动。

本模块自身不做线程、不弹界面：每个方法就是一次同步调用，**阻塞的是调用方的线程**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..sdk.media import DecodedFrame, MediaError, MediaInfo, MediaStream, SubtitleCue
from . import media_service

__all__ = ["MediaApi", "api"]


_API: "MediaApi | None" = None


def api() -> "MediaApi":
    """媒体接口单例（`main.py` 注册扩展点时取它）。"""
    global _API
    if _API is None:
        _API = MediaApi()
    return _API


class MediaApi:
    """`media.open` 扩展点的实现：逐项委托给 `services.media_service`。"""

    # ---- 引擎自述
    def available(self) -> bool:
        """媒体引擎能不能用（`av` 缺了就为假）。"""
        return media_service.available()

    def engine_version(self) -> str:
        """引擎版本文案（探测页 / 诊断用）。"""
        return media_service.engine_version()

    # ---- 只读：读不到就降级，不抛错
    def probe(self, path: str | Path) -> MediaInfo | None:
        """读媒体信息；读不到返回 `None`。"""
        return media_service.probe(path)

    def frame(
        self,
        source: str | Path,
        target: str | Path,
        *,
        at: float | None = None,
        size: int | None = media_service.DEFAULT_FRAME_SIZE,
    ) -> str:
        """抽一帧存成图片，返回路径；失败返回空串。"""
        return media_service.frame(source, target, at=at, size=size)

    def iter_frames(
        self,
        source: str | Path,
        *,
        start: float | None = None,
        count: int | None = None,
        step: int = 1,
        size: int | None = None,
    ) -> Any:
        """逐帧解码（生成器，出 `DecodedFrame`）。"""
        return media_service.iter_frames(source, start=start, count=count, step=step, size=size)

    def subtitle(self, source: str | Path, *, stream: int | None = None) -> list[SubtitleCue]:
        """解内嵌字幕轨；解不出来返回空列表。"""
        return media_service.subtitle(source, stream=stream)

    # ---- 写操作：失败抛 MediaError，由调用方决定怎么提示
    def remux(
        self,
        source: str | Path,
        target: str | Path,
        *,
        container: str = "mp4",
        keep_audio: int | None = None,
        keep_subtitle: int | None = None,
        on_progress: Any = None,
        cancel: Any = None,
    ) -> str:
        """只换容器（不解码）：mkv / flv / avi → mp4 这类兜底；可只保留指定音轨 / 字幕轨。"""
        return media_service.remux(
            source,
            target,
            container=container,
            keep_audio=keep_audio,
            keep_subtitle=keep_subtitle,
            on_progress=on_progress,
            cancel=cancel,
        )

    def transcode(
        self,
        source: str | Path,
        target: str | Path,
        *,
        vcodec: str = media_service.DEFAULT_VIDEO_CODEC,
        acodec: str = media_service.DEFAULT_AUDIO_CODEC,
        scale: int | None = None,
        on_progress: Any = None,
        cancel: Any = None,
    ) -> str:
        """转码（参数是白名单项，不接受任意命令行参数）。"""
        return media_service.transcode(
            source,
            target,
            vcodec=vcodec,
            acodec=acodec,
            scale=scale,
            on_progress=on_progress,
            cancel=cancel,
        )

    def clip(
        self,
        source: str | Path,
        target: str | Path,
        *,
        start: float = 0.0,
        duration: float | None = None,
        container: str = "mp4",
    ) -> str:
        """按区间导出片段（转封装式）。"""
        return media_service.clip(source, target, start=start, duration=duration, container=container)

    def extract_audio(
        self,
        source: str | Path,
        target: str | Path,
        *,
        codec: str = "copy",
        on_progress: Any = None,
        cancel: Any = None,
    ) -> str:
        """抽出音轨另存。"""
        return media_service.extract_audio(source, target, codec=codec, on_progress=on_progress, cancel=cancel)

    # ---- 临时产物：统一落在媒体临时目录，窗口关闭 / 任务结束由调用方清理
    def temp_path(self, suffix: str = "") -> Path:
        """在媒体临时目录里起一个唯一产物名。"""
        return media_service.temp_path(suffix)

    def temp_dir(self) -> Path:
        """媒体临时目录（`<根>/.tmp/media`）。"""
        return media_service.temp_dir()

    def cleanup_temp(self, target: str | Path | None) -> bool:
        """删掉某个临时产物（只认媒体临时目录里的路径）。"""
        return media_service.cleanup_temp(target)


#: 导出给类型检查 / 文档：插件侧从 `app.sdk.media` 拿到的就是这些快照类型
SNAPSHOT_TYPES = (DecodedFrame, MediaError, MediaInfo, MediaStream, SubtitleCue)
