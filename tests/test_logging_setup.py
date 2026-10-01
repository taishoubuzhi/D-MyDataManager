"""日志初始化：控制台配色的格式串与渲染结果。"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loguru import logger  # noqa: E402
from app.core.logging_setup import LEVEL_COLORS, _apply_level_colors, console_format  # noqa: E402

TEMPLATE = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"


class ConsoleFormatCase(unittest.TestCase):
    """控制台格式串要按字段上色，文件输出保持纯文本。"""

    def test_console_format_tags_every_field(self) -> None:
        text = console_format(TEMPLATE)
        self.assertIn("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green>", text)
        self.assertIn("<level>{level: <8}</level>", text)
        self.assertIn("<cyan>{name}</cyan>", text)
        self.assertIn("<cyan>{function}</cyan>", text)
        self.assertIn("<cyan>{line}</cyan>", text)
        self.assertIn("<level>{message}</level>", text)
        self.assertNotIn("<<", text)

    def test_console_format_leaves_unknown_fields_alone(self) -> None:
        self.assertEqual(console_format("{extra} 文本"), "{extra} 文本")
        self.assertEqual(console_format("{module}:{message}"), "<cyan>{module}</cyan>:<level>{message}</level>")

    def test_every_level_has_its_own_color(self) -> None:
        for name in ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"):
            self.assertIn(name, LEVEL_COLORS)
        self.assertEqual(LEVEL_COLORS["INFO"], "<green>")
        self.assertEqual(LEVEL_COLORS["WARNING"], "<yellow>")
        self.assertEqual(LEVEL_COLORS["ERROR"], "<red>")
        self.assertEqual(LEVEL_COLORS["CRITICAL"], "<red><bold>")

    def test_console_renders_colors_and_file_stays_plain(self) -> None:
        _apply_level_colors()
        coloured = io.StringIO()
        handler = logger.add(coloured, colorize=True, format=console_format(TEMPLATE), level="TRACE")
        try:
            logger.info("示例信息")
            logger.warning("示例警告")
            logger.error("示例错误")
        finally:
            logger.remove(handler)
        text = coloured.getvalue()
        self.assertIn("\x1b[32m", text)  # INFO 绿色
        self.assertIn("\x1b[33m", text)  # WARNING 黄色
        self.assertIn("\x1b[31m", text)  # ERROR 红色

        plain = io.StringIO()
        handler = logger.add(plain, colorize=False, format=console_format(TEMPLATE), level="TRACE")
        try:
            logger.info("文件内容")
        finally:
            logger.remove(handler)
        self.assertIn("文件内容", plain.getvalue())
        self.assertNotIn("\x1b[", plain.getvalue())


if __name__ == "__main__":
    unittest.main()
