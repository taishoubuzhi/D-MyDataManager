"""标签页：默认全局标签与表格逐列筛选的单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.harness import IsolatedCase  # noqa: E402
from app.repositories import TagRepository  # noqa: E402
from app.services import TaxonomyService  # noqa: E402
from app.ui.widgets.data_table import match_filters  # noqa: E402


class MatchFiltersCase(unittest.TestCase):
    def test_empty_filters_match_everything(self):
        self.assertTrue(match_filters({"name": "a"}, {}))

    def test_text_filter_is_case_insensitive_substring(self):
        values = {"name": "Photos 2024", "usage": "12"}
        self.assertTrue(match_filters(values, {"name": "photos"}))
        self.assertTrue(match_filters(values, {"usage": "1"}))
        self.assertFalse(match_filters(values, {"name": "视频"}))

    def test_choice_filter_matches_exact_value_text(self):
        self.assertTrue(match_filters({"scope": "全局"}, {"scope": "全局"}))
        self.assertFalse(match_filters({"scope": "个人"}, {"scope": "全局"}))

    def test_blank_filter_is_ignored(self):
        self.assertTrue(match_filters({"name": "a"}, {"name": "   "}))


class TagPageCase(IsolatedCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        super().setUp()
        from app.ui.pages.tag_page import TagPage

        self.page = TagPage()

    def tearDown(self) -> None:
        # 页面持有自己的会话，必须先关闭，否则下一个用例重建数据库时文件被锁住。
        self.drop_widget(self.page)
        super().tearDown()

    def _visible(self) -> list[int]:
        return [
            row
            for row in range(self.page.table.rowCount())
            if not self.page.table.isRowHidden(row)
        ]

    def test_default_tags_are_global_and_listed(self):
        repo = TagRepository(self.page.session)
        for name in ("重要", "待整理", "收藏"):
            tag = repo.by_name(name)
            self.assertIsNotNone(tag)
            self.assertTrue(tag.is_global)
            self.assertIsNone(tag.user_id)
        names = [tag.name for tag in self.page._tags]
        self.assertTrue({"重要", "待整理", "收藏"}.issubset(set(names)))
        self.assertEqual(len(self._visible()), self.page.table.rowCount())

    def test_name_filter_hides_rows_and_updates_caption(self):
        total = self.page.table.rowCount()
        self.page.filter_bar.set_filter("name", "重要")
        visible = self._visible()
        self.assertEqual(len(visible), 1)
        self.assertEqual(self.page.table.item(visible[0], 0).text(), "重要")
        self.assertEqual(self.page.filter_caption.text(), f"显示 1 / {total} 个标签")

    def test_scope_filter_keeps_only_global_tags(self):
        self.page.filter_bar.set_filter("scope", "全局")
        visible = self._visible()
        self.assertGreaterEqual(len(visible), 3)
        for row in visible:
            self.assertEqual(self.page.table.item(row, 1).text(), "全局")

    def test_reset_filters_shows_every_row_again(self):
        total = self.page.table.rowCount()
        self.page.filter_bar.set_filter("name", "不存在的标签")
        self.assertEqual(self._visible(), [])
        self.page._on_reset_filters()
        self.assertEqual(len(self._visible()), total)
        self.assertEqual(self.page.filter_caption.text(), f"显示 {total} / {total} 个标签")

    def test_selection_is_ignored_when_its_row_is_hidden(self):
        self.page.filter_bar.set_filter("name", "重要")
        visible = self._visible()
        self.page.table.setCurrentCell(visible[0], 0)
        self.assertIsNotNone(self.page._selected_tag())
        self.page.filter_bar.set_filter("name", "不存在的标签")
        self.assertIsNone(self.page._selected_tag())

    def test_created_personal_tag_shows_in_personal_scope(self):
        service = TaxonomyService(self.page.session)
        service.create_tag("临时个人标签", user_id=self.page._user_id)
        self.page.session.commit()
        self.page.refresh()
        self.page.filter_bar.set_filter("name", "临时个人标签")
        visible = self._visible()
        self.assertEqual(len(visible), 1)
        self.assertEqual(self.page.table.item(visible[0], 1).text(), "个人（我）")


__all__ = ["MatchFiltersCase", "TagPageCase"]
