"""临时清单（`kind = "journal"`）：创建、重启重登记、节流落盘、终态清理。

协议见 `docs/MANIFEST_PROTOCOL.md` §2.1。全程在临时目录里跑，登记用唯一批次 id，
跑完把自己的登记删干净，不影响仓库里真实的清单。
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from app.core.journals import (
    FLUSH_EVERY,
    ITEM_ACTIVE,
    ITEM_CANCELLED,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_PENDING,
    ITEM_SKIPPED,
    JOURNAL_KIND,
    JournalStore,
    journal_id,
    new_batch,
)
from app.core.manifest import MANIFEST_VERSION, ManifestNotFoundError, manifest_kit, read_json
from tmpenv import tests_tmp

_TMP = tests_tmp("journal")
_KEEP_TMP = os.environ.get("DM_KEEP_TMP") == "1"


class JournalStoreCase(unittest.TestCase):
    """一份临时清单的完整生命周期。"""

    def setUp(self) -> None:
        shutil.rmtree(_TMP, ignore_errors=True)
        _TMP.mkdir(parents=True, exist_ok=True)
        self._ids: list[str] = []
        self.store = JournalStore("import", root=_TMP)

    def tearDown(self) -> None:
        for manifest_id in self._ids:
            manifest_kit.drop(manifest_id)
        if not _KEEP_TMP:
            shutil.rmtree(_TMP, ignore_errors=True)

    # -------------------------------------------------------------- 工具
    def _create(self, **kwargs):
        journal = self.store.create(**kwargs)
        self._ids.append(journal.id)
        return journal

    # -------------------------------------------------------------- 创建
    def test_create_writes_file_and_registers(self) -> None:
        journal = self._create(
            options={"user_id": 1, "duplicate_policy": "rename"},
            items=[{"key": "a", "source": "a.txt"}, {"key": "b", "source": "b.txt"}],
        )

        self.assertTrue(Path(journal.path).is_file())
        self.assertEqual(read_json(Path(journal.path))["id"], journal.id)

        data = manifest_kit.load(journal.id)
        self.assertEqual(data.kind, JOURNAL_KIND)
        self.assertEqual(data.meta["journal_kind"], "import")
        self.assertEqual(data.meta["state"], "running")
        self.assertEqual(data.meta["batch"], journal.batch)
        self.assertEqual(data.meta["options"]["user_id"], 1)
        # items 的文件顺序就是处理顺序，不能被重排
        self.assertEqual([item["key"] for item in data.list()], ["a", "b"])

    def test_ids_obey_manifest_rules(self) -> None:
        self.assertTrue(journal_id("import", "20260101_120000_ab12").startswith("core.journal.import."))
        # 数字开头不是合法的一段，slug 会给它加前缀而不是产出非法 id
        self.assertEqual(journal_id("import", "123"), "core.journal.import.b123")
        self.assertRegex(new_batch(), r"^\d{8}_\d{6}_[0-9a-f]{6}$")

    def test_payload_omits_empty_description(self) -> None:
        journal = self._create(items=[{"key": "a"}])
        payload = journal.payload()
        self.assertEqual(payload["manifest"], MANIFEST_VERSION)
        self.assertEqual(payload["kind"], JOURNAL_KIND)
        self.assertNotIn("description", payload)

        journal.description = "为什么删不掉"
        self.assertEqual(journal.payload()["description"], "为什么删不掉")

    # -------------------------------------------------------------- 恢复
    def test_scan_reregisters_after_restart(self) -> None:
        journal = self._create(items=[{"key": "a"}, {"key": "b"}])

        # 模拟进程重启：ManifestRegistry 只是内存字典，登记没了
        manifest_kit.drop(journal.id)
        self._ids.remove(journal.id)
        with self.assertRaises(ManifestNotFoundError):
            manifest_kit.load(journal.id)

        found = JournalStore("import", root=_TMP).scan()
        self._ids.append(journal.id)

        self.assertEqual([item.id for item in found], [journal.id])
        self.assertEqual([item["key"] for item in manifest_kit.load(journal.id).list()], ["a", "b"])

    def test_scan_keeps_broken_file(self) -> None:
        journal = self._create(items=[{"key": "a"}])
        broken = _TMP / "broken.json"
        broken.write_text("{ 不是 JSON", encoding="utf-8")

        found = JournalStore("import", root=_TMP).scan()

        self.assertEqual([item.id for item in found], [journal.id])
        self.assertTrue(broken.is_file())
        self.assertEqual(broken.read_text(encoding="utf-8"), "{ 不是 JSON")

    def test_recover_returns_active_to_pending(self) -> None:
        journal = self._create(items=[{"key": "a"}, {"key": "b"}])
        journal.set_status("a", ITEM_ACTIVE, detail="正在复制")
        journal.set_status("b", ITEM_DONE)
        self.store.flush(journal)

        touched = journal.recover()

        self.assertEqual([item["key"] for item in touched], ["a"])
        self.assertEqual(journal.status_of("a"), ITEM_PENDING)
        self.assertEqual(journal.find("a")["detail"], "")
        self.assertEqual(journal.status_of("b"), ITEM_DONE)

    # -------------------------------------------------------------- 落盘
    def test_save_is_throttled_until_forced(self) -> None:
        journal = self._create(items=[{"key": "a"}, {"key": "b"}])
        with mock.patch("app.core.journals.FLUSH_INTERVAL", 3600.0):
            journal.set_status("a", ITEM_DONE)
            self.store.save(journal)
            journal.set_status("b", ITEM_DONE)
            self.store.save(journal)

            # 未到阈值，盘上还是旧状态
            self.assertEqual(read_json(Path(journal.path))["items"][0]["status"], ITEM_PENDING)

            self.store.flush(journal)
            on_disk = read_json(Path(journal.path))["items"]
            self.assertEqual([item["status"] for item in on_disk], [ITEM_DONE, ITEM_DONE])

    def test_save_flushes_every_n_writes(self) -> None:
        journal = self._create(items=[{"key": "a"}])
        with mock.patch("app.core.journals.FLUSH_INTERVAL", 3600.0):
            for _ in range(FLUSH_EVERY):
                journal.set_status("a", ITEM_ACTIVE)
                self.store.save(journal)
            self.assertEqual(read_json(Path(journal.path))["items"][0]["status"], ITEM_ACTIVE)

    def test_flush_all_writes_pending_changes(self) -> None:
        journal = self._create(items=[{"key": "a"}])
        with mock.patch("app.core.journals.FLUSH_INTERVAL", 3600.0):
            journal.set_status("a", ITEM_SKIPPED)
            self.store.save(journal)
            self.assertEqual(read_json(Path(journal.path))["items"][0]["status"], ITEM_PENDING)

        self.store.flush_all()
        self.assertEqual(read_json(Path(journal.path))["items"][0]["status"], ITEM_SKIPPED)

    # -------------------------------------------------------------- 清理
    def test_settle_and_clear_settled(self) -> None:
        journal = self._create(items=[{"key": "a"}, {"key": "b"}])
        self.assertFalse(journal.terminal())
        self.assertFalse(self.store.settle(journal))

        journal.set_status("a", ITEM_DONE)
        journal.set_status("b", ITEM_CANCELLED)
        self.store.save(journal, force=True)

        self.assertTrue(journal.terminal())
        removed = self.store.clear_settled()

        self.assertIn(journal.id, removed)
        self.assertFalse(Path(journal.path).exists())
        with self.assertRaises(ManifestNotFoundError):
            manifest_kit.load(journal.id)

    def test_remove_deletes_file_and_registration(self) -> None:
        journal = self._create(items=[{"key": "a"}])

        self.store.remove(journal)

        self.assertFalse(Path(journal.path).exists())
        with self.assertRaises(ManifestNotFoundError):
            manifest_kit.load(journal.id)
        # 已经注销过，再删一次不报错、也不再是「删掉了」
        self.assertFalse(manifest_kit.drop(journal.id))

    # -------------------------------------------------------------- 单项
    def test_item_helpers(self) -> None:
        journal = self._create(items=[{"key": "a", "source": "a.txt"}])

        self.assertEqual(journal.find("a")["source"], "a.txt")
        self.assertIsNone(journal.find("missing"))
        self.assertEqual(journal.status_of("missing"), "")
        self.assertEqual(journal.counts(), {ITEM_PENDING: 1})

        journal.set_status("a", ITEM_FAILED, detail="坏文件")
        self.assertEqual(journal.status_of("a"), ITEM_FAILED)
        self.assertEqual(journal.find("a")["detail"], "坏文件")
        self.assertEqual(journal.open_items(), [])

        journal.add_item("b", source="b.txt")
        self.assertEqual(journal.status_of("b"), ITEM_PENDING)
        self.assertEqual(journal.counts(), {ITEM_FAILED: 1, ITEM_PENDING: 1})
        self.assertEqual([item["key"] for item in journal.open_items()], ["b"])


if __name__ == "__main__":
    unittest.main()
