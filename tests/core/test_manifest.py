"""清单机制：读取 / 查询 / 对照 / 变更 / 重置 / 备份，以及 SDK 面。

批 B 的实现，协议见 `docs/MANIFEST_PROTOCOL.md`。全程用临时目录里的
假清单，不碰仓库里真实的 JSON；真实清单的一致性由 `tests/core/test_core_module_data.py` 保证。
"""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from app import sdk
from app.core.manifest import (
    BACKUP_KEEP,
    FORMAT_LEGACY,
    ManifestEntry,
    ManifestFormatError,
    ManifestKit,
    ManifestNotFoundError,
    ManifestRegistry,
    ManifestValidationError,
    manifest_kit,
)
from app.core.plugins.extensions import extension_registry
from app.core.runtime import paths
from app.services.plugin_service import STATE_MANIFEST_ID
from tmpenv import tests_tmp

_TMP = tests_tmp("manifest")
_KEEP_TMP = os.environ.get("DM_KEEP_TMP") == "1"


def _payload(**over) -> dict:
    payload = {
        "manifest": "1",
        "id": "test.one",
        "version": "1",
        "kind": "registry",
        "items": [
            {"key": "alpha", "value": 1, "label": "甲", "group": "a"},
            {"key": "beta", "value": 2, "label": "乙", "group": "b"},
        ],
    }
    payload.update(over)
    return payload


class RegistryCase(unittest.TestCase):
    """内置登记表：17 份清单，只剩插件状态那一份还是历史格式。"""

    def test_builtin_entries(self):
        entries = manifest_kit.entries()
        self.assertEqual(len(entries), 17)
        self.assertEqual(len({entry.id for entry in entries}), 17)
        managed = [entry.id for entry in entries if entry.managed]
        self.assertEqual(len(managed), 16)
        self.assertNotIn(STATE_MANIFEST_ID, managed)
        self.assertIn("lib.model.model_list", managed)
        self.assertIn("builtin.viewer.image.viewer", managed)
        self.assertIn("builtin.editor.office.editor", managed)
        legacy = [entry for entry in entries if not entry.managed]
        self.assertEqual([entry.id for entry in legacy], [STATE_MANIFEST_ID])
        self.assertTrue(all(entry.format == FORMAT_LEGACY for entry in legacy))
        self.assertTrue(all(entry.path.suffix == ".json" for entry in entries))

    def test_backup_root_follows_current_config_dir(self):
        """模块级 `manifest_kit` 的备份目录按当前 paths 现算。

        隔离用例会把 `CONFIG_DIR` 重挂到临时根；若把备份目录冻在 import 那一刻，
        备份会写到「第一个用例的目录」里，`reset_state()` 就可能拿回别人的备份。
        """
        kit = ManifestKit()
        with mock.patch.object(paths, "CONFIG_DIR", Path("X:/临时配置")):
            self.assertEqual(kit.backup_root, Path("X:/临时配置") / "backups" / "manifest")
        self.assertEqual(ManifestKit(backup_root=_TMP / "b").backup_root, _TMP / "b")

    def test_plugin_state_is_registered_legacy(self):
        entry = manifest_kit.entries()[2]
        self.assertEqual(entry.id, STATE_MANIFEST_ID)
        self.assertEqual(entry.path, paths.PLUGIN_STATE_FILE)
        self.assertFalse(entry.managed)

    def test_describe_all_and_one(self):
        rows = manifest_kit.describe()
        self.assertEqual(len(rows), 17)
        info = manifest_kit.describe("core.plugins")
        self.assertEqual(info["owner"], "core")
        self.assertTrue(info["exists"])
        self.assertEqual(info["error"], "")
        self.assertGreater(info["count"], 0)

    def test_unknown_id_is_refused(self):
        with self.assertRaises(ManifestNotFoundError):
            manifest_kit.describe("nope.nope")

    def test_duplicate_registration_is_refused(self):
        entry = ManifestEntry(id="core.runtime", path=Path("x.json"), kind="registry", owner="core")
        with self.assertRaises(ValueError):
            manifest_kit.register(entry)


