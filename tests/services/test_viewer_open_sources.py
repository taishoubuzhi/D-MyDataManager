"""查看器调度：把「调用方当前看到的文件顺序」交给查看器插件（用户 m00828）。

图片查看器的「上一张 / 下一张」原本只认同目录，而库里的文件按分类平铺、导入时还保留来源
子目录，同一目录常常只有一张图，于是切换等于没反应。修法是由调用方（数据管理页）把自己
这一页的路径顺序传下来，插件再按自己认的扩展名筛。这里盯住调度层的三件事：
`sources` 原样透传、`sources` 为空时不带这个关键字、老插件不认它时退回老签名而不是报失败。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services import viewer_service
from tests.harness import TempDir


class OpenPathSourcesCase(unittest.TestCase):
    """`open_path()` / `open_viewer_with()` 与查看器插件之间的调用约定。"""

    def setUp(self) -> None:
        self._tmp = TempDir("viewersources")
        root = self._tmp.__enter__()
        self.root = root
        self.first = self._file("a.png")
        self.second = self._file("b.png")
        self.viewer = SimpleNamespace(
            id="builtin.viewer.image", name="图片查看器", extensions=(".png",),
            kind="builtin", plugin_id="builtin.viewer.image", host="dialog",
            description="", capabilities=(),
        )

    def tearDown(self) -> None:
        self._tmp.__exit__(None, None, None)

    def _file(self, name: str) -> Path:
        target = self.root / name
        target.write_bytes(b"x")
        return target

    def _api(self, **methods) -> SimpleNamespace:
        return SimpleNamespace(**methods)

    def test_open_path_forwards_sources_in_order(self) -> None:
        calls: list = []

        def open_path(path, parent=None, *, sources=()):
            calls.append((Path(path), tuple(sources)))
            return True, "已打开"

        with mock.patch.object(viewer_service, "open_api", return_value=self._api(open_path=open_path)):
            ok, message = viewer_service.open_path(
                self.second, None, sources=(self.second, self.first)
            )

        self.assertTrue(ok, message)
        self.assertEqual(calls, [(self.second, (self.second, self.first))])

    def test_empty_sources_keeps_the_old_call_shape(self) -> None:
        """空 sources 不带关键字：老接口连 `sources` 都不认识，别自找 TypeError。"""
        calls: list = []

        def open_path(path, parent=None):  # 老签名：没有 sources
            calls.append((Path(path), parent))
            return True, "已打开"

        with mock.patch.object(viewer_service, "open_api", return_value=self._api(open_path=open_path)):
            first = viewer_service.open_path(self.second, None)
            second = viewer_service.open_path(self.second, None, sources=())

        self.assertTrue(first[0] and second[0])
        self.assertEqual(len(calls), 2)

    def test_old_plugin_falls_back_to_the_old_signature(self) -> None:
        """插件不认 `sources` 关键字时按老签名重试，不能变成「打开失败」。"""
        calls: list = []

        def open_path(path, parent=None):
            calls.append(Path(path))
            return True, "已打开"

        with mock.patch.object(viewer_service, "open_api", return_value=self._api(open_path=open_path)):
            ok, message = viewer_service.open_path(
                self.second, None, sources=(self.second, self.first)
            )

        self.assertTrue(ok, message)
        self.assertEqual(calls, [self.second])

    def test_open_viewer_with_forwards_sources(self) -> None:
        calls: list = []

        def open_viewer(path, viewer, parent=None, *, sources=()):
            calls.append((Path(path), viewer.id, tuple(sources)))
            return True, "已打开"

        with mock.patch.object(viewer_service, "open_api", return_value=self._api(open_viewer=open_viewer)):
            ok, message = viewer_service.open_viewer_with(
                self.second, self.viewer, None, sources=(self.first, self.second)
            )

        self.assertTrue(ok, message)
        self.assertEqual(calls, [(self.second, "builtin.viewer.image", (self.first, self.second))])

    def test_failure_is_reported_with_the_chinese_message(self) -> None:
        def open_path(path, parent=None, *, sources=()):
            raise RuntimeError("插件炸了")

        with mock.patch.object(viewer_service, "open_api", return_value=self._api(open_path=open_path)):
            ok, message = viewer_service.open_path(self.second, None, sources=(self.second,))

        self.assertFalse(ok)
        self.assertIn("打开失败", message)


if __name__ == "__main__":
    unittest.main()
