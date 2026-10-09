"""媒体引擎服务（PyAV）的单元测试：探测、抽帧、逐帧、转封装、转码、切片、抽音轨。

样例文件由 PyAV 现场生成（不需要外部素材）：一段 1 秒、10 帧、96×64 的 mp4，
以及一段 0.3 秒的正弦波 wav。没有引擎（av 缺失）时全部走降级路线，不抛错。

跑法（仓库根目录）：

    .venv\\Scripts\\python.exe -m pytest tests/services/test_media_service.py -q
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase

from app.services import media_service


def _write_video(path: Path, *, frames: int = 10, fps: int = 10, size: tuple[int, int] = (96, 64)) -> Path:
    """生成一段测试视频（mpeg4，无音轨）。"""
    import av
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(frames):
            image = Image.new("RGB", size, ((index * 24) % 256, 60, 200))
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def _write_wav(path: Path, *, seconds: float = 0.3, rate: int = 8000) -> Path:
    """生成一段单声道正弦波 wav。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        data = bytearray()
        for index in range(total):
            value = int(12000 * math.sin(2 * math.pi * 440 * index / rate))
            data += struct.pack("<h", value)
        handle.writeframes(bytes(data))
    return path


class MediaEngineCase(IsolatedCase):
    """媒体引擎可用时的正常路径。"""

    sample: Path
    wav: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not media_service.available():
            return
        cls.sample = _write_video(cls.root / "media" / "样例.mp4")
        cls.wav = _write_wav(cls.root / "media" / "样例.wav")

    def setUp(self) -> None:
        super().setUp()
        if not media_service.available():
            self.skipTest("本机没有媒体引擎（av）")

    def test_engine_version_text(self) -> None:
        text = media_service.engine_version()
        self.assertTrue(text.startswith("PyAV "), f"版本文案应带 PyAV：{text!r}")

    def test_probe_reports_container_and_streams(self) -> None:
        info = media_service.probe(self.sample)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.has_video, "样例应当有视频流")
        self.assertEqual(info.width_height, (96, 64))
        self.assertAlmostEqual(info.duration, 1.0, delta=0.35)
        self.assertEqual(info.duration_text, media_service.format_time(info.duration))
        self.assertIn("96", info.describe_streams())

    def test_probe_unknown_file_returns_none(self) -> None:
        self.assertIsNone(media_service.probe(self.root / "根本没有.mp4"), "读不到时应当是 None")
        broken = self.root / "坏文件.mp4"
        broken.write_bytes(b"not a video at all")
        self.assertIsNone(media_service.probe(broken), "不是媒体文件时也应当是 None")

    def test_frame_writes_scaled_image(self) -> None:
        target = self.root / "media" / "帧.png"
        result = media_service.frame(self.sample, target, at=0.3, size=48)
        self.assertEqual(result, str(target))
        self.assertTrue(target.is_file(), "抽帧应当写出图片文件")
        from PIL import Image

        with Image.open(target) as image:
            self.assertLessEqual(max(image.size), 48, "长边不应超过上限")

    def test_frame_without_engine_or_bad_source_returns_empty(self) -> None:
        self.assertEqual(media_service.frame(self.root / "没有.mp4", self.root / "x.png"), "")
        with mock.patch.object(media_service, "available", return_value=False):
            self.assertEqual(media_service.frame(self.sample, self.root / "y.png"), "")

    def test_iter_frames_is_lazy_and_counted(self) -> None:
        frames = list(media_service.iter_frames(self.sample, count=3))
        self.assertEqual(len(frames), 3, "count 应当限制帧数")
        for item in frames:
            self.assertIsNotNone(item.image, "逐帧应当给出解码结果")
        seconds = [item.time for item in frames]
        self.assertEqual(seconds, sorted(seconds), "逐帧时间应当递增")
        self.assertEqual(list(media_service.iter_frames(self.root / "没有.mp4")), [], "读不到时是空迭代")

    def test_remux_keeps_streams(self) -> None:
        target = self.root / "media" / "转封装.mkv"
        result = media_service.remux(self.sample, target, container="matroska")
        self.assertEqual(result, str(target))
        info = media_service.probe(target)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.has_video)
        self.assertAlmostEqual(info.duration, 1.0, delta=0.35)

    def test_remux_can_keep_one_track(self) -> None:
        """换音轨 / 换字幕轨靠流筛选：只保留指定索引，其它原样保留。"""

        class _Fake:
            def __init__(self, index: int, kind: str) -> None:
                self.index = index
                self.type = kind

        streams = [_Fake(0, "video"), _Fake(1, "audio"), _Fake(2, "audio"), _Fake(3, "subtitle")]
        self.assertEqual(
            [item.index for item in media_service._select_streams(streams, keep_audio=2)],
            [0, 2, 3],
            "换音轨应只留下选中的那一条",
        )
        self.assertEqual(
            [item.index for item in media_service._select_streams(streams, keep_subtitle=99)],
            [0, 1, 2],
            "选了一条不存在的字幕轨应把字幕全筛掉，其它流不动",
        )
        self.assertEqual(len(media_service._select_streams(streams)), 4, "不给筛选时全部保留")

    def test_remux_with_unknown_track_raises(self) -> None:
        """选了一条不存在的音轨、把唯一的流筛没了：按「没有可用流」报错，且不留下半成品。"""
        target = self.root / "media" / "空轨.mkv"
        with self.assertRaises(media_service.MediaError):
            media_service.remux(self.wav, target, container="matroska", keep_audio=7)
        self.assertFalse(target.exists(), "失败后不该留下半成品")

    def test_transcode_scales_and_progress(self) -> None:
        target = self.root / "media" / "转码.mp4"
        seen: list[float] = []
        result = media_service.transcode(self.sample, target, scale=48, on_progress=seen.append)
        self.assertEqual(result, str(target))
        info = media_service.probe(target)
        self.assertIsNotNone(info)
        assert info is not None
        width, height = info.width_height
        self.assertLessEqual(max(width, height), 48, "scale 应当限制长边")
        self.assertTrue(seen, "转码应当报告进度")
        self.assertAlmostEqual(seen[-1], 1.0, delta=0.01)

    def test_transcode_cancel_removes_partial_file(self) -> None:
        target = self.root / "media" / "取消.mp4"
        with self.assertRaises(media_service.MediaCancelled):
            media_service.transcode(self.sample, target, cancel=lambda: True)
        self.assertFalse(target.exists(), "取消后不该留下半成品")

    def test_transcode_missing_source_raises(self) -> None:
        with self.assertRaises(media_service.MediaError):
            media_service.transcode(self.root / "没有.mp4", self.root / "x.mp4")

    def test_clip_trims_range(self) -> None:
        target = self.root / "media" / "片段.mp4"
        result = media_service.clip(self.sample, target, start=0.2, duration=0.4)
        self.assertEqual(result, str(target))
        info = media_service.probe(target)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertLess(info.duration, 0.95, "片段应当被裁短")
        self.assertGreater(info.duration, 0.05, "片段不该是空的")

    def test_extract_audio_from_wav(self) -> None:
        target = self.root / "media" / "音轨.mp3"
        result = media_service.extract_audio(self.wav, target, codec="mp3")
        self.assertEqual(result, str(target))
        info = media_service.probe(target)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.has_audio, "抽出的文件应当有音轨")

    def test_extract_audio_without_track_raises(self) -> None:
        with self.assertRaises(media_service.MediaError):
            media_service.extract_audio(self.sample, self.root / "media" / "无音轨.mp3")

    def test_subtitle_on_file_without_tracks(self) -> None:
        self.assertEqual(media_service.subtitle(self.sample), [], "没有字幕轨时应当返回空列表")

    def test_temp_helpers(self) -> None:
        produced = media_service.temp_path(".mp4")
        self.assertEqual(produced.parent, media_service.temp_dir())
        produced.write_bytes(b"x")
        self.assertTrue(media_service.cleanup_temp(produced))
        self.assertFalse(produced.exists())
        self.assertTrue(media_service.cleanup_temp(produced), "已经没了也算干净（幂等）")
        outside = self.root / "media" / "不在临时目录.mp4"
        outside.write_bytes(b"x")
        self.assertFalse(media_service.cleanup_temp(outside), "临时目录之外的文件不许删")
        self.assertTrue(outside.exists())


