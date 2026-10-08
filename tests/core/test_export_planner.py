"""导出分包与打包的单测：不碰界面与数据库，压缩包写到临时目录里。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core.export.packer import PART_SUFFIX, PackEntry, PackError, ZipPack, write_zip
from app.core.export.planner import (
    UNASSIGNED_LABEL,
    PlannedItem,
    mode_label,
    name_packages,
    plan_packages,
)

_MOMENT = dt.datetime(2026, 10, 8, 20, 3, 5)


def _item(key: str, name: str, category: str = "", user: str = "甲用户", size: int = 10) -> PlannedItem:
    return PlannedItem(key=key, name=name, category=category, user=user, size=size)


class PlanTests(unittest.TestCase):
    def test_single_keeps_everything_in_one_package(self) -> None:
        plan = plan_packages([_item("1", "a.txt", "影视/电影"), _item("2", "b.txt")], mode="single")
        self.assertEqual(plan.mode, "single")
        self.assertEqual(len(plan.packages), 1)
        self.assertEqual(plan.total, 2)
        self.assertEqual(plan.packages[0].label, "")

    def test_top_groups_by_root_category(self) -> None:
        plan = plan_packages(
            [
                _item("1", "a.txt", "影视/电影"),
                _item("2", "b.txt", "音乐/专辑/2026"),
                _item("3", "c.txt", "影视"),
            ],
            mode="top",
        )
        self.assertEqual(plan.mode, "top")
        self.assertEqual(plan.labels, ("影视", "音乐"))
        self.assertEqual(plan.packages[0].count, 2)
        self.assertEqual(plan.packages[1].count, 1)
        self.assertEqual(plan.total, 3)

    def test_top_puts_unassigned_last(self) -> None:
        plan = plan_packages(
            [_item("1", "a.txt"), _item("2", "b.txt", "影视"), _item("3", "c.txt", "")],
            mode="top",
        )
        self.assertEqual(plan.labels, ("影视", UNASSIGNED_LABEL))
        self.assertEqual(plan.packages[-1].top, "")

    def test_top_handles_backslash_paths(self) -> None:
        plan = plan_packages([_item("1", "a.txt", "影视\\电影")], mode="top")
        self.assertEqual(plan.labels, ("影视",))

    def test_empty_plan(self) -> None:
        plan = plan_packages([], mode="top")
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.labels, ())
        self.assertIn("没有要导出的数据", plan.summary())

    def test_unknown_mode_falls_back_to_single(self) -> None:
        plan = plan_packages([_item("1", "a.txt", "影视")], mode="乱写")
        self.assertEqual(plan.mode, "single")

    def test_package_helpers(self) -> None:
        plan = plan_packages([_item("1", "a.txt", "影视", size=7), _item("2", "b.txt", "影视", size=5)])
        package = plan.packages[0]
        self.assertEqual(package.size, 12)
        self.assertEqual(package.users, ("甲用户",))
        self.assertIn("1 个压缩包", plan.summary())

    def test_mode_label(self) -> None:
        self.assertEqual(mode_label("top"), "按最顶层分类分别打包")
        self.assertEqual(mode_label("single"), "打成一个压缩包")
        self.assertEqual(mode_label("其他"), "其他")


class NameTests(unittest.TestCase):
    def _packages(self, *labels: str):
        items = tuple(_item(str(index), f"{label or 'x'}.txt", label) for index, label in enumerate(labels, 1))
        return plan_packages(items, mode="top").packages

    def test_numbers_are_per_package(self) -> None:
        named = name_packages(
            self._packages("影视", "音乐", "游戏"),
            "{number,3,2}-{category}",
            now=_MOMENT,
        )
        self.assertEqual([row.filename for row in named], ["3-影视.zip", "5-音乐.zip", "7-游戏.zip"])

    def test_template_gets_user_and_count(self) -> None:
        named = name_packages(self._packages("影视"), "{user}-{category}-{count}份", now=_MOMENT, user="乙用户")
        self.assertEqual(named[0].filename, "乙用户-影视-1份.zip")
        self.assertEqual(named[0].count, 1)

    def test_suffix_only_added_when_missing(self) -> None:
        named = name_packages(self._packages("影视"), "全包.zip", now=_MOMENT)
        self.assertEqual(named[0].filename, "全包.zip")

    def test_illegal_characters_are_cleaned(self) -> None:
        named = name_packages(self._packages("影视"), "包:{category}?", now=_MOMENT)
        self.assertEqual(named[0].filename, "包影视.zip")

    def test_duplicates_get_numbers(self) -> None:
        named = name_packages(self._packages("影视", "音乐"), "同名", now=_MOMENT)
        self.assertEqual([row.filename for row in named], ["同名.zip", "同名_1.zip"])

    def test_warnings_are_reported(self) -> None:
        named = name_packages(self._packages("影视"), "{nope}-{category}", now=_MOMENT)
        self.assertTrue(named[0].warnings)
        self.assertEqual(named[0].title, "{nope}-影视")

    def test_creator_prefers_package_user(self) -> None:
        named = name_packages(self._packages("影视"), "{creator}", now=_MOMENT, user="乙用户")
        self.assertEqual(named[0].filename, "甲用户.zip")

    def test_no_packages(self) -> None:
        self.assertEqual(name_packages((), "{number}", now=_MOMENT), ())


class PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="dm_export_pack_")
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_write_and_read_back(self) -> None:
        target = self.root / "包.zip"
        with ZipPack(target) as pack:
            self.assertEqual(pack.path, target)
            self.assertEqual(pack.add_text("清单.csv", "名称\n甲\n"), "清单.csv")
            self.assertEqual(pack.add_bytes("内容/a.bin", b"\x00\x01"), "内容/a.bin")
        self.assertTrue(target.is_file())
        self.assertFalse((self.root / f"包.zip{PART_SUFFIX}").exists())
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(sorted(archive.namelist()), ["内容/a.bin", "清单.csv"])
            self.assertEqual(archive.read("清单.csv").decode("utf-8"), "名称\n甲\n")
            self.assertEqual(archive.read("内容/a.bin"), b"\x00\x01")

    def test_suffix_is_added(self) -> None:
        with ZipPack(self.root / "没有后缀") as pack:
            pack.add_text("a.txt", "a")
        self.assertTrue((self.root / "没有后缀.zip").is_file())

    def test_duplicate_member_names_get_numbers(self) -> None:
        with ZipPack(self.root / "包") as pack:
            first = pack.add_text("清单.csv", "1")
            second = pack.add_text("清单.csv", "2")
        self.assertEqual((first, second), ("清单.csv", "清单_1.csv"))

    def test_streaming_chunks(self) -> None:
        target = self.root / "流式.zip"
        with ZipPack(target) as pack:
            pack.add_stream("大文件.bin", (b"aaa", b"", b"bbb"))
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("大文件.bin"), b"aaabbb")

    def test_add_file_reads_from_disk(self) -> None:
        source = self.root / "源.txt"
        source.write_text("内容", encoding="utf-8")
        target = self.root / "文件.zip"
        with ZipPack(target) as pack:
            pack.add_file("深/源.txt", source)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("深/源.txt").decode("utf-8"), "内容")

    def test_exception_inside_with_leaves_nothing(self) -> None:
        target = self.root / "半成品.zip"
        with self.assertRaises(RuntimeError):
            with ZipPack(target) as pack:
                pack.add_text("a.txt", "a")
                raise RuntimeError("中途失败")
        self.assertFalse(target.exists())
        self.assertFalse((self.root / f"半成品.zip{PART_SUFFIX}").exists())

    def test_abort_removes_part(self) -> None:
        pack = ZipPack(self.root / "放弃")
        pack.add_text("a.txt", "a")
        pack.abort()
        self.assertFalse((self.root / "放弃.zip").exists())
        self.assertFalse((self.root / f"放弃.zip{PART_SUFFIX}").exists())

    def test_write_after_close_raises(self) -> None:
        pack = ZipPack(self.root / "关了的包")
        pack.close()
        with self.assertRaises(PackError):
            pack.add_text("a.txt", "a")

    def test_replace_failure_cleans_part(self) -> None:
        """「挪不动」的时候不能留一个看起来像成品的 `.part`。"""
        from app.core.export import packer

        target = self.root / "占用.zip"
        original = packer._replace
        packer._replace = lambda source, goal: False
        try:
            pack = ZipPack(target)
            pack.add_text("a.txt", "a")
            part = pack.part
            with self.assertRaises(PackError):
                pack.close()
            self.assertFalse(part.exists())
            self.assertFalse(target.exists())
        finally:
            packer._replace = original

    def test_write_zip_convenience(self) -> None:
        target = write_zip(self.root / "便捷", [PackEntry(name="a.txt", data=b"aa")])
        self.assertEqual(target, self.root / "便捷.zip")
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(archive.read("a.txt"), b"aa")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
