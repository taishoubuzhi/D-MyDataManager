"""库目录结构迁移：找回文件的三种真实布局（用户 m42407）。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.services import layout_migration


def _patch_existing(*paths: str):
    """把「哪些路径存在」打桩：测试进程在沙箱里建不了真文件。"""
    normalised = {item.replace("\\", "/") for item in paths}

    def is_file(self):
        return str(self).replace("\\", "/") in normalised

    return mock.patch.object(Path, "is_file", is_file)


class LocateLibraryFileCase(unittest.TestCase):
    """`_locate_library_file`：数据库路径优先，找不到时按真实布局回退。"""

    def test_authoritative_path_wins(self) -> None:
        with _patch_existing("库/默认用户/未分类/logo_6.png"):
            found = layout_migration._locate_library_file(Path("库"), "默认用户/未分类/logo_6.png")
        self.assertEqual(str(found).replace("\\", "/"), "库/默认用户/未分类/logo_6.png")

    def test_uncategorized_file_sits_in_the_user_root(self) -> None:
        with _patch_existing("库/默认用户/logo_6.png"), mock.patch.object(
            Path, "is_dir", lambda self: True
        ), mock.patch.object(Path, "iterdir", lambda self: iter(())):
            found = layout_migration._locate_library_file(Path("库"), "默认用户/未分类/logo_6.png")
        self.assertEqual(str(found).replace("\\", "/"), "库/默认用户/logo_6.png")

    def test_hidden_file_lives_under_dot_hiddens(self) -> None:
        with _patch_existing("库/默认用户/学习资料/.hiddens/秘密.txt"), mock.patch.object(
            Path, "is_dir", lambda self: True
        ), mock.patch.object(
            Path, "iterdir", lambda self: iter([Path("库/默认用户/学习资料")])
        ):
            found = layout_migration._locate_library_file(Path("库"), "默认用户/学习资料/秘密.txt")
        self.assertEqual(
            str(found).replace("\\", "/"), "库/默认用户/学习资料/.hiddens/秘密.txt"
        )

    def test_missing_file_returns_none(self) -> None:
        with _patch_existing(), mock.patch.object(Path, "is_dir", lambda self: False):
            self.assertIsNone(
                layout_migration._locate_library_file(Path("库"), "默认用户/未分类/没了.png")
            )


if __name__ == "__main__":
    unittest.main()
