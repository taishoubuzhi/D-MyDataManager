"""分类 ↔ 库目录同步：分类树就是数据文件目录结构的可视化。

规则（与 `docs/HELP.md`、`TaxonomyService` 保持一致）：

- **磁盘目录是权威**：`<库>/<用户名>/<目录链>` 的每一层目录都对应一个同名分类；
  用户名文件夹本身是「未分类」——它下面直接放的文件属于「未分类」，没有单独的目录。
- **反向也要补齐**：分类在磁盘上没有目录时，有子分类或数据的重建目录，纯空壳的收掉。
- **路径对齐**：数据项的 `file_path` 与磁盘不符时按真实位置重新定位（复用 `layout_migration`
  的找回逻辑，覆盖早期版本「文件没搬却写了新路径」的坏记录）。

首次运行（还没有 `categories-1.json` 标记）只为既有分类补齐目录、不做删除，避免把升级前
「还没有目录概念」的分类误删；之后每次运行都会按目录结构整理空壳分类。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.runtime import jsonio, paths
from ..db.models import Category, DataItem, Library, User
from ..db.seed import UNCATEGORIZED_NAME
from .layout_migration import _locate_library_file
from .library_service import LibraryService, is_uncategorized_category, sanitize_dir_name

#: 首次运行标记：写下之后才启用「磁盘为准」的清理
CATEGORY_LAYOUT_MARKER = "categories-1.json"


def reconcile_categories(
    session: Session, *, prune: bool | None = None, library: Library | None = None
) -> dict:
    """把分类与库目录对齐一遍（幂等）；返回统计。"""
    service = LibraryService(session)
    library = library or service.ensure_default()
    root = Path(library.path)
    stats = {
        "created": 0,
        "recategorized": 0,
        "relinked": 0,
        "missing": 0,
        "folded": 0,
        "pruned": 0,
        "healed": 0,
    }
    if not root.is_dir():
        logger.warning("库文件夹不存在，跳过分类同步：{}", root)
        return {**stats, "skipped": True}

    marker = service.meta_dir(library) / CATEGORY_LAYOUT_MARKER
    first_run = not marker.exists()
    if prune is None:
        prune = not first_run

    for user in service.users.list_all():
        _reconcile_user(service, library, user, stats)
    session.flush()
    if prune:
        stats["pruned"] = _prune_empty_categories(service, library)
    stats["healed"] = service.ensure_category_dirs(library)

    if first_run:
        paths.make_dir(marker.parent)
        jsonio.write_json(marker, {"version": 1})
        logger.info("分类目录模型已启用：为既有分类补齐了目录（本次不做清理）")
    session.flush()
    if any(stats[key] for key in ("created", "recategorized", "relinked", "folded", "pruned")):
        logger.info("分类与库目录已同步：{}", stats)
    return stats


# --------------------------------------------------------------------- 用户
def _reconcile_user(service: LibraryService, library: Library, user: User, stats: dict) -> None:
    """同步单个用户：旧「未分类」目录回迁 → 目录建分类 → 数据项路径与归属对齐。"""
    user_dir = service.user_dir(library, user)
    if not user_dir.is_dir():
        return
    stats["folded"] += _fold_uncategorized_dir(service, user_dir)
    _mirror_dirs(service, user, user_dir, stats)
    _align_items(service, library, user, stats)


def _fold_uncategorized_dir(service: LibraryService, user_dir: Path) -> int:
    """旧布局里「未分类」有自己的目录；现在它就是用户根目录，把内容搬回去并删掉空目录。"""
    legacy = user_dir / sanitize_dir_name(UNCATEGORIZED_NAME)
    if not legacy.is_dir():
        return 0
    moved = 0
    for entry in sorted(legacy.iterdir()):
        target = user_dir / entry.name
        if entry.is_dir() and target.is_dir():
            service.merge_dir(entry, target)
            moved += 1
        elif target.exists():
            continue
        else:
            paths.make_dir(target.parent)
            entry.rename(target)
            moved += 1
    try:
        legacy.rmdir()
    except OSError:
        pass
    logger.info("「{}」目录已并回用户根目录（{} 项）", UNCATEGORIZED_NAME, moved)
    return moved


def _mirror_dirs(service: LibraryService, user: User, user_dir: Path, stats: dict) -> None:
    """磁盘目录 → 分类：每个目录层级都对应一个同名分类。"""
    for path in sorted(user_dir.rglob("*")):
        if not path.is_dir():
            continue
        parts = path.relative_to(user_dir).parts
        if any(part.startswith(".") or part == paths.HIDDEN_DIR_NAME for part in parts):
            continue
        _ensure_chain(service, user, parts, stats)


def _ensure_chain(service: LibraryService, user: User, parts, stats: dict) -> int | None:
    """确保这条目录链上的分类都存在，返回最深一级的分类 id。"""
    parent_id: int | None = None
    for name in parts:
        category = _sibling_by_dir_name(service, name, parent_id, user.id)
        if category is None:
            if service.categories.siblings_named(name, parent_id):
                return parent_id  # 同级同名被其它归属占用，只能停在这一层
            category = service.categories.create(name, parent_id, user_id=user.id)
            service.session.flush()
            stats["created"] += 1
        parent_id = category.id
    return parent_id


def _sibling_by_dir_name(
    service: LibraryService, dir_name: str, parent_id: int | None, user_id: int | None
) -> Category | None:
    """按目录名在同级里找分类：目录名是分类名 `sanitize_dir_name()` 之后的结果。"""
    shared: Category | None = None
    for row in service.categories.children_of(parent_id):
        if sanitize_dir_name(row.name) != dir_name:
            continue
        if user_id is not None and row.user_id == user_id:
            return row
        if row.user_id is None and shared is None:
            shared = row
    return shared


def _align_items(service: LibraryService, library: Library, user: User, stats: dict) -> None:
    """数据项按真实文件位置重新定位，并按所在目录反推分类。"""
    root = Path(library.path)
    rows = list(service.session.scalars(select(DataItem).where(DataItem.user_id == user.id)))
    for item in rows:
        rel = str(item.file_path or "")
        if not rel:
            continue
        if not (root / rel).is_file():
            found = _locate_library_file(root, rel)
            if found is None:
                stats["missing"] += 1
                logger.warning("库内找不到文件，保留原路径：{}", rel)
                continue
            try:
                item.file_path = found.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - 只在路径跑到库外时
                item.file_path = str(found)
            rel = item.file_path
            stats["relinked"] += 1

        parts = Path(rel).parts
        dir_parts = list(parts[1:-1])
        if paths.HIDDEN_DIR_NAME in dir_parts:
            dir_parts = dir_parts[: dir_parts.index(paths.HIDDEN_DIR_NAME)]
        dir_parts = [part for part in dir_parts if part and not part.startswith(".")]
        category_id = _ensure_chain(service, user, dir_parts, stats) if dir_parts else None
        if category_id is None:
            category = service.categories.by_name(UNCATEGORIZED_NAME, None, user.id)
            if category is None:
                category = service.categories.create(UNCATEGORIZED_NAME, None, user_id=user.id)
                service.session.flush()
                stats["created"] += 1
            category_id = category.id
        if item.category_id != category_id:
            item.category_id = category_id
            stats["recategorized"] += 1


# --------------------------------------------------------------------- 清理
def _depth(category: Category) -> int:
    depth, node, guard = 0, category, set()
    while node is not None and node.id not in guard:
        guard.add(node.id)
        depth += 1
        node = node.parent
    return depth


def _prune_empty_categories(service: LibraryService, library: Library) -> int:
    """删掉「磁盘上没有目录、也没有数据与子分类」的空壳分类（自下而上）。

    只有在**所有**相关用户的用户名文件夹都存在、而分类目录确实不存在时才删；用户名文件夹
    本身缺失（换过库位置、盘没挂上）时保持不动，免得把分类误删。
    """
    counts = service.categories.item_counts()
    rows = sorted(service.session.scalars(select(Category)), key=_depth, reverse=True)
    pruned = 0
    for category in rows:
        if is_uncategorized_category(category):
            continue
        if counts.get(category.id, 0) or service.categories.children_of(category.id):
            continue
        missing, unknown = 0, False
        for owner in service.category_users(category):
            base = Path(library.path) / service.owner_dir_name(owner)
            if not base.is_dir():
                unknown = True
                break
            if not service.directory_for(library, category.id, owner).is_dir():
                missing += 1
        if unknown or not missing:
            continue
        name = category.name
        service.session.expunge(category)
        service.session.execute(delete(Category).where(Category.id == category.id))
        pruned += 1
        logger.info("分类在磁盘上没有对应目录，已收掉：{}", name)
    return pruned


__all__ = ["CATEGORY_LAYOUT_MARKER", "reconcile_categories"]
