"""内置图片查看器：上一张 / 下一张走哪一串图片，以及切换后的外壳联动（离线用例）。

用户 m00828 报「切上一张 / 下一张失效」：库里的图片按分类平铺、导入时还保留来源子目录，
同一目录往往只有一张图，只按同目录找 siblings 就等于切不动。这里的用例盯住两条：
宿主（数据管理页）给的列表优先且保持它的顺序，宿主没给（或只给了一张）才退回同目录。
"""

from __future__ import annotations

import struct
import sys
import types
import unittest
import zlib
from pathlib import Path

from tests.harness import IsolatedCase, TempDir

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "plugins"


def _png_bytes(width: int = 4, height: int = 2) -> bytes:
    """现造一张合法的 1×1 级 PNG（不引第三方库，QPixmap 能真的装出来）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


#: 用例只关心路径与顺序，画面内容无所谓
PNG_BYTES = _png_bytes()


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.*` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.builtin.lib", None),
        ("dm_plugin.builtin.lib.ui", PLUGINS / "builtin.lib.ui"),
        ("dm_plugin.builtin.lib.viewer", PLUGINS / "builtin.lib.viewer"),
        ("dm_plugin.builtin.viewer", None),
        ("dm_plugin.builtin.viewer.image", PLUGINS / "builtin.viewer.image"),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()


def _png(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG_BYTES)
    return target


class ImageBrowseCase(unittest.TestCase):
    """纯逻辑：`browse_images()` 取谁、按什么顺序、什么时候退回同目录。"""

    def setUp(self) -> None:
        self._tmp = TempDir("viewerimage")
        root = self._tmp.__enter__()
        self.root = root
        self.a = _png(root / "a.png")
        self.b = _png(root / "b.png")
        self.c = _png(root / "c.png")
        self.text = _png(root / "note.txt")
        self.solo = _png(root / "sub" / "solo.png")

    def tearDown(self) -> None:
        self._tmp.__exit__(None, None, None)

    def _browse(self, path: Path, sources, extensions=("png",)):
        from dm_plugin.builtin.viewer.image.image_view import browse_images

        return browse_images(path, sources, extensions)

    def test_host_list_wins_and_keeps_its_order(self) -> None:
        files = self._browse(self.b, [self.c, self.b, self.a])
        self.assertEqual([item.name for item in files], ["c.png", "b.png", "a.png"])

    def test_host_list_drops_other_suffixes_and_missing_files(self) -> None:
        files = self._browse(self.b, [self.c, self.text, self.root / "gone.png", self.b])
        self.assertEqual([item.name for item in files], ["c.png", "b.png"])

    def test_falls_back_to_siblings_when_host_list_has_one(self) -> None:
        # 宿主这一页只有当前这张图：退回同目录（表里 a/b/c 三张）
        files = self._browse(self.b, [self.b])
        self.assertEqual([item.name for item in files], ["a.png", "b.png", "c.png"])

    def test_falls_back_when_current_image_absent_from_host_list(self) -> None:
        files = self._browse(self.solo, [self.a, self.b, self.c])
        self.assertEqual([item.name for item in files], ["solo.png"])

    def test_single_image_stays_alone(self) -> None:
        files = self._browse(self.solo, [self.solo])
        self.assertEqual([item.name for item in files], ["solo.png"])


class ImageStepCase(IsolatedCase):
    """Qt：上一张 / 下一张、切换后发出的信号、只有一张时的按钮状态、外壳标题。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        super().setUp()
        imgs = type(self).root / "imgs"
        self.a = _png(imgs / "a.png")
        self.b = _png(imgs / "b.png")
        self.c = _png(imgs / "c.png")
        self.solo = _png(imgs / "sub" / "solo.png")

    def _viewer(self, path: Path, sources=()):
        from dm_plugin.builtin.viewer.image.image_view import ImageViewer

        viewer = ImageViewer(path, None, extensions=("png",), sources=sources)
        return viewer

    def test_next_follows_host_order(self) -> None:
        viewer = self._viewer(self.b, sources=(self.c, self.b, self.a))
        try:
            self.assertFalse(viewer._pixmap.isNull())  # 打开的就是当前这张
            self.assertEqual([item.name for item in viewer._siblings], ["c.png", "b.png", "a.png"])
            self.assertIn("第 2/3 张", viewer.status_label.text())
            viewer._next_button.click()
            self.assertEqual(viewer._path.name, "a.png")
            self.assertIn("第 3/3 张", viewer.status_label.text())
            viewer._prev_button.click()
            self.assertEqual(viewer._path.name, "b.png")
        finally:
            self.drop_widget(viewer)

    def test_switching_emits_path_and_caption(self) -> None:
        viewer = self._viewer(self.b, sources=(self.c, self.b, self.a))
        try:
            paths: list[str] = []
            captions: list[str] = []
            viewer.pathChanged.connect(paths.append)
            viewer.captionChanged.connect(captions.append)
            viewer._next_button.click()
            self.assertEqual(paths, [str(self.a)])
            self.assertTrue(captions)
            self.assertIn("a.png", captions[-1])
            # 副标题文字与内容页说明保持一致，外壳才刷得对
            self.assertEqual(viewer.caption, captions[-1])
        finally:
            self.drop_widget(viewer)

    def test_single_image_disables_buttons(self) -> None:
        viewer = self._viewer(self.solo, sources=(self.solo,))
        try:
            self.assertFalse(viewer._prev_button.isEnabled())
            self.assertFalse(viewer._next_button.isEnabled())
            self.assertIn("没有别的图片", viewer._next_button.toolTip())
            viewer._next_button.click()
            viewer._step_image(1)
            self.assertEqual(viewer._path.name, "solo.png")
        finally:
            self.drop_widget(viewer)

    def test_window_applies_sources_and_follows_title(self) -> None:
        from PyQt6.QtWidgets import QWidget

        from dm_plugin.builtin.lib.viewer.viewer_window import ViewerWindow
        from dm_plugin.builtin.viewer.image.image_view import ImageViewer

        class _Popup:
            def __init__(self) -> None:
                self.actions: list[str] = []
                self.meta = ""
                self.title = ""

            def add_action(self, icon, tooltip, callback) -> None:
                self.actions.append(tooltip)

            def set_meta(self, text) -> None:
                self.meta = text

            def set_title(self, text) -> None:
                self.title = text

        holder = QWidget()
        window = ViewerWindow(
            self.b,
            lambda container: ImageViewer(self.b, container, extensions=("png",)),
            "图片查看器",
            holder,
            sources=(self.c, self.b, self.a),
        )
        try:
            content = window.content_widget
            self.assertEqual([item.name for item in content._siblings], ["c.png", "b.png", "a.png"])
            popup = _Popup()
            window.attach_popup(popup)
            self.assertIn("b.png", popup.meta)
            content._next_button.click()
            # 外壳跟着换：标题、副标题、以及「定位文件 / 用系统程序打开」的目标路径
            self.assertEqual(window.path.name, "a.png")
            self.assertEqual(popup.title, "a.png")
            self.assertIn("a.png", popup.meta)
        finally:
            self.drop_widget(window)
            self.drop_widget(holder)


if __name__ == "__main__":
    unittest.main()
