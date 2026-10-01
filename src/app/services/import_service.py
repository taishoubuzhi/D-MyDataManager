"""导入服务：把文本或文件放进选定的库文件夹，并在内容仓库留一份备份。

数据文件在库内按「分类目录 / 原文件名」组织，因此打开库文件夹即可看到内容；
同时按 SHA-256 在内容仓库（默认库的 `.datamanager`）保存副本，供存档与查重使用。
"""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from ..core.config import config, store_dir
from ..db.models import DataItem, DataType, Library, Version, guess_type
from ..repositories import BlobRepository, ItemRepository, TagRepository
from . import feature_service
from .blob_store import BlobStore, sha256_of
from .library_service import LibraryService
from .user_service import UserService

_TEXT_LIMIT = 512 * 1024  # 超过该大小的文本文件不复制正文


@dataclass
class ImportResult:
    added: list[DataItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def added_count(self) -> int:
        return len(self.added)

    def summary(self) -> str:
        parts = [f"成功 {len(self.added)}"]
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)}")
        if self.failed:
            parts.append(f"失败 {len(self.failed)}")
        return "，".join(parts)

    def extend(self, other: "ImportResult") -> None:
        self.added.extend(other.added)
        self.skipped.extend(other.skipped)
        self.failed.extend(other.failed)


def timestamp_name(source: Path | None = None) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}{source.suffix.lower() if source else ''}"


