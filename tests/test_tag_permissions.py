"""标签权限：默认用户（管理员）可管理任意标签，其他用户只能管理自己创建的标签。"""

from __future__ import annotations

from app.services import TaxonomyService, UserService

from tests.harness import IsolatedCase


class TagPermissionCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.users = UserService(self.session)
        self.taxonomy = TaxonomyService(self.session)
        self.admin = self.users.default()
        self.member = self.users.create("权限用例成员", "")
        self.session.flush()
        assert self.admin is not None and self.member is not None
        self.admin_id = int(self.admin.id)
        self.member_id = int(self.member.id)
        self.own = self.taxonomy.create_tag("成员标签", user_id=self.member_id)
        self.other = self.taxonomy.create_tag("管理员标签", user_id=self.admin_id)
        self.session.flush()
        assert self.own is not None and self.other is not None

    def test_member_can_manage_own_tag(self) -> None:
        repo = self.taxonomy.tags
        self.assertTrue(repo.can_manage(self.own, self.member_id, False))
        self.assertTrue(self.taxonomy.rename_tag(self.own, "成员标签改", user_id=self.member_id))
        self.assertEqual(self.own.name, "成员标签改")
        self.assertTrue(self.taxonomy.set_tag_global(self.own, True, user_id=self.member_id))
        self.assertTrue(self.own.is_global)
        self.assertGreaterEqual(self.taxonomy.delete_tag(self.own, user_id=self.member_id), 0)
        self.assertIsNone(repo.by_name("成员标签改", user_id=None))

    def test_member_cannot_touch_other_tag(self) -> None:
        repo = self.taxonomy.tags
        self.assertFalse(repo.can_manage(self.other, self.member_id, False))
        self.assertFalse(self.taxonomy.rename_tag(self.other, "越权改名", user_id=self.member_id))
        self.assertEqual(self.other.name, "管理员标签")
        self.assertFalse(self.taxonomy.set_tag_global(self.other, True, user_id=self.member_id))
        self.assertFalse(self.other.is_global)
        self.assertEqual(self.taxonomy.delete_tag(self.other, user_id=self.member_id), -1)
        self.assertIsNotNone(repo.by_name("管理员标签", user_id=None))

    def test_admin_can_manage_any_tag(self) -> None:
        repo = self.taxonomy.tags
        self.assertTrue(repo.can_manage(self.other, self.admin_id, True))
        self.assertTrue(
            self.taxonomy.rename_tag(
                self.other, "管理员标签改", user_id=self.admin_id, is_admin=True
            )
        )
        self.assertEqual(self.other.name, "管理员标签改")
        self.assertTrue(
            self.taxonomy.set_tag_global(
                self.own, True, user_id=self.admin_id, is_admin=True
            )
        )
        self.assertTrue(self.own.is_global)
        self.assertGreaterEqual(
            self.taxonomy.delete_tag(self.other, user_id=self.admin_id, is_admin=True), 0
        )
        self.assertIsNone(repo.by_name("管理员标签改", user_id=None))

    def test_cleanup_keeps_others_tags_for_member(self) -> None:
        shared = self.taxonomy.create_tag("他人全局标签", user_id=self.admin_id, is_global=True)
        self.session.flush()
        assert shared is not None
        self.taxonomy.cleanup_unused(user_id=self.member_id)
        self.assertIsNotNone(self.taxonomy.tags.by_name("他人全局标签", user_id=None))
        self.taxonomy.cleanup_unused(user_id=self.admin_id, is_admin=True)
        self.assertIsNone(self.taxonomy.tags.by_name("他人全局标签", user_id=None))
