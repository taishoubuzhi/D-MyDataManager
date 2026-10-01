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


class SelectionRulesTest(unittest.TestCase):
    """多选的纯函数：Shift 区间与三态全选框。"""

    def test_range_ids_covers_both_ends(self) -> None:
        from app.ui.pages.manage_page import range_ids

        self.assertEqual(range_ids([1, 2, 3, 4], 2, 4), {2, 3, 4})
        self.assertEqual(range_ids([1, 2, 3, 4], 4, 2), {2, 3, 4})
        self.assertEqual(range_ids([1, 2, 3, 4], 3, 3), {3})

    def test_range_ids_falls_back_without_anchor(self) -> None:
        from app.ui.pages.manage_page import range_ids

        self.assertEqual(range_ids([1, 2, 3], 9, 2), {2})

    def test_tri_state(self) -> None:
        from PyQt6.QtCore import Qt

        from app.ui.pages.manage_page import tri_state

        self.assertEqual(tri_state(0, 5), Qt.CheckState.Unchecked)
        self.assertEqual(tri_state(2, 5), Qt.CheckState.PartiallyChecked)
        self.assertEqual(tri_state(5, 5), Qt.CheckState.Checked)
        self.assertEqual(tri_state(0, 0), Qt.CheckState.Unchecked)


