"""导出服务（`app.services.export_service`）的用例。

覆盖三件事：老的单包/平铺导出不许退化、分包导出按「最顶层分类」切包并走命名模板、
以及「源文件缺失」要被数出来而不是静默漏掉。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.db.models import Category, DataType
from app.repositories import ItemRepository
from app.services.export_service import ExportService, MANIFEST_FIELDS

from tests.harness import IsolatedCase

_MOMENT = dt.datetime(2026, 10, 8, 20, 30, 0)


class ExportServiceCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.library = self.default_library()
        self.user = self.current_user()
        self.service = ExportService(self.session)
        self._temp = tempfile.TemporaryDirectory(prefix="dm_export_svc_")
        self.out = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()
        super().tearDown()

    # ------------------------------------------------------------------ 便捷
    def _category(self, *names: str) -> Category:
        parent = None
        node = None
        for name in names:
            node = Category(name=name, parent_id=parent.id if parent else None, user_id=self.user.id)
            self.session.add(node)
            self.session.flush()
            parent = node
        return node

    def _text(self, name: str, content: str = "正文", *, category: Category | None = None):
        item = self.importer(library=self.library).import_text(
            name,
            content,
            user_id=self.user.id,
            category_id=category.id if category else None,
        )
        self.session.flush()
        return item

    def _broken(self, name: str = "缺源.bin"):
        """一条内容不在仓库里的条目：导出时应被算作「源文件缺失」。"""
        item = ItemRepository(self.session).create(
            name=name,
            type=DataType.OTHER,
            checksum="0" * 64,
            size=11,
            library_id=self.library.id,
            user_id=self.user.id,
        )
        self.session.flush()
        return item

    def _read(self, path: Path) -> dict[str, bytes]:
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    # ------------------------------------------------------------------ 平铺
    def test_export_items_writes_files_and_manifest(self) -> None:
        items = [self._text("甲"), self._text("乙")]
        result = self.service.export_items(items, self.out)
        self.assertEqual(result.exported, 2)
        self.assertEqual(result.missing, 0)
        self.assertTrue((self.out / "甲.txt").is_file())
        self.assertTrue((self.out / "乙.txt").is_file())
        self.assertTrue(result.manifest.is_file())
        self.assertIn("已导出 2 个文件", result.summary())

    def test_export_archive_leaves_no_part_file(self) -> None:
        items = [self._text("甲", "内容甲")]
        result = self.service.export_archive(items, self.out / "整包")
        self.assertEqual(result.path, self.out / "整包.zip")
        self.assertTrue(result.path.is_file())
        self.assertFalse((self.out / "整包.zip.part").exists())
        members = self._read(result.path)
        self.assertEqual(members["甲.txt"].decode("utf-8"), "内容甲")
        self.assertEqual(len([name for name in members if name.startswith("清单-")]), 1)

    def test_export_archive_rejects_empty_selection(self) -> None:
        with self.assertRaises(ValueError):
            self.service.export_archive([], self.out / "空.zip")

    def test_manifest_columns_are_stable(self) -> None:
        result = self.service.export_archive([self._text("甲")], self.out / "清单包")
        members = self._read(result.path)
        manifest = next(name for name in members if name.startswith("清单-"))
        rows = list(csv.DictReader(io.StringIO(members[manifest].decode("utf-8-sig"))))
        self.assertEqual(list(rows[0]), MANIFEST_FIELDS)
        self.assertEqual(rows[0]["名称"], "甲")

    def test_missing_source_is_counted(self) -> None:
        result = self.service.export_archive([self._text("甲"), self._broken()], self.out / "缺源包")
        self.assertEqual(result.exported, 1)
        self.assertEqual(result.missing, 1)
        self.assertIn("1 个源文件缺失", result.summary())

    # ------------------------------------------------------------------ 分包
    def test_single_mode_packs_everything_into_one_file(self) -> None:
        items = [self._text("甲", category=self._category("影视", "电影")), self._text("乙")]
        result = self.service.export_packages(items, self.out, now=_MOMENT, user="甲用户")
        self.assertEqual(len(result.packages), 1)
        self.assertEqual(result.paths[0].name, "导出-2026-10-08-1.zip")
        self.assertEqual(result.exported, 2)
        self.assertEqual(sorted(self._read(result.paths[0])), sorted(["甲.txt", "乙.txt", "清单-20261008-203000.csv"]))

    def test_top_mode_splits_by_root_category(self) -> None:
        film = self._category("影视", "电影")
        music = self._category("音乐")
        items = [self._text("甲", category=film), self._text("乙", category=music), self._text("丙")]
        result = self.service.export_packages(items, self.out, mode="top", now=_MOMENT)
        self.assertEqual(len(result.packages), 3)
        self.assertEqual([row.label for row in result.packages], ["影视", "音乐", "未分类"])
        self.assertEqual(result.exported, 3)
        names = [path.name for path in result.paths]
        self.assertEqual(names, ["导出-2026-10-08-1.zip", "导出-2026-10-08-2.zip", "导出-2026-10-08-3.zip"])
        self.assertIn("甲.txt", self._read(result.paths[0]))
        self.assertNotIn("乙.txt", self._read(result.paths[0]))

    def test_top_mode_honours_template_and_numbering(self) -> None:
        items = [
            self._text("甲", category=self._category("影视")),
            self._text("乙", category=self._category("音乐")),
        ]
        result = self.service.export_packages(
            items, self.out, mode="top", template="{number,3,2}-{category}", now=_MOMENT
        )
        self.assertEqual([path.name for path in result.paths], ["3-影视.zip", "5-音乐.zip"])

    def test_unknown_variables_are_reported_not_fatal(self) -> None:
        items = [self._text("甲", category=self._category("影视"))]
        result = self.service.export_packages(
            items, self.out, mode="top", template="{nope}-{category}", now=_MOMENT
        )
        self.assertTrue(result.warnings)
        self.assertEqual(result.paths[0].name, "{nope}-影视.zip")
        self.assertTrue(result.paths[0].is_file())

    def test_duplicate_package_names_are_numbered(self) -> None:
        items = [
            self._text("甲", category=self._category("影视")),
            self._text("乙", category=self._category("音乐")),
        ]
        result = self.service.export_packages(items, self.out, mode="top", template="同名", now=_MOMENT)
        self.assertEqual([path.name for path in result.paths], ["同名.zip", "同名_1.zip"])

    def test_packages_reject_empty_selection(self) -> None:
        with self.assertRaises(ValueError):
            self.service.export_packages([], self.out)

    # ------------------------------------------------------------------ 计划
    def test_category_path_walks_parents(self) -> None:
        node = self._category("影视", "电影", "2026")
        item = self._text("甲", category=node)
        self.assertEqual(self.service.category_path(item), "影视/电影/2026")
        # 导入时没指定分类的条目会被放进「未分类」分类，不是没有分类
        self.assertEqual(self.service.category_path(self._text("乙")), "未分类")
        # 真正没有分类的条目（直接建行、不经过导入）给空串
        self.assertEqual(self.service.category_path(self._broken()), "")


    def test_planned_items_carry_owner_and_category(self) -> None:
        item = self._text("甲", category=self._category("影视"))
        planned = self.service.planned_items([item])
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].key, str(item.id))
        self.assertEqual(planned[0].category, "影视")
        self.assertEqual(planned[0].top, "影视")
        self.assertEqual(planned[0].user, self.user.name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
