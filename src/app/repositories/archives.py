"""存档仓储：内容寻址的 blob 与历史批次。"""

from __future__ import annotations

from sqlalchemy import func, select

from ..db.models import Archive, ArchiveEntry, Blob
from .base import Repository


class BlobRepository(Repository[Blob]):
    model = Blob

    def by_checksum(self, checksum: str) -> Blob | None:
        return self.session.scalars(select(Blob).where(Blob.checksum == checksum)).first()

    def register(self, checksum: str, size: int, mime: str, rel_path: str) -> tuple[Blob, bool]:
        """登记一份内容；已存在时只增加引用计数。

        返回 (blob, 是否新建)。
        """
        blob = self.by_checksum(checksum)
        if blob is not None:
            blob.ref_count += 1
            self.session.flush()
            return blob, False
        blob = self.add(Blob(checksum=checksum, size=size, mime=mime, rel_path=rel_path, ref_count=1))
        return blob, True

    def release(self, blob: Blob | None) -> None:
        if blob is None:
            return
        blob.ref_count = max(0, blob.ref_count - 1)
        self.session.flush()

    def orphans(self) -> list[Blob]:
        return list(self.session.scalars(select(Blob).where(Blob.ref_count <= 0)))

    def total_size(self) -> int:
        return int(self.session.scalar(select(func.coalesce(func.sum(Blob.size), 0))) or 0)

    def count(self) -> int:  # type: ignore[override]
        return int(self.session.scalar(select(func.count(Blob.id))) or 0)


class ArchiveRepository(Repository[Archive]):
    model = Archive

    def latest(self, limit: int = 50) -> list[Archive]:
        stmt = select(Archive).order_by(Archive.created_at.desc(), Archive.id.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def create_archive(
        self,
        name: str,
        note: str,
        entries: list[dict],
        new_blobs: int,
        total_size: int,
        logical_size: int = 0,
    ) -> Archive:
        archive = Archive(
            name=name,
            note=note,
            item_count=len(entries),
            total_size=total_size,
            logical_size=logical_size,
            new_blobs=new_blobs,
        )
        self.add(archive)
        for data in entries:
            archive.entries.append(ArchiveEntry(**data))
        self.session.flush()
        return archive

    def entries_of(self, archive: Archive) -> list[ArchiveEntry]:
        stmt = select(ArchiveEntry).where(ArchiveEntry.archive_id == archive.id).order_by(ArchiveEntry.id)
        return list(self.session.scalars(stmt))

    def entry_by_item(self, archive: Archive, item_id: int) -> ArchiveEntry | None:
        stmt = select(ArchiveEntry).where(
            ArchiveEntry.archive_id == archive.id, ArchiveEntry.item_id == item_id
        )
        return self.session.scalars(stmt).first()

    def delete_archive(self, archive: Archive) -> None:
        self.delete(archive)
