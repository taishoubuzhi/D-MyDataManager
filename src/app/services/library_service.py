"""库文件夹服务：唯一库文件夹、用户文件夹、扫描入库与路径解析。

库文件夹是磁盘上用户可见的数据存放根目录，只允许存在一个：

    <库>/全局/                       全局资源：内容仓库 store、封面 covers、备份 backups、元数据 .datamanager
    <库>/<用户名>/<分类链>/<文件>      各用户的数据；分类目录因此可以每个用户各不相同
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import library_root, store_dir
from ..db.models import DataItem, Library, User
from ..repositories import (
    BlobRepository,
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    LibraryRepository,
    TagRepository,
    UserRepository,
)
from .blob_store import BlobStore

_INVALID_CHARS = '<>:"/\\|?*'


def sanitize_dir_name(name: str) -> str:
    """把分类名 / 用户名转换成合法的目录名。"""
    cleaned = "".join("_" if ch in _INVALID_CHARS else ch for ch in (name or "").strip())
    return cleaned.strip(" .") or "未命名"


class LibraryService:
    def __init__(self, session: Session, store: BlobStore | None = None) -> None:
        self.session = session
        self.store = store or BlobStore(store_dir())
        self.libraries = LibraryRepository(session)
        self.items = ItemRepository(session)
        self.categories = CategoryRepository(session)
        self.users = UserRepository(session)
        self.blobs = BlobRepository(session)
        self.tags = TagRepository(session)

    # ---------------------------------------------------------------- 唯一库
    def list_all(self) -> list[Library]:
        return self.libraries.all()

    def default(self) -> Library | None:
        return self.libraries.default()

    def ensure_default(self) -> Library:
        """返回唯一的库：路径取自配置，多余的历史库记录会被并入并删除。"""
        root = library_root()
        library = self.libraries.default() or next(iter(self.libraries.all()), None)
        if library is None:
            root.mkdir(parents=True, exist_ok=True)
            library = self.libraries.create(
                "默认库", str(root), description="唯一的库文件夹，用于存放用户数据与全局资源", is_default=True,
            )
            logger.info("已创建库文件夹：{}", library.path)
        else:
            self._absorb_extra_libraries(library)
            if Path(library.path) != root:
                logger.info("库文件夹路径按配置更新：{} -> {}", library.path, root)
                library.path = str(root)
            if not library.is_default:
                self.libraries.set_default(library)
        self.ensure_layout(library)
        self.session.flush()
        return library

    def _absorb_extra_libraries(self, keep: Library) -> None:
        """多库设计已取消：把其它库的数据项改挂到唯一库上，并删除多余的登记。"""
        for library in [item for item in self.libraries.all() if item.id != keep.id]:
            moved = self.items.query(
                ItemFilter(library_ids={library.id}, include_hidden=True, include_deleted=True)
            )
            for item in moved:
                item.library_id = keep.id
            self.session.flush()
            # 用 Core DELETE 删库登记：ORM 级联会把子项的 library_id 置空（FK 为 SET NULL）。
            name = library.name
            self.session.expunge(library)
            self.session.execute(delete(Library).where(Library.id == library.id))
            self.session.flush()
            logger.warning("多库设计已取消：库「{}」的 {} 项数据已并入「{}」", name, len(moved), keep.name)

    def item_count(self, library: Library) -> int:
        return self.libraries.item_count(library)

    def rebuild_layout(self) -> dict[str, Path]:
        """重建全局文件夹与所有用户的用户名文件夹。"""
        library = self.ensure_default()
        created = {"全局": self.global_dir(library)}
        created.update({user.name: self.user_dir(library, user) for user in self.users.list_all()})
        return created

    def set_path(self, new_path: str | Path) -> Path:
        """修改唯一库文件夹的位置，并把已有内容整体搬过去。"""
        library = self.ensure_default()
        old = Path(library.path)
        new = Path(new_path).expanduser().resolve()
        if new == old:
            return new
        if new.exists() and any(new.iterdir()):
            raise ValueError(f"目标文件夹不是空的：{new}")
        if old.is_dir():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        else:
            new.mkdir(parents=True, exist_ok=True)
        from qfluentwidgets import qconfig

        from ..core.config import config

        qconfig.set(config.libraryPath, str(new))
        library.path = str(new)
        self.ensure_layout(library)
        self.session.flush()
        logger.info("库文件夹已迁移：{} -> {}", old, new)
        return new

    # ---------------------------------------------------------------- 路径
    def global_dir(self, library: Library) -> Path:
        return Path(library.path) / paths.GLOBAL_DIR_NAME

    def meta_dir(self, library: Library) -> Path:
        """库内全局文件夹下的隐藏元数据目录。"""
        return self.global_dir(library) / paths.LIBRARY_META_DIR

    def store_dir(self, library: Library) -> Path:
        return self.global_dir(library) / paths.LIBRARY_STORE_DIRNAME

    def cover_dir(self, library: Library) -> Path:
        return self.global_dir(library) / paths.LIBRARY_COVER_DIRNAME

    def backup_dir(self, library: Library) -> Path:
        return self.global_dir(library) / paths.LIBRARY_BACKUP_DIRNAME

    def ensure_layout(self, library: Library) -> None:
        """建立全局文件夹与每个用户的用户名文件夹（幂等）。"""
        for directory in paths.global_subdirs(Path(library.path)):
            directory.mkdir(parents=True, exist_ok=True)
        for user in self.users.list_all():
            self.user_dir(library, user).mkdir(parents=True, exist_ok=True)

    def user_dir(self, library: Library, user: User) -> Path:
        """该用户在库内的用户名文件夹。"""
        return Path(library.path) / sanitize_dir_name(user.name)

    def user_by_dir(self, library: Library, dir_name: str) -> User | None:
        """按用户名文件夹反查用户。"""
        for user in self.users.list_all():
            if sanitize_dir_name(user.name) == dir_name:
                return user
        return None

    def owner_dir_name(self, user_id: int | None) -> str:
        """数据项所属用户的用户名文件夹名；没有归属时使用「未归属」。"""
        user = self.session.get(User, user_id) if user_id else None
        return sanitize_dir_name(user.name) if user is not None else paths.UNASSIGNED_DIR_NAME

    def rename_user_dir(self, user: User, old_name: str) -> Path | None:
        """用户改名后同步重命名其用户名文件夹，并改写其数据项的库内路径。"""
        library = self.ensure_default()
        old_dir_name = sanitize_dir_name(old_name)
        new_dir_name = sanitize_dir_name(user.name)
        if old_dir_name == new_dir_name:
            return self.user_dir(library, user)
        old = Path(library.path) / old_dir_name
        new = Path(library.path) / new_dir_name
        if old.is_dir():
            if new.exists():
                self._move_into(old, new)
            else:
                old.rename(new)
        new.mkdir(parents=True, exist_ok=True)
        prefix = f"{old_dir_name}/"
        updated = 0
        for item in self.session.scalars(
            select(DataItem).where(DataItem.user_id == user.id, DataItem.file_path.like(f"{prefix}%"))
        ):
            item.file_path = f"{new_dir_name}/{item.file_path[len(prefix):]}"
            updated += 1
        self.session.flush()
        logger.info("用户名文件夹已重命名：{} -> {}（改写 {} 项路径）", old_dir_name, new_dir_name, updated)
        return new

    def dir_name_of(self, name: str) -> str:
        """用户名 / 分类名对应的目录名。"""
        return sanitize_dir_name(name)

    def relocate_user_dir(self, library: Library, old_name: str, new_parent: str) -> Path:
        """把用户文件夹整目录搬进另一个用户目录下（删除用户时保留其分类结构）。"""
        old_dir_name = sanitize_dir_name(old_name)
        source = Path(library.path) / old_dir_name
        target = Path(library.path) / sanitize_dir_name(new_parent) / old_dir_name
        if source.is_dir():
            if target.exists():
                self._move_into(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
            logger.info("用户文件夹已并入：{} -> {}", old_dir_name, target)
        return target

    def category_chain(self, category_id: int | None) -> list[str]:
        names: list[str] = []
        category = self.categories.get(category_id) if category_id else None
        while category is not None:
            names.append(sanitize_dir_name(category.name))
            category = category.parent
        return list(reversed(names))

    def directory_for(
        self, library: Library, category_id: int | None, user_id: int | None = None
    ) -> Path:
        """数据文件应放到的目录：<库>/<用户名>/<分类链>。"""
        return Path(library.path).joinpath(self.owner_dir_name(user_id), *self.category_chain(category_id))

    def unique_rel_path(
        self, library: Library, category_id: int | None, filename: str, user_id: int | None = None
    ) -> str:
        """在库内为 filename 取一个不冲突的相对路径（POSIX 风格）。"""
        base = self.directory_for(library, category_id, user_id)
        base.mkdir(parents=True, exist_ok=True)
        stem, suffix = Path(filename).stem or "未命名", Path(filename).suffix
        candidate = base / f"{stem}{suffix}"
        index = 1
        while candidate.exists():
            candidate = base / f"{stem}_{index}{suffix}"
            index += 1
        return candidate.relative_to(Path(library.path)).as_posix()

    def abs_path(self, item) -> Path | None:
        if not item.file_path or not item.library_id:
            return None
        library = item.library or self.libraries.get(item.library_id)
        if library is None:
            return None
        return Path(library.path) / item.file_path

    def _move_into(self, source: Path, target: Path) -> None:
        """把 source 目录的内容合并进 target（改名或迁移时的冲突处理）。"""
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(source.iterdir()):
            destination = target / entry.name
            if entry.is_dir() and destination.is_dir():
                self._move_into(entry, destination)
            elif destination.exists():
                destination = target / f"{entry.stem}_{len(list(target.iterdir()))}{entry.suffix}"
                shutil.move(str(entry), str(destination))
            else:
                shutil.move(str(entry), str(destination))
        try:
            source.rmdir()
        except OSError:
            pass

    # ---------------------------------------------------------------- 扫描
    def scan(self, library: Library, *, category_id: int | None = None, recursive: bool = True):
        """把库文件夹里已有的文件登记为数据项（只登记用户名文件夹下的文件）。"""
        from .import_service import ImportResult, ImportService

        service = ImportService(self.session, library=library, store=self.store)
        result = ImportResult()
        root = Path(library.path)
        if not root.is_dir():
            result.failed.append((str(root), "库文件夹不存在"))
            return result

        known = self.items.library_paths(library.id)
        pattern = "**/*" if recursive else "*"
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if any(part.startswith(".") for part in parts) or parts[0] == paths.GLOBAL_DIR_NAME:
                continue
            rel = path.relative_to(root).as_posix()
            if rel in known:
                result.skipped.append(rel)
                continue
            owner = self.user_by_dir(library, parts[0]) if len(parts) > 1 else None
            if owner is None:
                logger.warning("扫描跳过无法识别所属用户的文件：{}", rel)
                result.skipped.append(rel)
                continue
            target_category = (
                category_id if category_id is not None else self._match_category(parts[1:-1], owner.id)
            )
            try:
                item = service.register_file(
                    path, library=library, rel_path=rel, category_id=target_category, user_id=owner.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("登记库文件失败：{}", path)
                result.failed.append((str(path), str(exc)))
                continue
            result.added.append(item)

        library.last_scan_at = dt.datetime.now()
        self.session.flush()
        logger.info("库「{}」扫描完成：登记 {} 项，跳过 {} 项", library.name, len(result.added), len(result.skipped))
        return result

    def _match_category(self, parts: tuple[str, ...], user_id: int | None = None) -> int | None:
        """把库内的目录层级映射到同名分类，找不到就停在上一层。"""
        parent_id: int | None = None
        for part in parts:
            category = self.categories.by_name(part, parent_id, user_id=user_id)
            if category is None:
                return parent_id
            parent_id = category.id
        return parent_id
