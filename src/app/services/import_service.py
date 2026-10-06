"""导入服务：把文本或文件放进选定的库文件夹，并在内容仓库留一份备份。

数据文件在库内按「分类目录 / 原文件名」组织，因此打开库文件夹即可看到内容；
同时按 SHA-256 在内容仓库（默认库的 `.datamanager`）保存副本，供存档与查重使用。
"""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from ..core.runtime import paths
from ..core.config import config
from ..db.models import Category, DataItem, DataType, Library, Version, guess_type
from ..db.seed import UNCATEGORIZED_NAME
from ..repositories import (
    BlobRepository,
    CategoryRepository,
    ItemRepository,
    TagRepository,
)
from . import feature_service
from .blob_store import sha256_of
from .library_service import LibraryService
from .content_store import ContentStore
from .privacy_service import guarded
from .taxonomy_service import is_uncategorized
from .user_service import UserService

_TEXT_LIMIT = 512 * 1024  # 超过该大小的文本文件不复制正文

# 批量导入时直接跳过的系统文件 / 目录，避免把噪声带进库
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_DIRS = {"__MACOSX", ".git", ".svn", "__pycache__", "node_modules", ".datamanager"}

# 进度回调：每处理完一个文件调用一次（已是最终状态）
ImportEventHook = Callable[["ImportEvent"], None]


def sanitize_subdir(subdir: str, is_hidden: bool = False) -> str:
    """库内子目录前缀：隐藏数据固定落在 <分类>/.hiddens 下，再叠加原始子目录。"""
    parts = [part for part in (subdir or "").replace("\\", "/").split("/") if part not in ("", ".", "..")]
    if is_hidden:
        parts.insert(0, paths.HIDDEN_DIR_NAME)
    return "/".join(parts)


@dataclass(frozen=True)
class ImportEvent:
    """单个文件的导入结果，用于界面实时显示进度与过程。"""

    index: int
    total: int
    source: str
    status: str  # added / skipped / failed
    detail: str = ""

    @property
    def label(self) -> str:
        return {"added": "已导入", "skipped": "已跳过", "failed": "失败"}.get(self.status, self.status)