class ReleaseWidgetTest(unittest.TestCase):
    """摘控件的公用函数：必须先隐藏，否则会残留可见的顶层小窗口。"""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_hides_before_detaching(self) -> None:
        from PyQt6.QtWidgets import QWidget

        from app.ui.common import release_widget

        host = QWidget()
        self.addCleanup(host.deleteLater)
        child = QWidget(host)
        host.show()
        child.show()
        self.assertTrue(child.isVisible())
        release_widget(child)
        self.assertFalse(child.isVisible())
        self.assertIsNone(child.parent())


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

    def test_rerender_leaves_no_stray_top_level_widgets(self) -> None:
        """回归：重渲染摘掉的旧条目不能残留成可见的顶层小窗口（切换用户时会乱闪）。"""
        from app.ui.widgets.item_card import ItemCard, ItemListRow

        page = self._page()
        page.refresh()
        page._set_mode("card")
        page._set_mode("list")
        self.app.processEvents()
        strays = [
            widget
            for widget in self.app.topLevelWidgets()
            if isinstance(widget, (ItemCard, ItemListRow)) and widget.isVisible()
        ]
        self.assertEqual(strays, [])

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

    def _rows(self, page) -> list:
        """当前页的行控件（列表视图按顺序排列，末尾是伸缩哨兵）。"""
        rows = []
        for index in range(page.list_layout.count()):
            widget = page.list_layout.itemAt(index).widget()
            if widget is not None and hasattr(widget, "item"):
                rows.append(widget)
        return rows

    def test_single_click_selects_without_opening(self) -> None:
        from PyQt6.QtCore import Qt

        page = self._page()
        opened: list = []
        page._on_open = opened.append
        first = page._items[0]
        page._on_item_activated(first, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(page._selected, {first.id})
        self.assertEqual(opened, [])

    def test_double_click_opens(self) -> None:
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        page = self._page()
        row = self._rows(page)[0]
        opened: list = []
        row.opened.connect(opened.append)
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        row.mouseDoubleClickEvent(event)
        self.assertEqual(opened, [row.item])

    def test_ctrl_and_shift_click_multi_select(self) -> None:
        from PyQt6.QtCore import Qt

        page = self._page()
        ids = [item.id for item in page._items]
        self.assertGreaterEqual(len(ids), 4)
        page._on_item_activated(page._items[0], Qt.KeyboardModifier.NoModifier)
        page._on_item_activated(page._items[2], Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page._selected, {ids[0], ids[2]})
        # Ctrl 再点一次取消该项。
        page._on_item_activated(page._items[2], Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page._selected, {ids[0]})
        # Shift：从锚点选到点击项（含两端）。
        page._on_item_activated(page._items[0], Qt.KeyboardModifier.NoModifier)
        page._on_item_activated(page._items[3], Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(page._selected, set(ids[0:4]))
        # Ctrl + Shift：把区间并入已有选择。
        page._on_item_activated(page._items[0], Qt.KeyboardModifier.NoModifier)
        page._on_item_activated(
            page._items[2], Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        )
        self.assertEqual(page._selected, {ids[0], ids[1], ids[2]})

    def test_checkbox_and_select_all_stay_in_sync(self) -> None:
        from PyQt6.QtCore import Qt

        page = self._page()
        rows = self._rows(page)
        rows[0].check_box.setChecked(True)
        self.assertEqual(page._selected, {rows[0].item.id})
        self.assertEqual(page.select_all_box.checkState(), Qt.CheckState.PartiallyChecked)
        page.select_all_box.setChecked(True)
        self.assertEqual(page._selected, {item.id for item in page._items})
        self.assertEqual(page.select_all_box.checkState(), Qt.CheckState.Checked)
        page.select_all_box.setChecked(False)
        self.assertEqual(page._selected, set())
        self.assertEqual(page.select_all_box.checkState(), Qt.CheckState.Unchecked)

    def test_menu_offers_open_open_with_and_move(self) -> None:
        from app.ui.pages.manage_page import menu_items, open_with_items

        page = self._page()
        single = dict(menu_items(1))
        self.assertEqual(single["open"], "直接打开")
        self.assertEqual(single["open_with"], "打开方式")
        self.assertEqual(single["move"], "移动到分类…")
        many = dict(menu_items(3))
        self.assertEqual(many["move"], "移动到分类…（3 项）")
        self.assertIn("3", many["delete"])
        self.assertIn("3", many["purge"])

        entries = open_with_items(".txt")
        self.assertEqual(entries[0][0], "system")
        self.assertEqual(entries[-1][0], "ask")
        self.assertIsNotNone(page._build_menu(page._items[0]))

    def test_right_click_keeps_selection_in_list_mode(self) -> None:
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        page = self._page()
        rows = self._rows(page)
        rows[0].check_box.setChecked(True)
        activated: list = []
        for row in rows:
            row.activated.connect(activated.append)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(5, 5),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        rows[1].mousePressEvent(event)
        self.assertEqual(activated, [])
        self.assertEqual(page._selected, {rows[0].item.id})

    def test_right_release_keeps_selection_in_card_mode(self) -> None:
        # CardWidget 的 mouseReleaseEvent 无条件发 clicked，页面用按键过滤掉右键。
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        page = self._page()
        page._set_mode("card")
        card = next(
            page.card_layout.itemAt(index).widget()
            for index in range(page.card_layout.count())
            if getattr(page.card_layout.itemAt(index).widget(), "item", None) is not None
        )
        activated: list = []
        card.activated.connect(activated.append)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            event = QMouseEvent(
                kind,
                QPointF(5, 5),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
            )
            if kind == QEvent.Type.MouseButtonPress:
                card.mousePressEvent(event)
            else:
                card.mouseReleaseEvent(event)
        self.assertEqual(activated, [])

    def test_move_selected_moves_single_and_batch(self) -> None:
        from app.services import TaxonomyService

        page = self._page()
        target = TaxonomyService(self.session).create_category(
            "移动目标", user_id=self.current_user().id
        )
        self.session.commit()
        page._selected = {page._items[0].id}
        self.assertEqual(page.move_selected(target.id), 1)
        self.assertEqual(page.item_repo.get(page._items[0].id).category_id, target.id)

        page._selected = {item.id for item in page._items[1:3]}
        self.assertEqual(page.move_selected(target.id), 2)
        for item in page._items[1:3]:
            self.assertEqual(page.item_repo.get(item.id).category_id, target.id)

    def test_move_selected_to_uncategorized(self) -> None:
        from app.db.seed import UNCATEGORIZED_NAME

        page = self._page()
        page._selected = {page._items[0].id}
        self.assertEqual(page.move_selected(None), 1)
        item = page.item_repo.get(page._items[0].id)
        self.assertIsNotNone(item.category_id)
        self.assertIn(UNCATEGORIZED_NAME, Path(item.file_path).parts)


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