class ImportService:
    def __init__(
        self,
        session: Session,
        library: Library | None = None,
        store: BlobStore | None = None,
    ) -> None:
        self.session = session
        self.store = store or BlobStore(store_dir())
        self.libraries = LibraryService(session, store=self.store)
        self.library = library or self.libraries.ensure_default()
        self.items = ItemRepository(session)
        self.tags = TagRepository(session)
        self.blobs = BlobRepository(session)

    # ----------------------------------------------------------------- 文本
    def import_text(
        self,
        name: str,
        content: str,
        *,
        library: Library | None = None,
        category_id: int | None = None,
        user_id: int | None = None,
        keywords: list[str] | None = None,
        tags: list[str] | None = None,
        is_hidden: bool = False,
    ) -> DataItem | None:
        target_library = library or self.library
        name = (name or "").strip()
        if not name:
            name = timestamp_name()

        policy = config.duplicatePolicy.value
        checksum, _store_rel, size = self.store.put_text(content)
        if policy == "skip" and self.items.by_checksum(checksum):
            logger.info("文本内容已存在，按策略跳过：{}", name)
            return None

        name = self._unique_name(name, policy)
        filename = name if Path(name).suffix else f"{name}.txt"
        owner_id = self._resolve_user_id(user_id)
        rel_path = self.libraries.unique_rel_path(
            target_library, category_id, filename, user_id=owner_id
        )
        (Path(target_library.path) / rel_path).write_text(content, encoding="utf-8")

        item = self.items.create(
            name=name,
            type=DataType.TEXT,
            content=content,
            keywords=list(keywords or []),
            size=size,
            mime="text/plain",
            checksum=checksum,
            file_path=rel_path,
            library_id=target_library.id,
            category_id=category_id,
            user_id=owner_id,
            is_hidden=is_hidden,
        )
        item.tags = self.tags.ensure_many(tags or [], user_id=item.user_id)
        self._register_blob(checksum, size, "text/plain")
        feature_service.replace_features(self.session, item)
        self._add_version(item, "导入")
        self.session.flush()
        logger.info("已导入文本：{} -> {}", name, rel_path)
        return item

    # ----------------------------------------------------------------- 文件
    def import_file(
        self,
        source: str | Path,
        *,
        library: Library | None = None,
        name: str | None = None,
        category_id: int | None = None,
        user_id: int | None = None,
        keywords: list[str] | None = None,
        tags: list[str] | None = None,
        is_hidden: bool = False,
    ) -> DataItem | None:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")

        target_library = library or self.library
        policy = config.duplicatePolicy.value
        display_name = name.strip() if name and name.strip() else (
            timestamp_name(path) if config.nameByTime.value else path.name
        )
        checksum = sha256_of(path)
        size = path.stat().st_size
        if policy == "skip" and self.items.by_checksum(checksum):
            logger.info("内容重复，按策略跳过：{}", path.name)
            return None

        mime = feature_service.guess_mime(path.name)
        data_type = guess_type(path.name)
        content = ""
        if data_type is DataType.TEXT and size <= _TEXT_LIMIT:
            content = path.read_text(encoding="utf-8", errors="replace")
        target_name = self._unique_name(display_name, policy)
        existing = self.items.by_checksum(checksum) if policy == "overwrite" else []
        owner_id = existing[0].user_id if existing else self._resolve_user_id(user_id)
        rel_path = self.libraries.unique_rel_path(
            target_library, category_id, target_name, user_id=owner_id
        )
        target = Path(target_library.path) / rel_path
        if existing:
            item = existing[0]
            shutil.copy2(path, target)
            self.items.update(
                item,
                name=target_name,
                content=content,
                file_path=rel_path,
                library_id=target_library.id,
                size=size,
                mime=mime,
                source_path=str(path),
                type=data_type,
            )
            self._add_version(item, "覆盖导入")
        else:
            shutil.copy2(path, target)
            item = self.items.create(
                name=target_name,
                type=data_type,
                content=content,
                keywords=list(keywords or []),
                size=size,
                mime=mime,
                checksum=checksum,
                file_path=rel_path,
                source_path=str(path),
                library_id=target_library.id,
                category_id=category_id,
                user_id=owner_id,
                is_hidden=is_hidden,
            )
            self._add_version(item, "导入")

        item.tags = self.tags.ensure_many(list(item.tag_names) + list(tags or []), user_id=item.user_id)
        if data_type is DataType.IMAGE:
            item.cover_path = feature_service.make_cover(path, checksum)
        self.store.put_file(target)
        self._register_blob(checksum, size, mime)
        feature_service.replace_features(self.session, item, path)
        self.session.flush()
        logger.info("已导入文件：{} -> {}", path.name, rel_path)
        return item

    def import_files(self, sources, **options) -> ImportResult:
        result = ImportResult()
        for source in sources:
            try:
                item = self.import_file(source, **options)
            except Exception as exc:  # 单个文件失败不影响整体
                logger.exception("导入失败：{}", source)
                result.failed.append((str(source), str(exc)))
                continue
            (result.skipped if item is None else result.added).append(
                str(source) if item is None else item
            )
        return result

    def import_directory(self, directory: str | Path, recursive: bool = True, **options) -> ImportResult:
        root = Path(directory)
        pattern = "**/*" if recursive else "*"
        files = [p for p in root.glob(pattern) if p.is_file()]
        return self.import_files(files, **options)

    # ------------------------------------------------------------- 扫描登记
    def register_file(
        self,
        path: str | Path,
        *,
        library: Library,
        rel_path: str,
        category_id: int | None = None,
        user_id: int | None = None,
        keywords: list[str] | None = None,
        tags: list[str] | None = None,
        is_hidden: bool = False,
    ) -> DataItem:
        """把库文件夹里已有的文件登记为数据项（不复制文件）。"""
        path = Path(path)
        checksum = sha256_of(path)
        size = path.stat().st_size
        mime = feature_service.guess_mime(path.name)
        data_type = guess_type(path.name)
        content = ""
        if data_type is DataType.TEXT and size <= _TEXT_LIMIT:
            content = path.read_text(encoding="utf-8", errors="replace")

        item = self.items.create(
            name=path.name,
            type=data_type,
            content=content,
            keywords=list(keywords or []),
            size=size,
            mime=mime,
            checksum=checksum,
            file_path=rel_path,
            source_path=str(path),
            library_id=library.id,
            category_id=category_id,
            user_id=self._resolve_user_id(user_id),
            is_hidden=is_hidden,
        )
        item.tags = self.tags.ensure_many(tags or [], user_id=item.user_id)
        if data_type is DataType.IMAGE:
            item.cover_path = feature_service.make_cover(path, checksum)
        self.store.put_file(path)
        self._register_blob(checksum, size, mime)
        feature_service.replace_features(self.session, item, path)
        self._add_version(item, "扫描登记")
        self.session.flush()
        return item

    # ----------------------------------------------------------------- 内部
    def _resolve_user_id(self, user_id: int | None) -> int | None:
        """未显式指定用户时归属当前用户，避免出现无主数据。"""
        if user_id is not None:
            return user_id
        return UserService(self.session).current_id()

    def _register_blob(self, checksum: str, size: int, mime: str) -> None:
        self.blobs.register(checksum, size, mime, self.store.rel_path_for(checksum))

    def _add_version(self, item: DataItem, label: str) -> None:
        blob = self.blobs.by_checksum(item.checksum)
        self.session.add(
            Version(item_id=item.id, blob_id=blob.id if blob else None, label=label, size=item.size)
        )

    def _unique_name(self, name: str, policy: str) -> str:
        if policy == "overwrite":
            return name
        exists = self.session.query(DataItem).filter(DataItem.name == name).first()
        if exists is None:
            return name
        stem, suffix = Path(name).stem, Path(name).suffix
        index = 1
        while True:
            candidate = f"{stem}_{index}{suffix}"
            if not self.session.query(DataItem).filter(DataItem.name == candidate).first():
                return candidate
            index += 1
