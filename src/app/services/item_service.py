"""数据项服务：编辑、批量操作、回收站、打开文件。"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import shell
from ..core.config import store_dir
from ..db.models import ArchiveEntry, DataItem, DataType, Library
from ..repositories import BlobRepository, ItemRepository, TagRepository
from . import feature_service
from .blob_store import BlobStore
from .library_service import LibraryService


class ItemService:
    def __init__(self, session: Session, store: BlobStore | None = None) -> None:
        self.session = session
        self.store = store or BlobStore(store_dir())
        self.items = ItemRepository(session)
        self.tags = TagRepository(session)
        self.blobs = BlobRepository(session)
        self.libraries = LibraryService(session, store=self.store)

    # ----------------------------------------------------------------- 编辑
    def update(self, item: DataItem, **fields) -> DataItem:
        if "tags" in fields:
            item.tags = self.tags.ensure_many(fields.pop("tags"), user_id=item.user_id)
        if "keywords" in fields:
            fields["keywords"] = [str(k).strip() for k in fields["keywords"] if str(k).strip()]
        changed_content = "content" in fields and fields["content"] != item.content
        item = self.items.update(item, **fields)
        if changed_content:
            checksum, _store_rel, size = self.store.put_text(item.content)
            item.checksum = checksum
            item.size = size
            self._sync_text_file(item)
            self.blobs.register(checksum, size, "text/plain", self.store.rel_path_for(checksum))
            feature_service.replace_features(self.session, item)
            self.session.flush()
        return item

    def set_category(self, items: list[DataItem], category_id: int | None) -> int:
        for item in items:
            self.move_item(item, category_id)
        return len(items)

    def move_item(self, item: DataItem, category_id: int | None, library: Library | None = None) -> None:
        """改变数据项的分类，必要时把文件移动到对应目录并跟随库变更。"""
        target_library = library or (item.library if item.library_id else None) or self.libraries.ensure_default()
        if item.file_path:
            source = self.libraries.abs_path(item)
            rel_path = self.libraries.unique_rel_path(
                target_library, category_id, Path(item.file_path).name, user_id=item.user_id
            )
            target = Path(target_library.path) / rel_path
            if source is not None and source.exists() and source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
            item.file_path = rel_path
        item.category_id = category_id
        item.library_id = target_library.id
        self.session.flush()

    def set_hidden(self, items: list[DataItem], hidden: bool) -> int:
        return self.items.bulk_set_field(items, "is_hidden", hidden)

    def add_tags(self, items: list[DataItem], names: list[str]) -> int:
        user_id = items[0].user_id if items else None
        return self.items.bulk_add_tags(items, self.tags.ensure_many(names, user_id=user_id))

    def remove_tags(self, items: list[DataItem], names: list[str]) -> int:
        user_id = items[0].user_id if items else None
        tags = [
            tag
            for tag in (self.tags.by_name(name, user_id=user_id) for name in names)
            if tag is not None
        ]
        return self.items.bulk_remove_tags(items, tags)

    # ----------------------------------------------------------------- 回收站
    def delete(self, items: list[DataItem]) -> int:
        return self.items.soft_delete(items)

    def restore(self, items: list[DataItem]) -> int:
        return self.items.restore(items)

    def purge(self, items: list[DataItem]) -> int:
        for item in items:
            path = self.file_path_of(item)
            if path is not None:
                try:
                    path.unlink()
                except OSError as exc:  # 文件被占用或已删除都不影响数据库清理
                    logger.warning("删除库内文件失败：{}（{}）", path, exc)
            blob = self.blobs.by_checksum(item.checksum)
            if blob is not None:
                blob.ref_count = max(0, blob.ref_count - 1)
                if blob.ref_count == 0 and not self._archived(item.checksum):
                    self.store.remove(blob.rel_path)
                    self.session.delete(blob)
        count = self.items.purge(items)
        logger.info("已彻底删除 {} 个数据项", count)
        return count

    def _archived(self, checksum: str | None) -> bool:
        """内容仍被存档引用时保留仓库文件，由存档服务统一清理。"""
        if not checksum:
            return False
        statement = select(ArchiveEntry.id).where(ArchiveEntry.checksum == checksum).limit(1)
        return self.session.scalar(statement) is not None

    def purge_trash(self) -> int:
        from ..repositories import ItemFilter

        items = self.items.query(ItemFilter(only_deleted=True))
        return self.purge(items)

    # ----------------------------------------------------------------- 文件
    def file_path_of(self, item: DataItem) -> Path | None:
        path = self.libraries.abs_path(item)
        return path if path is not None and path.exists() else None

    def open_item(self, item: DataItem) -> bool:
        """用系统默认程序打开；内置查看器由界面层按「打开方式」规则处理。"""
        path = self.file_path_of(item)
        if path is None:
            logger.warning("文件不存在，无法打开：{}", item.name)
            return False
        return shell.open_default(path)

    def reveal_item(self, item: DataItem) -> bool:
        path = self.file_path_of(item)
        if path is None:
            return False
        return shell.reveal(path)

    def copy_path(self, item: DataItem) -> str:
        path = self.file_path_of(item)
        return str(path) if path else item.source_path

    def extensions_in_use(self, user_id: int | None = None) -> dict[str, int]:
        """库里实际出现的扩展名 → 数量，供「打开方式」页列出可配置的格式。"""
        from ..repositories import ItemFilter

        filters = ItemFilter(include_hidden=True, include_deleted=True)
        if user_id:
            filters.user_ids = {int(user_id)}
        counts: dict[str, int] = {}
        for item in self.items.query(filters):
            suffix = Path(item.file_path or item.source_path).suffix.lower().lstrip(".")
            if suffix:
                counts[suffix] = counts.get(suffix, 0) + 1
        return counts

    # ----------------------------------------------------------------- 查重
    def duplicate_map(self, user_id: int | None = None) -> dict[str, list[DataItem]]:
        """按内容指纹分组重复项；传入 user_id 时只统计该用户自己的数据。"""
        from collections import defaultdict

        from ..repositories import ItemFilter

        filters = ItemFilter(include_hidden=True)
        if user_id:
            filters.user_ids = {int(user_id)}
        groups: dict[str, list[DataItem]] = defaultdict(list)
        for item in self.items.query(filters):
            if item.checksum:
                groups[item.checksum].append(item)
        return {key: value for key, value in groups.items() if len(value) > 1}

    # ----------------------------------------------------------------- 内部
    def _sync_text_file(self, item: DataItem) -> None:
        """文本项内容变化后重写库内的 .txt 文件。"""
        if item.type is not DataType.TEXT:
            return
        library = item.library if item.library_id else None
        if library is None:
            return
        if not item.file_path:
            item.file_path = self.libraries.unique_rel_path(
                library, item.category_id, f"{item.name}.txt", user_id=item.user_id,
            )
        target = Path(library.path) / item.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.content or "", encoding="utf-8")
