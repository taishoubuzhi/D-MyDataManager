"""分类与标签的维护逻辑。"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Category, DataItem, Tag
from ..repositories import CategoryRepository, TagRepository


@dataclass
class CategoryNode:
    category: Category
    depth: int
    item_count: int
    total_count: int


class TaxonomyService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)

    # ---------------------------------------------------------------- 分类
    def tree(self, include_hidden: bool = True, user_id: int | None = None) -> list[CategoryNode]:
        counts = self.categories.item_counts(user_id=user_id)
        nodes: list[CategoryNode] = []
        queue: list[tuple[Category, int]] = [
            (category, 0)
            for category in self.categories.roots(include_hidden=include_hidden, user_id=user_id)
        ]
        while queue:
            category, depth = queue.pop(0)
            own = counts.get(category.id, 0)
            total = own + sum(self._subtree_count(child, counts) for child in category.children)
            nodes.append(CategoryNode(category, depth, own, total))
            children = [c for c in category.children if include_hidden or not c.is_hidden]
            queue.extend(
                (child, depth + 1) for child in sorted(children, key=lambda c: (c.sort_order, c.name))
            )
        return nodes

    def _subtree_count(self, category: Category, counts: dict[int, int]) -> int:
        return counts.get(category.id, 0) + sum(
            self._subtree_count(child, counts) for child in category.children
        )

    def create_category(
        self,
        name: str,
        parent_id: int | None = None,
        description: str = "",
        icon: str = "",
        color: str = "",
        user_id: int | None = None,
    ) -> Category | None:
        name = (name or "").strip()
        if not name:
            return None
        if self.categories.by_name(name, parent_id, user_id) is not None:
            logger.warning("同级下已存在分类：{}", name)
            return None
        return self.categories.create(name, parent_id, description, icon, color, user_id)

    def update_category(self, category: Category, **fields) -> Category:
        for key, value in fields.items():
            if hasattr(category, key):
                setattr(category, key, value)
        self.session.flush()
        return category

    def move_category(self, category: Category, parent_id: int | None) -> bool:
        return self.categories.move(category, parent_id)

    def delete_category(
        self, category: Category, move_items_to: int | None = None, recursive: bool = False
    ) -> int:
        """删除分类；子分类默认上移到父级，recursive 时一并删除。"""
        for child in self.categories.children_of(category.id):
            if recursive:
                self.delete_category(child, move_items_to, recursive=True)
            else:
                child.parent_id = category.parent_id
        items = list(self.session.scalars(select(DataItem).where(DataItem.category_id == category.id)))
        for item in items:
            item.category_id = move_items_to
        # 先把子分类的新父级落库，再用 Core DELETE 删本行：ORM 的 delete-orphan 级联会把
        # 刚上移的子分类连同其数据项一起删掉。
        self.session.flush()
        self.session.expunge(category)
        self.session.execute(delete(Category).where(Category.id == category.id))
        return len(items)

    def path_of(self, category: Category | None) -> str:
        return self.categories.path_of(category)

    # ---------------------------------------------------------------- 标签
    def create_tag(
        self,
        name: str,
        description: str = "",
        color: str = "",
        user_id: int | None = None,
        is_global: bool | None = None,
    ) -> Tag | None:
        """新建标签；同名可见标签（含全局）已存在时拒绝，保证全局标签不重复。"""
        name = (name or "").strip()
        if not name or self.tags.by_name(name, user_id=user_id) is not None:
            logger.warning("标签已存在（含全局标签）：{}", name)
            return None
        return self.tags.ensure(name, description, color, user_id=user_id, is_global=is_global)

    def update_tag(self, tag: Tag, **fields) -> Tag:
        for key, value in fields.items():
            if hasattr(tag, key):
                setattr(tag, key, value)
        self.session.flush()
        return tag

    def rename_tag(self, tag: Tag, new_name: str, user_id: int | None = None) -> bool:
        new_name = (new_name or "").strip()
        if not new_name or new_name == tag.name:
            return False
        if self.tags.by_name(new_name, user_id=user_id) is not None:
            logger.warning("标签已存在：{}", new_name)
            return False
        self.tags.rename(tag, new_name)
        return True

    def set_tag_global(self, tag: Tag, is_global: bool, user_id: int | None = None) -> bool:
        """在全局标签与个人标签之间切换；只有创建者可以操作，且不得与已有全局标签重名。"""
        if not self.tags.is_creator(tag, user_id):
            logger.warning("只有创建者可以切换标签归属：{}", tag.name)
            return False
        if not self.tags.set_global(tag, is_global):
            logger.warning("已存在同名全局标签：{}", tag.name)
            return False
        return True

    def delete_tag(self, tag: Tag) -> int:
        count = self.tags.item_count(tag)
        for item in list(tag.items):
            item.tags.remove(tag)
        self.session.delete(tag)
        self.session.flush()
        return count

    def merge_tags(self, source: Tag, target: Tag) -> Tag:
        return self.tags.merge(source, target)

    def cleanup_unused(self, user_id: int | None = None) -> int:
        """清理未使用标签；他人创建的全局标签保留。"""
        tags = [
            tag
            for tag in self.tags.unused(user_id)
            if not tag.is_global or self.tags.is_creator(tag, user_id)
        ]
        for tag in tags:
            self.session.delete(tag)
        self.session.flush()
        logger.info("已清理 {} 个未使用标签", len(tags))
        return len(tags)

    def usage(self, user_id: int | None = None) -> dict[str, int]:
        return self.tags.usage_counts(user_id)
