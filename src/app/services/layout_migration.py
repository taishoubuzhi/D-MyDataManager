"""库目录结构迁移：把旧布局（v1）一次性升级为新的单一库 + 用户名文件夹布局（v2）。

旧布局：库根目录下平铺“分类目录/文件”，内容仓库在 resources/store、封面在 resources/covers，可登记多个库。
新布局：<库>/全局/{store,covers,backups,.datamanager} 放全局资源，<库>/<用户名>/<分类链>/文件 放各用户数据。

迁移是幂等的：成功后会在 <库>/全局/.datamanager/layout-2.json 写下标记，再次启动直接跳过。
迁移前会先把数据库备份到 <库>/全局/backups/，移动失败的文件保留原样并记录日志。
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.runtime import jsonio, paths
from ..db.database import backup_database_file
from ..db.models import DataItem, Library, User
from ..db.seed import UNCATEGORIZED_NAME
from .library_service import LibraryService, sanitize_dir_name


def _move_into(source: Path, target: Path) -> None:
    """把 source 目录的内容合并进 target 目录。"""
    paths.make_dir(target)
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if entry.is_dir() and destination.is_dir():
            _move_into(entry, destination)
        elif destination.exists():
            destination = target / f"{entry.stem}_{len(list(target.iterdir()))}{entry.suffix}"
            shutil.move(str(entry), str(destination))
        else:
            shutil.move(str(entry), str(destination))
    try:
        source.rmdir()
    except OSError:
        pass


def _unique_target(root: Path, owner: str, legacy_rel: Path) -> str:
    """<用户名>/<旧分类链>/<文件名>；目标已存在时加 _N 后缀。"""
    directory = root / owner / legacy_rel.parent
    stem, suffix = legacy_rel.stem or "未命名", legacy_rel.suffix
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate.relative_to(root).as_posix()


def _backup_database(target_dir: Path) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"data-before-layout-v{paths.LAYOUT_VERSION}-{stamp}.db.bak"
    backup = backup_database_file(target)
    if backup is None:
        return ""
    logger.info("迁移前已备份数据库：{}", backup)
    return str(backup)


def migrate_layout(session: Session) -> dict:
    """执行一次库目录结构迁移；返回迁移统计（已迁移过时 migrated 为 False）。"""
    service = LibraryService(session)
    # 迁移会改写 library_id（多库合并），先记录每个数据项原来的库根目录，便于旧文件定位
    sources: dict[int, Path] = {}
    for item in session.query(DataItem).all():
        library = session.get(Library, item.library_id) if item.library_id else None
        if library is not None:
            sources[item.id] = Path(library.path)

    library = service.ensure_default()
    root = Path(library.path)
    marker = service.meta_dir(library) / paths.LAYOUT_MARKER_FILE
    if marker.exists():
        return {"migrated": False, "reason": "已是新布局"}

    # 备份要从数据库文件里读，先把本会话尚未提交的改动（如启动时的 seed）落盘
    session.commit()

    stats = {"moved": 0, "missing": 0, "skipped": 0, "unassigned": 0, "store": 0, "covers": 0, "backup": ""}
    stats["backup"] = _backup_database(service.backup_dir(library))

    for legacy, target_dir in (
        (paths.LEGACY_STORE_DIR, service.store_dir(library)),
        (paths.LEGACY_COVER_DIR, service.cover_dir(library)),
    ):
        if not legacy.is_dir() or legacy.resolve() == target_dir.resolve():
            continue
        paths.make_dir(target_dir)
        count = sum(1 for path in legacy.rglob("*") if path.is_file())
        _move_into(legacy, target_dir)
        stats["store" if "store" in legacy.name else "covers"] = count
        logger.info("已迁移 {} 个文件：{} -> {}", count, legacy, target_dir)

    for item in session.query(DataItem).order_by(DataItem.id).all():
        legacy_rel = Path(item.file_path)
        if not item.file_path or not legacy_rel.name:
            stats["skipped"] += 1
            continue
        if item.user_id is None:
            stats["unassigned"] += 1
        # 保留旧的分类目录结构，只在最前面加一层用户名文件夹（分类因此可以每个用户不同）。
        owner = service.owner_dir_name(item.user_id)
        source = sources.get(item.id, root) / item.file_path
        # 记录里已经带了用户名目录（例如刚重建过数据库 / 标记被删）：不能再套一层用户名目录
        if legacy_rel.parts[:1] == (owner,):
            stats["skipped"] += 1
            continue
        target_rel = _unique_target(root, owner, legacy_rel)
        if not source.is_file():
            stats["missing"] += 1
            logger.warning("迁移跳过（源文件不存在）：{}", source)
            continue
        destination = root / target_rel
        paths.make_dir(destination.parent)
        shutil.move(str(source), str(destination))
        item.file_path = target_rel
        stats["moved"] += 1

    # 清掉迁移后残留的空目录（保留全局文件夹）
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and paths.GLOBAL_DIR_NAME != path.name and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass

    paths.make_dir(marker.parent)
    jsonio.write_json(
        marker,
        {"version": paths.LAYOUT_VERSION, "at": dt.datetime.now().isoformat(timespec="seconds"), "stats": stats},
    )
    service.ensure_layout(library)
    session.flush()
    logger.info("库目录结构迁移完成：{}", stats)
    return {"migrated": True, **stats}


def _locate_library_file(root: Path, rel_path: str) -> Path | None:
    """在库里找回条目对应的真实文件。

    `root / rel_path` 是权威位置；但 `migrate_uncategorized` 早期版本在文件没真的搬动时
    也把新路径写进了数据库，于是库里出现「数据库说在 <用户>/未分类/x.png、盘上却在
    <用户>/x.png」的条目（用户 m42407 的 logo_6.png）。找不到权威位置时按真实布局逐个试：
    `<用户>/<文件名>`（未分类直接在用户根）、`<分类>/.hiddens/<文件名>`、`<分类>/<文件名>`。
    """
    if not rel_path:
        return None
    candidate = root / rel_path
    if candidate.is_file():
        return candidate
    parts = Path(rel_path).parts
    name = parts[-1]
    user_root = root / parts[0] if len(parts) > 1 else root
    tries: list[Path] = [user_root / name, user_root / paths.HIDDEN_DIR_NAME / name]
    if user_root.is_dir():
        for entry in sorted(user_root.iterdir()):
            if not entry.is_dir():
                continue
            tries.append(entry / name)
            tries.append(entry / paths.HIDDEN_DIR_NAME / name)
            try:
                for sub in sorted(entry.iterdir()):
                    if sub.is_dir():
                        tries.append(sub / name)
            except OSError:
                continue
    for candidate in tries:
        if candidate.is_file():
            return candidate
    return None


def migrate_uncategorized(session: Session) -> dict:
    """给每个用户补齐「未分类」分类，并把没有分类的数据项归入其中；同时把库内路径对齐到磁盘（幂等）。

    **只对齐路径、不搬动文件**：数据库里记的 `file_path` 有时与盘上位置不符——早期版本在
    文件没真的搬动时也写了新路径（用户 m42407 的 `logo_6.png`：盘上在 `<用户>/` 根、数据库
    却记 `<用户>/未分类/logo_6.png`）。这里对**每一条**「`<库根>/file_path` 在盘上不存在」的
    条目用 `_locate_library_file()` 找回真实文件并修正 `file_path`：文件原地不动，之后
    「库根 + file_path」就与磁盘一致；找不到的条目保留原路径并记 warning。
    """
    service = LibraryService(session)
    library = service.ensure_default()
    root = Path(library.path)
    stats = {"created": 0, "moved": 0, "updated": 0, "unassigned": 0, "missing": 0, "relinked": 0}
    for user in session.scalars(select(User).order_by(User.id)):
        if service.categories.by_name(UNCATEGORIZED_NAME, None, user.id) is None:
            service.categories.create(UNCATEGORIZED_NAME, None, user_id=user.id)
            stats["created"] += 1

    # 分类归置与路径对齐都要跑：条目可能早就归到「未分类」，但库内路径仍与磁盘不符
    for item in session.scalars(select(DataItem)):
        if item.category_id is None:
            category = (
                service.categories.by_name(UNCATEGORIZED_NAME, None, item.user_id)
                if item.user_id is not None
                else None
            )
            if category is None:  # 无归属数据或用户已不存在
                stats["unassigned"] += 1
                continue
            item.category_id = category.id
            stats["updated"] += 1
        rel_path = str(item.file_path or "")
        if rel_path and (root / rel_path).is_file():
            continue  # 权威位置就对：一次 stat 就跳过，别为每条都做回退查找
        source = _locate_library_file(root, rel_path)
        if source is not None:
            try:
                item.file_path = source.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - 只在路径跑到库外时
                item.file_path = str(source)
            stats["relinked"] += 1
        elif rel_path:
            stats["missing"] += 1
            logger.warning("库内找不到文件，保留原路径：{}", rel_path)

    if stats["created"] or stats["updated"] or stats["relinked"]:
        session.flush()
        logger.info("未分类与库内路径已对齐：{}", stats)
    return stats


__all__ = ["migrate_layout", "migrate_uncategorized", "sanitize_dir_name"]
