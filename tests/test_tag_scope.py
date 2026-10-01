"""标签归属测试：全局标签对所有用户可见，个人标签只属于创建者且不得与全局重名。"""

from __future__ import annotations

from tests.harness import IsolatedCase


class TagScopeCase(IsolatedCase):
    def _service(self):
        from app.services import TaxonomyService

        return TaxonomyService(self.session)

    def _users(self):
        from app.services import UserService

        return UserService(self.session)

    def _extra_user(self, name: str):
        users = self._users()
        owner = users.current()
        user = users.create(name, "")
        self.assertIsNotNone(user)
        users.set_current(owner)
        self.session.commit()
        return user

    def test_global_tag_visible_to_all_users(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()

        tag = taxonomy.create_tag("全局收藏", user_id=owner.id, is_global=True)
        self.session.commit()
        self.assertIsNotNone(tag)
        self.assertTrue(tag.is_global)
        self.assertIsNone(tag.user_id)
        self.assertEqual(tag.created_by, owner.id)

        self.assertIn("全局收藏", taxonomy.tags.names(user_id=owner.id))
        self.assertIn("全局收藏", taxonomy.tags.names(user_id=other.id))

    def test_personal_tag_cannot_duplicate_global_name(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()
        taxonomy.create_tag("共享标签", user_id=owner.id, is_global=True)
        self.session.commit()

        self.assertIsNone(taxonomy.create_tag("共享标签", user_id=other.id))
        self.assertIsNone(taxonomy.create_tag("共享标签", user_id=other.id, is_global=True))

    def test_personal_tag_is_invisible_to_other_users(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()

        tag = taxonomy.create_tag("私人标签", user_id=owner.id)
        self.session.commit()
        self.assertFalse(tag.is_global)
        self.assertEqual(tag.user_id, owner.id)
        self.assertIn("私人标签", taxonomy.tags.names(user_id=owner.id))
        self.assertNotIn("私人标签", taxonomy.tags.names(user_id=other.id))
        # 其他用户也不受重名限制，可以建自己的同名个人标签
        self.assertIsNotNone(taxonomy.create_tag("私人标签", user_id=other.id))
        self.session.commit()

    def test_only_creator_can_switch_scope(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()

        tag = taxonomy.create_tag("照片", user_id=owner.id)
        self.session.commit()
        self.assertFalse(taxonomy.set_tag_global(tag, True, user_id=other.id))
        self.assertFalse(tag.is_global)

        self.assertTrue(taxonomy.set_tag_global(tag, True, user_id=owner.id))
        self.session.commit()
        self.assertTrue(tag.is_global)
        self.assertIn("照片", taxonomy.tags.names(user_id=other.id))

        self.assertTrue(taxonomy.set_tag_global(tag, False, user_id=owner.id))
        self.session.commit()
        self.assertFalse(tag.is_global)
        self.assertEqual(tag.user_id, owner.id)
        self.assertNotIn("照片", taxonomy.tags.names(user_id=other.id))

    def test_switching_to_global_rejects_duplicate_name(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()
        mine = taxonomy.create_tag("年度归档", user_id=owner.id)
        personal = taxonomy.create_tag("年度归档", user_id=other.id)
        self.assertIsNotNone(personal)
        self.session.commit()

        self.assertTrue(taxonomy.set_tag_global(mine, True, user_id=owner.id))
        self.session.commit()
        self.assertFalse(taxonomy.set_tag_global(personal, True, user_id=other.id))
        self.assertFalse(personal.is_global)

    def test_default_tags_are_global_and_imported_tags_are_personal(self):
        owner = self._users().current()
        other = self._extra_user("另一个用户")
        seeded = {tag.name: tag for tag in self._service().tags.all(user_id=owner.id)}
        for name in ("重要", "待整理", "收藏"):
            # 基础标签是全局标签：开箱即用，所有用户可见。
            self.assertTrue(seeded[name].is_global)
            self.assertIsNone(seeded[name].user_id)
            self.assertEqual(seeded[name].created_by, owner.id)
            self.assertIn(name, self._service().tags.names(user_id=other.id))

        item = self.importer().import_text("笔记", "内容", tags=["学习", "课题"])
        self.session.commit()
        tags = {tag.name: tag for tag in item.tags}
        self.assertEqual(set(tags), {"学习", "课题"})
        for tag in tags.values():
            self.assertFalse(tag.is_global)
            self.assertEqual(tag.user_id, owner.id)
            self.assertEqual(tag.created_by, owner.id)

    def test_cleanup_unused_keeps_foreign_global_tags(self):
        users = self._users()
        owner = users.current()
        other = self._extra_user("另一个用户")
        taxonomy = self._service()
        taxonomy.create_tag("全局闲置", user_id=owner.id, is_global=True)
        taxonomy.create_tag("我的闲置", user_id=owner.id)
        self.session.commit()

        taxonomy.cleanup_unused(user_id=other.id)
        self.session.commit()
        self.assertIn("全局闲置", taxonomy.tags.global_names())
        self.assertIn("我的闲置", taxonomy.tags.names(user_id=owner.id))

        # 创建者可以清掉自己创建的闲置全局标签；被别人引用时不会被清理
        removed = taxonomy.cleanup_unused(user_id=owner.id)
        self.session.commit()
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("全局闲置", taxonomy.tags.global_names())
        self.assertNotIn("我的闲置", taxonomy.tags.names(user_id=owner.id))
