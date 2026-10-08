"""整库包：把数据库与库文件夹打成一个 `.zip`，换台机器也能整份搬过去。

包里的结构：

```
数据库包.json      # 清单：格式 / 版本 / 程序版本 / 库结构版本 / 数量 / 时间
data/data.db       # 数据库快照（SQLite 在线备份导出，不带 WAL 边车）
library/**         # 库文件夹里的全部内容（数据文件、内容仓库、封面、备份、元数据）
```

两种导入方式：

* **新增式（merge）**：按名字并用户 / 分类 / 标签，数据项与存档各建新记录，
  库文件按需落盘（同名文件加 `_1` 后缀并改指向），**不删任何现有数据**；
* **覆盖式（replace）**：先把现有数据库与库文件夹改名备份，再把当前的库原地清空
  （删表重建 + 播种默认数据），然后用新增式把包里的内容整份搬回来，不可撤销；
  要求包里的库结构版本不高于本程序。

覆盖式清空是原地做的，所以别的页面开着的会话不会挡住它；写包走
`app.core.export.ZipPack`：先写 `<包名>.zip.part`，成了再原子改名。无论哪种方式
都先读清单，读不了就直接拒绝，一个字节都不动。
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from loguru import logger

from ..core import config
from ..core.export import ZipPack
from ..core.runtime.paths import GLOBAL_DIR_NAME, LIBRARY_STORE_DIRNAME, ensure_dirs
from ..core.runtime.sizes import human_size
from ..core.runtime.version import APP_VERSION
from ..db import database
from ..db.models import Category, DataItem, DataType, Feature, Tag, User, Version
from ..db.seed import seed
from ..repositories.archives import ArchiveRepository, BlobRepository
from ..repositories.categories import CategoryRepository
from ..repositories.items import ItemRepository
from ..repositories.libraries import LibraryRepository
from ..repositories.tags import TagRepository
from ..repositories.users import UserRepository
from .content_store import CODEC_RAW, ContentStore, iter_decoded, sha256_of
from .taxonomy_service import TaxonomyService

#: 清单文件名与格式标识（换格式时递增 VERSION，旧包仍能读）。
MANIFEST_NAME = "数据库包.json"
DB_MEMBER = "data/data.db"
LIBRARY_PREFIX = "library/"
FORMAT = "dm-database"
VERSION = 1

MODE_MERGE = "merge"
MODE_REPLACE = "replace"
MODE_LABELS = ((MODE_MERGE, "新增式导入"), (MODE_REPLACE, "覆盖式导入"))

#: 清单里记几类数量（顺带当作完整性检查的指标）。
COUNT_KEYS = ("users", "categories", "tags", "items", "archives", "contents")

#: 打库内文件时跳过的东西：不进包的缓存与半成品。
SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db", ".git"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".part", ".tmp")

#: 内容文件名必须是完整 sha256，别的一律不认（顺带挡住路径穿越）。
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024


class BundleError(RuntimeError):
    """整库包读不了 / 写不进 / 结构不认识。"""


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _backup_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _parse_stamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _json_value(value: Any, fallback: Any = None) -> Any:
    """数据库里 JSON 列存的是文本，读出来还原成对象。"""
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except ValueError:
        return fallback


def _data_type(value: Any) -> DataType:
    try:
        return DataType(str(value or "").upper())
    except ValueError:
        return DataType.OTHER


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _remap_path(value: Any, remap: dict[str, str]) -> str:
    text = str(value or "")
    if not text or not remap:
        return text
    return remap.get(_norm_path(text), text)


def _unique_name(name: str, existing: set[str]) -> str:
    """重名时依次加「（导入）」「（导入 2）」…，绝不覆盖。"""
    if name not in existing:
        return name
    for index in range(1, 1000):
        label = f"{name}（导入）" if index == 1 else f"{name}（导入 {index}）"
        if label not in existing:
            return label
    return f"{name}（导入 {uuid4().hex[:6]}）"


def _skip_rel(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in SKIP_NAMES for part in parts):
        return True
    return rel.lower().endswith(SKIP_SUFFIXES)


def _unique_rel(root: Path, rel: str) -> str:
    """同名库文件挪个位置：`a.bin` → `a_1.bin`。"""
    path = Path(rel)
    stem = path.stem
    suffix = "".join(path.suffixes)
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not (root / candidate).exists():
            return candidate.as_posix()
    return path.with_name(f"{stem}_{uuid4().hex[:6]}{suffix}").as_posix()


def _read_chunks(path: Path, size: int = _CHUNK) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(size)
            if not chunk:
                return
            yield chunk


def _library_members(names: Iterable[str]) -> list[str]:
    """包里属于库文件夹的成员（跳过目录与缓存文件）。"""
    result: list[str] = []
    for name in names:
        if not name.startswith(LIBRARY_PREFIX) or name.endswith("/"):
            continue
        rel = name[len(LIBRARY_PREFIX) :]
        if not rel or _skip_rel(rel):
            continue
        result.append(name)
    return result


def _read_manifest(bundle: ZipFile, source: Path) -> dict[str, Any]:
    try:
        raw = bundle.read(MANIFEST_NAME)
    except KeyError as exc:
        raise BundleError(f"这不是整库包（包里没有 {MANIFEST_NAME}）：{source}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"整库包里的清单读不出来：{source}（{exc}）") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise BundleError(f"这不是本程序的整库包：{source}")
    return payload


def _int_or(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class _SourceDB:
    """只读地看包里的数据库快照（stdlib sqlite3，不建第二个 SQLAlchemy 引擎）。"""

    def __init__(self, path: str | Path, *, readonly: bool = True) -> None:
        self.path = Path(path)
        if readonly:
            self._conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        else:
            self._conn = sqlite3.connect(str(self.path))

    def __enter__(self) -> _SourceDB:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover - 关不掉也没别的办法
            pass

    def rows(self, table: str) -> list[dict[str, Any]]:
        try:
            cursor = self._conn.execute(f'SELECT * FROM "{table}"')
        except sqlite3.Error:
            return []
        names = [item[0] for item in (cursor.description or ())]
        return [dict(zip(names, values)) for values in cursor.fetchall()]

    def count(self, table: str) -> int:
        try:
            row = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
        except sqlite3.Error:
            return 0
        return _int_or(row[0] if row else 0)

    def schema_version(self) -> int:
        for row in self.rows("app_meta"):
            if str(row.get("key") or "") == "schema_version":
                return _int_or(row.get("value"), 0)
        return 0


@dataclass
class DatabaseInfo:
    """整库包的概要（只读清单，不用解完整包）。"""

    path: Path
    created_at: str = ""
    app_version: str = ""
    schema_version: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    files: int = 0
    size: int = 0

    def _count(self, key: str) -> int:
        return int(self.counts.get(key, 0) or 0)

    @property
    def users(self) -> int:
        return self._count("users")

    @property
    def categories(self) -> int:
        return self._count("categories")

    @property
    def tags(self) -> int:
        return self._count("tags")

    @property
    def items(self) -> int:
        return self._count("items")

    @property
    def archives(self) -> int:
        return self._count("archives")

    @property
    def contents(self) -> int:
        return self._count("contents")

    def summary(self) -> str:
        return (
            f"{self.items} 个数据项、{self.archives} 份存档、{self.files} 个库内文件"
            f"（结构版本 {self.schema_version}，{human_size(self.size)}）"
        )


@dataclass
class DatabaseExportResult:
    """整库导出的结果。"""

    path: Path
    size: int = 0
    files: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    skipped: int = 0

    def summary(self) -> str:
        text = (
            f"已导出 {self.counts.get('items', 0)} 个数据项、"
            f"{self.counts.get('archives', 0)} 份存档、{self.files} 个库内文件"
            f"（{human_size(self.size)}）到 {self.path.parent}"
        )
        if self.skipped:
            text += f"，{self.skipped} 个文件读不了被跳过"
        return text


@dataclass
class DatabaseImportResult:
    """整库导入的结果。"""

    mode: str = MODE_MERGE
    users: int = 0
    categories: int = 0
    tags: int = 0
    items: int = 0
    archives: int = 0
    contents: int = 0
    files: int = 0
    skipped: int = 0
    missing: int = 0
    backup: str = ""

    @property
    def merge(self) -> bool:
        return self.mode == MODE_MERGE

    def summary(self) -> str:
        if not self.merge:
            text = (
                f"覆盖式导入完成：库里有 {self.items} 个数据项、{self.archives} 份存档、"
                f"{self.files} 个库内文件"
            )
            return text + (f"；原数据已备份到 {self.backup}" if self.backup else "")
        text = (
            f"新增式导入完成：{self.users} 个用户、{self.categories} 个分类、"
            f"{self.tags} 个标签、{self.items} 个数据项、{self.archives} 份存档"
        )
        if self.files:
            text += f"、{self.files} 个库内文件"
        if self.missing:
            text += f"；{self.missing} 份内容在包里缺失，已跳过"
        return text


@dataclass
class _ContentMap:
    """包里内容 → 本机内容仓库的对应关系。"""

    by_checksum: dict[str, int] = field(default_factory=dict)
    by_pkg_blob: dict[int, str] = field(default_factory=dict)
    restored: int = 0
    reused: int = 0
    missing: int = 0

    def blob_of(self, pkg_blob_id: Any) -> int | None:
        if pkg_blob_id is None:
            return None
        checksum = self.by_pkg_blob.get(_int_or(pkg_blob_id))
        if not checksum:
            return None
        return self.by_checksum.get(checksum)


def _read_counts(source: _SourceDB) -> dict[str, int]:
    return {key: source.count(key) for key in COUNT_KEYS}


def inspect_package(source: str | Path) -> DatabaseInfo:
    """看一个整库包的清单（顺带确认包里有数据库快照）。"""
    path = Path(source)
    if not path.is_file():
        raise BundleError(f"找不到整库包：{path}")
    try:
        with ZipFile(path) as bundle:
            payload = _read_manifest(bundle, path)
            names = bundle.namelist()
            if DB_MEMBER not in names:
                raise BundleError(f"这不是完整的整库包（包里没有 {DB_MEMBER}）：{path}")
            files = len(_library_members(names))
    except BadZipFile as exc:
        raise BundleError(f"这不是一个压缩包：{path}（{exc}）") from exc
    counts = payload.get("counts")
    return DatabaseInfo(
        path=path,
        created_at=str(payload.get("created_at") or ""),
        app_version=str(payload.get("app_version") or ""),
        schema_version=_int_or(payload.get("schema_version"), 0),
        counts={str(key): _int_or(value) for key, value in counts.items()} if isinstance(counts, dict) else {},
        files=files,
        size=path.stat().st_size,
    )


class DatabaseBundleService:
    """整库包的导出与新增式导入（覆盖式见模块级 `import_replace`）。"""

    def __init__(self, session: Any = None, store: ContentStore | None = None) -> None:
        self._own = session is None
        self.session = session if session is not None else database.new_session()
        self.store = store if store is not None else ContentStore(self.session)
        self.users = UserRepository(self.session)
        self.libraries = LibraryRepository(self.session)
        self.categories = CategoryRepository(self.session)
        self.tags = TagRepository(self.session)
        self.items = ItemRepository(self.session)
        self.archives = ArchiveRepository(self.session)
        self.blobs = BlobRepository(self.session)
        self.taxonomy = TaxonomyService(self.session)

    # -- 生命周期 ---------------------------------------------------------

    def close(self) -> None:
        if self._own:
            self.session.close()

    def __enter__(self) -> DatabaseBundleService:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- 路径工具 ---------------------------------------------------------

    def _rel_under_library(self, target: Path) -> str:
        try:
            return target.relative_to(config.library_root()).as_posix()
        except ValueError:
            return ""

    def _store_prefix(self) -> str:
        return self._rel_under_library(config.store_dir()) or f"{GLOBAL_DIR_NAME}/{LIBRARY_STORE_DIRNAME}"

    def _cover_prefix(self) -> str:
        return self._rel_under_library(config.cover_dir())

    def _content_member(self, checksum: str) -> str:
        prefix = self._store_prefix()
        return f"{LIBRARY_PREFIX}{prefix}/{checksum[:2]}/{checksum[2:4]}/{checksum}"

    def _library_id(self) -> int | None:
        library = self.libraries.default()
        if library is None:
            rows = self.libraries.all()
            library = rows[0] if rows else None
        return int(library.id) if library is not None and library.id is not None else None

    def _library_files(self) -> list[Path]:
        root = config.library_root()
        if not root.is_dir():
            return []
        result: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _skip_rel(rel):
                continue
            result.append(path)
        return result

    # -- 导出 -------------------------------------------------------------

    def export(self, target: str | Path) -> DatabaseExportResult:
        """把数据库快照与库文件夹打成一个整库包。"""
        path = Path(target)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        with tempfile.TemporaryDirectory(prefix="dm_dbexport_") as tmp:
            snapshot = Path(tmp) / "data.db"
            if database.backup_database_file(snapshot) is None:
                raise BundleError(f"没有可导出的数据库：{database.db_file()}")
            with _SourceDB(snapshot) as source:
                counts = _read_counts(source)
            payload = {
                "format": FORMAT,
                "version": VERSION,
                "app_version": APP_VERSION,
                "schema_version": database.SCHEMA_VERSION,
                "created_at": _stamp(),
                "counts": counts,
            }
            files = 0
            skipped = 0
            with ZipPack(path) as pack:
                pack.add_bytes(MANIFEST_NAME, _manifest_bytes(payload))
                pack.add_file(DB_MEMBER, snapshot)
                for file in self._library_files():
                    member = f"{LIBRARY_PREFIX}{file.relative_to(config.library_root()).as_posix()}"
                    try:
                        pack.add_stream(member, _read_chunks(file))
                    except OSError as exc:
                        logger.warning("整库导出：库内文件读不了，跳过 {}（{}）", file, exc)
                        skipped += 1
                        continue
                    files += 1
                final = Path(pack.close())
        result = DatabaseExportResult(
            path=final,
            size=final.stat().st_size if final.is_file() else 0,
            files=files,
            counts=counts,
            skipped=skipped,
        )
        logger.info("整库导出：{}", result.summary())
        return result

    def inspect(self, source: str | Path) -> DatabaseInfo:
        return inspect_package(source)

    # -- 新增式导入 -------------------------------------------------------

    def import_merge(self, source: str | Path, *, commit: bool = True) -> DatabaseImportResult:
        """把包里的数据并进现有库：只新增，不删不改现有记录。"""
        info = inspect_package(source)
        result = DatabaseImportResult(mode=MODE_MERGE)
        with ZipFile(info.path) as bundle:
            names = bundle.namelist()
            with tempfile.TemporaryDirectory(prefix="dm_dbimport_") as tmp:
                db_path = Path(tmp) / "data.db"
                with bundle.open(DB_MEMBER) as src, open(db_path, "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
                with _SourceDB(db_path) as source_db:
                    if source_db.schema_version() > database.SCHEMA_VERSION:
                        raise BundleError(
                            f"包里的库结构版本比本程序新"
                            f"（{source_db.schema_version()} > {database.SCHEMA_VERSION}），先升级程序：{info.path}"
                        )
                    users, created_users = self._merge_users(source_db)
                    libraries = self._merge_libraries(source_db)
                    categories, created_categories = self._merge_categories(source_db, users)
                    tags, created_tags = self._merge_tags(source_db, users)
                    contents = self._restore_contents(bundle, source_db, names)
                    remap, files, skipped = self._restore_library(bundle, names)
                    items, created_items = self._merge_items(
                        source_db, users, categories, tags, libraries, contents, remap
                    )
                    versions = self._merge_versions(source_db, items, contents)
                    self._merge_features(source_db, items)
                    archives = self._merge_archives(source_db, users, items)
                    self.session.flush()
        result.users = created_users
        result.categories = created_categories
        result.tags = created_tags
        result.items = created_items
        result.archives = archives
        result.contents = contents.restored
        result.files = files
        result.skipped = skipped
        result.missing = contents.missing
        if commit:
            self.session.commit()
        logger.info("整库导入（新增式）：{}", result.summary())
        return result

    def _merge_users(self, source: _SourceDB) -> tuple[dict[int, int], int]:
        mapping: dict[int, int] = {}
        pending: list[tuple[int, User]] = []
        created = 0
        for row in source.rows("users"):
            old_id = row.get("id")
            name = str(row.get("name") or "").strip()
            if old_id is None or not name:
                continue
            user = self.users.by_name(name)
            if user is None:
                user = self.users.create(
                    name,
                    str(row.get("password_hash") or ""),
                    bool(row.get("is_hidden") or 0),
                    False,
                )
                created += 1
            pending.append((_int_or(old_id), user))
        self.session.flush()
        for old_id, user in pending:
            if user.id is not None:
                mapping[old_id] = int(user.id)
        return mapping, created

    def _merge_libraries(self, source: _SourceDB) -> dict[int, int | None]:
        """包里可能有多个库，本程序只有一个库根：全部落到本机默认库。"""
        target = self._library_id()
        mapping: dict[int, int | None] = {}
        for row in source.rows("libraries"):
            if row.get("id") is not None:
                mapping[_int_or(row.get("id"))] = target
        return mapping

    def _ensure_category(self, row: dict[str, Any], parent_id: int | None, user_id: int | None) -> tuple[Category | None, bool]:
        name = str(row.get("name") or "").strip()
        if not name:
            return None, False
        found = self.categories.by_name(name, parent_id, user_id)
        if found is not None:
            return found, False
        safe = self.categories.unique_sibling_name(name, parent_id, user_id)
        category = self.categories.create(
            safe,
            parent_id=parent_id,
            description=str(row.get("description") or ""),
            icon=str(row.get("icon") or ""),
            color=str(row.get("color") or ""),
            user_id=user_id,
            is_hidden=bool(row.get("is_hidden") or 0),
        )
        category.sort_order = _int_or(row.get("sort_order"))
        stamp = _parse_stamp(row.get("created_at"))
        if stamp is not None:
            category.created_at = stamp
        return category, True

    def _merge_categories(
        self, source: _SourceDB, users: dict[int, int]
    ) -> tuple[dict[int, int], int]:
        """按父先子后的顺序并分类；父分类找不到就挂到根上。"""
        rows = [row for row in source.rows("categories") if row.get("id") is not None]
        mapping: dict[int, int] = {}
        created = 0
        pending = rows
        while pending:
            rest: list[dict[str, Any]] = []
            progressed = False
            for row in pending:
                parent_old = row.get("parent_id")
                if parent_old is not None and _int_or(parent_old) not in mapping:
                    rest.append(row)
                    continue
                parent_new = mapping.get(_int_or(parent_old)) if parent_old is not None else None
                user_old = row.get("user_id")
                user_new = users.get(_int_or(user_old)) if user_old is not None else None
                category, is_new = self._ensure_category(row, parent_new, user_new)
                if is_new:
                    created += 1
                if category is not None and category.id is not None:
                    mapping[_int_or(row.get("id"))] = int(category.id)
                progressed = True
            if not progressed:
                for row in rest:
                    user_old = row.get("user_id")
                    user_new = users.get(_int_or(user_old)) if user_old is not None else None
                    category, is_new = self._ensure_category(row, None, user_new)
                    if is_new:
                        created += 1
                    if category is not None and category.id is not None:
                        mapping[_int_or(row.get("id"))] = int(category.id)
                break
            pending = rest
        self.session.flush()
        return mapping, created

    def _merge_tags(self, source: _SourceDB, users: dict[int, int]) -> tuple[dict[int, int], int]:
        mapping: dict[int, int] = {}
        created = 0
        for row in source.rows("tags"):
            old_id = row.get("id")
            name = str(row.get("name") or "").strip()
            if old_id is None or not name:
                continue
            user_old = row.get("user_id")
            user_new = users.get(_int_or(user_old)) if user_old is not None else None
            is_global = bool(row.get("is_global") or 0) or user_new is None
            owner = None if is_global else user_new
            before = self.tags.by_name(name, user_id=owner)
            tag = self.tags.ensure(
                name,
                str(row.get("description") or ""),
                str(row.get("color") or ""),
                owner,
                is_global,
            )
            if before is None:
                created += 1
            if tag.id is not None:
                mapping[_int_or(old_id)] = int(tag.id)
        self.session.flush()
        return mapping, created

    def _restore_contents(
        self, bundle: ZipFile, source: _SourceDB, names: list[str]
    ) -> _ContentMap:
        """把包里的内容写回本机内容仓库（已在库里的只补一条引用）。"""
        result = _ContentMap()
        pending: dict[str, Any] = {}
        for row in source.rows("contents"):
            checksum = str(row.get("checksum") or "").strip().lower()
            if not _CHECKSUM_RE.match(checksum):
                continue
            if not self.store.content_available(checksum):
                member = self._content_member(checksum)
                if member not in names:
                    logger.warning("整库包里缺内容：{}", checksum[:12])
                    result.missing += 1
                    continue
                if not self._write_content(
                    bundle,
                    member,
                    checksum,
                    str(row.get("codec") or CODEC_RAW),
                    str(row.get("name") or ""),
                    str(row.get("mime") or ""),
                ):
                    result.missing += 1
                    continue
                result.restored += 1
            else:
                result.reused += 1
            blob, _is_new = self.blobs.register(
                checksum,
                _int_or(row.get("size")),
                str(row.get("mime") or ""),
                self.store.loose_rel_path(checksum),
            )
            pending[checksum] = blob
        self.session.flush()
        result.by_checksum = {
            checksum: int(blob.id)
            for checksum, blob in pending.items()
            if blob.id is not None
        }
        for row in source.rows("blobs"):
            if row.get("id") is None:
                continue
            result.by_pkg_blob[_int_or(row.get("id"))] = str(row.get("checksum") or "").strip().lower()
        return result

    def _write_content(
        self, bundle: ZipFile, member: str, checksum: str, codec: str, name: str, mime: str
    ) -> bool:
        """解压一份内容、解码回原始字节、比对校验和，对了再入库。"""
        with tempfile.TemporaryDirectory(prefix="dm_dbcontent_") as tmp:
            packed = Path(tmp) / "packed.bin"
            raw = Path(tmp) / "raw.bin"
            try:
                with bundle.open(member) as src, open(packed, "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
                with open(raw, "wb") as out:
                    for chunk in iter_decoded(packed, codec):
                        out.write(chunk)
                actual = sha256_of(raw)
                if actual != checksum:
                    logger.warning(
                        "整库包里的内容与校验和对不上，跳过：{}（实为 {}）",
                        checksum[:12],
                        actual[:12],
                    )
                    return False
                self.store.put_file(raw, name=name, mime=mime)
                return True
            except (OSError, ValueError, BadZipFile) as exc:
                logger.warning("整库包里写回内容失败，跳过：{}（{}）", checksum[:12], exc)
                return False

    def _restore_library(
        self, bundle: ZipFile, names: list[str]
    ) -> tuple[dict[str, str], int, int]:
        """把库文件夹里的文件按需落盘，返回「原相对路径 → 新相对路径」的重名映射。"""
        root = config.library_root()
        store_prefix = f"{LIBRARY_PREFIX}{self._store_prefix()}"
        cover_prefix = f"{LIBRARY_PREFIX}{self._cover_prefix()}"
        remap: dict[str, str] = {}
        files = 0
        skipped = 0
        for member in names:
            if not member.startswith(LIBRARY_PREFIX) or member.endswith("/"):
                continue
            rel = member[len(LIBRARY_PREFIX) :]
            if not rel or _skip_rel(rel):
                skipped += 1
                continue
            if member.startswith(store_prefix + "/"):
                # 内容仓库按校验和单独搬（见 _restore_contents）
                continue
            target = root / rel
            if target.exists():
                if cover_prefix and member.startswith(cover_prefix + "/"):
                    skipped += 1
                    continue
                new_rel = _unique_rel(root, rel)
                remap[_norm_path(rel)] = new_rel
                target = root / new_rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, _CHUNK)
            except OSError as exc:
                logger.warning("整库导入：库内文件写不进去，跳过 {}（{}）", rel, exc)
                skipped += 1
                continue
            files += 1
        return remap, files, skipped

    def _merge_items(
        self,
        source: _SourceDB,
        users: dict[int, int],
        categories: dict[int, int],
        tags: dict[int, int],
        libraries: dict[int, int | None],
        contents: _ContentMap,
        remap: dict[str, str],
    ) -> tuple[dict[int, int], int]:
        mapping: dict[int, int] = {}
        pending: list[tuple[int, DataItem]] = []
        created = 0
        for row in source.rows("items"):
            old_id = row.get("id")
            if old_id is None:
                continue
            user_old = row.get("user_id")
            user_new = users.get(_int_or(user_old)) if user_old is not None else None
            category_old = row.get("category_id")
            category_new = categories.get(_int_or(category_old)) if category_old is not None else None
            if category_new is None:
                # 分类没并进来（或被删了）就落到「未分类」，别让数据项悬空
                uncategorized = self.taxonomy.uncategorized_category(user_new, create=True)
                category_new = int(uncategorized.id) if uncategorized is not None else None
            library_old = row.get("library_id")
            library_new = (
                libraries.get(_int_or(library_old)) if library_old is not None else self._library_id()
            )
            fields: dict[str, Any] = {
                "name": str(row.get("name") or ""),
                "type": _data_type(row.get("type")),
                "content": str(row.get("content") or ""),
                "keywords": _json_value(row.get("keywords"), []) or [],
                "file_path": _remap_path(row.get("file_path"), remap),
                "source_path": "",
                "cover_path": _remap_path(row.get("cover_path"), remap),
                "size": _int_or(row.get("size")),
                "mime": str(row.get("mime") or ""),
                "checksum": str(row.get("checksum") or ""),
                "extra": _json_value(row.get("extra"), {}) or {},
                "library_id": library_new,
                "category_id": category_new,
                "user_id": user_new,
                "is_hidden": bool(row.get("is_hidden") or 0),
                "is_deleted": bool(row.get("is_deleted") or 0),
            }
            for key in ("created_at", "updated_at", "deleted_at"):
                stamp = _parse_stamp(row.get(key))
                if stamp is not None:
                    fields[key] = stamp
            item = self.items.create(**fields)
            pending.append((_int_or(old_id), item))
            created += 1
        self.session.flush()
        items_by_id: dict[int, DataItem] = {}
        for old_id, item in pending:
            if item.id is None:
                continue
            mapping[old_id] = int(item.id)
            items_by_id[int(item.id)] = item
        for row in source.rows("item_tags"):
            new_item_id = mapping.get(_int_or(row.get("item_id")))
            new_tag_id = tags.get(_int_or(row.get("tag_id")))
            if new_item_id is None or new_tag_id is None:
                continue
            item = items_by_id.get(new_item_id)
            tag = self.session.get(Tag, new_tag_id)
            if item is not None and tag is not None and tag not in item.tags:
                item.tags.append(tag)
        self.session.flush()
        return mapping, created

    def _merge_versions(
        self, source: _SourceDB, items: dict[int, int], contents: _ContentMap
    ) -> int:
        created = 0
        for row in source.rows("versions"):
            new_item = items.get(_int_or(row.get("item_id")))
            if new_item is None:
                continue
            fields: dict[str, Any] = {
                "item_id": new_item,
                "blob_id": contents.blob_of(row.get("blob_id")),
                "label": str(row.get("label") or ""),
                "note": str(row.get("note") or ""),
                "size": _int_or(row.get("size")),
            }
            stamp = _parse_stamp(row.get("created_at"))
            if stamp is not None:
                fields["created_at"] = stamp
            self.session.add(Version(**fields))
            created += 1
        return created

    def _merge_features(self, source: _SourceDB, items: dict[int, int]) -> int:
        created = 0
        for row in source.rows("features"):
            new_item = items.get(_int_or(row.get("item_id")))
            if new_item is None:
                continue
            fields: dict[str, Any] = {
                "item_id": new_item,
                "kind": str(row.get("kind") or ""),
                "value": _json_value(row.get("value")),
            }
            stamp = _parse_stamp(row.get("created_at"))
            if stamp is not None:
                fields["created_at"] = stamp
            self.session.add(Feature(**fields))
            created += 1
        return created

    def _merge_archives(
        self, source: _SourceDB, users: dict[int, int], items: dict[int, int]
    ) -> int:
        entries_by_archive: dict[int, list[dict[str, Any]]] = {}
        for row in source.rows("archive_entries"):
            archive_old = row.get("archive_id")
            if archive_old is None:
                continue
            entries_by_archive.setdefault(_int_or(archive_old), []).append(row)
        existing = {archive.name for archive in self.archives.latest(limit=1000)}
        created = 0
        for row in source.rows("archives"):
            old_id = row.get("id")
            name = str(row.get("name") or "导入的存档")
            safe_name = _unique_name(name, existing)
            existing.add(safe_name)
            entries: list[dict[str, Any]] = []
            for entry in entries_by_archive.get(_int_or(old_id), ()) if old_id is not None else ():
                item_old = entry.get("item_id")
                user_old = entry.get("user_id")
                entries.append(
                    {
                        "item_id": items.get(_int_or(item_old)) if item_old is not None else None,
                        "user_id": users.get(_int_or(user_old)) if user_old is not None else None,
                        "user_name": str(entry.get("user_name") or ""),
                        "name": str(entry.get("name") or ""),
                        "type": str(entry.get("type") or ""),
                        "checksum": str(entry.get("checksum") or ""),
                        "size": _int_or(entry.get("size")),
                        "category": str(entry.get("category") or ""),
                        "tags": _json_value(entry.get("tags"), []) or [],
                        "content": str(entry.get("content") or ""),
                        "is_hidden": bool(entry.get("is_hidden") or 0),
                    }
                )
            archive = self.archives.create_archive(
                name=safe_name,
                note=str(row.get("note") or ""),
                entries=entries,
                new_blobs=0,
                total_size=0,
                logical_size=_int_or(row.get("logical_size")),
            )
            archive.pinned = bool(row.get("pinned") or 0)
            stamp = _parse_stamp(row.get("created_at"))
            if stamp is not None:
                archive.created_at = stamp
            created += 1
        return created


def import_replace(source: str | Path, *, backup: bool = True) -> DatabaseImportResult:
    """覆盖式导入：备份现有数据，清空整库与库文件夹，再把包里的内容整份搬回来。

    清空是**原地**做的（`init_db(force=True)` 删表重建，再播种默认数据），
    所以别的页面开着的会话不会挡住它——不像整份换数据库文件那样会被 Windows
    的文件句柄卡住，还可能把别人写下的 `-wal` 重放回来。搬回来复用新增式导入，
    于是包里的东西按名字重建，最终数量以搬完之后的现状为准。
    """
    info = inspect_package(source)
    if info.schema_version > database.SCHEMA_VERSION:
        raise BundleError(
            f"包里的库结构版本比本程序新（{info.schema_version} > {database.SCHEMA_VERSION}），"
            f"先升级程序再导入：{info.path}"
        )
    if info.users < 1:
        raise BundleError(f"整库包里一个用户都没有，不像本程序导出的：{info.path}")
    stamp = _backup_stamp()
    backup_path = ""
    if backup:
        target = database.db_file().with_name(f"{database.db_file().name}.bak-{stamp}")
        saved = database.backup_database_file(target)
        backup_path = str(saved) if saved is not None else ""
    config.release_resource_root()
    root = config.library_root()
    if root.exists():
        moved = root.with_name(f"{root.name}.bak-{stamp}")
        try:
            root.rename(moved)
        except OSError as exc:
            logger.warning("整库导入：旧库文件夹挪不动，改成逐个文件清掉（{}）", exc)
            for entry in root.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    try:
                        entry.unlink()
                    except OSError:  # pragma: no cover - 删不掉只能留着
                        pass
    root.mkdir(parents=True, exist_ok=True)
    ensure_dirs()
    # 原地清库：删表重建 + 播种默认数据，好让包里的同名用户 / 分类 / 库按名字命中
    database.init_db(force=True)
    session = database.new_session()
    try:
        seed(session)
        session.commit()
        report = DatabaseBundleService(session, store=ContentStore(session)).import_merge(
            info.path, commit=True
        )
    finally:
        session.close()
    ensure_dirs()
    counts: dict[str, int] = {}
    with _SourceDB(database.db_file(), readonly=False) as live:
        for table in COUNT_KEYS:
            counts[table] = live.count(table)
    result = DatabaseImportResult(
        mode=MODE_REPLACE,
        users=counts.get("users", report.users),
        categories=counts.get("categories", report.categories),
        tags=counts.get("tags", report.tags),
        items=counts.get("items", report.items),
        archives=counts.get("archives", report.archives),
        contents=counts.get("contents", report.contents),
        files=report.files,
        skipped=report.skipped,
        missing=report.missing,
        backup=backup_path,
    )
    logger.info("整库导入（覆盖式）：{}", result.summary())
    return result
