"""界面样式统一：页面骨架尺寸、面板边距与主题强调色。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402
from qfluentwidgets import CardWidget, FluentIcon, PushButton, themeColor  # noqa: E402

from app.ui.common import (  # noqa: E402
    DETAIL_MARGINS,
    PAGE_MARGINS,
    PAGE_SPACING,
    PANEL_MARGINS,
    accent_color,
    accent_name,
    page_header,
    page_layout,
    panel_card,
)


class StyleHelpersTest(unittest.TestCase):
    """统一样式的常量与构件（不依赖主窗口）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_page_layout_uses_unified_margins(self) -> None:
        host = QWidget()
        layout = page_layout(host)
        box = layout.contentsMargins()
        self.assertEqual((box.left(), box.top(), box.right(), box.bottom()), PAGE_MARGINS)
        self.assertEqual(layout.spacing(), PAGE_SPACING)

    def test_page_header_puts_title_and_caption(self) -> None:
        host = QWidget()
        root = page_layout(host)
        header = page_header(root, host, "标题", "说明")
        self.assertEqual(root.count(), 2)
        self.assertEqual(header.count(), 2)

    def test_panel_card_margins(self) -> None:
        card, layout = panel_card()
        self.assertIsInstance(card, CardWidget)
        box = layout.contentsMargins()
        self.assertEqual((box.left(), box.top(), box.right(), box.bottom()), PANEL_MARGINS)

        _, detail_layout = panel_card(margins=DETAIL_MARGINS, spacing=10)
        box = detail_layout.contentsMargins()
        self.assertEqual((box.left(), box.top(), box.right(), box.bottom()), DETAIL_MARGINS)
        self.assertEqual(detail_layout.spacing(), 10)

    def test_accent_follows_theme_color(self) -> None:
        self.assertEqual(accent_name(), themeColor().name())
        self.assertEqual(accent_color(), themeColor())

    def test_settings_action_card_uses_fluent_button(self) -> None:
        from app.ui.pages.settings_page import ActionCard

        card = ActionCard("打开目录", FluentIcon.FOLDER, "日志目录", "说明")
        self.assertIsInstance(card.button, PushButton)
        self.assertEqual(type(card.button).__name__, "PushButton")
        self.assertEqual(card.button.text(), "打开目录")


    def test_page_background_paints_theme_color(self) -> None:
        from app.ui.common import PAGE_BG_DARK, PAGE_BG_LIGHT, page_background

        host = QWidget()
        host.setAutoFillBackground(True)
        page_background(host, "testHost")
        self.assertEqual(host.objectName(), "testHost")
        self.assertFalse(host.autoFillBackground())
        sheet = host.styleSheet()
        self.assertTrue(PAGE_BG_LIGHT.name() in sheet or PAGE_BG_DARK.name() in sheet, sheet)

    def test_clear_background_makes_widget_transparent(self) -> None:
        from app.ui.common import clear_background

        host = QWidget()
        host.setAutoFillBackground(True)
        clear_background(host)
        self.assertFalse(host.autoFillBackground())
        self.assertIn("transparent", host.styleSheet())

    def test_clear_scroll_background_covers_viewport_and_inner(self) -> None:
        from PyQt6.QtWidgets import QScrollArea

        from app.ui.common import clear_scroll_background

        area = QScrollArea()
        inner = QWidget()
        area.setWidget(inner)
        clear_scroll_background(area)
        self.assertIn("transparent", area.viewport().styleSheet())
        self.assertIn("transparent", inner.styleSheet())

    def test_theme_palette_matches_page_colors(self) -> None:
        from PyQt6.QtGui import QPalette

        from app.ui.common import PAGE_BG_DARK, PAGE_BG_LIGHT, theme_palette

        light = theme_palette(False)
        dark = theme_palette(True)
        self.assertEqual(light.color(QPalette.ColorRole.Window), PAGE_BG_LIGHT)
        self.assertEqual(dark.color(QPalette.ColorRole.Window), PAGE_BG_DARK)
        self.assertEqual(light.color(QPalette.ColorRole.Highlight), accent_color())

    def test_install_app_theme_is_idempotent(self) -> None:
        from PyQt6.QtGui import QPalette

        from app.ui.common import install_app_theme, theme_palette

        before = QPalette(self.app.palette())
        try:
            install_app_theme()
            install_app_theme()
            expected = theme_palette().color(QPalette.ColorRole.Window)
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window), expected)
        finally:
            self.app.setPalette(before)


if __name__ == "__main__":
    unittest.main()
