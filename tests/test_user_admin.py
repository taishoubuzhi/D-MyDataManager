"""用户权限：默认用户标记，以及删除用户时分类/文件/存档并入默认用户。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.models import ArchiveEntry, Category, DataItem
from app.repositories import CategoryRepository
from app.services import ArchiveService, LibraryService, UserService
from tests.harness import IsolatedCase


class UserAdminCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.users = UserService(self.session)
        self.libraries = LibraryService(self.session)
        self.library = self.libraries.ensure_default()
        self.library_root = Path(self.library.path)
        self.service = self.importer()
        self.admin = self.users.current()
        self.member = self.users.create("小李")
        self.session.commit()

    def _categories(self, name, user_id):
        return list(
            self.session.scalars(
                select(Category).where(Category.name == name, Category.user_id == user_id)
            )
        )

    def test_default_user_keeps_admin_flag(self):
        self.assertTrue(self.admin.is_default)
        self.assertTrue(self.users.is_admin(self.admin))
        self.assertEqual(self.users.default().id, self.admin.id)
        self.assertFalse(self.member.is_default)
        self.assertFalse(self.users.is_admin(self.member))

    def test_default_user_cannot_be_deleted(self):
        self.assertFalse(self.users.delete(self.admin))
        self.session.commit()
        self.assertIsNotNone(self.users.by_id(self.admin.id))

    def test_delete_member_merges_categories_files_and_archives(self):
        category = CategoryRepository(self.session).by_name(
            "学习资料", None, user_id=self.member.id
        )
        item = self.service.import_text(
            "成员笔记", "成员内容", category_id=category.id, user_id=self.member.id
        )
        self.session.commit()
        ArchiveService(self.session).create("快照")
        self.session.commit()

        member_name = self.member.name
        member_dir = self.libraries.dir_name_of(member_name)
        admin_dir = self.libraries.dir_name_of(self.admin.name)
        item_id = item.id
        self.assertTrue(item.file_path.startswith(f"{member_dir}/"))
        self.assertTrue((self.library_root / item.file_path).is_file())

        self.assertTrue(self.users.delete(self.member))
        self.session.commit()

        merged = self.session.get(DataItem, item_id)
        self.assertEqual(merged.user_id, self.admin.id)
        root = self._categories(member_name, self.admin.id)
        self.assertEqual(len(root), 1)
        self.assertIsNone(root[0].parent_id)
        mirrored = [
            row
            for row in self._categories("学习资料", self.admin.id)
            if row.parent_id == root[0].id
        ]
        self.assertEqual(len(mirrored), 1)
        self.assertEqual(merged.category_id, mirrored[0].id)
        self.assertTrue(merged.file_path.startswith(f"{admin_dir}/{member_dir}/学习资料/"))
        self.assertTrue((self.library_root / merged.file_path).is_file())
        self.assertFalse((self.library_root / member_dir).exists())
        self.assertEqual(
            list(self.session.scalars(select(Category).where(Category.user_id == self.member.id))),
            [],
        )
        entry = self.session.scalars(select(ArchiveEntry)).one()
        self.assertEqual(entry.user_id, self.admin.id)
        self.assertEqual(entry.user_name, self.admin.name)
        self.assertEqual(entry.category, f"{member_name} / 学习资料")

    def test_rename_user_dir_moves_files_and_paths(self):
        item = self.service.import_text("笔记", "内容", user_id=self.member.id)
        self.session.commit()
        old_name = self.member.name
        old_dir = self.libraries.dir_name_of(old_name)
        self.assertTrue((self.library_root / old_dir).is_dir())

        self.assertTrue(self.users.rename(self.member, "小李（新）"))
        self.libraries.rename_user_dir(self.member, old_name)
        self.session.commit()

        new_dir = self.libraries.dir_name_of(self.member.name)
        self.assertNotEqual(old_dir, new_dir)
        self.assertFalse((self.library_root / old_dir).exists())
        self.assertTrue((self.library_root / new_dir).is_dir())
        self.assertTrue(item.file_path.startswith(f"{new_dir}/"))
        self.assertTrue((self.library_root / item.file_path).is_file())
