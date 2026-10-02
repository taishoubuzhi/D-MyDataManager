"""标签仓储。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_, select

from ..db.models import DataItem, Tag, item_tags
from .base import Repository


class TagRepository(Repository[Tag]):
    """标签仓储。

    标签分全局与个人两种：全局标签（`is_global`）所有用户可见，个人标签只对创建者可见；
    传入 user_id 时按此规则过滤，传 None 表示不限制（管理员视角）。
    """

    model = Tag

    @staticmethod
    def _scope(stmt, user_id: int | None):
        if user_id is None:
            return stmt
        return stmt.where(
            or_(Tag.is_global.is_(True), Tag.user_id == user_id, Tag.created_by == user_id)
        )

    def all(self, user_id: int | None = None) -> list[Tag]:
        return list(self.session.scalars(self._scope(select(Tag).order_by(Tag.name), user_id)))

    def by_name(self, name: str, user_id: int | None = None) -> Tag | None:
        """按名称取可见标签；同名时优先返回全局标签（个人标签不得与全局重名）。"""
        stmt = self._scope(select(Tag).where(Tag.name == name), user_id)
        return self.session.scalars(stmt.order_by(Tag.is_global.desc(), Tag.id)).first()

    def search(self, text: str = "", user_id: int | None = None) -> list[Tag]:
        stmt = select(Tag).order_by(Tag.name)
        if text:
            stmt = stmt.where(Tag.name.like(f"%{text}%"))
        return list(self.session.scalars(self._scope(stmt, user_id)))

    def names(self, user_id: int | None = None) -> list[str]:
        """可见标签名（按名称排序并去重：同名个人标签不得重复出现在选择列表里）。"""
        seen: dict[str, None] = {}
        for tag in self.all(user_id):
            seen.setdefault(tag.name, None)
        return list(seen)

    def global_names(self) -> list[str]:
        stmt = select(Tag).where(Tag.is_global.is_(True)).order_by(Tag.name)
        return [tag.name for tag in self.session.scalars(stmt)]

    def is_creator(self, tag: Tag, user_id: int | None) -> bool:
        """是否为标签的创建者（历史遗留标签按 user_id 判定）。"""
        if user_id is None:
            return True
        return tag.created_by == user_id or (tag.created_by is None and tag.user_id == user_id)

    def can_manage(self, tag: Tag, user_id: int | None, is_admin: bool = False) -> bool:
        """默认用户（管理员）可以管理任意标签，其他用户只能管理自己创建的标签。"""
        return bool(is_admin) or self.is_creator(tag, user_id)

    def set_global(self, tag: Tag, is_global: bool) -> bool:
        """切换全局 / 个人归属；转为全局前检查是否已有同名全局标签。"""
        if bool(tag.is_global) == bool(is_global):
            return True
        if is_global:
            conflict = self.session.scalars(
                select(Tag).where(Tag.is_global.is_(True), Tag.name == tag.name, Tag.id != tag.id)
            ).first()
            if conflict is not None:
                return False
            tag.user_id = None
        else:
            # 转回个人标签要挂在创建者名下，否则 user_id 为空会被当成全局标签。
            tag.user_id = tag.created_by or tag.user_id
        tag.is_global = bool(is_global)
        self.session.flush()
        return True

    def ensure(
        self,
        name: str,
        description: str = "",
        color: str = "",
        user_id: int | None = None,
        is_global: bool | None = None,
    ) -> Tag:
        """按名称取可见标签；不存在时新建（is_global 缺省为「无归属用户即全局」）。"""
        name = name.strip()
        tag = self.by_name(name, user_id=user_id)
        if tag is None:
            global_flag = user_id is None if is_global is None else bool(is_global)
            tag = self.add(
                Tag(
                    name=name,
                    description=description,
                    color=color,
                    user_id=None if global_flag else user_id,
                    is_global=global_flag,
                    created_by=user_id,
                )
            )
        return tag

    def ensure_many(self, names: Iterable[str], user_id: int | None = None) -> list[Tag]:
        result: list[Tag] = []
        seen: set[str] = set()
        for raw in names:
            name = raw.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(self.ensure(name, user_id=user_id))
        return result

    def rename(self, tag: Tag, new_name: str, user_id: int | None = None) -> Tag:
        new_name = new_name.strip()
        if new_name and new_name != tag.name:
            tag.name = new_name
            self.session.flush()
        return tag

    def merge(self, source: Tag, target: Tag) -> Tag:
        """把 source 的引用全部转到 target，然后删除 source。"""
        for item in list(source.items):
            if target not in item.tags:
                item.tags.append(target)
            if source in item.tags:
                item.tags.remove(source)
        self.session.flush()
        self.delete(source)
        return target

    def usage_counts(self, user_id: int | None = None) -> dict[str, int]:
        stmt = (
            select(Tag.name, func.count(item_tags.c.item_id))
            .outerjoin(item_tags, item_tags.c.tag_id == Tag.id)
            .group_by(Tag.id)
        )
        rows = self.session.execute(self._scope(stmt, user_id)).all()
        return {name: count for name, count in rows}

    def unused(self, user_id: int | None = None) -> list[Tag]:
        counts = self.usage_counts(user_id)
        return [tag for tag in self.all(user_id) if counts.get(tag.name, 0) == 0]

    def purge_unused(self, user_id: int | None = None) -> int:
        tags = self.unused(user_id)
        for tag in tags:
            self.session.delete(tag)
        self.session.flush()
        return len(tags)

    def item_count(self, tag: Tag) -> int:
        return self.session.scalar(
            select(func.count(item_tags.c.item_id)).where(item_tags.c.tag_id == tag.id)
        ) or 0

    def distinct_keywords(self, user_id: int | None = None) -> list[str]:
        """从数据项的关键词 JSON 中汇总出现过的关键词。"""
        stmt = select(DataItem.keywords)
        if user_id is not None:
            stmt = stmt.where(DataItem.user_id == user_id)
        words: set[str] = set()
        for (keywords,) in self.session.execute(stmt):
            if isinstance(keywords, list):
                words.update(str(word) for word in keywords if str(word).strip())
        return sorted(words)
