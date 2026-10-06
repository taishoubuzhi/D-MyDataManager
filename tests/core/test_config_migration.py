"""配置迁移（批 C）：旧版「简化显示」布尔值在启动时一次性换算成三挡位。

三挡位定义见 `src/app/core/config.py`；换算只在 `load_config()` 读配置之前跑一次，
之后配置文件里就该只有 `none` / `default` / `full`。
"""

from __future__ import annotations

import json
import unittest

from app.core.config import SIMPLE_MODES, _migrate_legacy_simple_display, config
from app.core.runtime import paths
from tests.harness import IsolatedCase


class SimpleDisplayMigrationCase(IsolatedCase):
    """配置文件里的旧布尔值 → 挡位。"""

    def _write_raw_value(self, value) -> None:
        payload = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
        payload.setdefault("Layout", {})["Simple-Display"] = value
        paths.CONFIG_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_raw_value(self):
        return json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["Layout"]["Simple-Display"]

    def test_validator_only_knows_three_modes(self) -> None:
        self.assertEqual(list(config.simpleDisplay.validator.options), list(SIMPLE_MODES))

    def test_legacy_bool_is_converted(self) -> None:
        for raw, wanted in ((True, "full"), (False, "none")):
            self._write_raw_value(raw)
            _migrate_legacy_simple_display()
            self.assertEqual(self._read_raw_value(), wanted)

    def test_valid_mode_is_left_alone(self) -> None:
        self._write_raw_value("default")
        _migrate_legacy_simple_display()
        self.assertEqual(self._read_raw_value(), "default")

    def test_other_keys_survive(self) -> None:
        payload = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
        payload.setdefault("Layout", {})["Page-Size"] = 77
        payload.setdefault("Layout", {})["Simple-Display"] = True
        paths.CONFIG_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _migrate_legacy_simple_display()
        after = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(after["Layout"]["Page-Size"], 77)
        self.assertEqual(after["Layout"]["Simple-Display"], "full")

    def test_missing_file_and_broken_json_are_ignored(self) -> None:
        paths.CONFIG_FILE.unlink()
        _migrate_legacy_simple_display()  # 文件不在：直接返回
        paths.CONFIG_FILE.write_text("不是 JSON", encoding="utf-8")
        _migrate_legacy_simple_display()  # 坏 JSON：不动文件
        self.assertEqual(paths.CONFIG_FILE.read_text(encoding="utf-8"), "不是 JSON")


if __name__ == "__main__":
    unittest.main()
