"""数据项服务：编辑、批量操作、回收站、打开文件。"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import paths, shell
from ..db.models import ArchiveEntry, DataItem, DataType, Library
from ..repositories import BlobRepository, ItemRepository, TagRepository
from . import feature_service
from .library_service import LibraryService
from .content_store import ContentStore


class ItemService:
    def __init__(self, session: Session, store: ContentStore | None = None) -> None:
        self.session = session
        self.store = store or ContentStore(session)
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
        old_name = item.name
        changed_content = "content" in fields and fields["content"] != item.content
        item = self.items.update(item, **fields)
        if item.name != old_name:
            # 改名同时重命名库内文件，避免显示名与磁盘文件名对不上。
            self.libraries.rename_item_file(item, item.name)
        if changed_content:
            checksum, store_rel, size = self.store.put_text(
                item.content, name=item.name, mime=item.mime or "text/plain"
            )
            item.checksum = checksum
            item.size = size
            self._sync_text_file(item)
            self.blobs.register(checksum, size, "text/plain", store_rel)
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
            # 隐藏数据要保持在目标分类的 .hiddens 里
            subdir = paths.HIDDEN_DIR_NAME if item.is_hidden else ""
            rel_path = self.libraries.unique_rel_path(
                target_library, category_id, Path(item.file_path).name, user_id=item.user_id, subdir=subdir
            )
            target = Path(target_library.path) / rel_path
            if source is not None and source.exists() and source != target:
                paths.make_dir(target.parent)
                source.replace(target)
            item.file_path = rel_path
        item.category_id = category_id
        item.library_id = target_library.id
        self.session.flush()

    def set_hidden(self, items: list[DataItem], hidden: bool) -> int:
        """切换隐藏状态：同时把文件搬进 / 搬出分类目录下的 .hiddens。"""
        for item in items:
            self.libraries.set_item_hidden(item, hidden)
        return len(items)

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

    def add_keywords(self, items: list[DataItem], words: list[str]) -> int:
        """给一批数据追加关键词，返回被改动的数据条数（已有的词不重复添加）。"""
        names: list[str] = []
        for word in words:
            word = str(word).strip()
            if word and word not in names:
                names.append(word)
        if not names or not items:
            return 0
        changed = 0
        for item in items:
            current = [str(word) for word in (item.keywords or [])]
            merged = current + [name for name in names if name not in current]
            if len(merged) != len(current):
                item.keywords = merged
                changed += 1
        if changed:
            self.session.flush()
        return changed

    def remove_keywords(self, items: list[DataItem], words: list[str]) -> int:
        """从一批数据里去掉若干关键词，返回被改动的数据条数。"""
        drop = {str(word).strip() for word in words if str(word).strip()}
        if not drop or not items:
            return 0
        changed = 0
        for item in items:
            current = [str(word) for word in (item.keywords or [])]
            kept = [word for word in current if word not in drop]
            if len(kept) != len(current):
                item.keywords = kept
                changed += 1
        if changed:
            self.session.flush()
        return changed

    # ----------------------------------------------------------------- 回收站
    def delete(self, items: list[DataItem]) -> int:
        """把数据项移入回收站；已在回收站里的项会被跳过（重复删除没有意义）。"""
        targets = [item for item in items if not item.is_deleted]
        if not targets:
            return 0
        count = self.items.soft_delete(targets)
        # 广播条目删除事件：插件可以订阅 item.deleted
        from ..sdk import Events
        from .plugin_service import plugin_service

        for item in targets:
            plugin_service.publish(Events.ITEM_DELETED, item_id=item.id, name=item.name)
        return count

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
                    if blob.rel_path:  # 分块内容没有松散文件，块由 pack 可达性 GC 回收
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

    def refresh_file(self, item: DataItem) -> bool:
        """按磁盘上的库内文件重算 checksum / size（编辑器保存后调用），文本项同步 content。"""
        path = self.file_path_of(item)
        if path is None:
            return False
        checksum, store_rel, size = self.store.put_file(path, name=item.name, mime=item.mime or "")
        item.checksum = checksum
        item.size = size
        if item.type is DataType.TEXT:
            try:
                item.content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning("重新读取文本内容失败：{}", path)
        self.blobs.register(checksum, size, item.mime or "", store_rel)
        feature_service.replace_features(self.session, item)
        self.session.flush()
        return True

    def open_item(self, item: DataItem) -> bool:
        """用系统默认程序打开；内置查看器由查看器插件库按规则处理。"""
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
        """库里实际出现的扩展名 → 数量，供「查看器」页列出可配置的格式。"""
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
        paths.make_dir(target.parent)
        target.write_text(item.content or "", encoding="utf-8")
