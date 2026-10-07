"""配置迁移（批 C）：旧版「简化显示」布尔值在启动时一次性换算成三挡位。

三挡位定义见 `src/app/core/config.py`；换算只在 `load_config()` 读配置之前跑一次，
之后配置文件里就该只有 `none` / `default` / `full`。

另含「更改资源文件夹位置」的搬迁语义：先落新位置 → 再改配置 → 最后删旧目录，
旧目录删不掉就记下来下次启动补删，绝不留下「配置指着旧目录、旧目录却被搬空」的半截状态。
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from app.core import config as config_module
from app.core.config import (
    SIMPLE_MODES,
    _migrate_legacy_simple_display,
    cleanup_pending_resource_root,
    config,
    resources_root,
    set_resource_root,
)
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


class ResourceRootMoveCase(IsolatedCase):
    """更改资源文件夹位置：先落新位置、再改配置，旧目录删不掉就留着下次启动清理。"""

    def setUp(self) -> None:
        super().setUp()
        # 每个用例都从干净的「新位置」开始（同一个类共用一份隔离根目录）
        shutil.rmtree(Path(self.root) / "moved", ignore_errors=True)
        config_module.PENDING_CLEANUP_FILE.unlink(missing_ok=True)

    def _seed(self) -> Path:
        """在当前资源文件夹里放一个可辨认的文件。"""
        current = resources_root()
        paths.make_dir(current)
        (current / "hello.txt").write_text("hi", encoding="utf-8")
        return current

    def _target(self) -> Path:
        """用户在文件对话框里选的目录（真正的资源文件夹是它下面的 .resources）。"""
        return Path(self.root) / "moved"

    def _release_session(self) -> None:
        """模拟界面：迁移前先放掉自己的会话，否则它会占着 data.db 让目录改名失败。"""
        self.session.close()

    def test_move_relocates_data_and_switches_config(self) -> None:
        current = self._seed()
        self._release_session()
        moved = set_resource_root(self._target())
        self.assertEqual(moved, paths.resource_root(self._target()))
        self.assertTrue((moved / "hello.txt").is_file())
        self.assertFalse(current.exists())  # 同一卷直接改名：旧目录整个不在了
        self.assertEqual(str(config.resourcePath.value), str(moved))
        self.assertEqual(resources_root(), moved)
        self.assertFalse(config_module.PENDING_CLEANUP_FILE.exists())

    def test_existing_target_is_rejected(self) -> None:
        current = self._seed()
        paths.make_dir(paths.resource_root(self._target()))
        self._release_session()
        with self.assertRaises(FileExistsError):
            set_resource_root(self._target())
        self.assertTrue((current / "hello.txt").is_file())
        self.assertEqual(resources_root(), current)

    def test_existing_target_is_rejected_with_a_typed_error(self) -> None:
        """目标里已经有一个资源文件夹：给一个能认出来的错，界面据此问用户要不要覆盖。"""
        self._seed()
        target = paths.resource_root(self._target())
        paths.make_dir(target)
        self._release_session()
        with self.assertRaises(config_module.ResourceRootExists) as caught:
            set_resource_root(self._target())
        self.assertIsInstance(caught.exception, FileExistsError)
        self.assertEqual(caught.exception.target, target)

    def test_existing_target_is_replaced_when_asked(self) -> None:
        """用户同意覆盖：先把目标位置那份（上次没搬完留下的）删掉，再搬过去。"""
        current = self._seed()
        target = paths.resource_root(self._target())
        paths.make_dir(target)
        (target / "stale.txt").write_text("上次搬到一半留下的", encoding="utf-8")
        self._release_session()
        moved = set_resource_root(self._target(), replace=True)
        self.assertEqual(moved, target)
        self.assertTrue((moved / "hello.txt").is_file())
        self.assertFalse((moved / "stale.txt").exists())
        self.assertEqual(resources_root(), moved)
        self.assertFalse(current.exists())

    def test_copy_failure_keeps_original_and_config(self) -> None:
        """不能改名、复制又失败：原目录与配置原样不动，新目录清掉。"""
        current = self._seed()
        before = str(config.resourcePath.value)
        self._release_session()
        with mock.patch.object(Path, "rename", side_effect=OSError("跨卷")), mock.patch(
            "app.core.config.shutil.copytree", side_effect=OSError("空间不足")
        ):
            with self.assertRaises(OSError):
                set_resource_root(self._target())
        self.assertTrue((current / "hello.txt").is_file())
        self.assertFalse(paths.resource_root(self._target()).exists())
        self.assertEqual(str(config.resourcePath.value), before)
        self.assertEqual(resources_root(), current)

    def test_locked_old_dir_is_remembered_for_next_start(self) -> None:
        """旧目录被占用删不掉：配置照样切到新位置，旧目录记下来下次启动补删。"""
        current = self._seed()
        self._release_session()
        with mock.patch.object(Path, "rename", side_effect=OSError("占用")), mock.patch(
            "app.core.config.shutil.rmtree", side_effect=OSError("被占用")
        ):
            moved = set_resource_root(self._target())
        self.assertTrue((moved / "hello.txt").is_file())
        self.assertTrue(current.exists())
        self.assertEqual(str(config.resourcePath.value), str(moved))
        payload = json.loads(config_module.PENDING_CLEANUP_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["path"], str(current))

    def test_pending_cleanup_removes_old_dir_on_startup(self) -> None:
        current = self._seed()
        self._release_session()
        with mock.patch.object(Path, "rename", side_effect=OSError("占用")), mock.patch(
            "app.core.config.shutil.rmtree", side_effect=OSError("被占用")
        ):
            set_resource_root(self._target())
        self.assertTrue(current.exists())
        cleanup_pending_resource_root()  # 这次没东西占着，能删掉
        self.assertFalse(current.exists())
        self.assertFalse(config_module.PENDING_CLEANUP_FILE.exists())

    def test_pending_cleanup_never_deletes_current_root(self) -> None:
        current = self._seed()
        config_module._remember_pending_cleanup(current)
        cleanup_pending_resource_root()
        self.assertTrue((current / "hello.txt").is_file())
        self.assertFalse(config_module.PENDING_CLEANUP_FILE.exists())


if __name__ == "__main__":
    unittest.main()