@dataclass
class ImportResult:
    added: list[DataItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    category_name: str = ""
    total: int = 0

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
        store: ContentStore | None = None,
    ) -> None:
        self.session = session
        self.store = store or ContentStore(session)
        self.libraries = LibraryService(session, store=self.store)
        self.library = library or self.libraries.ensure_default()
        self.items = ItemRepository(session)
        self.tags = TagRepository(session)
        self.blobs = BlobRepository(session)
        self.categories = CategoryRepository(session)

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
        checksum, _store_rel, size = self.store.put_text(content, name=name, mime="text/plain")
        if policy == "skip" and self.items.by_checksum(checksum):
            logger.info("文本内容已存在，按策略跳过：{}", name)
            return None

        name = self._unique_name(name, policy)
        filename = name if Path(name).suffix else f"{name}.txt"
        owner_id = self._resolve_user_id(user_id)
        category_id = self._category_for(category_id, owner_id)
        rel_path = self.libraries.unique_rel_path(
            target_library, category_id, filename, user_id=owner_id,
            subdir=sanitize_subdir("", is_hidden),
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
        self._announce_import(item)
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
        subdir: str = "",
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

        mime = feature_service.guess_mime(path.name, path)
        data_type = guess_type(path.name)
        content = ""
        if data_type is DataType.TEXT and size <= _TEXT_LIMIT:
            content = path.read_text(encoding="utf-8", errors="replace")
        target_name = self._unique_name(display_name, policy)
        existing = self.items.by_checksum(checksum) if policy == "overwrite" else []
        owner_id = existing[0].user_id if existing else self._resolve_user_id(user_id)
        category_id = self._category_for(category_id, owner_id)
        rel_path = self.libraries.unique_rel_path(
            target_library, category_id, target_name, user_id=owner_id,
            subdir=sanitize_subdir(subdir, is_hidden),
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
        _checksum, store_rel, _size = self.store.put_file(target, name=name, mime=mime)
        self._register_blob(checksum, size, mime, store_rel)
        feature_service.replace_features(self.session, item, path)
        self.session.flush()
        logger.info("已导入文件：{} -> {}", path.name, rel_path)
        self._announce_import(item)
        return item

    def import_files(self, sources, *, on_event: ImportEventHook | None = None, **options) -> ImportResult:
        """批量导入多个文件；每个文件处理完都会回调 on_event，便于界面实时显示。"""
        result = ImportResult()
        files = [Path(source) for source in sources]
        result.total = len(files)
        for index, path in enumerate(files, start=1):
            item = self._import_one(path, result, on_event, index, len(files), **options)
            if item is not None:
                result.added.append(item)
        return result

    def import_directory(
        self,
        directory: str | Path,
        recursive: bool = True,
        *,
        on_event: ImportEventHook | None = None,
        **options,
    ) -> ImportResult:
        root = Path(directory)
        pattern = "**/*" if recursive else "*"
        files = [p for p in root.glob(pattern) if p.is_file()]
        return self.import_files(files, on_event=on_event, **options)

    def import_tree(
        self,
        directory: str | Path,
        *,
        on_event: ImportEventHook | None = None,
        **options,
    ) -> ImportResult:
        """把整个文件夹导入到某个分类下，并保留文件在文件夹里的相对子目录结构。"""
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"文件夹不存在：{root}")
        files = [path for path in sorted(root.rglob("*")) if path.is_file() and not _ignored(path, root)]
        result = ImportResult()
        result.total = len(files)
        for index, path in enumerate(files, start=1):
            relative = path.parent.relative_to(root).as_posix()
            subdir = "" if relative == "." else relative
            item = self._import_one(
                path, result, on_event, index, len(files), subdir=subdir, **options
            )
            if item is not None:
                result.added.append(item)
        return result

    def import_folder(
        self,
        directory: str | Path,
        *,
        name: str = "",
        user_id: int | None = None,
        parent_category_id: int | None = None,
        on_event: ImportEventHook | None = None,
        **options,
    ) -> ImportResult:
        """把文件夹作为一个新分类导入：新建（或复用同名）分类，原样复制整个文件夹。"""
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"文件夹不存在：{root}")
        category = self.ensure_category(
            name or root.name, user_id=user_id, parent_id=parent_category_id
        )
        options.setdefault("category_id", category.id)
        options.setdefault("user_id", user_id)
        result = self.import_tree(root, on_event=on_event, **options)
        result.category_name = category.name
        logger.info("文件夹已作为分类导入：{}（{} 个文件）", category.name, result.total)
        return result

    def ensure_category(
        self, name: str, *, user_id: int | None = None, parent_id: int | None = None
    ) -> Category:
        """按名称取分类，不存在就新建（同名同级复用，避免重复导入时堆积）。"""
        clean = (name or "").strip() or "未命名文件夹"
        if parent_id is not None and is_uncategorized(self.categories.get(parent_id)):
            # 「未分类」是固定分类，不能在其下创建子分类：文件夹改为导入成一级分类。
            logger.info("「{}」是固定分类，{} 改为导入成一级分类", UNCATEGORIZED_NAME, clean)
            parent_id = None
        created = self.categories.ensure(clean, parent_id, user_id)
        self.session.flush()
        return created

    def _announce_import(self, item: DataItem) -> None:
        """导入成功后广播事件：插件可以订阅 item.imported。"""
        from ..sdk import Events
        from .plugin_service import plugin_service

        plugin_service.publish(Events.ITEM_IMPORTED, item_id=item.id, name=item.name)

    @guarded
    def _import_one(
        self,
        path: Path,
        result: ImportResult,
        on_event: ImportEventHook | None,
        index: int,
        total: int,
        **options,
    ) -> DataItem | None:
        try:
            item = self.import_file(path, **options)
        except Exception as exc:  # 单个文件失败不影响整体
            logger.exception("导入失败：{}", path)
            result.failed.append((str(path), str(exc)))
            _report(on_event, index, total, path.name, "failed", str(exc))
            return None
        if item is None:
            result.skipped.append(str(path))
            _report(on_event, index, total, path.name, "skipped", "内容重复")
        else:
            _report(on_event, index, total, path.name, "added", item.file_path or "")
        return item

    # ------------------------------------------------------------- 扫描登记
    @guarded
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
        mime = feature_service.guess_mime(path.name, path)
        data_type = guess_type(path.name)
        content = ""
        if data_type is DataType.TEXT and size <= _TEXT_LIMIT:
            content = path.read_text(encoding="utf-8", errors="replace")

        owner_id = self._resolve_user_id(user_id)
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
            category_id=self._category_for(category_id, owner_id),
            user_id=owner_id,
            is_hidden=is_hidden,
        )
        item.tags = self.tags.ensure_many(tags or [], user_id=item.user_id)
        if data_type is DataType.IMAGE:
            item.cover_path = feature_service.make_cover(path, checksum)
        _checksum, store_rel, _size = self.store.put_file(path, name=path.name, mime=mime)
        self._register_blob(checksum, size, mime, store_rel)
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

    def _category_for(self, category_id: int | None, owner_id: int | None) -> int | None:
        """没有指定分类时归入该用户的「未分类」分类；无归属数据保持无分类。"""
        if category_id is not None or owner_id is None:
            return category_id
        category = self.categories.by_name(UNCATEGORIZED_NAME, None, owner_id)
        if category is None:
            category = self.categories.create(UNCATEGORIZED_NAME, None, user_id=owner_id)
            self.session.flush()
        return category.id

    def _register_blob(self, checksum: str, size: int, mime: str, store_rel: str | None = None) -> None:
        """登记内容引用；`store_rel` 为空串表示内容已分块、没有松散文件。"""
        if store_rel is None:
            store_rel = self.store.rel_path_for(checksum)
        self.blobs.register(checksum, size, mime, store_rel)

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


def _ignored(path: Path, root: Path) -> bool:
    """跳过系统文件与缓存目录，避免把噪声复制进库。"""
    if path.name in SKIP_NAMES or path.name.startswith("."):
        return True
    return any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1])


def _report(
    hook: ImportEventHook | None, index: int, total: int, source: str, status: str, detail: str = ""
) -> None:
    if hook is None:
        return
    try:
        hook(ImportEvent(index=index, total=total, source=source, status=status, detail=detail))
    except Exception:  # 界面回调异常不能影响导入本身
        logger.exception("导入进度回调失败")