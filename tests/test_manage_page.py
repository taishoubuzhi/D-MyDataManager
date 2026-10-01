"""数据管理页与筛选/翻页控件：纯函数与离屏 UI 冒烟测试。"""

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

from app.ui.widgets.pager import (  # noqa: E402
    DEFAULT_PAGE_SIZE,
    PAGE_SIZES,
    page_size_options,
    pages_of,
    selection_hint,
    selection_summary,
)
from tests.harness import IsolatedCase  # noqa: E402


class PagesOfTest(unittest.TestCase):
    def test_rounds_up(self) -> None:
        self.assertEqual(pages_of(101, 100), 2)
        self.assertEqual(pages_of(100, 100), 1)
        self.assertEqual(pages_of(1, 100), 1)

    def test_empty_is_one_page(self) -> None:
        self.assertEqual(pages_of(0, 100), 1)

    def test_guards_bad_size(self) -> None:
        self.assertEqual(pages_of(10, 0), 10)
        self.assertEqual(pages_of(-5, 100), 1)


class PageSizeOptionsTest(unittest.TestCase):
    def test_default_sizes(self) -> None:
        options = page_size_options()
        self.assertEqual([size for size, _label in options], list(PAGE_SIZES))
        self.assertEqual(options[0][1], "每页 50 条")

    def test_labels(self) -> None:
        self.assertEqual(page_size_options([10]), [(10, "每页 10 条")])


class SelectionTextTest(unittest.TestCase):
    def test_summary(self) -> None:
        self.assertEqual(selection_summary(128, 3), "共 128 项 · 已选 3 项")
        self.assertEqual(selection_summary(-1, -2), "共 0 项 · 已选 0 项")

    def test_hint_only_when_cross_page(self) -> None:
        self.assertEqual(selection_hint(3, 3), "")
        self.assertEqual(selection_hint(4, 3), "已跨页选择 4 项")


