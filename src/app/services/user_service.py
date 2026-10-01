"""用户（配置档）与数据隔离：每个用户拥有自己的数据项与分类。"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import config
from ..db.models import ArchiveEntry, Category, DataItem, Tag, User
from ..db.seed import seed_user_defaults
from ..repositories import (
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
    UserRepository,
)
from ..core.security import hash_password, verify_hash

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
        return verify_hash(user.password_hash, password)

    def data_count(self, user: User) -> int:
        """该用户的数据项数量。"""
        return int(
            self.session.scalar(select(func.count(DataItem.id)).where(DataItem.user_id == user.id)) or 0
        )

    def delete(self, user: User, move_to: User | None = None) -> bool:
        """删除用户：有数据时并入默认用户（显式传入 move_to 时并入该用户），没有数据时只清理其分类与目录。"""
        if user.is_default:
            logger.warning("默认用户不可删除")
            return False
        target = move_to if move_to is not None and move_to.id != user.id else self.default()
        if target is None or target.id == user.id:
            logger.warning("没有可接收数据的用户，取消删除")
            return False
        libraries = _library_service(self.session)
        library = libraries.ensure_default()
        old_dir = libraries.dir_name_of(user.name)
        has_data = self.data_count(user) > 0
        # 用户没有数据时不再把它的一级分类与空目录并入目标用户。
        mapping = self._mirror_categories(user, target) if has_data else {}
        if has_data:
            for item in self.session.scalars(select(DataItem).where(DataItem.user_id == user.id)):
                item.user_id = target.id
                if item.category_id in mapping:
                    item.category_id = mapping[item.category_id]
                prefix = f"{old_dir}/"
                if item.file_path.startswith(prefix):
                    item.file_path = f"{libraries.dir_name_of(target.name)}/{item.file_path}"
        for category in self.session.scalars(select(Category).where(Category.user_id == user.id)):
            self.session.delete(category)
        for tag in self.session.scalars(select(Tag).where(Tag.user_id == user.id)):
            existing = self.tags.by_name(tag.name, user_id=target.id)
            if existing is not None and existing.id != tag.id:
                self.tags.merge(tag, existing)
            else:
                # 归属与创建者一并转移，避免删除后标签的创建者指向已不存在的用户。
                tag.user_id = target.id
                tag.created_by = target.id
        for entry in self.session.scalars(select(ArchiveEntry).where(ArchiveEntry.user_id == user.id)):
            entry.user_id = target.id
            entry.user_name = target.name
            entry.category = f"{user.name} / {entry.category}" if entry.category else user.name
        if has_data:
            libraries.relocate_user_dir(library, user.name, target.name)
        else:
            libraries.remove_user_dir(library, user.name)
        if config.currentUserId.value == user.id:
            self.set_current(target)
        self.session.delete(user)
        self.session.flush()
        if has_data:
            logger.info("已删除用户 {}，数据并入 {}", user.name, target.name)
        else:
            logger.info("已删除无数据的用户 {}，其分类与目录已清理", user.name)
        return True

    def _mirror_categories(self, source: User, target: User) -> dict[int, int]:
        """把 source 的分类层级镜像到 target 下的一级分类「source.name」，返回旧 -> 新 id 映射。"""
        root = self.categories.ensure(source.name, None, user_id=target.id)
        mapping: dict[int, int] = {}
        categories = list(
            self.session.scalars(select(Category).where(Category.user_id == source.id)).all()
        )
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
        return mapping


__all__ = ["DEFAULT_USER_NAME", "UserInfo", "UserService"]
