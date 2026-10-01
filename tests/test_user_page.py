"""用户卡片页：纯逻辑与卡片渲染测试。"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import IsolatedCase  # noqa: E402
from app.ui.pages.user_page import (  # noqa: E402
    AVATAR_SIZE,
    CARD_MAX_COLUMNS,
    CARD_MIN_WIDTH,
    CARD_WIDTH,
    card_badges,
    card_permissions,
    card_summary,
    grid_columns,
    is_current_user,
)


class UserCardLogicCase(unittest.TestCase):
    """不依赖 Qt 的纯函数。"""

    def test_grid_columns_clamps_width(self) -> None:
        self.assertEqual(grid_columns(0), 1)
        self.assertEqual(grid_columns(CARD_MIN_WIDTH), 1)
        self.assertEqual(grid_columns(CARD_MIN_WIDTH * 2 + 24), 2)
        self.assertEqual(grid_columns(100000), CARD_MAX_COLUMNS)

    def test_is_current_user(self) -> None:
        self.assertTrue(is_current_user(3, 3))
        self.assertFalse(is_current_user(3, 4))

    def test_card_badges(self) -> None:
        self.assertEqual(card_badges(is_current=False, is_default=False, protected=False), [])
        self.assertEqual(
            card_badges(is_current=True, is_default=True, protected=True),
            ["当前用户", "默认用户", "已设口令"],
        )
        self.assertEqual(
            card_badges(is_current=False, is_default=True, protected=False), ["默认用户"]
        )

    def test_card_summary_contains_counts_and_created(self) -> None:
        summary = card_summary(
            item_count=5, category_count=2, created_at=dt.datetime(2024, 5, 1, 8, 30)
        )
        self.assertIn("5 项数据", summary)
        self.assertIn("2 个分类", summary)
        self.assertIn("创建于 2024-05-01 08:30", summary)
        self.assertNotIn("创建于", card_summary(item_count=0, category_count=0, created_at=None))

    def test_card_permissions(self) -> None:
        # 默认用户：可管理他人，自己不可切换，默认用户不可删除。
        admin_other = card_permissions(
            is_current=False, is_default=False, protected=False, is_admin=True
        )
        self.assertTrue(admin_other["switch"])
        self.assertTrue(admin_other["rename"])
        self.assertTrue(admin_other["delete"])
        admin_self = card_permissions(
            is_current=True, is_default=True, protected=False, is_admin=True
        )
        self.assertFalse(admin_self["switch"])
        self.assertFalse(admin_self["delete"])
        # 普通用户：可以切换到别人（切换本身许可），但不能管理别人的账号。
        stranger = card_permissions(
            is_current=False, is_default=False, protected=False, is_admin=False
        )
        self.assertTrue(stranger["switch"])
        self.assertFalse(stranger["rename"])
        self.assertFalse(stranger["delete"])
        mine = card_permissions(
            is_current=True, is_default=False, protected=True, is_admin=False
        )
        self.assertTrue(mine["rename"])
        self.assertTrue(mine["clear_password"])
        self.assertTrue(mine["delete"])


class UserPageUiCase(IsolatedCase):
    """卡片网格渲染与高亮。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _make_page(self):
        from app.ui.pages.user_page import UserPage

        page = UserPage()
        self.addCleanup(self.drop_widget, page)
        return page

    def _create_user(self, name: str):
        from app.services import UserService

        user = UserService(self.session).create(name)
        self.session.commit()
        return user

    def test_cards_and_current_user_highlight(self) -> None:
        from qfluentwidgets import PushButton

        self._create_user("小明")
        self._create_user("小红")
        page = self._make_page()

        cards = page.cards()
        self.assertEqual(len(cards), 3)
        current = page.current_card()
        self.assertIsNotNone(current)
        highlighted = [card for card in cards if card.is_highlighted()]
        self.assertEqual([card.user_id for card in highlighted], [current.user_id])

        switch = [b for b in current.findChildren(PushButton) if b.text() == "当前用户"]
        self.assertEqual(len(switch), 1)
        self.assertFalse(switch[0].isEnabled())

        for card in cards:
            if card is current:
                continue
            buttons = [b for b in card.findChildren(PushButton) if b.text() == "切换为当前用户"]
            self.assertEqual(len(buttons), 1)
            self.assertTrue(buttons[0].isEnabled())

    def test_grid_holds_every_card(self) -> None:
        self._create_user("小明")
        page = self._make_page()
        page._layout_cards(force=True)
        self.assertEqual(page.grid.count(), len(page.cards()))
        self.assertGreaterEqual(page._columns, 1)
        self.assertLessEqual(page._columns, CARD_MAX_COLUMNS)

    def test_narrow_viewport_keeps_cards_fully_visible(self) -> None:
        """窄视口（700px）下固定宽度卡片必须完整落在滚动区域里。"""
        self._create_user("小明")
        page = self._make_page()
        page.resize(700, 600)
        page.show()
        self.app.processEvents()
        page._layout_cards(force=True)
        self.app.processEvents()

        viewport = page.scroll.viewport().width()
        self.assertGreater(viewport, 0)
        self.assertEqual(page._columns, 1)
        for card in page.cards():
            self.assertEqual(card.width(), CARD_WIDTH)
            self.assertLessEqual(card.geometry().right(), viewport)

    def test_cards_keep_fixed_width_when_window_grows(self) -> None:
        """卡片宽度固定：窗口加宽只增加列数，卡片本身不被拉伸。"""
        self._create_user("小明")
        self._create_user("小红")
        page = self._make_page()
        page.resize(700, 600)
        page.show()
        self.app.processEvents()
        page._layout_cards(force=True)
        self.app.processEvents()

        widths = [card.width() for card in page.cards()]
        self.assertEqual(widths, [CARD_WIDTH] * len(widths))
        self.assertEqual(page._columns, 1)
        # 卡片列不伸缩，其后追加占位伸缩列 → 卡片靠左。
        self.assertEqual(page.grid.columnStretch(0), 0)
        self.assertEqual(page.grid.columnStretch(page._columns), 1)

        page.resize(1600, 600)
        self.app.processEvents()
        page._layout_cards(force=True)
        self.app.processEvents()
        self.assertGreater(page._columns, 1)
        self.assertEqual([card.width() for card in page.cards()], widths)

    def test_card_is_self_contained_unit(self) -> None:
        """卡片即对象：头像、摘要与针对该用户的操作都在卡片内且不被裁剪。"""
        from PyQt6.QtWidgets import QLabel
        from qfluentwidgets import PushButton

        self._create_user("小明")
        page = self._make_page()
        page.resize(700, 600)
        page.show()
        self.app.processEvents()

        cards = page.cards()
        self.assertEqual(len(cards), 2)

        current = page.current_card()
        member = next(card for card in cards if card is not current)

        for card in cards:
            avatars = [
                label
                for label in card.findChildren(QLabel)
                if label.width() == AVATAR_SIZE and label.height() == AVATAR_SIZE
            ]
            self.assertEqual(len(avatars), 1)
            self.assertEqual(avatars[0].text(), card._info.name[:1])
            buttons = card.findChildren(PushButton)
            self.assertGreaterEqual(len(buttons), 1)
            for button in buttons:
                self.assertTrue(button.isVisibleTo(card))
                self.assertLessEqual(button.geometry().right(), card.width())
                self.assertLessEqual(button.geometry().bottom(), card.height())

        self.assertEqual(
            {button.text() for button in current.findChildren(PushButton)},
            {"当前用户", "重命名", "口令"},
        )
        self.assertEqual(
            {button.text() for button in member.findChildren(PushButton)},
            {"切换为当前用户", "重命名", "口令", "删除"},
        )
        # 卡片高度一致。
        self.assertEqual(len({card.height() for card in cards}), 1)

    def test_new_user_button_only_for_admin(self) -> None:
        from app.services import UserService

        page = self._make_page()
        self.assertFalse(page.create_button.isHidden())

        service = UserService(self.session)
        normal = service.create("小明")
        self.session.commit()
        service.set_current(normal)
        self.session.commit()
        page.refresh()
        self.assertTrue(page.create_button.isHidden())
        self.assertTrue(page.current_card().is_highlighted())


if __name__ == "__main__":
    unittest.main()