class MediaEngineMissingCase(IsolatedCase):
    """没有引擎（av 缺失）时的降级路线：能读的返回空值，动作类抛 MediaError。"""

    def setUp(self) -> None:
        super().setUp()
        self._patch = mock.patch.object(media_service, "available", return_value=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_available_and_version(self) -> None:
        self.assertFalse(media_service.available())
        self.assertEqual(media_service.engine_version(), "")

    def test_read_paths_degrade_silently(self) -> None:
        self.assertIsNone(media_service.probe("任意.mp4"))
        self.assertEqual(media_service.frame("任意.mp4", "任意.png"), "")
        self.assertEqual(list(media_service.iter_frames("任意.mp4")), [])
        self.assertEqual(media_service.subtitle("任意.mp4"), [])

    def test_action_paths_raise(self) -> None:
        for call in (
            lambda: media_service.remux("a.mp4", "b.mp4"),
            lambda: media_service.transcode("a.mp4", "b.mp4"),
            lambda: media_service.clip("a.mp4", "b.mp4"),
            lambda: media_service.extract_audio("a.mp4", "b.mp3"),
        ):
            with self.subTest(call=call):
                with self.assertRaises(media_service.MediaError):
                    call()


class MediaFacadeCase(IsolatedCase):
    """插件门面 `app.sdk.media`：程序本体登记接口后，插件侧调用与内部一致。"""

    sample: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.sample = _write_video(cls.root / "media" / "门面.mp4", frames=6)

    def setUp(self) -> None:
        super().setUp()
        from app.core.plugins.extensions import extension_registry
        from app.sdk import media as sdk_media
        from app.sdk.errors import SdkError
        from app.services import media_api

        if not media_service.available():
            self.skipTest("本机没有媒体引擎（av）")
        self.sdk_media = sdk_media
        self.SdkError = SdkError
        self.registry = extension_registry
        self.registry.provide(sdk_media.MEDIA_EXTENSION, media_api.api(), "")
        self.addCleanup(self.registry.clear)

    def test_facade_matches_internal(self) -> None:
        info = self.sdk_media.probe(self.sample)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.width_height, media_service.probe(self.sample).width_height)
        self.assertTrue(self.sdk_media.available())
        self.assertEqual(self.sdk_media.engine_version(), media_service.engine_version())

    def test_facade_frame_and_temp(self) -> None:
        target = self.sdk_media.temp_path(".png")
        self.assertEqual(self.sdk_media.frame(self.sample, target, size=32), str(target))
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, Path(self.sdk_media.temp_dir()))
        self.assertTrue(self.sdk_media.cleanup_temp(target))
        self.assertFalse(target.exists())

    def test_facade_transcode_and_clip(self) -> None:
        smaller = self.root / "media" / "门面-小.mp4"
        self.assertEqual(self.sdk_media.transcode(self.sample, smaller, scale=32), str(smaller))
        self.assertLessEqual(max(media_service.probe(smaller).width_height), 32)
        piece = self.root / "media" / "门面-片段.mp4"
        self.assertEqual(self.sdk_media.clip(self.sample, piece, start=0.0, duration=0.2), str(piece))
        self.assertTrue(piece.is_file())

    def test_facade_without_provider(self) -> None:
        self.registry.clear()
        self.assertIsNone(self.sdk_media.probe(self.sample), "没有接口时探测应当返回空")
        self.assertEqual(self.sdk_media.frame(self.sample, self.root / "x.png"), "")
        self.assertEqual(list(self.sdk_media.iter_frames(self.sample)), [])
        self.assertEqual(self.sdk_media.subtitle(self.sample), [])
        with self.assertRaises(self.SdkError):
            self.sdk_media.transcode(self.sample, self.root / "x.mp4")
        with self.assertRaises(self.SdkError):
            self.sdk_media.temp_dir()
