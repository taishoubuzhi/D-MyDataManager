"""存档服务：为整个库拍摄内容寻址快照，支持对比、还原与清理。

存档不依赖 git：每个数据项的内容以 sha256 存放在 BlobStore 中，
快照只记录当时的条目状态（ArchiveEntry），因此天然支持图片、视频等任意二进制文件，
并且内容未变时可复用同一份 blob，实现增量保存。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import config, store_dir
from ..db.models import Archive, ArchiveEntry, Blob, DataItem, DataType, User
from ..repositories import (
    ArchiveRepository,
    BlobRepository,
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
)
from .blob_store import BlobStore
from .library_service import LibraryService
from .privacy_service import guarded
from .user_service import UserService

MAX_ARCHIVES = 1000


@dataclass
class ArchiveDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    missing_blobs: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.missing_blobs)


class ArchiveService:
    def __init__(self, session: Session, store: BlobStore | None = None) -> None:
        self.session = session
        self.store = store or BlobStore(store_dir())
        self.archives = ArchiveRepository(session)
        self.blobs = BlobRepository(session)
        self.items = ItemRepository(session)
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)

    # ---------------------------------------------------------------- 快照
    def create(self, name: str = "", note: str = "") -> Archive:
        items = self._all_items()
        previous = self.archives.latest(limit=1)
        known: set[str] = set()
        if previous:
            known = {entry.checksum for entry in self.archives.entries_of(previous[0]) if entry.checksum}

        user_names = {user.id: user.name for user in self.session.scalars(select(User)).all()}
        entries: list[dict] = []
        total_size = 0
        new_blobs = 0
        for item in items:
            total_size += item.size
            if item.checksum and item.checksum not in known:
                new_blobs += 1
                known.add(item.checksum)
            entries.append(
                {
                    "item_id": item.id,
                    "name": item.name,
                    "type": str(item.type.value) if isinstance(item.type, DataType) else str(item.type),
                    "checksum": item.checksum,
                    "size": item.size,
                    "category": self.categories.path_of(item.category) if item.category else "",
                    "tags": item.tag_names,
                    "content": (item.content or "")[:2000],
                    "user_id": item.user_id,
                    "user_name": user_names.get(item.user_id or 0, ""),
                    "is_hidden": bool(item.is_hidden),
                }
            )

        archive = self.archives.create_archive(
            name or self._default_name(), note, entries, new_blobs, total_size
        )
        logger.info("存档完成：{} 项 / {} 字节 / 新增内容 {}", len(entries), total_size, new_blobs)
        removed, freed = self.auto_prune()
        if removed:
            logger.info("按清理策略删除了 {} 个较早的存档，释放 {} 字节", removed, freed)
        return archive

    def _default_name(self) -> str:
        return f"存档 {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    def history(self, limit: int = 50) -> list[Archive]:
        return self.archives.latest(limit)

    def entries(self, archive: Archive) -> list[ArchiveEntry]:
        return self.archives.entries_of(archive)

    # ---------------------------------------------------------------- 对比
    def compare(self, archive: Archive, user_id: int | None = None) -> ArchiveDiff:
        """与当前数据对比；传入 user_id 时只比较该用户自己的数据与条目。"""
        entries = self.archives.entries_of(archive)
        if user_id is not None:
            entries = [entry for entry in entries if entry.user_id in (None, user_id)]
        old = {entry.item_id: entry for entry in entries if entry.item_id}
        current = {item.id: item for item in self._all_items(user_id)}
        diff = ArchiveDiff()
        for item_id, item in current.items():
            entry = old.get(item_id)
            if entry is None:
                diff.added.append(item.name)
            elif entry.checksum != item.checksum:
                diff.changed.append(item.name)
        for item_id, entry in old.items():
            if item_id not in current:
                diff.removed.append(entry.name)
        for entry in entries:
            if entry.checksum and not self.store.exists(self.store.rel_path_for(entry.checksum)):
                diff.missing_blobs.append(entry.name)
        return diff

    # ---------------------------------------------------------------- 还原
    def ensure_category_path(self, path: str, user_id: int | None = None) -> int | None:
        """按「一级 / 二级」路径在指定用户下逐级重建分类，返回末级分类 id。"""
        parent_id: int | None = None
        for part in (segment.strip() for segment in path.split("/")):
            if not part:
                continue
            parent_id = self.categories.ensure(part, parent_id, user_id=user_id).id
        return parent_id

    def visible_entries(self, archive: Archive, user_id: int, is_admin: bool) -> list[ArchiveEntry]:
        """普通用户只能看到自己的条目；早期无归属的条目对所有人生效。"""
        entries = self.entries(archive)
        if is_admin:
            return entries
        return [entry for entry in entries if entry.user_id in (None, user_id)]

    def entry_state(self, entry: ArchiveEntry, owner_id: int | None = None) -> str:
        """条目相对当前数据的状态。

        `same` 与当前数据完全一致（无需还原）、`changed` 内容已变化、
        `removed` 已删除、`missing` 存档文件缺失。只有非 `same` 的条目允许还原。

        内容一致但隐藏状态不同时算 `changed`：还原会按存档把隐藏状态调整回来。
        """
        if not entry.checksum or not self.store.exists(self.store.rel_path_for(entry.checksum)):
            return "missing"
        item = self.items.get(entry.item_id) if entry.item_id else None
        if item is None or item.is_deleted:
            return "removed"
        owner = owner_id or entry.user_id
        if owner is not None and item.user_id != owner:
            return "changed"
        if (item.checksum or "") != (entry.checksum or ""):
            return "changed"
        return "same" if bool(item.is_hidden) == bool(entry.is_hidden) else "changed"

    @guarded
    def restore_entry(
        self,
        entry: ArchiveEntry,
        category_id: int | None = None,
        user_id: int | None = None,
    ) -> DataItem | None:
        """还原单条存档；与当前数据完全一致的条目视为无需还原，返回 None。

        归属用户优先取传入的 user_id，其次取条目记录的用户，最后回落到当前用户；
        未显式指定分类时按条目记录的「分类 / 路径」在目标用户下重建。
        """
        if not entry.checksum:
            return None
        rel_path = self.store.rel_path_for(entry.checksum)
        if not self.store.exists(rel_path):
            logger.warning("存档内容已丢失，无法还原：{}", entry.name)
            return None
        owner_id = user_id or entry.user_id or UserService(self.session).current_id()
        if self.entry_state(entry, owner_id) == "same":
            logger.info("存档条目与当前数据一致，无需还原：{}", entry.name)
            return None
        for item in self.items.by_checksum(entry.checksum):
            if item.user_id == owner_id:
                libraries = LibraryService(self.session, store=self.store)
                if bool(item.is_hidden) != bool(entry.is_hidden):
                    libraries.set_item_hidden(item, bool(entry.is_hidden))
                    logger.info(
                        "已按存档同步隐藏状态：{}（{}）",
                        entry.name,
                        "隐藏" if entry.is_hidden else "显示",
                    )
                return item
        try:
            data_type = DataType(entry.type)
        except ValueError:
            data_type = DataType.OTHER
        libraries = LibraryService(self.session, store=self.store)
        library = libraries.ensure_default()
        if category_id is None and entry.category:
            category_id = self.ensure_category_path(entry.category, owner_id)
        subdir = paths.HIDDEN_DIR_NAME if entry.is_hidden else ""
        library_rel = libraries.unique_rel_path(
            library, category_id, entry.name, user_id=owner_id, subdir=subdir
        )
        target = Path(library.path) / library_rel
        paths.make_dir(target.parent)
        target.write_bytes(self.store.read_bytes(rel_path))

        item = self.items.create(
            name=entry.name,
            type=data_type,
            content=entry.content,
            size=entry.size,
            checksum=entry.checksum,
            file_path=library_rel,
            library_id=library.id,
            category_id=category_id,
            user_id=owner_id,
            is_hidden=bool(entry.is_hidden),
        )
        item.tags = self.tags.ensure_many(entry.tags or [], user_id=owner_id)
        self.blobs.register(entry.checksum, entry.size, "", rel_path)
        self.session.flush()
        logger.info("已从存档还原：{}（归属用户 {}）", entry.name, owner_id)
        return item

    def restore_all(self, archive: Archive, user_id: int | None = None) -> dict[str, int]:
        """整档还原：每条回到其所属用户的原分类目录。"""
        restored = 0
        skipped = 0
        for entry in self.entries(archive):
            if self.restore_entry(entry, user_id=user_id) is None:
                skipped += 1
            else:
                restored += 1
        logger.info("整档还原完成：成功 {} 项，跳过 {} 项", restored, skipped)
        return {"restored": restored, "skipped": skipped}

    # ---------------------------------------------------------------- 维护
    def orphans(self) -> list:
        """没有任何数据项或存档引用的 blob。"""
        entries = self.session.scalars(select(ArchiveEntry)).all()
        referenced = {entry.checksum for entry in entries if entry.checksum}
        referenced |= {item.checksum for item in self.session.scalars(select(DataItem)) if item.checksum}
        return [blob for blob in self.blobs.orphans() if blob.checksum not in referenced]

    def cleanup_orphans(self) -> tuple[int, int]:
        """删除无引用的 blob，返回 (数量, 释放字节数)。"""
        removed = 0
        freed = 0
        for blob in self.orphans():
            self.store.remove(blob.rel_path)
            freed += blob.size
            self.session.delete(blob)
            removed += 1
        self.session.flush()
        logger.info("已清理 {} 份冗余内容，释放 {} 字节", removed, freed)
        return removed, freed

    def prune(self, keep: int = 20) -> int:
        """按数量修剪：除已标记的存档外，只保留最近 keep 个快照。"""
        archives = self.archives.latest(limit=MAX_ARCHIVES)
        removed = 0
        kept = 0
        for archive in archives:
            if archive.pinned:
                continue
            kept += 1
            if kept > keep:
                self.session.delete(archive)
                removed += 1
        self.session.flush()
        return removed

    def prune_by_size(self, max_bytes: int) -> int:
        """按容量修剪：从最旧的未标记快照开始删除，直到占用不超过 max_bytes。

        已标记的快照不会被删除，其内容仍计入占用；也始终保留最新的一个快照。
        数据项自身引用的内容不会被删除，因此当这些内容本身就超过上限时，
        只能删到只剩最新快照为止。
        """
        archives = self.archives.latest(limit=MAX_ARCHIVES)
        if len(archives) <= 1:
            return 0
        sizes = {blob.checksum: blob.size for blob in self.session.scalars(select(Blob))}
        item_checks = {item.checksum for item in self._all_items() if item.checksum}
        retained = sum(sizes.get(checksum, 0) for checksum in item_checks)
        refs: dict[str, set[int]] = {}
        per_archive: dict[int, set[str]] = {}
        for archive in archives:
            checks = {entry.checksum for entry in self.archives.entries_of(archive) if entry.checksum}
            per_archive[archive.id] = checks
            for checksum in checks:
                refs.setdefault(checksum, set()).add(archive.id)
        for checksum, ids in refs.items():
            if checksum not in item_checks:
                retained += sizes.get(checksum, 0)

        removed = 0
        for archive in reversed(archives[1:]):
            if retained <= max_bytes:
                break
            if archive.pinned:
                continue
            freed = 0
            for checksum in per_archive[archive.id]:
                ids = refs[checksum]
                ids.discard(archive.id)
                if not ids and checksum not in item_checks:
                    freed += sizes.get(checksum, 0)
            retained -= freed
            self.session.delete(archive)
            removed += 1
        self.session.flush()
        if removed and retained > max_bytes:
            logger.warning("仓库容量上限 {} 字节仍被数据项自身的内容占用，已删到只剩最新快照", max_bytes)
        return removed

    def prune_by_age(self, days: int) -> int:
        """按时间修剪：删除超过 days 天的未标记快照，始终保留最新的一个。"""
        archives = self.archives.latest(limit=MAX_ARCHIVES)
        if len(archives) <= 1:
            return 0
        cutoff = dt.datetime.now() - dt.timedelta(days=days)
        removed = 0
        for archive in archives[1:]:
            if archive.pinned:
                continue
            if archive.created_at is not None and archive.created_at < cutoff:
                self.session.delete(archive)
                removed += 1
        self.session.flush()
        return removed

    def auto_prune(self) -> tuple[int, int]:
        """按配置的清理策略修剪存档，并释放不再被引用的内容。

        返回 (删除的存档数, 释放的字节数)。
        """
        mode = str(config.pruneMode.value)
        if mode == "none":
            return 0, 0
        if mode == "size":
            removed = self.prune_by_size(int(config.keepSize.value) * 1024 * 1024)
        elif mode == "age":
            removed = self.prune_by_age(int(config.keepDays.value))
        else:
            removed = self.prune(keep=int(config.keepVersions.value))
        if not removed:
            return 0, 0
        _, freed = self.cleanup_orphans()
        return removed, freed

    def policy_summary(self) -> str:
        mode = str(config.pruneMode.value)
        if mode == "size":
            return f"自动清理：仓库容量上限 {int(config.keepSize.value)} MB"
        if mode == "age":
            return f"自动清理：保留最近 {int(config.keepDays.value)} 天"
        if mode == "none":
            return "自动清理：已关闭"
        return f"自动清理：保留最近 {int(config.keepVersions.value)} 个存档"

    def _all_items(self, user_id: int | None = None) -> list[DataItem]:
        filters = ItemFilter(include_hidden=True, include_deleted=True)
        if user_id:
            filters.user_ids = {int(user_id)}
        return self.items.query(filters)

    def set_pinned(self, archive: Archive, pinned: bool) -> bool:
        """标记 / 取消标记存档。

        标记后的存档不会被自动清理删除，只有手动删除或先取消标记才会消失。
        """
        archive.pinned = bool(pinned)
        self.session.flush()
        logger.info("存档「{}」{}", archive.name, "已标记" if archive.pinned else "已取消标记")
        return archive.pinned

    def delete(self, archive: Archive) -> None:
        self.archives.delete_archive(archive)
