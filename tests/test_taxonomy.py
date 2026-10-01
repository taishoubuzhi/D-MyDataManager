import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.seed import UNCATEGORIZED_NAME
from app.repositories import CategoryRepository, ItemFilter, ItemRepository, TagRepository
from app.services import TaxonomyService, is_uncategorized
from tests.harness import IsolatedCase


class CategoryCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.taxonomy = TaxonomyService(self.session)
        self.user = self.current_user()

    def _node(self, name, depth=None):
        for node in self.taxonomy.tree(user_id=self.user.id):
            if node.category.name == name and (depth is None or node.depth == depth):
                return node
        raise AssertionError(f"未找到分类节点：{name}")

    def test_seeded_root_categories(self):
        roots = [node for node in self.taxonomy.tree(user_id=self.user.id) if node.depth == 0]
        self.assertEqual(
            {node.category.name for node in roots},
            {"学习资料", "工作文档", "图片素材", "影音资料", "未分类"},
        )

    def test_duplicate_sibling_is_rejected(self):
        self.assertIsNotNone(self.taxonomy.create_category("数学", user_id=self.user.id))
        self.assertIsNone(self.taxonomy.create_category("数学", user_id=self.user.id))
        parent = self._node("学习资料").category
        self.assertIsNotNone(
            self.taxonomy.create_category("数学", parent_id=parent.id, user_id=self.user.id)
        )
        self.assertIsNone(
            self.taxonomy.create_category("数学", parent_id=parent.id, user_id=self.user.id)
        )

    def test_tree_depth_and_counts(self):
        parent = self._node("学习资料").category
        child = self.taxonomy.create_category("线性代数", parent_id=parent.id, user_id=self.user.id)
        self.importer().import_text("笔记", "内容", category_id=child.id)
        self.session.commit()
        self.assertEqual(self._node("学习资料").item_count, 0)
        self.assertEqual(self._node("学习资料").total_count, 1)
        self.assertEqual(self._node("线性代数").depth, 1)
        self.assertEqual(self._node("线性代数").item_count, 1)

    def test_path_of_contains_chain(self):
        parent = self._node("学习资料").category
        child = self.taxonomy.create_category("线性代数", parent_id=parent.id, user_id=self.user.id)
        path = self.taxonomy.path_of(child)
        self.assertIn("学习资料", path)
        self.assertIn("线性代数", path)

    def test_move_category_rejects_cycles(self):
        first = self.taxonomy.create_category("甲", user_id=self.user.id)
        second = self.taxonomy.create_category("乙", user_id=self.user.id)
        third = self.taxonomy.create_category("丙", parent_id=second.id, user_id=self.user.id)
        self.assertTrue(self.taxonomy.move_category(third, first.id))
        self.assertFalse(self.taxonomy.move_category(first, third.id))
        self.assertTrue(self.taxonomy.move_category(third, None))
        self.session.commit()
        self.assertIsNone(third.parent_id)

    def test_delete_category_moves_children_and_items(self):
        parent = self.taxonomy.create_category("临时", user_id=self.user.id)
        child = self.taxonomy.create_category("子", parent_id=parent.id, user_id=self.user.id)
        self.importer().import_text("笔记一", "内容一", category_id=child.id)
        self.importer().import_text("笔记二", "内容二", category_id=parent.id)
        self.session.commit()
        moved = self.taxonomy.delete_category(parent, recursive=False)
        self.session.commit()
        # 返回值只统计被删除分类直属的数据项；子分类上移时其数据项随分类保留。
        self.assertEqual(moved, 1)
        self.assertIsNone(child.parent_id)
        self.assertEqual(
            ItemRepository(self.session).count(ItemFilter(category_ids={child.id})), 1,
        )
        self.assertEqual(
            ItemRepository(self.session).count(ItemFilter(category_ids={parent.id})), 0,
        )

    def test_delete_category_recursively(self):
        parent = self.taxonomy.create_category("临时", user_id=self.user.id)
        child = self.taxonomy.create_category("子", parent_id=parent.id, user_id=self.user.id)
        parent_id, child_id = parent.id, child.id
        self.taxonomy.delete_category(parent, recursive=True)
        self.session.commit()
        self.assertIsNone(self.session.get(type(parent), parent_id))
        self.assertIsNone(self.session.get(type(parent), child_id))

    def test_delete_category_moves_items_to_target(self):
        source = self.taxonomy.create_category("来源", user_id=self.user.id)
        target = self.taxonomy.create_category("去处", user_id=self.user.id)
        self.importer().import_text("笔记", "内容", category_id=source.id)
        self.session.commit()
        self.assertEqual(self.taxonomy.delete_category(source, move_items_to=target.id), 1)
        self.session.commit()
        rows = ItemRepository(self.session).query(ItemFilter(category_ids={target.id}))
        self.assertEqual(len(rows), 1)

    def test_rename_category_rejects_duplicate_sibling(self):
        first = self.taxonomy.create_category("甲", user_id=self.user.id)
        second = self.taxonomy.create_category("乙", user_id=self.user.id)
        self.session.commit()
        self.assertFalse(self.taxonomy.rename_category(second, "甲"))
        self.assertFalse(self.taxonomy.rename_category(second, "   "))
        self.assertFalse(self.taxonomy.rename_category(second, "乙"))
        self.assertEqual(second.name, "乙")
        self.assertTrue(self.taxonomy.rename_category(second, "丙"))
        self.session.commit()
        self.assertEqual(second.name, "丙")
        self.assertEqual(first.name, "甲")

    def test_delete_category_renames_conflicting_children(self):
        parent = self.taxonomy.create_category("父", user_id=self.user.id)
        sibling = self.taxonomy.create_category("子", user_id=self.user.id)
        child = self.taxonomy.create_category("子", parent_id=parent.id, user_id=self.user.id)
        self.session.commit()
        self.assertEqual([conflict.id for conflict in self.taxonomy.promotion_conflicts(parent)], [child.id])

        self.taxonomy.delete_category(parent)
        self.session.commit()
        self.assertIsNone(child.parent_id)
        # 子分类上移后与真实的同级分类重名，自动加编号后缀而不是撞名。
        self.assertEqual(child.name, "子-1")
        self.assertEqual(sibling.name, "子")
        roots = [c.name for c in CategoryRepository(self.session).roots(user_id=self.user.id)]
        self.assertEqual(roots.count("子"), 1)

    def test_delete_category_applies_explicit_renames(self):
        parent = self.taxonomy.create_category("父", user_id=self.user.id)
        self.taxonomy.create_category("子", user_id=self.user.id)
        child = self.taxonomy.create_category("子", parent_id=parent.id, user_id=self.user.id)
        self.session.commit()
        self.taxonomy.delete_category(parent, renames={child.id: "拷贝"})
        self.session.commit()
        self.assertEqual(child.name, "拷贝")
        self.assertIsNone(child.parent_id)

    def test_uncategorized_category_is_created_on_demand(self):
        category = self.taxonomy.uncategorized_category(user_id=self.user.id)
        self.assertEqual(category.name, UNCATEGORIZED_NAME)
        self.assertIsNone(category.parent_id)
        self.assertEqual(
            self.taxonomy.uncategorized_category(user_id=self.user.id, create=False).id, category.id,
        )
        self.assertEqual(len(CategoryRepository(self.session).roots(user_id=self.user.id)), 5)

    def test_uncategorized_is_fixed_and_sorted_last(self):
        self.taxonomy.create_category("甲", user_id=self.user.id)
        self.session.commit()
        category = self.taxonomy.uncategorized_category(user_id=self.user.id)
        self.assertTrue(is_uncategorized(category))
        self.assertFalse(is_uncategorized(None))
        self.assertFalse(is_uncategorized(self._node("学习资料").category))
        roots = [node for node in self.taxonomy.tree(user_id=self.user.id) if node.depth == 0]
        # 「未分类」固定排在最后，其余根分类保持在它前面。
        self.assertEqual(roots[-1].category.id, category.id)
        self.assertNotIn(category.id, [node.category.id for node in roots[:-1]])

    def test_uncategorized_cannot_be_renamed_or_deleted(self):
        category = self.taxonomy.uncategorized_category(user_id=self.user.id)
        self.session.commit()
        self.assertFalse(self.taxonomy.rename_category(category, "杂物"))
        self.assertEqual(category.name, UNCATEGORIZED_NAME)
        self.assertEqual(self.taxonomy.delete_category(category), 0)
        self.session.commit()
        self.assertIsNotNone(self.session.get(type(category), category.id))

    def test_uncategorized_rejects_child_categories(self):
        category = self.taxonomy.uncategorized_category(user_id=self.user.id)
        other = self.taxonomy.create_category("甲", user_id=self.user.id)
        self.session.commit()
        self.assertIsNone(
            self.taxonomy.create_category("子", parent_id=category.id, user_id=self.user.id)
        )
        self.assertFalse(self.taxonomy.move_category(other, category.id))
        self.assertFalse(self.taxonomy.move_category(category, other.id))
        self.session.commit()
        self.assertIsNone(other.parent_id)
        self.assertIsNone(category.parent_id)


class TagCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.taxonomy = TaxonomyService(self.session)
        self.tags = TagRepository(self.session)
        self.user = self.current_user()

    def test_create_and_reject_duplicate(self):
        tag = self.taxonomy.create_tag("算法", description="算法相关", user_id=self.user.id)
        self.assertIsNotNone(tag)
        self.assertIsNone(self.taxonomy.create_tag("算法", user_id=self.user.id))
        self.assertEqual(len(self.tags.all(user_id=self.user.id)), 4)

    def test_rename_conflict(self):
        tag = self.taxonomy.create_tag("算法", user_id=self.user.id)
        self.assertFalse(self.taxonomy.rename_tag(tag, "重要", user_id=self.user.id))
        self.assertTrue(self.taxonomy.rename_tag(tag, "算法二", user_id=self.user.id))
        self.session.commit()
        self.assertEqual(tag.name, "算法二")

    def test_merge_tags_moves_items(self):
        source = self.taxonomy.create_tag("待合并", user_id=self.user.id)
        target = self.taxonomy.create_tag("目标", user_id=self.user.id)
        item = self.importer().import_text("笔记", "内容", tags=["待合并"])
        self.session.commit()
        self.taxonomy.merge_tags(source, target)
        self.session.commit()
        self.session.expire_all()  # 合并是数据库层删标签，会话里已加载的标签集合需要重新读取。
        self.assertEqual(item.tag_names, ["目标"])
        self.assertIsNone(self.session.get(type(source), source.id))

    def test_cleanup_unused_removes_idle_tags(self):
        self.taxonomy.create_tag("没人用", user_id=self.user.id)
        self.session.commit()
        removed = self.taxonomy.cleanup_unused(user_id=self.user.id)
        self.session.commit()
        self.assertGreaterEqual(removed, 4)
        item = self.importer().import_text("笔记", "内容", tags=["重要"])
        self.session.commit()
        self.assertEqual(item.tag_names, ["重要"])

    def test_delete_tag_removes_links(self):
        item = self.importer().import_text("笔记", "内容", tags=["重要"])
        self.session.commit()
        tag = self.tags.by_name("重要", user_id=self.user.id)
        self.taxonomy.delete_tag(tag)
        self.session.commit()
        self.assertEqual(item.tag_names, [])

    def test_distinct_keywords(self):
        self.importer().import_text("笔记一", "内容", keywords=["算法", "数学"])
        self.importer().import_text("笔记二", "内容", keywords=["数学", "统计"])
        self.session.commit()
        self.assertEqual(sorted(self.tags.distinct_keywords()), ["数学", "算法", "统计"])
