import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from pathlib import Path

from app.core.config import config
from app.core.security import ITERATIONS, hash_password, verify_hash
from app.db.models import Tag
from app.repositories import CategoryRepository, ItemFilter, ItemRepository, TagRepository
from app.services import ItemService, LibraryService, UserService, overview
from tests.harness import IsolatedCase


class UserCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.service = UserService(self.session)
        self.user = self.service.current()

    def test_default_user_is_protected(self):
        info = self.service.list_users()[0]
        self.assertEqual(info.name, "默认用户")
        # protected 表示“已设口令”，默认用户初始没有口令。
        self.assertFalse(info.protected)
        self.service.set_password(info.user, "pw123456")
        self.session.commit()
        self.assertTrue(self.service.list_users()[0].protected)
        self.assertEqual(len(self.service.list_users()), 1)

    def test_create_user_seeds_defaults(self):
        other = self.service.create("小明", "pw123456")
        self.session.commit()
        self.assertIsNotNone(other)
        self.assertIsNone(self.service.create("小明", "x"))
        self.assertEqual([info.name for info in self.service.list_users()], ["默认用户", "小明"])
        self.assertEqual(len(CategoryRepository(self.session).roots(user_id=other.id)), 5)
        self.assertEqual(len(TagRepository(self.session).all(user_id=other.id)), 3)
        self.assertTrue(self.service.verify(other, "pw123456"))
        self.assertFalse(self.service.verify(other, "wrong"))

    def test_rename_rejects_duplicate(self):
        other = self.service.create("小红")
        self.session.commit()
        self.assertTrue(self.service.rename(other, "小红花"))
        self.assertFalse(self.service.rename(other, "默认用户"))
        self.session.commit()
        self.assertEqual(other.name, "小红花")

    def test_set_password_and_verify(self):
        other = self.service.create("小红")
        self.session.commit()
        self.service.set_password(other, "abc12345")
        self.session.commit()
        self.assertTrue(self.service.verify(other, "abc12345"))
        self.assertFalse(self.service.verify(other, "abc"))

    def test_items_are_isolated_per_user(self):
        other = self.service.create("小红")
        self.session.commit()
        self.importer().import_text("我的笔记", "内容")
        self.importer().import_text("她的笔记", "内容", user_id=other.id)
        self.session.commit()
        repo = ItemRepository(self.session)
        self.assertEqual(repo.count(ItemFilter(user_ids={self.user.id})), 1)
        self.assertEqual(repo.count(ItemFilter(user_ids={other.id})), 1)
        self.assertEqual(overview(self.session, user_id=self.user.id)["total"], 1)
        self.assertEqual(overview(self.session, user_id=other.id)["total"], 1)

    def test_set_current_user_is_persisted(self):
        other = self.service.create("小红")
        self.session.commit()
        self.service.set_current(other)
        self.assertEqual(config.currentUserId.value, other.id)
        self.assertEqual(self.service.current_id(), other.id)
        self.service.set_current(self.user)
        self.assertEqual(self.service.current_id(), self.user.id)

    def test_delete_user_moves_data_and_merges_tags(self):
        other = self.service.create("小红")
        self.session.commit()
        item = self.importer().import_text("她的笔记", "内容", user_id=other.id, tags=["重要"])
        self.session.commit()
        self.assertTrue(self.service.delete(other, move_to=self.user))
        self.session.commit()
        self.session.refresh(item)
        self.assertEqual(item.user_id, self.user.id)
        self.assertEqual(item.tag_names, ["重要"])
        # 基础标签是全局标签，删除用户不会带走它们；该用户自己的标签必须全部消失。
        remaining = [
            tag.name
            for tag in TagRepository(self.session).all(user_id=other.id)
            if not tag.is_global
        ]
        self.assertEqual(remaining, [])
        self.assertEqual(
            ItemRepository(self.session).count(ItemFilter(user_ids={self.user.id})), 1,
        )

    def test_delete_empty_user_cleans_directory_and_categories(self):
        other = self.service.create("小红")
        self.session.commit()
        libraries = LibraryService(self.session)
        library = libraries.ensure_default()
        user_dir = Path(library.path) / libraries.dir_name_of(other.name)
        user_dir.mkdir(parents=True, exist_ok=True)
        self.assertTrue(user_dir.is_dir())

        self.assertTrue(self.service.delete(other))
        self.session.commit()

        # 数据为空时目录直接清理，不搬到默认用户名下。
        self.assertFalse(user_dir.exists())
        self.assertFalse((Path(library.path) / self.user.name / other.name).exists())
        # 也不把已删除用户的分类镜像给默认用户。
        roots = {c.name for c in CategoryRepository(self.session).roots(user_id=self.user.id)}
        self.assertEqual(roots, {"学习资料", "工作文档", "图片素材", "影音资料", "未分类"})

    def test_delete_empty_user_keeps_directory_with_leftover_files(self):
        other = self.service.create("小红")
        self.session.commit()
        libraries = LibraryService(self.session)
        library = libraries.ensure_default()
        user_dir = Path(library.path) / libraries.dir_name_of(other.name)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "残留.txt").write_text("surprise", encoding="utf-8")

        self.assertTrue(self.service.delete(other))
        self.session.commit()

        # 目录里还有数据库不认识的文件时保留目录，避免误删用户手工放进去的内容。
        self.assertTrue(user_dir.is_dir())
        self.assertTrue((user_dir / "残留.txt").is_file())

    def test_cannot_delete_last_user(self):
        self.assertFalse(self.service.delete(self.user))

    def test_password_lifecycle(self):
        info = self.service.list_users()[0]
        self.assertFalse(info.protected)
        self.service.set_password(info.user, "s3cret!")
        self.session.commit()
        self.assertTrue(self.service.list_users()[0].protected)
        self.assertTrue(self.service.verify(info.user, "s3cret!"))
        self.assertFalse(self.service.verify(info.user, "wrong"))
        self.service.set_password(info.user, "")
        self.session.commit()
        self.assertFalse(self.service.list_users()[0].protected)
        self.assertTrue(self.service.verify(info.user, "任意"))

    def test_delete_without_target_falls_back_to_default_user(self):
        other = self.service.create("小红")
        self.session.commit()
        item = self.importer().import_text("她的笔记", "内容", user_id=other.id, tags=["私有标签"])
        tag = TagRepository(self.session).by_name("私有标签", user_id=other.id)
        self.assertEqual(tag.created_by, other.id)
        self.session.commit()
        self.assertTrue(self.service.delete(other))
        self.session.commit()
        self.session.refresh(item)
        self.session.refresh(tag)
        self.assertEqual(item.user_id, self.user.id)
        self.assertEqual(tag.user_id, self.user.id)
        # 归属与创建者一并转移，避免创建者指向已不存在的用户。
        self.assertEqual(tag.created_by, self.user.id)

    def test_personal_tag_is_only_visible_to_its_creator(self):
        other = self.service.create("小红")
        self.session.commit()
        tags = TagRepository(self.session)
        tags.ensure("小红的标签", user_id=other.id)
        tags.ensure("全局标签", is_global=True)
        # 历史遗留的无归属标签不应泄漏给任何普通用户。
        self.session.add(Tag(name="遗留标签", user_id=None, is_global=False))
        self.session.commit()
        mine = {tag.name for tag in tags.all(user_id=self.user.id)}
        theirs = {tag.name for tag in tags.all(user_id=other.id)}
        self.assertIn("全局标签", theirs)
        self.assertIn("小红的标签", theirs)
        self.assertNotIn("小红的标签", mine)
        self.assertNotIn("遗留标签", mine)
        # 默认用户（管理员）传 None 表示不限制，可以看到全部标签。
        self.assertIn("遗留标签", {tag.name for tag in tags.all()})

    def test_duplicate_map_follows_scope(self):
        other = self.service.create("小红")
        self.session.commit()
        self.importer().import_text("我的笔记", "完全相同的内容")
        self.importer().import_text("她的笔记", "完全相同的内容", user_id=other.id)
        self.session.commit()
        service = ItemService(self.session)
        self.assertEqual(len(service.duplicate_map()), 1)
        self.assertEqual(service.duplicate_map(self.user.id), {})
        self.assertEqual(service.duplicate_map(other.id), {})


class HashCase(IsolatedCase):
    def test_hash_is_salted_and_verifiable(self):
        first = hash_password("same-password")
        second = hash_password("same-password")
        self.assertNotEqual(first, second)
        payload = json.loads(first)
        self.assertEqual(payload["iter"], ITERATIONS)
        self.assertTrue(verify_hash(first, "same-password"))
        self.assertFalse(verify_hash(first, "other"))
        self.assertFalse(verify_hash("not-json", "x"))
        self.assertTrue(verify_hash("", "任意"))
