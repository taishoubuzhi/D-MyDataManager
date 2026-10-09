"""core.runtime.qt_media：把 Qt 自带那份 FFmpeg 的控制台日志压到静音。

QtMultimedia 的 ffmpeg 后端打开容器时会往 stderr 打一大段 `Input #0 …`，并探测硬件
解码器（`[h264_mf]` / `[hevc_mf]`）。这些是 libav 的全局日志等级控制的，改一改
`PyQt6/Qt6/bin/avutil-*.dll` 里的等级就干净了。
"""

from __future__ import annotations

import ctypes
import unittest

from app.core.runtime import qt_media


class QtMediaLogsTest(unittest.TestCase):
    """在装了 PyQt6 的环境里验证：静音真的改到了 Qt 那份 libav 上。"""

    def setUp(self) -> None:
        path = qt_media._avutil_path()
        if path is None:
            self.skipTest("PyQt6 的 Qt6/bin 下没有 avutil-*.dll")
        try:
            library = ctypes.WinDLL(str(path))
        except OSError:
            self.skipTest("Qt 的 avutil 加载不了，跳过")
        get_level = getattr(library, "av_log_get_level", None)
        if get_level is None:
            self.skipTest("avutil 没有导出 av_log_get_level，跳过")
        get_level.restype = ctypes.c_int
        self._path = path
        self._get_level = get_level

    def test_silence_switches_to_panic(self) -> None:
        self.assertTrue(qt_media.silence_ffmpeg_logs())
        self.assertEqual(qt_media.AV_LOG_PANIC, self._get_level())

    def test_level_is_configurable(self) -> None:
        """留一条退路：只想藏掉 info 时能压到 ERROR（真错误照样打出来）。"""
        self.assertTrue(qt_media.silence_ffmpeg_logs(qt_media.AV_LOG_ERROR))
        self.assertEqual(qt_media.AV_LOG_ERROR, self._get_level())
        qt_media.silence_ffmpeg_logs()

    def test_missing_library_is_reported_not_raised(self) -> None:
        self.assertFalse(qt_media._apply_level(self._path.parent / "没有这个.dll", qt_media.AV_LOG_PANIC))


if __name__ == "__main__":
    unittest.main()
