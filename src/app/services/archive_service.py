"""存档服务：为整个库拍摄内容寻址快照，支持对比、还原与清理。

存档不依赖 git：每个数据项的内容以 sha256 标识，快照只记录当时的条目状态
（ArchiveEntry），因此天然支持图片、视频等任意二进制文件。内容本身在仓库里
整份压缩存放，同一份内容只落一份文件，内容没变就不会产生新的存储占用。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import config
from ..db.models import (
    Archive,
    ArchiveEntry,
    Blob,
    Category,
    DataItem,
    DataType,
    User,
)
from ..repositories import (
    ArchiveRepository,
    BlobRepository,
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
)
from .blob_store import sha256_of
from .content_store import ContentStore
from .library_service import LibraryService
from .privacy_service import guarded
from .user_service import UserService

MAX_ARCHIVES = 1000


#: 「重新加载存档文件」进行中：期间不允许建存档、回档或自动存档（只读浏览不受影响）。
_REBUILD_ACTIVE = False


class RebuildBlocked(RuntimeError):
    """重新加载存档文件期间不允许写存档。"""


def rebuild_in_progress() -> bool:
    """是否有「重新加载存档文件」正在进行。"""
    return _REBUILD_ACTIVE


def _ensure_writable() -> None:
    if _REBUILD_ACTIVE:
        raise RebuildBlocked("正在重新加载存档文件，暂时无法写存档")


def _notify(callback, phase: str, index: int, total: int, detail: str = "") -> None:
    """回报重建进度；回调自身出错不影响重建。"""
    if callback is None:
        return
    try:
        callback(phase, index, total, detail)
    except Exception as exc:
        logger.warning("重建进度回调失败：{}", exc)


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel():
        raise RebuildBlocked("已取消重新加载存档文件")


@dataclass
class ArchiveDiff:
    """存档与当前数据的差异：`added` 是当前另有、存档里没有的项（回档不会动它们）。"""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    missing_blobs: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.missing_blobs)


@dataclass
class Change:
    """回档会把某条数据改成什么样，供变更清单弹窗逐行展示。"""

    kind: str
    name: str
    category: str = ""
    user: str = ""
    archive_name: str = ""


#: 「内容缺失」的变更类型：这类条目在存档里没有内容，回档也变不出数据来。
#: 它也进变更清单（每个会被回档动到的条目都有行），但不属于「可执行的变更」。
CHANGE_MISSING = "内容缺失"


@dataclass
class RestoreReport:
    """回档结果：既用于 `preview_restore()` 的预演，也用于 `restore()` 的实际统计。"""

    source: str = ""
    mode: str = "restore"
    restored: int = 0
    replaced: int = 0
    repaired: int = 0
    renamed: int = 0
    hidden_fixed: int = 0
    undeleted: int = 0
    soft_deleted: int = 0
    skipped: int = 0
    missing: int = 0
    failed: int = 0
    conflicts: int = 0
    changes: list[Change] = field(default_factory=list)
    snapshot: Archive | None = None

    @property
    def actionable(self) -> bool:
        """是否存在真正能执行的变更（排除「内容缺失」这类只能跳过、补不回来的条目）。"""
        return any(change.kind != CHANGE_MISSING for change in self.changes)

    @property
    def is_empty(self) -> bool:
        """没有可执行的变更时为真 —— 界面据此跳过确认弹窗并禁用确认按钮。"""
        return not self.actionable

    @property
    def total(self) -> int:
        """变更条数：与变更清单里列出的行数一致（弹窗头部据此计数）。"""
        return len(self.changes)

    def grouped(self) -> list[tuple[str, int]]:
        """按变更类型分组计数，供弹窗头部展示（保持首次出现的顺序）。"""
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.kind] = counts.get(change.kind, 0) + 1
        return list(counts.items())

    def summary(self) -> str:
        # 内容缺失同样进 grouped()，这里不再单列「缺失 N」，避免同一件事报两遍
        parts = [f"{kind} {count}" for kind, count in self.grouped()]
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        return " / ".join(parts) if parts else "无变更"


@dataclass
class _Action:
    """一条待执行的回档动作（计划阶段产出，预览与执行共用）。"""

    kind: str
    entry: ArchiveEntry | None = None
    item: DataItem | None = None
    archive_name: str = ""
    change: Change | None = None


@dataclass
class RebuildEntry:
    """重建计划里的一条：真实文件在哪、属于哪类处理。"""

    entry: ArchiveEntry
    status: str = "reuse"  # reuse（直接使用）/ restore（从存档还原）/ diverged / missing
    library_id: int | None = None
    rel_path: str = ""
    item: DataItem | None = None
    checksum: str = ""
    size: int = 0

    @property
    def name(self) -> str:
        return self.entry.name


@dataclass
class RebuildPlan:
    """重建预检结果：每条真实文件会被怎么处理。"""

    archives: int = 0
    entries: list[RebuildEntry] = field(default_factory=list)

    def _pick(self, status: str) -> list[RebuildEntry]:
        return [entry for entry in self.entries if entry.status == status]

    @property
    def restore_needed(self) -> list[RebuildEntry]:
        return self._pick("restore")

    @property
    def reuse_existing(self) -> list[RebuildEntry]:
        return self._pick("reuse")

    @property
    def diverged(self) -> list[RebuildEntry]:
        return self._pick("diverged")

    @property
    def missing(self) -> list[RebuildEntry]:
        return self._pick("missing")

    def grouped(self) -> list[tuple[str, int]]:
        return [
            ("直接使用现有文件", len(self.reuse_existing)),
            ("从存档还原文件", len(self.restore_needed)),
            ("与存档不一致，以现有文件为准", len(self.diverged)),
            ("内容已丢失", len(self.missing)),
        ]

    def summary(self) -> str:
        parts = [f"{label} {count}" for label, count in self.grouped() if count]
        return " / ".join(parts) if parts else "没有需要重建的条目"


@dataclass
class RebuildReport:
    """重建结果：阶段 ④ 只改 archive_entries 的内容指向，逻辑行与行 id 都不变。"""

    plan: RebuildPlan | None = None
    restored_files: int = 0
    rebuilt: int = 0
    switched: int = 0
    failed: int = 0
    contents_before: int = 0
    contents_after: int = 0

    @property
    def reused(self) -> int:
        return len(self.plan.reuse_existing) if self.plan else 0

    @property
    def diverged(self) -> int:
        return len(self.plan.diverged) if self.plan else 0

    @property
    def missing(self) -> int:
        return len(self.plan.missing) if self.plan else 0

    def summary(self) -> str:
        parts = [f"重建 {self.rebuilt} 条"]
        if self.restored_files:
            parts.append(f"还原文件 {self.restored_files}")
        if self.missing:
            parts.append(f"内容缺失 {self.missing}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        parts.append(f"内容 {self.contents_before} → {self.contents_after}")
        return " / ".join(parts)


class ArchiveService:
    def __init__(self, session: Session, store: ContentStore | None = None) -> None:
        self.session = session
        self.store = store or ContentStore(session)
        self.archives = ArchiveRepository(session)
        self.blobs = BlobRepository(session)
        self.items = ItemRepository(session)
        self.categories = CategoryRepository(session)
        self.tags = TagRepository(session)
        self._libraries: LibraryService | None = None

    # ---------------------------------------------------------------- 快照
    def create(self, name: str = "", note: str = "") -> Archive:
        _ensure_writable()
        items = self._all_items(include_deleted=False)
        previous = self.archives.latest(limit=1)
        known: set[str] = set()
        if previous:
            known = {entry.checksum for entry in self.archives.entries_of(previous[0]) if entry.checksum}

        user_names = {user.id: user.name for user in self.session.scalars(select(User)).all()}
        before = self.store.usage()["total_bytes"]
        entries: list[dict] = []
        logical_size = 0
        new_blobs = 0
        missing = 0
        for item in items:
            logical_size += item.size
            if item.checksum and item.checksum not in known:
                new_blobs += 1
                known.add(item.checksum)
            if not self._content_ready(item):
                missing += 1
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

        stored = max(0, self.store.usage()["total_bytes"] - before)
        archive = self.archives.create_archive(
            name or self._default_name(), note, entries, new_blobs, stored, logical_size
        )
        logger.info(
            "存档完成：{} 项 / 逻辑 {} 字节 / 新增占用 {} 字节 / 新增内容 {}",
            len(entries),
            logical_size,
            stored,
            new_blobs,
        )
        if missing:
            logger.warning("有 {} 个数据项的内容已丢失，存档中记为缺失", missing)
        removed, freed = self.auto_prune()
        if removed:
            logger.info("按清理策略删除了 {} 个较早的存档，释放 {} 字节", removed, freed)
        return archive

    def _content_ready(self, item: DataItem) -> bool:
        """确认条目内容在仓库里可用；取不到就记为缺失。"""
        checksum = item.checksum or ""
        if not checksum:
            return False
        if self.store.content_available(checksum):
            return True
        logger.debug("内容已丢失，存档条目记为缺失：{}", item.name)
        return False

    def _default_name(self) -> str:
        return f"存档 {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    def history(self, limit: int = 50) -> list[Archive]:
        return self.archives.latest(limit)

    def entries(self, archive: Archive) -> list[ArchiveEntry]:
        return self.archives.entries_of(archive)

    # ---------------------------------------------------------------- 占用
    def footprints(self, archives) -> dict[int, int]:
        """每个存档实际占用的落盘字节：按内容身份去重（同一内容跨存档只算一次）。"""
        archives = list(archives)
        if not archives:
            return {}
        ids = [int(archive.id) for archive in archives]
        rows = self.session.execute(
            select(ArchiveEntry.archive_id, ArchiveEntry.checksum).where(
                ArchiveEntry.archive_id.in_(ids), ArchiveEntry.checksum.is_not(None)
            )
        ).all()
        per_archive: dict[int, set[str]] = {}
        for archive_id, checksum in rows:
            if checksum:
                per_archive.setdefault(int(archive_id), set()).add(str(checksum))
        sizes = self.store.used_bytes(
            {checksum for group in per_archive.values() for checksum in group}
        )
        return {
            archive_id: sum(sizes.get(checksum, 0) for checksum in group)
            for archive_id, group in per_archive.items()
        }

    def archive_usage(self) -> dict[str, int]:
        """存档整体占用（§9）：存档数、条目数、去重内容份数、逻辑大小与落盘占用。

        `stored_size` 按内容身份去重后累加：同一份内容被多条存档引用时只算一次，
        而 `footprints()` 是逐存档的占用，两者在内容共享时不会相等。
        """
        archives = list(self.session.scalars(select(Archive)))
        contents = {
            str(value) for value in self.session.scalars(select(ArchiveEntry.checksum)) if value
        }
        return {
            "archives": len(archives),
            "entries": int(
                self.session.scalar(select(func.count()).select_from(ArchiveEntry)) or 0
            ),
            "contents": len(contents),
            "logical_size": sum(int(archive.logical_size or 0) for archive in archives),
            "stored_size": sum(self.store.used_bytes(contents).values()),
        }

    # ---------------------------------------------------------------- 对比
    def compare(self, archive: Archive, user_id: int | None = None) -> ArchiveDiff:
        """与当前数据对比；传入 user_id 时只比较该用户自己的数据与条目。

        口径与回档预览（`_state_of`）保持一致：先按 `item_id`、再按「归属 + 文件名」匹配，
        这样详情标签不会出现「新增 N」而回档却提示「无需回档」的自相矛盾。
        `added` 只统计**未删除**的现存项（回收站里的项不算），它是「当前另有 N 项不在存档中」。
        """
        entries = self.archives.entries_of(archive)
        if user_id is not None:
            entries = [entry for entry in entries if entry.user_id in (None, user_id)]
        current = {item.id: item for item in self._all_items(user_id)}
        name_map = self._name_map(entries)
        diff = ArchiveDiff()
        matched: set[int] = set()
        for entry in entries:
            item = current.get(entry.item_id) if entry.item_id else None
            if item is None:
                item = self._match_item(entry, user_id, name_map)
            if item is None:
                diff.removed.append(entry.name)
                continue
            matched.add(int(item.id))
            if (item.checksum or "") != (entry.checksum or ""):
                diff.changed.append(entry.name)
        for item in current.values():
            if int(item.id) in matched or item.is_deleted:
                continue
            diff.added.append(item.name)
        for entry in entries:
            if entry.checksum and not self.store.content_available(entry.checksum):
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

    # ---------------------------------------------------------------- 状态
    def states_for(self, entries, owner_id: int | None = None) -> dict[int, str]:
        """批量算状态：一次载入相关数据项（含回收站项），避免逐条查询。"""
        entries = list(entries)
        self._libraries = LibraryService(self.session, store=self.store)
        item_map = {item.id: item for item in self.items.by_ids([e.item_id for e in entries if e.item_id])}
        name_map = self._name_map(entries)
        return {
            entry.id: self._state_of(entry, item_map, name_map, owner_id) for entry in entries
        }

    def _name_map(self, entries) -> dict[str, list[DataItem]]:
        """按文件名批量载入候选数据项（含回收站项），供「归属 + 文件名」匹配使用。"""
        name_map: dict[str, list[DataItem]] = {}
        names = [entry.name for entry in entries if entry.name]
        for item in self.items.by_names(names, include_deleted=True):
            name_map.setdefault(item.name or "", []).append(item)
        return name_map

    def states(self, archive: Archive, owner_id: int | None = None) -> dict[int, str]:
        """存档内全部条目相对当前数据的状态（供列表与预览共用）。"""
        entries = self.archives.entries_of(archive)
        if owner_id is not None:
            entries = [entry for entry in entries if entry.user_id in (None, owner_id)]
        return self.states_for(entries, owner_id)

    def entry_state(self, entry: ArchiveEntry, owner_id: int | None = None) -> str:
        """条目相对当前数据的状态。

        `same` 与当前数据完全一致（无需还原）、`changed` 内容已变化、
        `removed` 已删除（含在回收站里，还原会撤销删除）、`missing` 存档文件缺失、
        `lost` 数据项还在但库内文件已被删掉（还原会把文件补回来）。
        只有非 `same` 的条目允许还原。

        内容一致但隐藏状态不同时算 `changed`：还原会按存档把隐藏状态调整回来。
        条目原 id 找不到时按「归属 + 文件名」再匹配一次（含回收站项），
        避免同一份内容被复制出第二条数据。
        """
        return self.states_for([entry], owner_id)[entry.id]

    def _state_of(
        self,
        entry: ArchiveEntry,
        item_map: dict[int, DataItem],
        name_map: dict[str, list[DataItem]],
        owner_id: int | None,
    ) -> str:
        if not entry.checksum or not self.store.content_available(entry.checksum):
            return "missing"
        owner = owner_id or entry.user_id
        item = item_map.get(entry.item_id) if entry.item_id else None
        if item is not None and (owner is None or item.user_id == owner):
            if item.is_deleted:
                return "removed"
            if (item.checksum or "") != (entry.checksum or ""):
                return "changed"
            if bool(item.is_hidden) != bool(entry.is_hidden):
                return "changed"
            return "same" if self._file_present(item) else "lost"
        match = self._match_item(entry, owner, name_map)
        if match is None:
            return "removed"
        if (match.checksum or "") != (entry.checksum or ""):
            # 只是同名而已：内容不同就不是同一条数据，回档要按存档内容重建
            return "removed"
        if match.is_deleted:
            return "removed"
        if bool(match.is_hidden) != bool(entry.is_hidden):
            return "changed"
        return "same" if self._file_present(match) else "lost"

    def _file_present(self, item: DataItem) -> bool:
        """数据项在库内的文件是否还在。

        内容没变、却直接从磁盘删掉了库内文件时，仓库副本仍然在，只查仓库会误判为「一致」。
        没有库内路径的数据项（比如早期只在数据库里存正文的文本）不做判断。
        """
        if not item.file_path:
            return True
        libraries = getattr(self, "_libraries", None)
        if libraries is None:
            libraries = LibraryService(self.session, store=self.store)
            self._libraries = libraries
        path = libraries.abs_path(item)
        return path is not None and path.exists()

    def _match_item(
        self,
        entry: ArchiveEntry,
        owner_id: int | None,
        name_map: dict[str, list[DataItem]] | None = None,
    ) -> DataItem | None:
        """按「归属 + 文件名」找回条目对应的数据项，**包含回收站里的项**。

        身份 = 归属 + 文件名 + 内容，三者都相同才算同一条数据：
        只认文件名的话，删掉甲之后，只要库里还有一个同名的乙（内容不同），
        甲这条存档就会被认成「一致」，回档什么也不做。含回收站项，
        条目内容相同但被丢进回收站时，回档会撤销删除而不是新建重复项。
        """
        if not entry.name:
            return None
        if name_map is None:
            candidates = self.items.by_names([entry.name], include_deleted=True)
        else:
            candidates = name_map.get(entry.name, [])
        pool = [item for item in candidates if owner_id is None or item.user_id == owner_id]
        if entry.checksum:
            pool = [item for item in pool if (item.checksum or "") == entry.checksum]
        if not pool:
            return None
        wanted = entry.category or ""
        same_category = [item for item in pool if self._category_path_of(item) == wanted]
        pool = same_category or pool
        return min(pool, key=lambda item: bool(item.is_deleted))

    def _category_path_of(self, item: DataItem) -> str:
        """数据项所在分类的路径文本（与 `ArchiveEntry.category` 同一口径）。"""
        if not item.category_id:
            return ""
        category = self.session.get(Category, item.category_id)
        return self.categories.path_of(category) if category is not None else ""

    # ---------------------------------------------------------------- 回档
    def _resolve_scope(self, scope) -> tuple[list[ArchiveEntry], str]:
        """把作用域统一成条目列表；返回 (条目, 来源描述)。"""
        if isinstance(scope, Archive):
            return self.archives.entries_of(scope), scope.name
        entries = list(scope)
        archive_ids = {entry.archive_id for entry in entries}
        return entries, f"{len(archive_ids)} 个存档 / {len(entries)} 个条目"

    def _resolve_conflicts(self, entries: list[ArchiveEntry]) -> tuple[list[ArchiveEntry], list[Change]]:
        """跨存档挑选时，同一条数据被多个存档选中 ⇒ 按存档新旧只取最新的一份。"""
        keyed: dict[object, list[ArchiveEntry]] = {}
        for entry in entries:
            keyed.setdefault(entry.item_id or entry.checksum or entry.id, []).append(entry)
        names = {}
        if any(len(group) > 1 for group in keyed.values()):
            names = dict(
                self.session.execute(select(Archive.id, Archive.name)).all()  # type: ignore[arg-type]
            )
        kept: list[ArchiveEntry] = []
        ignored: list[Change] = []
        for group in keyed.values():
            if len(group) == 1:
                kept.append(group[0])
                continue
            ordered = sorted(group, key=lambda item: item.archive_id or 0, reverse=True)
            kept.append(ordered[0])
            for entry in ordered[1:]:
                ignored.append(
                    Change(
                        kind="冲突：忽略较早存档",
                        name=entry.name,
                        category=entry.category or "",
                        user=entry.user_name or "",
                        archive_name=names.get(entry.archive_id, ""),
                    )
                )
        return kept, ignored

    def _plan(self, scope, mode: str, user_id: int | None) -> tuple[list[_Action], str, list[Change]]:
        """回档计划：只做判定，不写任何数据（预览与执行共用）。"""
        _ensure_writable()
        if mode not in ("restore", "mirror"):
            raise ValueError(f"未知的回档方式：{mode}")
        if mode == "mirror" and not isinstance(scope, Archive):
            raise ValueError("覆盖式回档只能对整个存档执行")
        if mode == "mirror" and user_id is not None:
            raise PermissionError("覆盖式回档仅管理员可用")
        entries, source = self._resolve_scope(scope)
        conflicts: list[Change] = []
        if not isinstance(scope, Archive):
            entries, conflicts = self._resolve_conflicts(entries)
        if user_id is not None:
            entries = [entry for entry in entries if entry.user_id in (None, user_id)]
        item_map = {item.id: item for item in self.items.by_ids([e.item_id for e in entries if e.item_id])}
        states = self.states_for(entries, user_id)
        current_id = UserService(self.session).current_id()
        # 每条存档条目当前对应哪个数据项：匹配按 id 也可能按「归属 + 文件名」，
        # 覆盖式回档不能把这样认出来的项当成「存档里没有的多余数据」丢进回收站。
        claimed = self._claimed_ids(entries, user_id, item_map)
        actions: list[_Action] = []
        for entry in entries:
            state = states.get(entry.id, "missing")
            if state == "same":
                actions.append(_Action("skip", entry=entry, archive_name=source))
                continue
            if state == "missing":
                actions.append(_Action("missing", entry=entry, archive_name=source))
                continue
            owner = user_id or entry.user_id or current_id
            item = item_map.get(entry.item_id) if entry.item_id else None
            if item is not None and owner is not None and item.user_id != owner:
                item = None
            if item is not None and item.name != entry.name and (item.checksum or "") != (entry.checksum or ""):
                # 旧数据项已被彻底删除、id 被新数据项复用时，别认错人
                item = None
            if item is None:
                item = self._match_item(entry, owner)
            if item is not None:
                claimed.add(int(item.id))
            if state == "lost":
                # 内容还在仓库里，只是库内文件被删了：原地补回，不新建重复项
                if item is not None:
                    actions.append(_Action("repair", entry=entry, item=item, archive_name=source))
                else:
                    actions.append(_Action("create", entry=entry, archive_name=source))
                continue
            if item is None:
                actions.append(_Action("create", entry=entry, archive_name=source))
            elif not self._file_present(item):
                # 内容没变、库内文件却被直接删了：原地补回（顺带撤销删除、修隐藏位）
                actions.append(_Action("repair", entry=entry, item=item, archive_name=source))
            elif item.is_deleted or bool(item.is_hidden) != bool(entry.is_hidden):
                actions.append(_Action("align", entry=entry, item=item, archive_name=source))
            elif mode == "mirror" and (item.checksum or "") != (entry.checksum or ""):
                actions.append(_Action("replace", entry=entry, item=item, archive_name=source))
            elif mode == "mirror":
                # 库里已经有内容相同的一条（id 变了而已）：覆盖式以存档为镜像，不需要再复制一份
                actions.append(_Action("skip", entry=entry, archive_name=source))
            else:
                actions.append(_Action("create", entry=entry, archive_name=source))
        if mode == "mirror":
            known = {entry.item_id for entry in entries if entry.item_id} | claimed
            for item in self._all_items(user_id):
                if item.id not in known and not item.is_deleted:
                    actions.append(_Action("soft_delete", item=item, archive_name=source))
        return actions, source, conflicts

    def _claimed_ids(
        self, entries: list[ArchiveEntry], owner_id: int | None, item_map: dict[int, DataItem]
    ) -> set[int]:
        """条目当前对应的数据项 id（含按「归属 + 文件名」匹配到的项）。"""
        if not entries:
            return set()
        name_map = self._name_map(entries)
        claimed: set[int] = set()
        for entry in entries:
            owner = owner_id or entry.user_id
            item = item_map.get(entry.item_id) if entry.item_id else None
            if item is None or (owner is not None and item.user_id != owner):
                item = self._match_item(entry, owner, name_map)
            if item is not None:
                claimed.add(int(item.id))
        return claimed

    def _user_name(self, user_id: int | None) -> str:
        """数据项归属用户名（变更清单的「归属」列）。"""
        if not user_id:
            return ""
        user = self.session.get(User, user_id)
        return str(getattr(user, "name", "") or "")

    def _describe(self, action: _Action) -> Change | None:
        """把动作翻译成变更清单里的一行（`None` = 不产生变更）。

        条目自带分类与归属；只有「覆盖式把多余数据收走」这类没有条目的动作要自己
        从数据项上取，否则清单里这些行只剩文件名，看不出动的是谁的数据。
        同一条数据被改动多件事时把各项拼在一行里，避免计数与列出的行数对不上。
        """
        entry = action.entry
        item = action.item
        name = entry.name if entry else (item.name if item else "")
        if entry is not None:
            category, user = entry.category, entry.user_name
        else:
            category = self._category_path_of(item) if item is not None else ""
            user = self._user_name(item.user_id) if item is not None else ""
        if action.kind == "skip":
            return None
        if action.kind == "missing":
            return Change(CHANGE_MISSING, name, category, user, action.archive_name)
        if action.kind == "soft_delete":
            return Change("进回收站", name, category, user, action.archive_name)
        if action.kind == "replace":
            return Change("替换", name, category, user, action.archive_name)
        if action.kind == "repair":
            return Change("补回文件", name, category, user, action.archive_name)
        if action.kind == "create":
            return Change("新增", name, category, user, action.archive_name)
        if action.kind == "align":
            assert item is not None and entry is not None
            parts: list[str] = []
            if item.is_deleted:
                parts.append("撤销删除")
            if (item.checksum or "") != (entry.checksum or ""):
                parts.append("还原内容")
            if bool(item.is_hidden) != bool(entry.is_hidden):
                parts.append("仅改隐藏位")
            return Change(" + ".join(parts) or "无变化", name, category, user, action.archive_name)
        return None

    def _report(
        self, actions: list[_Action], source: str, mode: str, conflicts: list[Change]
    ) -> RestoreReport:
        report = RestoreReport(source=source, mode=mode, conflicts=len(conflicts))
        report.changes.extend(conflicts)
        for action in actions:
            if action.kind == "skip":
                report.skipped += 1
                continue
            if action.kind == "missing":
                # 补不回来，但仍要出现在变更清单里：页面详情写着「内容缺失 N」，
                # 弹窗清单若没有对应的行，用户就会以为漏了一项。
                report.missing += 1
                report.skipped += 1
                missing_change = self._describe(action)
                if missing_change is not None:
                    report.changes.append(missing_change)
                continue
            change = self._describe(action)
            if change is not None:
                report.changes.append(change)
            # 计划阶段就把计数算好，预览数字才能与实际执行一致（重命名只有执行时才知道）
            if action.kind == "soft_delete":
                report.soft_deleted += 1
            elif action.kind == "create":
                report.restored += 1
            elif action.kind == "replace":
                report.replaced += 1
            elif action.kind == "repair":
                report.repaired += 1
            elif action.kind == "align":
                item, entry = action.item, action.entry
                assert item is not None and entry is not None
                if item.is_deleted:
                    report.undeleted += 1
                if (item.checksum or "") != (entry.checksum or ""):
                    report.restored += 1
                elif bool(item.is_hidden) != bool(entry.is_hidden):
                    report.hidden_fixed += 1
        return report

    def preview_restore(
        self, scope, mode: str = "restore", user_id: int | None = None
    ) -> RestoreReport:
        """只算不写：给出回档会产生的变更清单，供确认弹窗展示。"""
        actions, source, conflicts = self._plan(scope, mode, user_id)
        return self._report(actions, source, mode, conflicts)

    @guarded
    def restore(
        self,
        scope,
        mode: str = "restore",
        user_id: int | None = None,
        snapshot_before: bool = False,
        progress=None,
    ) -> RestoreReport:
        """执行回档：`restore` = 恢复式（保留现有数据），`mirror` = 覆盖式（以存档为镜像）。

        `snapshot_before=True` 会先给当前数据建一份存档，成功后才回档；建快照失败则直接
        抛异常中止，绝不出现「快照没成功但回档照跑」。
        """
        if mode not in ("restore", "mirror"):
            raise ValueError(f"未知的回档方式：{mode}")
        actions, source, conflicts = self._plan(scope, mode, user_id)
        report = self._report(actions, source, mode, conflicts)
        if snapshot_before:
            report.snapshot = self.snapshot_before_restore(source)
        todo = [action for action in actions if action.kind not in ("skip", "missing")]
        for index, action in enumerate(todo):
            if progress is not None:
                progress("restore", index, len(todo), action.entry.name if action.entry else "")
            try:
                self._apply(action, report, user_id)
            except Exception as exc:  # noqa: BLE001 - 单条失败不该让整次回档崩掉
                report.failed += 1
                logger.warning("回档条目失败：{}（{}）", action.entry.name if action.entry else "", exc)
        self.session.flush()
        logger.info(
            "回档完成（{}）：{}，跳过 {} 项", source, report.summary(), report.skipped
        )
        return report

    def snapshot_before_restore(self, source: str) -> Archive:
        """回档前的自动存档。"""
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.create(name=f"回档前快照 {stamp}", note=f"回档自「{source}」前的自动存档")

    def _apply(self, action: _Action, report: RestoreReport, user_id: int | None) -> None:
        """执行一条动作（计数已在计划阶段算好，这里只写数据）。"""
        entry = action.entry
        if action.kind == "soft_delete":
            assert action.item is not None
            self.items.soft_delete([action.item])
            return
        assert entry is not None
        owner = user_id or entry.user_id or UserService(self.session).current_id()
        if action.kind == "align":
            assert action.item is not None
            libraries = LibraryService(self.session, store=self.store)
            if action.item.is_deleted:
                self.items.restore([action.item])
            if (action.item.checksum or "") != (entry.checksum or ""):
                # 内容也变过：顺手按存档把内容复原（仍然保留这个数据项本身）
                self._replace_in_place(entry, action.item)
            elif not self._file_present(action.item):
                # 预览之后库内文件又被删掉了：按存档补回内容
                self._replace_in_place(entry, action.item)
            elif bool(action.item.is_hidden) != bool(entry.is_hidden):
                libraries.set_item_hidden(action.item, bool(entry.is_hidden))
            return
        if action.kind == "replace" or action.kind == "repair":
            assert action.item is not None
            self._replace_in_place(entry, action.item)
            return
        _item, renamed = self._create_from_entry(entry, owner)
        if renamed:
            report.renamed += 1

    def _replace_in_place(self, entry: ArchiveEntry, item: DataItem) -> None:
        """覆盖式：原地替换内容，不删旧建新（避免 id 漂移与重复数据）。"""
        libraries = LibraryService(self.session, store=self.store)
        if bool(item.is_hidden) != bool(entry.is_hidden):
            libraries.set_item_hidden(item, bool(entry.is_hidden))
        if item.is_deleted:
            self.items.restore([item])
        target = libraries.abs_path(item)
        paths.make_dir(target.parent)
        if not self.store.export_content(entry.checksum or "", target):
            raise FileNotFoundError(f"存档内容已丢失：{entry.name}")
        item.checksum = entry.checksum
        item.size = entry.size
        item.is_hidden = bool(entry.is_hidden)
        if entry.type == DataType.TEXT.value:
            item.content = entry.content
        self.blobs.register(
            entry.checksum or "", entry.size, "", self.store.loose_rel_path(entry.checksum or "")
        )
        self.session.flush()
        logger.info("已按存档覆盖数据：{}（id {}）", entry.name, item.id)

    def _create_from_entry(
        self, entry: ArchiveEntry, owner_id: int | None, category_id: int | None = None
    ) -> tuple[DataItem | None, bool]:
        """复原一条条目为新数据项；返回 (数据项, 是否因冲突改名)。"""
        if not entry.checksum:
            return None, False
        if not self.store.content_available(entry.checksum):
            logger.warning("存档内容已丢失，无法还原：{}", entry.name)
            return None, False
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
        if not self.store.export_content(entry.checksum, target):
            logger.warning("存档内容已丢失，无法还原：{}", entry.name)
            return None, False
        renamed = Path(library_rel).name != entry.name
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
        self.blobs.register(entry.checksum, entry.size, "", self.store.loose_rel_path(entry.checksum))
        self.session.flush()
        logger.info("已从存档还原：{}（归属用户 {}）", entry.name, owner_id)
        return item, renamed

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
        actions, source, _conflicts = self._plan([entry], "restore", user_id)
        if not actions or actions[0].kind == "skip":
            logger.info("存档条目无需还原：{}", entry.name)
            return None
        if actions[0].kind == "missing":
            logger.warning("存档内容已丢失，无法还原：{}", entry.name)
            return None
        action = actions[0]
        if action.kind in ("align", "replace", "repair"):
            assert action.item is not None
            libraries = LibraryService(self.session, store=self.store)
            if action.item.is_deleted:
                self.items.restore([action.item])
            changed_content = (action.item.checksum or "") != (entry.checksum or "")
            if action.kind != "align" or changed_content or not self._file_present(action.item):
                self._replace_in_place(entry, action.item)
            elif bool(action.item.is_hidden) != bool(entry.is_hidden):
                libraries.set_item_hidden(action.item, bool(entry.is_hidden))
            self.session.flush()
            return action.item
        owner = user_id or entry.user_id or UserService(self.session).current_id()
        item, _renamed = self._create_from_entry(entry, owner, category_id=category_id)
        _ = source
        return item

    def restore_all(self, archive: Archive, user_id: int | None = None) -> dict[str, int]:
        """整档还原：每条回到其所属用户的原分类目录（恢复式）。"""
        report = self.restore(archive, "restore", user_id)
        restored = report.restored + report.repaired + report.hidden_fixed + report.undeleted
        return {"restored": restored, "skipped": report.skipped}

    # ---------------------------------------------------------------- 维护
    def orphans(self) -> list:
        """没有任何数据项或存档引用的 blob。"""
        entries = self.session.scalars(select(ArchiveEntry)).all()
        referenced = {entry.checksum for entry in entries if entry.checksum}
        referenced |= {item.checksum for item in self.session.scalars(select(DataItem)) if item.checksum}
        return [blob for blob in self.blobs.orphans() if blob.checksum not in referenced]

    def cleanup_orphans(self) -> tuple[int, int]:
        """可达性 GC：删无引用的 blob，并回收内容仓库里无引用的块 / pack / 写残残留。

        返回 (清理项数, 释放字节数)。
        """
        removed = 0
        freed = 0
        for blob in self.orphans():
            if blob.rel_path:  # 内容文件按 sha256 命名，与索引行同名同址
                self.store.remove(blob.rel_path)
                freed += blob.size
            self.session.delete(blob)
            removed += 1
        self.session.flush()
        stats = self.store.cleanup()
        removed += sum(
            int(stats.get(key, 0))
            for key in ("contents", "orphans", "part")
        )
        freed += int(stats.get("bytes", 0))
        logger.info("已清理 {} 份冗余内容，释放 {} 字节", removed, freed)
        return removed, freed

    def auto_cleanup(self) -> dict[str, int]:
        """自动清理（配置项 Auto-Cleanup）：回收无引用内容，顺带升级旧压缩方案。

        返回 `{'removed', 'freed', 'recoded', 'recompress_bytes'}`；开关关闭时全 0。
        """
        result = {"removed": 0, "freed": 0, "recoded": 0, "recompress_bytes": 0}
        if not bool(config.autoCleanup.value):
            return result
        removed, freed = self.cleanup_orphans()
        result["removed"] = removed
        result["freed"] = freed
        if self.store.needs_recode():  # 旧编码（deflate / 原样存）趁这次一并升级
            stats = self.store.recompress()
            result["recoded"] = int(stats.get("recoded", 0))
            result["recompress_bytes"] = int(stats.get("freed", 0))
            logger.info(
                "已按当前压缩方案重写 {} 份内容，省下 {} 字节",
                result["recoded"],
                result["recompress_bytes"],
            )
        return result

    def verify(self, level: str = "quick"):
        """完整性校验（§8）：quick 比索引与文件系统，deep 再解码并重算校验和。"""
        return self.store.verify(level)

    def reset_storage(self) -> dict[str, int]:
        """开发期重置：清空全部存档与仓库副本，数据项与库里的真实文件不动。"""
        archives = int(self.session.scalar(select(func.count()).select_from(Archive)) or 0)
        entries = int(self.session.scalar(select(func.count()).select_from(ArchiveEntry)) or 0)
        self.session.execute(delete(ArchiveEntry))
        self.session.execute(delete(Archive))
        self.session.flush()
        stats = self.store.reset()
        logger.warning(
            "已清空存档与仓库副本：存档 {} 个 / 条目 {} 条 / 内容 {} 份 / 释放 {} 字节",
            archives,
            entries,
            stats.get("contents", 0),
            stats.get("total_bytes", 0),
        )
        return {"archives": archives, "entries": entries, **stats}

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
            text = f"自动清理：仓库容量上限 {int(config.keepSize.value)} MB"
        elif mode == "age":
            text = f"自动清理：保留最近 {int(config.keepDays.value)} 天"
        elif mode == "none":
            text = "自动清理：已关闭"
        else:
            text = f"自动清理：保留最近 {int(config.keepVersions.value)} 个存档"
        if bool(config.autoCleanup.value):
            text += "；回收无引用内容，并按当前压缩方案重写旧内容"
        else:
            text += "；不自动回收内容"
        return text

    def _all_items(self, user_id: int | None = None, *, include_deleted: bool = True) -> list[DataItem]:
        filters = ItemFilter(include_hidden=True, include_deleted=include_deleted)
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

    def delete(self, archive: Archive) -> bool:
        """删除存档；已标记的存档受保护，必须先取消标记（返回 False 表示没有删除）。"""
        if archive.pinned:
            logger.warning("存档「{}」已标记，需要先取消标记才能删除", archive.name)
            return False
        self.archives.delete_archive(archive)
        return True

    # ------------------------------------------------- 重新加载存档文件
    def plan_rebuild(self) -> RebuildPlan:
        """预检（阶段 ①）：只看不写，逐条判断真实文件该从存档还原还是直接复用。"""
        plan = RebuildPlan()
        for archive in self.session.scalars(select(Archive).order_by(Archive.id)):
            plan.archives += 1
            for entry in self.archives.entries_of(archive):
                plan.entries.append(self._plan_entry(entry))
        logger.info("重新加载存档文件预检：{}", plan.summary())
        return plan

    def _plan_entry(self, entry: ArchiveEntry) -> RebuildEntry:
        libraries = LibraryService(self.session, store=self.store)
        library = libraries.ensure_default()
        item = self.session.get(DataItem, entry.item_id) if entry.item_id else None
        planned = RebuildEntry(
            entry=entry,
            library_id=library.id,
            item=item,
            checksum=entry.checksum or "",
            size=entry.size,
        )
        rel_path = item.file_path if (item is not None and item.file_path) else ""
        if not rel_path:
            category_id = self._category_by_path(entry.category, entry.user_id)
            subdir = paths.HIDDEN_DIR_NAME if entry.is_hidden else ""
            rel_path = libraries.unique_rel_path(
                library, category_id, entry.name, user_id=entry.user_id, subdir=subdir
            )
        planned.rel_path = rel_path
        target = Path(library.path) / rel_path
        if target.is_file():
            checksum = sha256_of(target)
            planned.checksum = checksum
            planned.size = target.stat().st_size
            planned.status = "reuse" if checksum == (entry.checksum or "") else "diverged"
        elif entry.checksum and self.store.content_available(entry.checksum):
            planned.status = "restore"
        else:
            planned.status = "missing"
        return planned

    def _category_by_path(self, path: str, user_id: int | None) -> int | None:
        """按路径找分类（只读，不新建）；任一级找不到就返回 None。"""
        parent_id: int | None = None
        for part in (segment.strip() for segment in (path or "").split("/")):
            if not part:
                continue
            found = self.categories.by_name(part, parent_id, user_id=user_id)
            if found is None:
                return None
            parent_id = found.id
        return parent_id

    def _restore_file(self, planned: RebuildEntry) -> bool:
        """阶段 ②：把存档里的内容写回缺失的真实文件。"""
        checksum = planned.entry.checksum or ""
        if not checksum:
            return False
        library = LibraryService(self.session, store=self.store).ensure_default()
        target = Path(library.path) / planned.rel_path
        paths.make_dir(target.parent)
        if not self.store.export_content(checksum, target):
            logger.warning("存档内容已丢失，无法还原文件：{}", planned.name)
            return False
        planned.checksum = checksum
        planned.size = planned.entry.size
        planned.status = "restore"
        return True

    def _rebuild_content(self, planned: RebuildEntry) -> bool:
        """阶段 ③：按真实文件（重新）整份压缩入库，记下它现在的 checksum。"""
        library = LibraryService(self.session, store=self.store).ensure_default()
        target = Path(library.path) / planned.rel_path
        if not target.is_file():
            return False
        checksum = sha256_of(target)
        size = target.stat().st_size
        try:
            stored, _rel_path, content_size = self.store.put_file(target, name=planned.name)
        except OSError as exc:
            logger.warning("重建内容索引失败：{}（{}）", planned.name, exc)
            return False
        planned.checksum = stored
        planned.size = content_size
        if planned.item is not None and planned.item.checksum != stored:
            planned.item.checksum = stored
            planned.item.size = size
            self.blobs.register(stored, size, "", self.store.loose_rel_path(stored))
        return True

    def rebuild_storage(self, *, on_event=None, cancel=None) -> RebuildReport:
        """重新加载存档文件：以真实文件为准，按四阶段重建内容索引。

        ① 预检 → ② 把丢失的真实文件从仓库内容还原出来 → ③ 为每条真实文件整份
        压缩入库 → ④ 统一把 archive_entries 的内容指向切到新校验和（逻辑行与行 id
        都不变）。阶段 ④ 之前任何失败或取消都不会改动现有索引。
        """
        global _REBUILD_ACTIVE
        if _REBUILD_ACTIVE:
            raise RebuildBlocked("已经有一个「重新加载存档文件」在进行")
        user = UserService(self.session).current()
        if not user.is_default:
            raise PermissionError("只有默认用户可以重新加载存档文件")
        report = RebuildReport()
        _REBUILD_ACTIVE = True
        try:
            report.contents_before = int(self.store.usage()["contents"])
            report.plan = self.plan_rebuild()
            plan = report.plan
            tasks = plan.restore_needed
            for index, planned in enumerate(tasks, 1):
                _check_cancel(cancel)
                _notify(on_event, "restore", index, len(tasks), planned.name)
                if self._restore_file(planned):
                    report.restored_files += 1
                else:
                    planned.status = "missing"
            targets = [item for item in plan.entries if item.status != "missing"]
            for index, planned in enumerate(targets, 1):
                _check_cancel(cancel)
                _notify(on_event, "rebuild", index, len(targets), planned.name)
                if self._rebuild_content(planned):
                    report.rebuilt += 1
                else:
                    planned.status = "missing"
                    report.failed += 1
            switched = 0
            for planned in plan.entries:
                if planned.status == "missing" or not planned.checksum:
                    continue
                planned.entry.checksum = planned.checksum
                planned.entry.size = planned.size
                switched += 1
            self.session.flush()
            report.switched = switched
            report.contents_after = int(self.store.usage()["contents"])
            logger.info("重新加载存档文件完成：{}", report.summary())
            return report
        finally:
            _REBUILD_ACTIVE = False
