"""首页仪表盘：纯函数与离屏 UI 冒烟测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.ui.pages.home_page import KPI_TITLES, format_summary, type_distribution  # noqa: E402
from tests.harness import IsolatedCase  # noqa: E402


class TypeDistributionTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(type_distribution({}, 0), [])

    def test_filters_zero_and_sorts_desc(self) -> None:
        rows = type_distribution({"IMAGE": 3, "VIDEO": 0, "TEXT": 1}, 4)
        self.assertEqual([(key, count) for key, count, _ratio in rows], [("IMAGE", 3), ("TEXT", 1)])
        self.assertAlmostEqual(rows[0][2], 0.75)
        self.assertAlmostEqual(rows[1][2], 0.25)

    def test_total_defaults_to_sum(self) -> None:
        rows = type_distribution({"IMAGE": 2, "TEXT": 2})
        self.assertAlmostEqual(rows[0][2], 0.5)
        self.assertAlmostEqual(rows[1][2], 0.5)

    def test_ratio_never_exceeds_one(self) -> None:
        rows = type_distribution({"IMAGE": 10}, 4)
        self.assertLessEqual(rows[0][2], 1.0)

    def test_accepts_pairs(self) -> None:
        rows = type_distribution([("IMAGE", 1), ("VIDEO", 2)])
        self.assertEqual([key for key, _count, _ratio in rows], ["VIDEO", "IMAGE"])


class FormatSummaryTest(unittest.TestCase):
    STATS = {
        "total": 5,
        "total_size": 2048,
        "today": 1,
        "week": 3,
        "categories": 2,
        "tags": 4,
        "trashed": 1,
        "duplicate_groups": 1,
        "storage": {"disk_size": 4096},
    }

    def test_titles_follow_kpi_order(self) -> None:
        entries = format_summary(self.STATS, users=2, archives=3)
        self.assertEqual(tuple(title for title, _value, _sub in entries), KPI_TITLES)
        self.assertEqual(len(entries), 7)

    def test_values(self) -> None:
        entries = dict((title, (value, sub)) for title, value, sub in format_summary(self.STATS, users=2, archives=3))
        self.assertEqual(entries["数据总量"][0], "5")
        self.assertEqual(entries["数据总量"][1], "今日 +1")
        self.assertIn("2.0 KB", entries["占用空间"][0])
        self.assertEqual(entries["用户数"][0], "2")
        self.assertEqual(entries["分类"][0], "2")
        self.assertEqual(entries["标签数"][0], "4")
        self.assertEqual(entries["存档数"][0], "3")

    def test_missing_keys_are_safe(self) -> None:
        entries = format_summary({"total": 0})
        self.assertEqual(entries[0][1], "0")
        self.assertEqual(entries[0][2], "今日 +0")


class HomePageTest(IsolatedCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from app.ui.pages.home_page import HomePage, _Row

        self.session.commit()
        page = HomePage()
        self.addCleanup(self._release, page)
        return page, _Row

    def _release(self, page) -> None:
        from PyQt6.QtCore import QCoreApplication, QEvent

        page.close()
        page.setParent(None)
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        page.session.close()

    def test_cards_and_type_bars(self) -> None:
        from app.services import overview

        service = self.importer()
        service.import_file(self.corpus.by_name("photo_gradient.png"), tags=["重要"])
        page, _Row = self._page()
        page.refresh()

        stats = overview(self.session, user_id=self.current_user().id)
        self.assertEqual(len(page.kpi_cards), 7)
        self.assertEqual(page._total_card._value.text(), "1")
        self.assertEqual(page._category_card._value.text(), str(stats["categories"]))
        self.assertEqual(page._tag_card._value.text(), str(stats["tags"]))
        self.assertEqual(len(page._type_bars), 1)
        self.assertEqual(len(page.findChildren(_Row)), 1)

    def test_cards_use_adaptive_flow(self) -> None:
        from qfluentwidgets import AdaptiveFlowLayout

        page, _Row = self._page()
        self.assertIsInstance(page._total_card.parent().layout(), AdaptiveFlowLayout)

    def test_narrow_width_does_not_break(self) -> None:
        service = self.importer()
        service.import_file(self.corpus.by_name("photo_gradient.png"))
        service.import_file(self.corpus.by_name("clip.mp4"))
        page, _Row = self._page()
        page.resize(420, 640)
        page.show()
        self.app.processEvents()
        self.assertEqual(len(page.kpi_cards), 7)
        self.assertEqual(len(page._type_bars), 2)

    def test_recent_row_emits_focus(self) -> None:
        from app.core.signals import signalBus

        service = self.importer()
        item = service.import_file(self.corpus.by_name("photo_gradient.png"))
        page, _Row = self._page()
        page.refresh()
        seen: list[int] = []
        signalBus.focusItem.connect(seen.append)
        self.addCleanup(signalBus.focusItem.disconnect, seen.append)
        page.findChildren(_Row)[0].clicked.emit()
        self.assertEqual(seen, [int(item.id)])


if __name__ == "__main__":
    unittest.main()
