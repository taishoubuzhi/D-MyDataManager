"""标签重名：个人标签不得与全局标签同名，重名时并入全局标签（含启动修复）。"""

from __future__ import annotations

from sqlalchemy import select

from app.db import database
from app.db.models import Tag
from app.repositories.items import ItemRepository
from app.services import TaxonomyService, UserService

from tests.harness import IsolatedCase


class TagShadowCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.users = UserService(self.session)
        self.taxonomy = TaxonomyService(self.session)
        self.items = ItemRepository(self.session)
        self.admin = self.users.default()
        self.member = self.users.create("影子用例成员", "")
        self.session.flush()
        assert self.admin is not None and self.member is not None
        self.admin_id = int(self.admin.id)
        self.member_id = int(self.member.id)

    def tags_named(self, name: str) -> list[Tag]:
        return list(self.session.scalars(select(Tag).where(Tag.name == name)).all())

    def global_tag(self, name: str) -> Tag:
        tag = self.session.scalars(
            select(Tag).where(Tag.name == name, Tag.is_global.is_(True))
        ).first()
        assert tag is not None
        return tag

    def test_new_global_merges_same_name_personal(self) -> None:
        personal = self.taxonomy.create_tag("重名标签", user_id=self.member_id)
        assert personal is not None
        item = self.items.create(name="影子数据", user_id=self.member_id)
        item.tags.append(personal)
        self.session.flush()
        merged = self.taxonomy.create_tag("重名标签", user_id=self.admin_id, is_global=True)
        self.session.flush()
        assert merged is not None
        self.assertTrue(merged.is_global)
        self.assertIsNone(self.session.get(Tag, personal.id))
        self.assertEqual([tag.id for tag in self.tags_named("重名标签")], [merged.id])
        self.assertIn(merged, item.tags)

    def test_personal_tag_cannot_duplicate_global(self) -> None:
        self.assertIsNone(self.taxonomy.create_tag("重要", user_id=self.member_id))
        self.assertEqual(len(self.tags_named("重要")), 1)

    def test_seed_merges_personal_before_creating_global(self) -> None:
        self.session.delete(self.global_tag("重要"))
        self.session.flush()
        personal = self.taxonomy.create_tag("重要", user_id=self.member_id)
        assert personal is not None
        item = self.items.create(name="种子数据", user_id=self.member_id)
        item.tags.append(personal)
        self.session.flush()
        self.users.create("影子用例新用户", "")
        self.session.flush()
        tags = self.tags_named("重要")
        self.assertEqual(len(tags), 1)
        self.assertTrue(tags[0].is_global)
        self.assertIsNone(self.session.get(Tag, personal.id))
        self.assertIn(tags[0], item.tags)

    def test_startup_repair_merges_shadow_tags(self) -> None:
        global_tag = self.global_tag("收藏")
        shadow = Tag(
            name="收藏", user_id=self.member_id, created_by=self.member_id, is_global=False
        )
        self.session.add(shadow)
        self.session.flush()
        item = self.items.create(name="修复数据", user_id=self.member_id)
        item.tags.append(shadow)
        self.session.commit()
        with database.get_engine().begin() as connection:
            database._merge_shadow_tags(connection)
        self.session.expire_all()
        self.assertEqual([tag.id for tag in self.tags_named("收藏")], [global_tag.id])
        self.assertIn(global_tag, item.tags)
