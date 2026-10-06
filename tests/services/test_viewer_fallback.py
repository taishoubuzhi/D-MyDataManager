"""「系统默认程序打不开」时的退路：改用程序内查看器（用户 m08240）。

本机实测 `os.startfile()` 对任何路径都 `[WinError 5]`，Store 版记事本这类 AppX 关联连
资源管理器代开都起不来；这时 `open_system()` 不能返回一个「成功」把用户晾在那儿，
而要落到能打开这个格式的内置查看器上。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services import viewer_service


def _viewer(name: str = "文本") -> SimpleNamespace:
    return SimpleNamespace(
        id="text", name=name, extensions=(".txt",), kind="builtin",
        plugin_id="builtin.viewer.text", host="", description="", capabilities=(),
    )


class SystemOpenFallbackCase(unittest.TestCase):
    """`open_system()`：系统拒绝 → 内置查看器；没有查看器 → 如实报失败。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.target = Path(self._dir.name) / "甲.txt"
        self.target.write_text("内容", encoding="utf-8")

    def test_missing_file_reports_failure(self):
        ok, message = viewer_service.open_system(Path(self._dir.name) / "没有这个.txt")
        self.assertFalse(ok)
        self.assertIn("不存在", message)

    def test_system_success_does_not_open_a_viewer(self):
        api = SimpleNamespace(open_external=lambda path, ask=False: (True, "已交给系统默认程序打开"))
        with mock.patch.object(viewer_service, "open_api", return_value=api), mock.patch.object(
            viewer_service, "open_viewer_with"
        ) as backup:
            ok, message = viewer_service.open_system(self.target)
        self.assertTrue(ok)
        self.assertIn("系统默认程序", message)
        backup.assert_not_called()

    def test_system_refused_falls_back_to_builtin_viewer(self):
        viewer = _viewer()
        opened: list = []
        with mock.patch.object(
            viewer_service, "open_api", return_value=None
        ), mock.patch.object(
            viewer_service, "viewers_for", return_value=(viewer,)
        ), mock.patch.object(
            viewer_service, "open_viewer_with",
            side_effect=lambda path, target_viewer, parent=None: (
                opened.append(target_viewer),
                (True, "已用内置查看器打开"),
            )[1],
        ), mock.patch("app.sdk.ui.open_default", return_value=False) as default:
            ok, message = viewer_service.open_system(self.target)
        self.assertTrue(ok)
        self.assertEqual(opened, [viewer])
        self.assertIn("内置查看器「文本」", message)
        default.assert_called_once_with(self.target)

    def test_without_any_viewer_reports_failure(self):
        with mock.patch.object(
            viewer_service, "open_api", return_value=None
        ), mock.patch.object(
            viewer_service, "viewers_for", return_value=()
        ), mock.patch("app.sdk.ui.open_default", return_value=False):
            ok, message = viewer_service.open_system(self.target)
        self.assertFalse(ok)
        self.assertEqual(message, "系统无法打开该文件")


if __name__ == "__main__":
    unittest.main()
