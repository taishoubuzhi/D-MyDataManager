"""存档包：把若干份存档连内容打包成一个 `.zip`，也能从包重建存档记录。

包里的结构很简单：

```
存档包.json          # 清单：每份存档的名字 / 备注 / 时间 / 条目（含正文与校验和）
内容/<checksum>      # 条目内容，按校验和命名，跨存档天然去重
```

导入时**只重建存档记录**（外加把内容写回内容仓库，这样之后真能回档），
不碰现有数据项、分类、标签与用户；同名存档自动加后缀，不会覆盖。

写包走 `app.core.export.ZipPack`：先写 `<包名>.zip.part`，成了再原子改名，
中断不会留下半份包。清单里的校验和只用作内容标识，导入前会逐份重算比对。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from loguru import logger
from sqlalchemy import select

from ..core.export import ZipPack
from ..db.database import new_session
from ..db.models import Archive, User
from ..repositories.archives import ArchiveRepository, BlobRepository
from .content_store import ContentStore, sha256_of

#: 清单文件名与格式标识（换格式时递增 VERSION，旧包仍能读）。
MANIFEST_NAME = "存档包.json"
CONTENT_DIR = "内容"
FORMAT = "dm-archives"
VERSION = 1

#: 内容文件名必须是完整 sha256，别的一律不认（顺带挡住路径穿越）。
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")


class BundleError(RuntimeError):
    """存档包读不了 / 内容对不上。"""


@dataclass(frozen=True)
class BundleArchive:
    """包内一份存档（导入时按它调 `ArchiveRepository.create_archive`）。"""

    key: str
    name: str
    note: str = ""
    created_at: str = ""
    entries: tuple[dict[str, Any], ...] = ()

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def logical_size(self) -> int:
        return sum(int(entry.get("size") or 0) for entry in self.entries)


@dataclass(frozen=True)
class BundleInfo:
    """只看清单就能知道的概况（导入前给用户看一眼）。"""

    path: Path
    created_at: str = ""
    archives: tuple[BundleArchive, ...] = ()
    files: int = 0

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(archive.name for archive in self.archives)

    @property
    def entries(self) -> int:
        return sum(archive.count for archive in self.archives)

    @property
    def is_empty(self) -> bool:
        return not self.archives

    def summary(self) -> str:
        if self.is_empty:
            return "这个存档包里没有存档"
        when = f"（{self.created_at} 导出）" if self.created_at else ""
        return f"包里有 {len(self.archives)} 份存档、共 {self.entries} 个条目{when}"


@dataclass(frozen=True)
class BundleExportResult:
    path: Path
    archives: int = 0
    entries: int = 0
    files: int = 0
    missing: int = 0
    size: int = 0

    def summary(self) -> str:
        if not self.archives:
            return "没有要导出的存档"
        text = f"已导出 {self.archives} 份存档（共 {self.entries} 个条目、{self.files} 份内容）到 {self.path}"
        if self.missing:
            text += f"，其中 {self.missing} 个条目的内容已丢失、没能打进包里"
        return text


@dataclass(frozen=True)
class BundleImportResult:
    archives: int = 0
    entries: int = 0
    restored: int = 0
    missing: int = 0
    names: tuple[str, ...] = ()

    def summary(self) -> str:
        if not self.archives:
            return "没有导入任何存档"
        text = f"已导入 {self.archives} 份存档（共 {self.entries} 个条目，写回 {self.restored} 份内容）"
        if self.missing:
            text += f"，{self.missing} 个条目的内容没能写回"
        return text


def _manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_manifest(archive: ZipFile, source: Path) -> dict[str, Any]:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise BundleError(f"这不是存档包（包里没有 {MANIFEST_NAME}）：{source}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"存档包里的清单读不出来：{source}（{exc}）") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise BundleError(f"这不是本程序的存档包：{source}")
    return payload


def _archive_rows(payload: dict[str, Any], source: Path) -> tuple[BundleArchive, ...]:
    rows = payload.get("archives")
    if not isinstance(rows, list):
        raise BundleError(f"存档包的清单里没有存档列表：{source}")
    result: list[BundleArchive] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        entries = tuple(entry for entry in (row.get("entries") or []) if isinstance(entry, dict))
        result.append(
            BundleArchive(
                key=str(row.get("key") or f"archive-{index + 1}"),
                name=str(row.get("name") or f"存档 {index + 1}"),
                note=str(row.get("note") or ""),
                created_at=str(row.get("created_at") or ""),
                entries=entries,
            )
        )
    return tuple(result)


def _entry_data(entry: dict[str, Any], users: dict[str, int]) -> dict[str, Any]:
    """把清单里的一条条目翻译成 `ArchiveEntry` 的字段。

    `item_id` 不写：那个 id 是**导出方**数据库里的行号，搬过来没有意义；
    `user_id` 只在同名用户存在时才认，认不出就留空（无归属条目对所有人生效）。
    """
    user_name = str(entry.get("user_name") or "")
    tags = entry.get("tags") or []
    return {
        "item_id": None,
        "user_id": users.get(user_name),
        "user_name": user_name,
        "name": str(entry.get("name") or ""),
        "type": str(entry.get("type") or "OTHER"),
        "checksum": str(entry.get("checksum") or ""),
        "size": int(entry.get("size") or 0),
        "category": str(entry.get("category") or ""),
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
        "content": str(entry.get("content") or ""),
        "is_hidden": bool(entry.get("is_hidden")),
    }


class ArchiveBundleService:
    """存档包的导出与导入。

    传了 `session` 就用调用方的会话（不提交，交给调用方）；没传就自己开一个、
    并在 `import_bundle` 末尾提交（`commit=True`）。
    """

    def __init__(self, session=None, store: ContentStore | None = None) -> None:
        self._own = session is None
        self.session = session or new_session()
        self.store = store or ContentStore(self.session)
        self.archives = ArchiveRepository(self.session)
        self.blobs = BlobRepository(self.session)

    # ------------------------------------------------------------------ 生命周期
    def close(self) -> None:
        if self._own:
            self.session.close()

    def __enter__(self) -> "ArchiveBundleService":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ 导出
    def export(self, archives: Iterable[Archive], target: str | Path) -> BundleExportResult:
        """把选中的存档连内容打包成 `target`（自动补 `.zip`）。"""
        selected = [archive for archive in archives]
        if not selected:
            raise BundleError("没有要导出的存档")
        path = Path(target)
        if path.suffix.lower() != ".zip":
            path = path.with_name(path.name + ".zip")

        payloads: list[dict[str, Any]] = []
        wanted: dict[str, str] = {}  # checksum → 代表名字（挑压缩方案用）
        missing = 0
        used: set[str] = set()
        for archive in selected:
            rows = self.archives.entries_of(archive)
            entries: list[dict[str, Any]] = []
            for entry in rows:
                checksum = str(entry.checksum or "")
                if checksum and self.store.content_available(checksum):
                    wanted.setdefault(checksum, entry.name or "")
                elif checksum:
                    missing += 1
                entries.append(
                    {
                        "user_name": entry.user_name or "",
                        "name": entry.name or "",
                        "type": entry.type or "OTHER",
                        "checksum": checksum,
                        "size": int(entry.size or 0),
                        "category": entry.category or "",
                        "tags": list(entry.tags or []),
                        "content": entry.content or "",
                        "is_hidden": bool(entry.is_hidden),
                    }
                )
            key = f"archive-{archive.id}"
            while key in used:
                key += "x"
            used.add(key)
            payloads.append(
                {
                    "key": key,
                    "name": archive.name or "",
                    "note": archive.note or "",
                    "created_at": archive.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if archive.created_at
                    else "",
                    "entries": entries,
                }
            )

        payload = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": _stamp(),
            "archives": payloads,
        }
        with ZipPack(path) as pack:
            # 清单放最前面：只看清单的调用方（导入前预览）不必解完整包
            pack.add_bytes(MANIFEST_NAME, _manifest_bytes(payload))
            for checksum in wanted:
                pack.add_stream(f"{CONTENT_DIR}/{checksum}", self.store.iter_content(checksum))

        result = BundleExportResult(
            path=pack.path,
            archives=len(payloads),
            entries=sum(len(row["entries"]) for row in payloads),
            files=len(wanted),
            missing=missing,
            size=pack.path.stat().st_size if pack.path.exists() else 0,
        )
        logger.info("存档包导出：{}", result.summary())
        return result

    # ------------------------------------------------------------------ 导入
    def inspect(self, source: str | Path) -> BundleInfo:
        """只读清单，看看包里有什么（不碰数据库、不解内容）。"""
        path = Path(source)
        try:
            with ZipFile(path) as archive:
                payload = _read_manifest(archive, path)
                rows = _archive_rows(payload, path)
                files = sum(
                    1
                    for name in archive.namelist()
                    if name.startswith(f"{CONTENT_DIR}/")
                    and _CHECKSUM_RE.match(name[len(CONTENT_DIR) + 1 :])
                )
        except BadZipFile as exc:
            raise BundleError(f"这不是一个压缩包：{path}（{exc}）") from exc
        return BundleInfo(
            path=path,
            created_at=str(payload.get("created_at") or ""),
            archives=rows,
            files=files,
        )

    def import_bundle(
        self,
        source: str | Path,
        *,
        keys: Sequence[str] | None = None,
        commit: bool = True,
    ) -> BundleImportResult:
        """从包重建存档记录（可选只导入其中几份，按 `keys` 挑）。"""
        path = Path(source)
        chosen = set(keys) if keys else None
        users = {user.name: user.id for user in self.session.scalars(select(User)).all()}
        existing = {archive.name for archive in self.archives.latest(limit=1000)}

        try:
            with ZipFile(path) as bundle:
                payload = _read_manifest(bundle, path)
                all_rows = _archive_rows(payload, path)
                rows = [row for row in all_rows if chosen is None or row.key in chosen]
                if not rows:
                    raise BundleError("没有选中要导入的存档")
                names = [self._unique_name(row.name or "导入的存档", existing) for row in rows]
                existing.update(names)
                wanted: dict[str, str] = {}  # checksum → 代表名字（写回内容时用）
                for row in rows:
                    for entry in row.entries:
                        checksum = str(entry.get("checksum") or "")
                        if _CHECKSUM_RE.match(checksum):
                            wanted.setdefault(checksum, str(entry.get("name") or ""))
                restored, missing, fresh = self._restore_contents(bundle, wanted)

                archives = 0
                entries = 0
                for row, name in zip(rows, names):
                    data = [_entry_data(entry, users) for entry in row.entries]
                    # 这份存档里真正新写进仓库的内容（共用同一份按校验和只算一次）
                    own = {
                        str(entry.get("checksum") or "")
                        for entry in data
                        if str(entry.get("checksum") or "") in fresh
                    }
                    archive = self.archives.create_archive(
                        name=name,
                        note=row.note or "",
                        entries=data,
                        new_blobs=len(own),
                        total_size=sum(fresh[checksum] for checksum in own),
                        logical_size=row.logical_size,
                    )
                    stamp = _parse_stamp(row.created_at)
                    if stamp is not None:
                        archive.created_at = stamp
                    archives += 1
                    entries += len(data)
        except BadZipFile as exc:
            raise BundleError(f"这不是一个压缩包：{path}（{exc}）") from exc

        if commit:
            self.session.commit()
        result = BundleImportResult(
            archives=archives,
            entries=entries,
            restored=restored,
            missing=missing,
            names=tuple(names),
        )
        logger.info("存档包导入：{}", result.summary())
        return result

    # ------------------------------------------------------------------ 内部
    def _unique_name(self, name: str, existing: set[str]) -> str:
        """重名就加「（导入）」「（导入 2）」，绝不覆盖或重名。"""
        if name not in existing:
            return name
        candidate = f"{name}（导入）"
        index = 2
        while candidate in existing:
            candidate = f"{name}（导入 {index}）"
            index += 1
        return candidate

    def _restore_contents(
        self, bundle: ZipFile, wanted: dict[str, str]
    ) -> tuple[int, int, dict[str, int]]:
        """把包里的内容写回内容仓库。

        返回 `(可用份数, 缺失份数, 这次真写进仓库的 {checksum: 字节数})`。
        """
        restored = 0
        missing = 0
        fresh: dict[str, int] = {}
        for checksum, name in wanted.items():
            member = f"{CONTENT_DIR}/{checksum}"
            try:
                info = bundle.getinfo(member)
            except KeyError:
                logger.warning("存档包里缺内容，跳过：{}（{}）", name or checksum, checksum[:12])
                missing += 1
                continue
            if self.store.content_available(checksum):
                # 仓库里已经有了：只补一笔引用，别的什么都不做
                self.blobs.register(
                    checksum, int(info.file_size), "", self.store.loose_rel_path(checksum)
                )
                restored += 1
                continue
            try:
                written = self._write_content(bundle, member, checksum, name)
            except (BadZipFile, OSError) as exc:
                logger.warning("写回内容失败，跳过：{}（{}）", name or checksum, exc)
                missing += 1
                continue
            if not written:
                missing += 1
            else:
                restored += 1
                fresh[checksum] = int(info.file_size)
        return restored, missing, fresh

    def _write_content(self, bundle: ZipFile, member: str, checksum: str, name: str) -> bool:
        """流式解到临时文件再入库；重算校验和不符就丢弃。"""
        temp: Path | None = None
        try:
            with bundle.open(member) as reader:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".part") as sink:
                    temp = Path(sink.name)
                    shutil.copyfileobj(reader, sink, 1024 * 1024)
            if sha256_of(temp) != checksum:
                logger.warning("存档包里的内容与清单对不上，跳过：{}（{}）", name or checksum, checksum[:12])
                return False
            stored, rel_path, size = self.store.put_file(temp, name=name)
            if stored != checksum:
                logger.warning("内容入库后校验和不一致，跳过：{}", checksum[:12])
                return False
            self.blobs.register(checksum, size, "", rel_path)
            return True
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def _parse_stamp(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
