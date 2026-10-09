"""分类与标签的维护逻辑。"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.runtime import paths
from ..db.models import Category, DataItem, Tag
from ..db.seed import UNCATEGORIZED_NAME
from ..repositories import CategoryRepository, TagRepository


@dataclass
class CategoryNode:
    category: Category
    depth: int
    item_count: int
    total_count: int
    #: 该分类下最近一条数据的导入时间（没有数据时为 None）；分类栏「按最新导入」排序用
    latest_at: object | None = None


def is_uncategorized(category: Category | None) -> bool:
    """「未分类」是系统固定的根分类：不可重命名、删除，也不能在其下新建子分类。"""
    return bool(
        category is not None
        and category.name == UNCATEGORIZED_NAME
        and category.parent_id is None
    )


class TaxonomyService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)
        self._libraries = None

    @property
    def libraries(self):
        """分类目录操作用到的库服务（延迟构建，避免服务之间互相导入）。"""
        if self._libraries is None:
            from .library_service import LibraryService

            self._libraries = LibraryService(self.session)
        return self._libraries

    def _ensure_dirs(self, category: Category) -> int:
        """在磁盘上补上这个分类自己的目录（「未分类」没有目录，它就在用户名文件夹下）。"""
        if is_uncategorized(category):
            return 0
        library = self.libraries.ensure_default()
        return self.libraries.ensure_category_dirs(library, category.user_id)

    def _relocate_dir(self, category: Category, old_chain: list[str]) -> int:
        """把分类目录搬到按当前分类树算出的新位置（改名或换父级），返回改写路径的条目数。"""
        if is_uncategorized(category):
            return 0
        library = self.libraries.ensure_default()
        return self.libraries.move_category_dir(
            library,
            category,
            old_chain,
            self.libraries.category_chain(category.id),
            self.libraries.category_users(category),
        )

    # ---------------------------------------------------------------- 分类
    def tree(self, include_hidden: bool = True, user_id: int | None = None) -> list[CategoryNode]:
        counts = self.categories.item_counts(user_id=user_id)
        latest = self.categories.latest_imports(user_id=user_id)
        nodes: list[CategoryNode] = []
        queue: list[tuple[Category, int]] = [
            (category, 0)
            for category in sorted(
                self.categories.roots(include_hidden=include_hidden, user_id=user_id),
                key=lambda item: (is_uncategorized(item), item.sort_order, item.name),
            )
        ]
        while queue:
            category, depth = queue.pop(0)
            own = counts.get(category.id, 0)
            total = own + sum(self._subtree_count(child, counts) for child in category.children)
            nodes.append(
                CategoryNode(
                    category,
                    depth,
                    own,
                    total,
                    self._subtree_latest(category, latest),
                )
            )
            children = [c for c in category.children if include_hidden or not c.is_hidden]
            queue.extend(
                (child, depth + 1) for child in sorted(children, key=lambda c: (c.sort_order, c.name))
            )
        return nodes

    def _subtree_count(self, category: Category, counts: dict[int, int]) -> int:
        return counts.get(category.id, 0) + sum(
            self._subtree_count(child, counts) for child in category.children
        )

    def _subtree_latest(self, category: Category, latest: dict[int, object]) -> object | None:
        """这个分类及其子孙里最近一条数据的导入时间（与 `total_count` 的口径一致）。"""
        value = latest.get(category.id)
        for child in category.children:
            child_value = self._subtree_latest(child, latest)
            if child_value is not None and (value is None or child_value > value):
                value = child_value
        return value

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
        if parent_id is not None and is_uncategorized(self.session.get(Category, parent_id)):
            logger.warning("「{}」是固定分类，不能创建子分类", UNCATEGORIZED_NAME)
            return None
        if self.categories.by_name(name, parent_id, user_id) is not None:
            logger.warning("同级下已存在分类：{}", name)
            return None
        category = self.categories.create(name, parent_id, description, icon, color, user_id)
        self.session.flush()
        # 分类即目录：新建分类的同时在库文件夹里建出同名目录
        self._ensure_dirs(category)
        return category

    def category_dir_hint(self, category: Category | None, user_id: int | None = None) -> str:
        """新建分类后，它的目录路径深不深（太深时给提示文案，否则空串）。

        分类每多一层就多一段目录名，套得深了再往里放文件就会顶破 Windows 单条路径
        260 字符的上限，表现成「文件写不进去」。这里只提示，不拦着建。
        """
        if category is None or is_uncategorized(category):
            return ""
        try:
            library = self.libraries.ensure_default()
            directory = self.libraries.directory_for(library, category.id, user_id)
        except Exception as exc:  # noqa: BLE001 - 算路径失败不该拦住建分类
            logger.warning("算分类目录失败：{}", exc)
            return ""
        return paths.long_path_hint(directory)

    def update_category(self, category: Category, **fields) -> Category:
        for key, value in fields.items():
            if hasattr(category, key):
                setattr(category, key, value)
        self.session.flush()
        return category

    def uncategorized_category(self, user_id: int | None = None, create: bool = True) -> Category | None:
        """用户的「未分类」根级分类，用来收纳没有指定分类的数据。"""
        category = self.categories.by_name(UNCATEGORIZED_NAME, None, user_id)
        if category is None and create:
            category = self.categories.create(UNCATEGORIZED_NAME, None, user_id=user_id)
        return category

    def rename_category(self, category: Category, name: str) -> bool:
        """重命名分类；同级已有同名分类时拒绝（返回 False）。磁盘上的同名目录一并改名。"""
        name = (name or "").strip()
        if is_uncategorized(category):
            logger.warning("「{}」是固定分类，不能重命名", UNCATEGORIZED_NAME)
            return False
        if not name or name == category.name:
            return False
        existing = self.categories.by_name(name, category.parent_id, category.user_id)
        if existing is not None and existing.id != category.id:
            logger.warning("同级下已存在分类：{}", name)
            return False
        old_chain = self.libraries.category_chain(category.id)
        category.name = name
        self.session.flush()
        self._relocate_dir(category, old_chain)
        return True

    def move_category(self, category: Category, parent_id: int | None) -> bool:
        if is_uncategorized(category):
            logger.warning("「{}」是固定分类，不能移动", UNCATEGORIZED_NAME)
            return False
        if parent_id is not None and is_uncategorized(self.session.get(Category, parent_id)):
            logger.warning("「{}」是固定分类，不能创建子分类", UNCATEGORIZED_NAME)
            return False
        old_chain = self.libraries.category_chain(category.id)
        if not self.categories.move(category, parent_id):
            return False
        self._relocate_dir(category, old_chain)
        return True

    def promotion_conflicts(self, category: Category) -> list[Category]:
        """删除该分类时，上移后会与父级下已有分类重名的子分类。"""
        conflicts: list[Category] = []
        for child in self.categories.children_of(category.id):
            existing = self.categories.by_name(child.name, category.parent_id, child.user_id)
            if existing is not None and existing.id != child.id:
                conflicts.append(child)
        return conflicts

    def delete_category(
        self,
        category: Category,
        move_items_to: int | None = None,
        recursive: bool = False,
        renames: dict[int, str] | None = None,
    ) -> int:
        """删除分类；子分类默认上移到父级，recursive 时一并删除。

        上移的子分类与父级下已有分类重名时：renames 里给了新名字就用它，否则自动加 -1、-2 后缀，
        保证同一父级下不会出现重名分类。分类的目录一并处理：上移的子分类目录跟着搬，
        本分类目录里的数据搬到目标分类目录（默认「未分类」= 用户名文件夹根目录）后收掉。
        """
        if is_uncategorized(category):
            logger.warning("「{}」是固定分类，不能删除", UNCATEGORIZED_NAME)
            return 0
        renames = dict(renames or {})
        libraries = self.libraries
        library = libraries.ensure_default()
        old_chain = libraries.category_chain(category.id)
        parent_chain = libraries.category_chain(category.parent_id)
        user_ids = libraries.category_users(category)
        for child in self.categories.children_of(category.id):
            if recursive:
                self.delete_category(child, move_items_to, recursive=True)
                continue
            child_chain = libraries.category_chain(child.id)
            child.name = self.categories.unique_sibling_name(
                renames.get(child.id) or child.name, category.parent_id, child.user_id
            )
            child.parent_id = category.parent_id
            self.session.flush()
            libraries.move_category_dir(
                library, child, child_chain, libraries.category_chain(child.id), libraries.category_users(child)
            )
        items = list(self.session.scalars(select(DataItem).where(DataItem.category_id == category.id)))
        if items:
            from .item_service import ItemService  # 延迟导入，避免服务之间互相导入

            mover = ItemService(self.session)
            for item in items:
                target = move_items_to
                if target is None:
                    fallback = self.uncategorized_category(item.user_id)
                    target = fallback.id if fallback is not None else None
                # 「分类即目录」：搬文件到目标分类目录（未分类 = 用户名文件夹根目录）
                mover.move_item(item, target)
        # 先把子分类的新父级落库，再用 Core DELETE 删本行：ORM 的 delete-orphan 级联会把
        # 刚上移的子分类连同其数据项一起删掉。
        self.session.flush()
        self.session.expunge(category)
        self.session.execute(delete(Category).where(Category.id == category.id))
        # 目录里可能还剩没登记的散件：并进父级目录后收掉空壳
        libraries.dissolve_category_dir(library, old_chain, parent_chain, user_ids)
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
        tag = self.tags.ensure(name, description, color, user_id=user_id, is_global=is_global)
        if tag is not None and tag.is_global:
            # 别人可能已有同名个人标签：并入新全局标签，避免出现同名个人 / 全局副本。
            merged = self.tags.merge_shadow_copies(tag)
            if merged:
                logger.warning("全局标签「{}」并入 {} 个同名个人标签", name, merged)
        return tag

    def update_tag(self, tag: Tag, **fields) -> Tag:
        for key, value in fields.items():
            if hasattr(tag, key):
                setattr(tag, key, value)
        self.session.flush()
        return tag

    def rename_tag(
        self,
        tag: Tag,
        new_name: str,
        user_id: int | None = None,
        is_admin: bool = False,
    ) -> bool:
        """重命名标签；默认用户（管理员）或创建者才能改，且不得与可见标签重名。"""
        if not self.tags.can_manage(tag, user_id, is_admin):
            logger.warning("只有默认用户或创建者可以重命名标签：{}", tag.name)
            return False
        new_name = (new_name or "").strip()
        if not new_name or new_name == tag.name:
            return False
        if self.tags.by_name(new_name, user_id=user_id) is not None:
            logger.warning("标签已存在：{}", new_name)
            return False
        self.tags.rename(tag, new_name)
        return True

    def set_tag_global(
        self, tag: Tag, is_global: bool, user_id: int | None = None, is_admin: bool = False
    ) -> bool:
        """在全局标签与个人标签之间切换；默认用户或创建者可以操作，且不得与已有全局标签重名。"""
        if not self.tags.can_manage(tag, user_id, is_admin):
            logger.warning("只有默认用户或创建者可以切换标签归属：{}", tag.name)
            return False
        if not is_global and self.tags.others_use(tag):
            logger.warning("标签仍被其他用户的数据项使用，不能转为个人：{}", tag.name)
            return False
        if not self.tags.set_global(tag, is_global):
            logger.warning("已存在同名全局标签：{}", tag.name)
            return False
        if is_global:
            merged = self.tags.merge_shadow_copies(tag)
            if merged:
                logger.warning("全局标签「{}」并入 {} 个同名个人标签", tag.name, merged)
        return True

    def delete_tag(
        self, tag: Tag, user_id: int | None = None, is_admin: bool = False
    ) -> int:
        """删除标签；默认用户或创建者才能删，无权时返回 -1 且不做任何修改。"""
        if not self.tags.can_manage(tag, user_id, is_admin):
            logger.warning("只有默认用户或创建者可以删除标签：{}", tag.name)
            return -1
        count = self.tags.item_count(tag)
        for item in list(tag.items):
            item.tags.remove(tag)
        self.session.delete(tag)
        self.session.flush()
        return count

    def merge_tags(self, source: Tag, target: Tag) -> Tag:
        return self.tags.merge(source, target)

    def cleanup_unused(self, user_id: int | None = None, is_admin: bool = False) -> int:
        """清理未使用标签；他人创建的标签保留（默认用户可全部清理）。"""
        tags = [
            tag
            for tag in self.tags.unused(user_id)
            if not tag.is_global or self.tags.can_manage(tag, user_id, is_admin)
        ]
        for tag in tags:
            self.session.delete(tag)
        self.session.flush()
        logger.info("已清理 {} 个未使用标签", len(tags))
        return len(tags)

    def usage(self, user_id: int | None = None) -> dict[str, int]:
        return self.tags.usage_counts(user_id)