class ManagePageTest(IsolatedCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _page(self, count: int = 3):
        from app.ui.pages.manage_page import ManagePage

        service = self.importer()
        for index in range(count):
            service.import_file(self.corpus.by_name("photo_gradient.png"))
            service.import_file(self.corpus.by_name("clip.mp4"))
        self.session.commit()
        page = ManagePage()
        self.addCleanup(self._release, page)
        page.resize(800, 600)
        page.show()
        page.refresh()
        self.app.processEvents()
        return page

    def _release(self, page) -> None:
        """立即销毁页面，避免离屏下延迟删除堆积导致后续用例崩溃。"""
        from PyQt6.QtCore import QCoreApplication, QEvent

        page.close()
        page.setParent(None)
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        page.session.close()

    def test_pager_state_and_summary(self) -> None:
        page = self._page()
        self.assertEqual(page.pager.pages, page.pager_page_count())
        self.assertEqual(page.count_label.text(), selection_summary(page._total, 0))
        self.assertEqual(page.pager.summary_label.text(), selection_summary(page._total, 0))
        self.assertEqual(page.pager.page_size, DEFAULT_PAGE_SIZE)

    def test_pager_visible_and_not_clipped_at_800px(self) -> None:
        page = self._page()
        page.pager._fit_height()
        self.app.processEvents()
        self.assertTrue(page.pager.isVisibleTo(page))
        self.assertGreater(page.pager.height(), 0)
        pager = page.pager
        for child in (pager.first_button, pager.prev_button, pager.page_box, pager.next_button,
                      pager.last_button, pager.size_box, pager.summary_label):
            self.assertTrue(child.isVisibleTo(pager), child)
            self.assertLessEqual(child.geometry().bottom(), pager.height(), child)

    def test_filter_sections_unchanged(self) -> None:
        page = self._page()
        sections = page.filter_panel.sections()
        self.assertEqual(len(sections), 4)
        for section in sections:
            self.assertTrue(section.all_box.isTristate())
            self.assertTrue(section.all_box.toolTip())
            self.assertTrue(section.search.placeholderText())
            self.assertTrue(section.scroll.isVisibleTo(section))
            section.toggle_button.click()
            self.assertFalse(section.scroll.isVisibleTo(section))
            section.toggle_button.click()
            self.assertTrue(section.scroll.isVisibleTo(section))

    def test_pager_page_size_change(self) -> None:
        page = self._page()
        total = page._total
        page._on_page_size_changed(1)
        self.assertEqual(page.pager.pages, total)
        self.assertEqual(page.pager.offset, 0)

    def test_selection_summary_tracks_selection(self) -> None:
        page = self._page()
        page._selected = {page._items[0].id}
        page._update_count_label()
        self.assertEqual(page.count_label.text(), selection_summary(page._total, 1))
        self.assertEqual(page.pager.summary_label.text(), selection_summary(page._total, 1))


class ItemEditDialogTest(IsolatedCase):
    """编辑数据项对话框：分类下拉只列出真实的分类，未分类数据落到真实的「未分类」。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, item):
        from PyQt6.QtWidgets import QWidget

        from app.services.taxonomy_service import TaxonomyService
        from app.ui.dialogs import ItemEditDialog

        user_id = self.current_user().id
        nodes = TaxonomyService(self.session).tree(user_id=user_id)
        parent = QWidget()
        self.addCleanup(self.drop_widget, parent)
        dialog = ItemEditDialog(
            item,
            categories=[(node.category.id, "　" * node.depth + node.category.name) for node in nodes],
            known_tags=[],
            parent=parent,
        )
        self.addCleanup(self.drop_widget, dialog)
        return dialog

    def _item(self):
        item = self.importer().import_file(self.corpus.by_name("photo_gradient.png"))
        self.session.commit()
        return item

    def test_uncategorized_item_selects_real_category(self) -> None:
        from app.db.seed import UNCATEGORIZED_NAME
        from app.repositories.categories import CategoryRepository

        item = self._item()
        uncategorized = CategoryRepository(self.session).by_name(
            UNCATEGORIZED_NAME, None, self.current_user().id
        )
        self.assertIsNotNone(uncategorized)
        item.category_id = None
        self.session.flush()

        dialog = self._dialog(item)
        labels = [
            dialog.category_box.itemText(index).lstrip("　")
            for index in range(dialog.category_box.count())
        ]
        self.assertEqual(labels.count(UNCATEGORIZED_NAME), 1)
        self.assertEqual(dialog.category_box.currentData(), uncategorized.id)

    def test_existing_category_is_preselected(self) -> None:
        from app.services.taxonomy_service import TaxonomyService

        item = self._item()
        category = TaxonomyService(self.session).create_category("专题", user_id=self.current_user().id)
        self.assertIsNotNone(category)
        item.category_id = category.id
        self.session.flush()

        dialog = self._dialog(item)
        self.assertEqual(dialog.category_box.currentData(), category.id)


class CategoryTreeMenuTest(unittest.TestCase):
    """分类树标签与右键菜单：「未分类」是固定分类，不提供任何修改入口。"""

    def _node(self, name: str, *, total: int = 0):
        from app.db.models import Category
        from app.services import CategoryNode

        return CategoryNode(Category(name=name, parent_id=None), 0, 0, total)

    def test_uncategorized_label_is_marked(self) -> None:
        from app.db.seed import UNCATEGORIZED_NAME
        from app.ui.widgets.category_tree import FIXED_SUFFIX, category_label

        node = self._node(UNCATEGORIZED_NAME, total=3)
        self.assertIn(FIXED_SUFFIX, category_label(node, fixed=True))
        self.assertEqual(category_label(node, fixed=False), f"{UNCATEGORIZED_NAME} (3)")

    def test_menu_entries_hide_actions_for_fixed(self) -> None:
        from app.ui.widgets.category_tree import menu_entries

        self.assertEqual(menu_entries(fixed=True), [])
        self.assertEqual([action for action, _ in menu_entries(all_data=True)], ["add"])
        self.assertEqual(
            [action for action, _ in menu_entries()], ["add", "rename", "delete"]
        )


if __name__ == "__main__":
    unittest.main()