class KitCase(unittest.TestCase):
    """工具包本体：用临时目录里的假清单跑全套读写。"""

    def setUp(self):
        shutil.rmtree(_TMP, ignore_errors=True)
        _TMP.mkdir(parents=True, exist_ok=True)
        self.path = _TMP / "one.json"
        self.path.write_text(json.dumps(_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.registry = ManifestRegistry(
            [ManifestEntry(id="test.one", path=self.path, kind="registry", owner="test")]
        )
        self.kit = ManifestKit(self.registry, backup_root=_TMP / "backups")

    def tearDown(self):
        if not _KEEP_TMP:
            shutil.rmtree(_TMP, ignore_errors=True)

    def _write_raw(self, payload, raw: str | None = None) -> None:
        self.path.write_text(raw if raw is not None else json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # 读 -----------------------------------------------------------------
    def test_load_items_and_values(self):
        data = self.kit.load("test.one")
        self.assertEqual(data.keys, ("alpha", "beta"))
        self.assertEqual(data.value("alpha"), 1)
        self.assertEqual(data.get("beta")["label"], "乙")
        self.assertEqual(data.kind, "registry")
        self.assertIn("key", data.items["alpha"])

    def test_load_missing_file(self):
        self.path.unlink()
        with self.assertRaises(ManifestNotFoundError):
            self.kit.load("test.one")

    def test_load_broken_json(self):
        self._write_raw({}, raw="{ not json")
        with self.assertRaises(ManifestFormatError) as ctx:
            self.kit.load("test.one")
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_load_missing_required_key(self):
        payload = _payload()
        payload.pop("version")
        self._write_raw(payload)
        with self.assertRaises(ManifestFormatError) as ctx:
            self.kit.load("test.one")
        self.assertIn("缺少必须项", str(ctx.exception))

    def test_duplicate_key_is_refused(self):
        self._write_raw(_payload(items=[{"key": "a"}, {"key": "a"}]))
        with self.assertRaises(ManifestFormatError) as ctx:
            self.kit.load("test.one")
        self.assertIn("重复", str(ctx.exception))

    def test_validate_false_skips_schema(self):
        self._write_raw(_payload(kind="乱写的"))
        data = self.kit.load("test.one", validate=False)
        self.assertEqual(data.value("alpha"), 1)

    def test_schema_rejects_bad_kind(self):
        self._write_raw(_payload(kind="乱写的"))
        with self.assertRaises(ManifestValidationError) as ctx:
            self.kit.load("test.one")
        self.assertIn("不符合 manifest 约定", str(ctx.exception))

    def test_entry_without_value_field(self):
        self._write_raw(_payload(items=[{"key": "a", "label": "A"}]))
        data = self.kit.load("test.one")
        with self.assertRaises(ManifestFormatError) as ctx:
            data.value("a")
        self.assertIn("没有 value 字段", str(ctx.exception))
        self.assertEqual(data.value("a", default=None), None)

    # 查 -----------------------------------------------------------------
    def test_query_filters(self):
        self.assertEqual([item["key"] for item in self.kit.query("test.one", group="a")], ["alpha"])
        self.assertEqual([item["key"] for item in self.kit.query("test.one", group=("a", "b"))], ["alpha", "beta"])
        self.assertEqual([item["key"] for item in self.kit.query("test.one", label="乙")], ["beta"])
        self.assertEqual(self.kit.query("test.one", group="z"), [])

    # 对照 ---------------------------------------------------------------
    def test_diff_is_empty_for_identical_content(self):
        self.assertEqual(self.kit.diff("test.one", self.kit.raw("test.one")), [])

    def test_diff_reports_added_removed_changed(self):
        candidate = _payload(
            items=[
                {"key": "alpha", "value": 1, "label": "甲", "group": "b"},
                {"key": "gamma", "value": 3},
            ]
        )
        lines = self.kit.diff("test.one", candidate)
        self.assertEqual(len(lines), 3)
        self.assertTrue(any(line.startswith("+ test.one.gamma") for line in lines))
        self.assertTrue(any(line.startswith("- test.one.beta") for line in lines))
        self.assertTrue(any(line.startswith("~ test.one.alpha") and "group" in line for line in lines))

    def test_diff_accepts_a_file(self):
        other = _TMP / "other.json"
        other.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.kit.diff("test.one", other), [])

    # 写 -----------------------------------------------------------------
    def test_update_merges_and_removes(self):
        self.kit.update("test.one", [{"key": "alpha", "label": "改名"}, {"key": "gamma", "value": 3}])
        data = self.kit.load("test.one")
        self.assertEqual(data.get("alpha")["label"], "改名")
        self.assertEqual(data.get("alpha")["value"], 1)  # 没提到的字段保持原样
        self.assertEqual(data.value("gamma"), 3)
        self.kit.update("test.one", [{"key": "beta", "remove": True}])
        self.assertNotIn("beta", self.kit.load("test.one").keys)

    def test_update_requires_key(self):
        with self.assertRaises(ManifestFormatError):
            self.kit.update("test.one", [{"value": 1}])

    def test_write_rejects_bad_content(self):
        with self.assertRaises(ManifestValidationError):
            self.kit.write("test.one", _payload(kind="乱写的"))
        self.assertEqual(self.kit.load("test.one").value("alpha"), 1)  # 坏内容没有落盘

    def test_update_leaves_no_part_file(self):
        self.kit.update("test.one", [{"key": "alpha", "value": 9}])
        self.assertEqual(list(_TMP.glob("*.part")), [])
        self.assertEqual(list(_TMP.glob("*.part.*")), [])

    # 备份与重置 ---------------------------------------------------------
    def test_update_creates_backup_and_reset_restores(self):
        self.kit.update("test.one", [{"key": "alpha", "value": 9}])
        self.assertEqual(len(self.kit.backups("test.one")), 1)
        self.assertEqual(self.kit.load("test.one").value("alpha"), 9)
        self.kit.reset("test.one")
        self.assertEqual(self.kit.load("test.one").value("alpha"), 1)

    def test_reset_without_backup(self):
        from app.core.manifest import ManifestBackupError

        with self.assertRaises(ManifestBackupError):
            self.kit.reset("test.one")

    def test_backup_prunes_to_keep_count(self):
        for index in range(BACKUP_KEEP + 3):
            self.kit.update("test.one", [{"key": "alpha", "value": index}])
        self.assertEqual(len(self.kit.backups("test.one")), BACKUP_KEEP)
        self.assertTrue(all(item.name.endswith(".json") for item in self.kit.backups("test.one")))

    def test_backup_of_missing_file(self):
        from app.core.manifest import ManifestBackupError

        self.path.unlink()
        with self.assertRaises(ManifestBackupError):
            self.kit.backup("test.one")


class LegacyCase(unittest.TestCase):
    """历史格式：能读、能整份对照，但不能查也不能改。"""

    def setUp(self):
        shutil.rmtree(_TMP, ignore_errors=True)
        _TMP.mkdir(parents=True, exist_ok=True)
        self.path = _TMP / "old.json"
        self.path.write_text(json.dumps({"anything": [1, 2, 3]}, ensure_ascii=False), encoding="utf-8")
        self.kit = ManifestKit(
            ManifestRegistry(
                [
                    ManifestEntry(
                        id="test.old",
                        path=self.path,
                        kind="catalog",
                        owner="test",
                        format=FORMAT_LEGACY,
                        schema="legacy",
                    )
                ]
            ),
            backup_root=_TMP / "backups",
        )

    def tearDown(self):
        if not _KEEP_TMP:
            shutil.rmtree(_TMP, ignore_errors=True)

    def test_raw_reads_and_describe_marks_format(self):
        self.assertEqual(self.kit.raw("test.old"), {"anything": [1, 2, 3]})
        self.assertFalse(self.kit.describe("test.old")["managed"])

    def test_query_and_write_are_refused(self):
        with self.assertRaises(ManifestFormatError):
            self.kit.query("test.old")
        with self.assertRaises(ManifestFormatError):
            self.kit.update("test.old", [{"key": "a", "value": 1}])

    def test_diff_compares_whole_content(self):
        self.assertEqual(self.kit.diff("test.old", {"anything": [1, 2, 3]}), [])
        self.assertEqual(len(self.kit.diff("test.old", {"anything": []})), 1)

    def test_legacy_load_returns_raw(self):
        data = self.kit.load("test.old")
        self.assertEqual(data.raw, {"anything": [1, 2, 3]})
        self.assertEqual(data.items, {})


class SdkCase(unittest.TestCase):
    """SDK 面：没有实现时安全退化，有实现时直接转发。"""

    def setUp(self):
        shutil.rmtree(_TMP, ignore_errors=True)
        _TMP.mkdir(parents=True, exist_ok=True)
        self.path = _TMP / "one.json"
        self.path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
        self.kit = ManifestKit(
            ManifestRegistry([ManifestEntry(id="test.one", path=self.path, kind="registry", owner="test")]),
            backup_root=_TMP / "backups",
        )

    def tearDown(self):
        extension_registry.clear()
        if not _KEEP_TMP:
            shutil.rmtree(_TMP, ignore_errors=True)

    def test_without_provider(self):
        self.assertFalse(sdk.manifest.available())
        self.assertEqual(sdk.manifest.ids(), ())
        self.assertEqual(sdk.manifest.entries(), ())
        self.assertEqual(sdk.manifest.describe(), [])
        with self.assertRaises(sdk.SdkError):
            sdk.manifest.load("test.one")

    def test_with_provider(self):
        extension_registry.provide(sdk.manifest.MANIFEST_EXTENSION, self.kit, "test")
        self.assertTrue(sdk.manifest.available())
        self.assertEqual(sdk.manifest.ids(), ("test.one",))
        self.assertEqual(sdk.manifest.describe("test.one")["owner"], "test")
        self.assertEqual([item["key"] for item in sdk.manifest.query("test.one", group="b")], ["beta"])
        self.assertEqual(sdk.manifest.diff("test.one", self.kit.raw("test.one")), [])
        sdk.manifest.update("test.one", [{"key": "alpha", "value": 5}])
        self.assertEqual(sdk.manifest.load("test.one").value("alpha"), 5)
        self.assertEqual(len(sdk.manifest.backups("test.one")), 1)


if __name__ == "__main__":
    unittest.main()
