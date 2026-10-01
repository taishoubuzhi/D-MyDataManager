import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import config, store_dir
from app.core import paths
from app.repositories import BlobRepository, ItemFilter, ItemRepository
from app.services import ArchiveService, ItemService
from tests.harness import IsolatedCase


class ArchiveCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.service = self.importer()
        self.items_service = ItemService(self.session)
        self.archives = ArchiveService(self.session)
        self.first = self.service.import_text("笔记一", "第一份内容")
        self.second = self.service.import_file(self.corpus.by_name("favicon.ico"))
        self.third = self.service.import_text("笔记二", "第三份内容", is_hidden=True)
        self.session.commit()

    def test_create_records_every_item(self):
        archive = self.archives.create("快照一", "第一份存档")
        self.session.commit()
        self.assertEqual(archive.item_count, 3)
        self.assertGreater(archive.total_size, 0)
        entries = self.archives.entries(archive)
        self.assertEqual(
            {entry.checksum for entry in entries},
            {item.checksum for item in (self.first, self.second, self.third)},
        )
        self.assertEqual(len(self.archives.history()), 1)
        self.assertTrue(self.archives.policy_summary())

    def test_default_name_is_generated(self):
        archive = self.archives.create()
        self.session.commit()
        self.assertTrue(archive.name)

    def test_compare_detects_added_changed_removed(self):
        archive = self.archives.create("快照")
        self.session.commit()
        self.assertTrue(self.archives.compare(archive).is_empty)

        added = self.service.import_text("笔记三", "新增内容")
        self.session.commit()
        self.assertIn(added.name, self.archives.compare(archive).added)

        self.items_service.update(self.first, content="改过的内容")
        self.session.commit()
        self.assertIn(self.first.name, self.archives.compare(archive).changed)

        self.items_service.purge([self.third])
        self.session.commit()
        self.assertIn(self.third.name, self.archives.compare(archive).removed)

    def test_missing_blob_is_reported(self):
        archive = self.archives.create("快照")
        self.session.commit()
        blob = BlobRepository(self.session).by_checksum(self.second.checksum)
        (store_dir() / blob.rel_path).unlink()
        self.assertIn(self.second.name, self.archives.compare(archive).missing_blobs)

    def test_restore_entry_recreates_item(self):
        archive = self.archives.create("快照")
        self.session.commit()
        entry = next(item for item in self.archives.entries(archive) if item.name == self.second.name)
        checksum = self.second.checksum
        self.items_service.purge([self.second])
        self.session.commit()
        restored = self.archives.restore_entry(entry)
        self.session.commit()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.name, entry.name)
        self.assertEqual(restored.checksum, checksum)
        self.assertTrue(self.items_service.file_path_of(restored))

    def test_delete_archive(self):
        archive = self.archives.create("快照")
        self.session.commit()
        self.archives.delete(archive)
        self.session.commit()
        self.assertEqual(self.archives.history(), [])

    def test_orphan_cleanup_keeps_archived_content(self):
        archive = self.archives.create("快照")
        self.session.commit()
        self.items_service.purge([self.first, self.second, self.third])
        self.session.commit()
        blob = BlobRepository(self.session).by_checksum(self.first.checksum)
        stored = store_dir() / blob.rel_path
        self.assertTrue(stored.is_file())
        self.assertEqual(self.archives.orphans(), [])
        self.assertEqual(self.archives.cleanup_orphans(), (0, 0))
        self.archives.delete(archive)
        self.session.commit()
        count, freed = self.archives.cleanup_orphans()
        self.assertGreater(count, 0)
        self.assertGreater(freed, 0)
        self.assertFalse(stored.is_file())

    def test_prune_by_count_keeps_latest(self):
        for index in range(3):
            self.archives.create(f"快照{index}")
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 3)
        self.assertEqual(self.archives.prune(keep=1), 2)
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 1)

    def test_prune_by_size_and_age(self):
        for index in range(3):
            self.archives.create(f"快照{index}")
        self.session.commit()
        self.assertEqual(self.archives.prune_by_size(0), 2)
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 1)
        self.archives.create("再来一个")
        self.session.commit()
        self.assertEqual(self.archives.prune_by_age(days=0), 1)
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 1)

    def test_auto_prune_follows_configuration(self):
        config.set(config.pruneMode, "count")
        config.set(config.keepVersions, 1)
        for index in range(3):
            self.archives.create(f"快照{index}")
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 1)
        self.assertIn("保留最近 1 个存档", self.archives.policy_summary())

        config.set(config.pruneMode, "none")
        self.archives.create("不清理")
        self.session.commit()
        self.assertEqual(len(self.archives.history()), 2)
        self.assertIn("已关闭", self.archives.policy_summary())

    def test_archive_covers_hidden_and_trashed_items(self):
        self.items_service.delete([self.third])
        self.session.commit()
        archive = self.archives.create("含回收站")
        self.session.commit()
        names = {entry.name for entry in self.archives.entries(archive)}
        self.assertIn(self.third.name, names)
        self.assertEqual(
            ItemRepository(self.session).count(
                ItemFilter(include_deleted=True, include_hidden=True)
            ),
            3,
        )
