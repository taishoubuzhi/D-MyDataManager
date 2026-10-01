"""存档页：筛选/分页纯逻辑与表格行映射测试。"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import IsolatedCase  # noqa: E402
from app.ui.pages.archive_page import (  # noqa: E402
    archive_name_text,
    archive_row_texts,
    filter_archives,
    match_filters,
    page_bounds,
    tab_index,
)


class ArchiveFilterLogicCase(unittest.TestCase):
    """不依赖 Qt 的纯函数。"""

    def test_match_filters_ignores_empty_and_matches_substring(self) -> None:
        texts = {"name": "存档 2026", "pinned": "已标记"}
        self.assertTrue(match_filters(texts, {}))
        self.assertTrue(match_filters(texts, {"name": ""}))
        self.assertTrue(match_filters(texts, {"name": "存档"}))
        self.assertTrue(match_filters(texts, {"pinned": "已标记"}))
        self.assertFalse(match_filters(texts, {"pinned": "未标记"}))
        self.assertFalse(match_filters(texts, {"name": "不存在"}))

    def test_match_filters_is_case_insensitive(self) -> None:
        self.assertTrue(match_filters({"name": "Backup"}, {"name": "backup"}))

    def test_archive_row_texts(self) -> None:
        texts = archive_row_texts(
            name="甲",
            note="备注",
            created_at=dt.datetime(2026, 10, 1, 9, 0, 0),
            item_count=3,
            total_size=2048,
            pinned=True,
        )
        self.assertEqual(texts["name"], "甲")
        self.assertEqual(texts["note"], "备注")
        self.assertEqual(texts["created"], "2026-10-01 09:00:00")
        self.assertEqual(texts["count"], "3")
        self.assertEqual(texts["size"], "2.0 KB")
        self.assertEqual(texts["pinned"], "已标记")
        self.assertEqual(
            archive_row_texts(
                name="乙", note="", created_at=None, item_count=0,
                total_size=0, pinned=False,
            )["pinned"],
            "未标记",
        )

    def test_filter_archives_keeps_order(self) -> None:
        rows = [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}]
        self.assertEqual(filter_archives(rows, {"name": "乙"}), [1])
        self.assertEqual(filter_archives(rows, {}), [0, 1, 2])

    def test_page_bounds_clamps(self) -> None:
        self.assertEqual(page_bounds(0, 0, 50), (0, 0))
        self.assertEqual(page_bounds(120, 0, 50), (0, 50))
        self.assertEqual(page_bounds(120, 2, 50), (100, 120))
        self.assertEqual(page_bounds(120, 99, 50), (100, 120))
        self.assertEqual(page_bounds(120, -5, 50), (0, 50))

    def test_archive_name_text_marks_pinned(self) -> None:
        self.assertEqual(
            archive_name_text(types.SimpleNamespace(pinned=True, name="甲")), "【已标记】甲"
        )
        self.assertEqual(
            archive_name_text(types.SimpleNamespace(pinned=False, name="甲")), "甲"
        )

    def test_tab_index_falls_back_to_first(self) -> None:
        self.assertEqual(tab_index("archives"), 0)
        self.assertEqual(tab_index("entries"), 1)
        self.assertEqual(tab_index("unknown"), 0)


class ArchivePageUiCase(IsolatedCase):
    """存档表格的行索引 -> 数据对象映射、筛选与分页。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _seed_archives(self, *names: str) -> None:
        from app.services import ArchiveService

        service = ArchiveService(self.session)
        for name in names:
            service.create(name=name)
        self.session.commit()

    def _make_page(self):
        from app.ui.pages.archive_page import ArchivePage

        page = ArchivePage()
        self.addCleanup(self.drop_widget, page)
        return page

    def test_table_headers_and_row_mapping(self) -> None:
        self._seed_archives("甲", "乙", "丙")
        page = self._make_page()

        self.assertEqual(len(page._archives), 3)
        self.assertEqual(page.archive_list.rowCount(), 3)
        headers = [
            page.archive_list.horizontalHeaderItem(i).text()
            for i in range(page.archive_list.columnCount())
        ]
        self.assertEqual(headers, ["名称", "备注", "创建时间", "条目数", "大小", "标记"])
        # 条目明细表保留「所属用户」列（第 6 列）。
        self.assertEqual(page.table.horizontalHeaderItem(5).text(), "所属用户")

        page.archive_list.setCurrentCell(1, 0)
        self.assertIsNotNone(page._current_archive())
        self.assertEqual(page._current_archive().id, page._visible[1].id)
        self.assertEqual(page.archive_list.item(1, 0).text(), page._visible[1].name)

    def test_filter_keeps_row_mapping(self) -> None:
        self._seed_archives("甲", "乙", "丙")
        page = self._make_page()

        page.filter_bar._editors["name"][1].setText("乙")
        self.assertEqual(page.archive_list.rowCount(), 1)
        self.assertEqual(page._current_archive().name, "乙")
        self.assertEqual(page.archive_list.item(0, 0).text(), "乙")

        page.filter_bar._editors["name"][1].setText("不存在")
        self.assertEqual(page.archive_list.rowCount(), 0)
        self.assertIsNone(page._current_archive())
        self.assertFalse(page.pin_button.isEnabled())

    def test_pinned_filter_follows_toggle(self) -> None:
        self._seed_archives("甲", "乙")
        page = self._make_page()
        page.archive_list.setCurrentCell(0, 0)
        page._on_toggle_pin()
        self.assertTrue(page._current_archive().pinned)

        pinned_box = page.filter_bar._editors["pinned"][1]
        pinned_box.setCurrentIndex(1)  # 已标记
        self.assertEqual(page.archive_list.rowCount(), 1)
        self.assertTrue(page._current_archive().pinned)

        pinned_box.setCurrentIndex(0)  # 全部
        self.assertEqual(page.archive_list.rowCount(), 2)

    def test_pagination_slices_visible_rows(self) -> None:
        self._seed_archives("甲", "乙", "丙")
        page = self._make_page()

        page.pager.set_state(len(page._visible), 0, 2)
        page._render_archive_rows()
        self.assertEqual(page.archive_list.rowCount(), 2)
        self.assertEqual([a.id for a in page._page_items], [a.id for a in page._visible[:2]])
        self.assertEqual(page._current_archive().id, page._visible[0].id)

        page.pager.set_state(len(page._visible), 1, 2)
        page._render_archive_rows()
        self.assertEqual(page.archive_list.rowCount(), 1)
        self.assertEqual(page._page_items, page._visible[2:])
        self.assertEqual(page._current_archive().id, page._visible[2].id)

    def test_filter_resets_page(self) -> None:
        self._seed_archives("甲", "乙", "丙")
        page = self._make_page()
        page.pager.set_state(len(page._visible), 1, 2)
        page._render_archive_rows()
        self.assertEqual(page.pager.page, 1)

        page.filter_bar._editors["name"][1].setText("甲")  # 触发重新筛选
        self.assertEqual(page.pager.page, 0)
        self.assertEqual(page.archive_list.rowCount(), 1)

    def test_tabs_auto_switch_and_manual_back(self) -> None:
        self._seed_archives("甲", "乙")
        page = self._make_page()

        self.assertEqual(page.tab_keys(), ["archives", "entries"])
        self.assertEqual(page.stack.count(), 2)
        self.assertEqual(page.current_tab(), "archives")
        self.assertEqual(page.stack.currentIndex(), 0)

        page.archive_list.setCurrentCell(1, 0)  # 用户点选存档
        self.assertEqual(page.current_tab(), "entries")
        self.assertEqual(page.stack.currentIndex(), 1)
        self.assertEqual(page._current_archive().id, page._visible[1].id)

        page.switch_tab("archives")  # 手动切回
        self.assertEqual(page.current_tab(), "archives")
        self.assertEqual(page.stack.currentIndex(), 0)

        page._reload_archives()  # 程序化刷新不应自动切换
        self.assertEqual(page.current_tab(), "archives")
        self.assertEqual(page.stack.currentIndex(), 0)

        page.archive_list.setCurrentCell(0, 0)  # 再次点选存档，仍自动切换
        self.assertEqual(page.current_tab(), "entries")
        self.assertEqual(page._current_archive().id, page._page_items[0].id)


if __name__ == "__main__":
    unittest.main()
