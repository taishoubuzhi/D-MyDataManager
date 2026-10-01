"""库目录结构迁移：把旧布局（v1）一次性升级为新的单一库 + 用户名文件夹布局（v2）。

旧布局：库根目录下平铺“分类目录/文件”，内容仓库在 resources/store、封面在 resources/covers，可登记多个库。
新布局：<库>/全局/{store,covers,backups,.datamanager} 放全局资源，<库>/<用户名>/<分类链>/文件 放各用户数据。

迁移是幂等的：成功后会在 <库>/全局/.datamanager/layout-2.json 写下标记，再次启动直接跳过。
迁移前会先把数据库备份到 <库>/全局/backups/，移动失败的文件保留原样并记录日志。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from ..core import paths
from ..db.database import backup_database_file
from ..db.models import DataItem, Library
from .library_service import LibraryService, sanitize_dir_name


def _move_into(source: Path, target: Path) -> None:
    """把 source 目录的内容合并进 target 目录。"""
    target.mkdir(parents=True, exist_ok=True)
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
        target_dir.mkdir(parents=True, exist_ok=True)
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
        destination.parent.mkdir(parents=True, exist_ok=True)
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

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {"version": paths.LAYOUT_VERSION, "at": dt.datetime.now().isoformat(timespec="seconds"), "stats": stats},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    service.ensure_layout(library)
    session.flush()
    logger.info("库目录结构迁移完成：{}", stats)
    return {"migrated": True, **stats}


__all__ = ["migrate_layout", "sanitize_dir_name"]
