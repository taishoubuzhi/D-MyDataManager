"""打开方式规则与决策的单元测试（不依赖 Qt）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core.viewers import Viewer, ViewerRegistry  # noqa: E402
from app.services.open_with_service import (  # noqa: E402
    MODE_ASK,
    MODE_BUILTIN,
    MODE_CUSTOM,
    MODE_INHERIT,
    OpenWithService,
)


def _factory(path, parent=None):
    return None


class OpenWithCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TempDir("openwith")
        self.root = Path(self._tmp.name)
        self.registry = ViewerRegistry()
        self.registry.register(
            Viewer(
                id="demo.markdown.1",
                name="Markdown 查看器",
                extensions=("md",),
                kind="markdown",
                plugin_id="demo.markdown",
                factory=_factory,
            )
        )
        self.config_file = self.root / "open_with.json"
        self.service = OpenWithService(config_file=self.config_file, registry=self.registry)
        self.markdown = self.root / "笔记.md"
        self.markdown.write_text("# 标题", encoding="utf-8")
        self.unknown = self.root / "数据.bin"
        self.unknown.write_bytes(b"\x00\x01\x02")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------ 默认
    def test_suffix_with_builtin_viewer_defaults_to_builtin(self) -> None:
        decision = self.service.resolve(self.markdown)
        self.assertEqual(decision.mode, MODE_BUILTIN)
        self.assertEqual(decision.viewer.name, "Markdown 查看器")
        self.assertTrue(decision.is_builtin)
        self.assertFalse(decision.needs_ask)

    def test_suffix_without_builtin_viewer_inherits_system(self) -> None:
        decision = self.service.resolve(self.unknown)
        self.assertEqual(decision.mode, MODE_INHERIT)
        self.assertIsNone(decision.viewer)

    def test_available_modes_hide_builtin_without_viewer(self) -> None:
        self.assertEqual(self.service.available_modes("md"), (MODE_BUILTIN, MODE_INHERIT, MODE_CUSTOM))
        self.assertEqual(self.service.available_modes("bin"), (MODE_INHERIT, MODE_CUSTOM))
        self.assertTrue(self.service.has_builtin("MD"))
        self.assertFalse(self.service.has_builtin("bin"))

    # ------------------------------------------------------------------ 规则
    def test_inherit_rule_overrides_builtin(self) -> None:
        self.service.set_rule("md", MODE_INHERIT)
        decision = self.service.resolve(self.markdown)
        self.assertEqual(decision.mode, MODE_INHERIT)
        self.assertIsNone(decision.viewer)

    def test_custom_rule_needs_program_and_asks_when_missing(self) -> None:
        self.service.set_rule("md", MODE_CUSTOM, program="C:/Tools/editor.exe")
        decision = self.service.resolve(self.markdown)
        self.assertEqual(decision.mode, MODE_CUSTOM)
        self.assertEqual(decision.program, "C:/Tools/editor.exe")
        self.assertFalse(decision.needs_ask)

        self.service.set_rule("md", MODE_CUSTOM)
        decision = self.service.resolve(self.markdown)
        self.assertEqual(decision.mode, MODE_ASK)
        self.assertTrue(decision.needs_ask)

    def test_builtin_rule_falls_back_when_viewer_missing(self) -> None:
        self.service.set_rule("bin", MODE_BUILTIN)
        decision = self.service.resolve(self.unknown)
        self.assertEqual(decision.mode, MODE_INHERIT)
        self.assertIn("内置查看器不可用", decision.reason)

    def test_rules_are_persisted_and_case_insensitive(self) -> None:
        self.service.set_rule("MD", MODE_INHERIT)
        self.assertEqual(self.service.rule_for("md").mode, MODE_INHERIT)
        reopened = OpenWithService(config_file=self.config_file, registry=self.registry)
        self.assertEqual(reopened.rule_for(".md").mode, MODE_INHERIT)
        self.assertEqual(reopened.rule_for("bin").mode, "")

    def test_empty_mode_restores_default(self) -> None:
        self.service.set_rule("md", MODE_INHERIT)
        self.service.set_rule("md", "")
        self.assertEqual(self.service.rule_for("md").mode, "")
        self.assertEqual(self.service.resolve(self.markdown).mode, MODE_BUILTIN)

    def test_remove_rule_is_idempotent(self) -> None:
        self.service.set_rule("md", MODE_CUSTOM, program="x")
        self.service.remove_rule("md")
        self.service.remove_rule("md")
        self.assertEqual(self.service.rule_for("md").mode, "")


if __name__ == "__main__":
    unittest.main()
