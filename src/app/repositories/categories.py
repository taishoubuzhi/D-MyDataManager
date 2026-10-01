"""分类仓储：分类是一棵可嵌套的树。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..db.models import Category, DataItem
from .base import Repository


class CategoryRepository(Repository[Category]):
    model = Category

    def roots(self, include_hidden: bool = True, user_id: int | None = None) -> list[Category]:
        stmt = (
            select(Category)
            .where(Category.parent_id.is_(None))
            .options(selectinload(Category.children))
            .order_by(Category.sort_order, Category.name)
        )
        if not include_hidden:
            stmt = stmt.where(Category.is_hidden.is_(False))
        if user_id is not None:
            stmt = stmt.where(Category.user_id == user_id)
        return list(self.session.scalars(stmt))

    def children_of(self, parent_id: int | None) -> list[Category]:
        stmt = (
            select(Category)
            .where(Category.parent_id.is_(parent_id) if parent_id is None else Category.parent_id == parent_id)
            .order_by(Category.sort_order, Category.name)
        )
        return list(self.session.scalars(stmt))

    def by_name(
        self, name: str, parent_id: int | None = None, user_id: int | None = None
    ) -> Category | None:
        stmt = select(Category).where(Category.name == name)
        stmt = stmt.where(Category.parent_id.is_(None)) if parent_id is None else stmt.where(Category.parent_id == parent_id)
        if user_id is not None:
            stmt = stmt.where(Category.user_id == user_id)
        return self.session.scalars(stmt).first()

    def unique_sibling_name(
        self, name: str, parent_id: int | None = None, user_id: int | None = None
    ) -> str:
        """同级不允许重名：name 已被占用时依次尝试 name-1、name-2…"""
        name = (name or "").strip()
        if not name or self.by_name(name, parent_id, user_id) is None:
            return name
        index = 1
        while True:
            candidate = f"{name}-{index}"
            if self.by_name(candidate, parent_id, user_id) is None:
                return candidate
            index += 1

    def create(
        self,
        name: str,
        parent_id: int | None = None,
        description: str = "",
        icon: str = "",
        color: str = "",
        user_id: int | None = None,
        is_hidden: bool = False,
    ) -> Category:
        category = Category(
            name=name.strip(),
            parent_id=parent_id,
            description=description,
            icon=icon,
            color=color,
            user_id=user_id,
            is_hidden=is_hidden,
        )
        if parent_id is not None:
            # 在 flush 之前挂到已加载的 children 集合上，否则同一会话里的分类树不会包含新分类。
            parent = self.session.get(Category, parent_id)
            if parent is not None:
                parent.children.append(category)
        return self.add(category)

    def ensure(self, name: str, parent_id: int | None = None, user_id: int | None = None) -> Category:
        category = self.by_name(name, parent_id, user_id)
        return category if category is not None else self.create(name, parent_id, user_id=user_id)

    def path_of(self, category: Category | None) -> str:
        if category is None:
            return ""
        parts, node, guard = [], category, set()
        while node is not None and node.id not in guard:
            guard.add(node.id)
            parts.append(node.name)
            node = node.parent
        return " / ".join(reversed(parts))

    def descendants(self, category: Category) -> list[Category]:
        result: list[Category] = []
        stack = list(category.children)
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(node.children)
        return result

    def move(self, category: Category, new_parent_id: int | None) -> bool:
        """把分类挂到新父节点下；不允许移动到自身或自己的子孙下。"""
        if new_parent_id == category.id:
            return False
        if new_parent_id is not None and any(c.id == new_parent_id for c in self.descendants(category)):
            return False
        category.parent_id = new_parent_id
        self.session.flush()
        return True

    def item_counts(self, user_id: int | None = None) -> dict[int, int]:
        stmt = (
            select(DataItem.category_id, func.count(DataItem.id))
            .where(DataItem.is_deleted.is_(False), DataItem.category_id.isnot(None))
            .group_by(DataItem.category_id)
        )
        if user_id is not None:
            stmt = stmt.where(DataItem.user_id == user_id)
        return {category_id: count for category_id, count in self.session.execute(stmt).all()}
