"""用户（配置档）与数据隔离：每个用户拥有自己的数据项与分类。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.config import config
from ..db.models import ArchiveEntry, Category, DataItem, Tag, User
from ..db.seed import UNCATEGORIZED_NAME, seed_user_defaults
from ..repositories import (
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
    UserRepository,
)
from ..core.runtime.security import hash_password, needs_rehash, verify_hash

DEFAULT_USER_NAME = "默认用户"


def _library_service(session: Session):
    """延迟导入，避免与 library_service 形成循环依赖。"""
    from .library_service import LibraryService

    return LibraryService(session)


@dataclass
class UserInfo:
    """用户列表项：用户本体 + 统计。"""

    user: User
    item_count: int
    category_count: int

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def protected(self) -> bool:
        return bool(self.user.password_hash)

    @property
    def is_default(self) -> bool:
        """默认用户（管理员）：可管理其他用户，且不可删除。"""
        return bool(self.user.is_default)


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.items = ItemRepository(session)
        self.tags = TagRepository(session)
        self.categories = CategoryRepository(session)

    # ---------------------------------------------------------------- 查询
    def list_users(self) -> list[UserInfo]:
        item_counts = {
            user_id: count
            for user_id, count in self.session.execute(
                select(DataItem.user_id, func.count(DataItem.id))
                .where(DataItem.is_deleted.is_(False))
                .group_by(DataItem.user_id)
            ).all()
        }
        category_counts = {
            user_id: count
            for user_id, count in self.session.execute(
                select(Category.user_id, func.count(Category.id)).group_by(Category.user_id)
            ).all()
        }
        return [
            UserInfo(user, item_counts.get(user.id, 0), category_counts.get(user.id, 0))
            for user in self.users.list_all()
        ]

    def by_id(self, user_id: int | None) -> User | None:
        return self.users.get(user_id) if user_id else None

    def item_count(self, user: User) -> int:
        return self.items.count(ItemFilter(user_ids={int(user.id)}))

    def current(self) -> User:
        """当前用户：取配置里的 id，失效时回退到默认用户。"""
        user = self.by_id(config.currentUserId.value)
        if user is None:
            user = self.users.ensure_default()
            self.session.flush()
            self.set_current(user)
        return user

    def current_id(self) -> int:
        return int(self.current().id)

    def default(self) -> User | None:
        """默认用户（管理员）。"""
        return self.users.default()

    def is_admin(self, user: User | None = None) -> bool:
        actor = user if user is not None else self.current()
        return bool(actor is not None and actor.is_default)

    # ---------------------------------------------------------------- 修改
    def set_current(self, user: User) -> None:
        config.set(config.currentUserId, int(user.id))
        logger.info("当前用户切换为 {}", user.name)
        # 广播用户切换事件：插件可以订阅 user.changed
        from ..sdk import Events
        from .plugin_service import plugin_service

        plugin_service.publish(Events.USER_CHANGED, user_id=int(user.id), name=user.name)

    def create(self, name: str, password: str = "") -> User | None:
        name = (name or "").strip()
        if not name:
            return None
        if self.users.by_name(name) is not None:
            logger.warning("已存在同名用户：{}", name)
            return None
        user = self.users.create(name, password_hash=hash_password(password) if password else "")
        self.session.flush()
        seed_user_defaults(self.session, user)
        # 分类即目录：新用户的默认分类要在磁盘上有同名目录
        libraries = _library_service(self.session)
        libraries.ensure_category_dirs(libraries.ensure_default(), user.id)
        return user

    def rename(self, user: User, name: str) -> bool:
        name = (name or "").strip()
        if not name or name == user.name:
            return False
        existing = self.users.by_name(name)
        if existing is not None and existing.id != user.id:
            logger.warning("已存在同名用户：{}", name)
            return False
        user.name = name
        self.session.flush()
        return True

    def set_password(self, user: User, password: str) -> None:
        user.password_hash = hash_password(password) if password else ""
        self.session.flush()

    def verify(self, user: User, password: str) -> bool:
        """校验口令；通过后顺手把老散列升级成当前算法（惰性 rehash，不必迁移全库）。"""
        if not verify_hash(user.password_hash, password):
            return False
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            self.session.flush()
        return True

    def data_count(self, user: User) -> int:
        """该用户的数据项数量。"""
        return int(
            self.session.scalar(select(func.count(DataItem.id)).where(DataItem.user_id == user.id)) or 0
        )

    def delete(self, user: User, move_to: User | None = None, *, allow_current: bool = False) -> bool:
        """删除用户：数据、分类、标签与存档条目一并并入目标用户（默认是默认用户）。

        默认用户不可删除；当前用户默认也不可删除（只有脚本显式传 allow_current=True 才能删），
        否则删号后当前用户会被悄悄切换成默认用户，等于给自己提权。
        """
        if user.is_default:
            logger.warning("默认用户不可删除")
            return False
        if not allow_current and config.currentUserId.value == user.id:
            logger.warning("不能删除当前用户 {}，请先切换到其他用户", user.name)
            return False
        target = move_to if move_to is not None and move_to.id != user.id else self.default()
        if target is None or target.id == user.id:
            logger.warning("没有可接收数据的用户，取消删除")
            return False
        libraries = _library_service(self.session)
        library = libraries.ensure_default()
        old_dir = libraries.dir_name_of(user.name)
        new_dir = libraries.dir_name_of(target.name)
        items = list(self.session.scalars(select(DataItem).where(DataItem.user_id == user.id)))
        has_data = any(not item.is_deleted for item in items)
        # 分类剪枝：整棵子树都没有数据项的分类不迁移，只镜像有数据的分类与它们的祖先。
        retained = self._retained_categories(user, items)
        mapping, mirror_root = self._mirror_categories(user, target, retained)
        if items:
            fallback_id = (
                mirror_root
                if mirror_root is not None
                else self.categories.ensure(UNCATEGORIZED_NAME, None, user_id=target.id).id
            )
        else:
            fallback_id = None
        moved_items = 0
        for item in items:
            item.user_id = target.id
            item.category_id = mapping.get(item.category_id, fallback_id)
            prefix = f"{old_dir}/"
            if item.file_path.startswith(prefix):
                item.file_path = f"{new_dir}/{item.file_path}"
            moved_items += 1
        for category in self.session.scalars(select(Category).where(Category.user_id == user.id)):
            self.session.delete(category)
        # 该用户的个人标签并入目标用户；它创建的全局标签保持全局，只改创建者，
        # 否则外键 SET NULL 会抹掉创建者，标签从此谁都改不了。
        # 没有任何数据项引用的标签不迁移，直接删除。
        moved_tags = 0
        dropped_tags = 0
        for tag in self.session.scalars(
            select(Tag).where(or_(Tag.user_id == user.id, Tag.created_by == user.id))
        ):
            if self.tags.item_count(tag) == 0:
                self.session.delete(tag)
                dropped_tags += 1
                continue
            if tag.user_id == user.id:
                existing = self.tags.by_name(tag.name, user_id=target.id)
                if existing is not None and existing.id != tag.id:
                    self.tags.merge(tag, existing)
                else:
                    # 归属转移；创建者只有在指向被删用户（或为空）时才改写。
                    tag.user_id = target.id
                    if tag.created_by is None or tag.created_by == user.id:
                        tag.created_by = target.id
            else:
                tag.created_by = target.id
            moved_tags += 1
        moved_entries = 0
        for entry in self.session.scalars(select(ArchiveEntry).where(ArchiveEntry.user_id == user.id)):
            entry.user_id = target.id
            entry.user_name = target.name
            entry.category = f"{user.name} / {entry.category}" if entry.category else user.name
            moved_entries += 1
        pruned_dirs = 0
        if has_data:
            mirror = libraries.relocate_user_dir(library, user.name, target.name)
            # 只留下有数据的分类目录，镜像目录里的空目录一并清掉。
            pruned_dirs = self._prune_empty_dirs(mirror)
        else:
            libraries.remove_user_dir(library, user.name)
        if config.currentUserId.value == user.id:
            self.set_current(target)
        self.session.delete(user)
        self.session.flush()
        logger.info(
            "已删除用户 {}：数据 {} 项、标签 {} 个（未使用删除 {} 个）、分类 {} 个、"
            "空目录 {} 个、存档条目 {} 条并入 {}",
            user.name,
            moved_items,
            moved_tags,
            dropped_tags,
            len(mapping),
            pruned_dirs,
            moved_entries,
            target.name,
        )
        return True

    def _retained_categories(self, user: User, items: list[DataItem]) -> set[int]:
        """需要迁移的分类：有数据项的分类及其全部祖先；其余分类为空，迁移时剪掉。"""
        categories = {
            category.id: category
            for category in self.session.scalars(
                select(Category).where(Category.user_id == user.id)
            )
        }
        retained: set[int] = set()
        for item in items:
            category_id = item.category_id
            while category_id in categories and category_id not in retained:
                retained.add(category_id)
                category_id = categories[category_id].parent_id
        return retained

    def _prune_empty_dirs(self, root: Path) -> int:
        """删除镜像目录里没有任何文件的空子目录（自底向上），返回删除数量。"""
        if not root.is_dir():
            return 0
        removed = 0
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                removed += 1
        return removed

    def _mirror_categories(
        self, source: User, target: User, retained: set[int]
    ) -> tuple[dict[int, int], int | None]:
        """把 source 中 retained 里的分类镜像到 target 下的一级分类「source.name」。

        返回旧 -> 新 id 映射与新一级分类 id；没有需要迁移的分类时返回 ({}, None)。
        """
        categories = [
            category
            for category in self.session.scalars(
                select(Category).where(Category.user_id == source.id)
            ).all()
            if category.id in retained
        ]
        if not categories:
            return {}, None
        root = self.categories.ensure(source.name, None, user_id=target.id)
        mapping: dict[int, int] = {}
        ids = {category.id for category in categories}
        pending = list(categories)
        while pending:
            progressed = False
            for category in list(pending):
                if category.parent_id in ids and category.parent_id not in mapping:
                    continue
                parent_id = mapping.get(category.parent_id, root.id)
                new_category = self.categories.create(
                    self.categories.unique_sibling_name(category.name, parent_id, target.id),
                    parent_id,
                    description=category.description,
                    icon=category.icon,
                    color=category.color,
                    user_id=target.id,
                    is_hidden=category.is_hidden,
                )
                mapping[category.id] = new_category.id
                pending.remove(category)
                progressed = True
            if not progressed:  # 结构异常（父分类丢失）时不再等待，挂到一级分类下
                for category in pending:
                    mapping[category.id] = self.categories.create(
                        self.categories.unique_sibling_name(category.name, root.id, target.id),
                        root.id,
                        user_id=target.id,
                        is_hidden=category.is_hidden,
                    ).id
                break
        return mapping, root.id


__all__ = ["DEFAULT_USER_NAME", "UserInfo", "UserService"]
