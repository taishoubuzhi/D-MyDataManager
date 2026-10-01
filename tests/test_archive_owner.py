"""存档条目记录归属用户，还原时回到该用户分类目录，并按权限过滤可见范围。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.models import Category
from app.repositories import CategoryRepository
from app.services import ArchiveService, ItemService, LibraryService, UserService
from tests.harness import IsolatedCase


class ArchiveOwnerCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.users = UserService(self.session)
        self.libraries = LibraryService(self.session)
        self.library = self.libraries.ensure_default()
        self.library_root = Path(self.library.path)
        self.service = self.importer()
        self.items_service = ItemService(self.session)
        self.archives = ArchiveService(self.session)
        self.admin = self.users.current()
        self.member = self.users.create("小李")
        self.session.commit()

        self.admin_item = self.service.import_text(
            "管理员笔记", "管理员内容", user_id=self.admin.id
        )
        member_category = CategoryRepository(self.session).by_name(
            "学习资料", None, user_id=self.member.id
        )
        self.member_item = self.service.import_text(
            "成员笔记", "成员内容", category_id=member_category.id, user_id=self.member.id
        )
        self.session.commit()
        self.archive = self.archives.create("快照")
        self.session.commit()

    def _entry(self, name):
        return next(item for item in self.archives.entries(self.archive) if item.name == name)

    def test_entries_record_owner(self):
        admin_entry = self._entry("管理员笔记")
        member_entry = self._entry("成员笔记")
        self.assertEqual(admin_entry.user_id, self.admin.id)
        self.assertEqual(admin_entry.user_name, self.admin.name)
        self.assertEqual(member_entry.user_id, self.member.id)
        self.assertEqual(member_entry.user_name, "小李")

    def test_visible_entries_follow_permission(self):
        admin_visible = self.archives.visible_entries(self.archive, self.admin.id, True)
        member_visible = self.archives.visible_entries(self.archive, self.member.id, False)
        self.assertEqual(len(admin_visible), 2)
        self.assertEqual([entry.name for entry in member_visible], ["成员笔记"])

    def test_restore_entry_returns_to_owner_directory(self):
        entry = self._entry("成员笔记")
        checksum = self.member_item.checksum
        self.items_service.purge([self.member_item])
        self.session.commit()

        restored = self.archives.restore_entry(entry)
        self.session.commit()

        self.assertIsNotNone(restored)
        self.assertEqual(restored.checksum, checksum)
        self.assertEqual(restored.user_id, self.member.id)
        self.assertTrue(restored.file_path.startswith("小李/学习资料/"))
        self.assertTrue((self.library_root / restored.file_path).is_file())
        category = self.session.get(Category, restored.category_id)
        self.assertEqual(category.name, "学习资料")
        self.assertEqual(category.user_id, self.member.id)

    def test_compare_can_be_scoped_to_one_user(self):
        self.items_service.purge([self.admin_item])
        self.session.commit()
        self.assertIn("管理员笔记", self.archives.compare(self.archive).removed)
        self.assertNotIn(
            "管理员笔记", self.archives.compare(self.archive, self.member.id).removed
        )

    def test_restore_all_reports_counts(self):
        self.items_service.purge([self.admin_item, self.member_item])
        self.session.commit()
        stats = self.archives.restore_all(self.archive)
        self.session.commit()
        self.assertEqual(stats, {"restored": 2, "skipped": 0})

    def test_entry_state_tracks_the_current_data(self):
        entry = self._entry("成员笔记")
        self.assertEqual(self.archives.entry_state(entry), "same")
        self.member_item.checksum = "0" * 64
        self.session.commit()
        self.assertEqual(self.archives.entry_state(entry), "changed")
        self.member_item.checksum = entry.checksum
        self.items_service.delete([self.member_item])
        self.session.commit()
        self.assertEqual(self.archives.entry_state(entry), "removed")

    def test_restore_refuses_identical_data(self):
        entry = self._entry("成员笔记")
        self.assertIsNone(self.archives.restore_entry(entry))
        stats = self.archives.restore_all(self.archive)
        self.session.commit()
        self.assertEqual(stats, {"restored": 0, "skipped": 2})

    def test_restore_does_not_reuse_another_users_item(self):
        self.service.import_text("同内容", "共享内容", user_id=self.admin.id)
        shared = self.service.import_text("同内容副本", "共享内容", user_id=self.member.id)
        self.session.commit()
        archive = self.archives.create("快照2")
        self.session.commit()
        entry = next(item for item in self.archives.entries(archive) if item.name == "同内容副本")
        self.items_service.purge([shared])
        self.session.commit()

        restored = self.archives.restore_entry(entry)
        self.session.commit()

        self.assertIsNotNone(restored)
        self.assertEqual(restored.user_id, self.member.id)
        self.assertTrue(restored.file_path.startswith("小李/"))
