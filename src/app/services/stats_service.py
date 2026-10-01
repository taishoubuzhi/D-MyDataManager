"""统计服务：首页概览与容量分析。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import store_dir
from ..db.models import Category, DataItem, DataType, Tag
from ..repositories import ItemRepository
from .blob_store import BlobStore


def type_label(value) -> str:
    if isinstance(value, DataType):
        return value.value
    text = str(value)
    return text.split(".")[-1]


def overview(session: Session, store: BlobStore | None = None, user_id: int | None = None) -> dict:
    items = ItemRepository(session)
    stats = items.stats(user_id=user_id)
    today = dt.datetime.combine(dt.date.today(), dt.time.min)
    week = today - dt.timedelta(days=7)

    def scoped(stmt, column=None):
        if not user_id:
            return stmt
        return stmt.where((column if column is not None else DataItem.user_id) == user_id)

    stats["categories"] = int(
        session.scalar(scoped(select(func.count(Category.id)), Category.user_id)) or 0
    )
    stats["tags"] = int(session.scalar(scoped(select(func.count(Tag.id)), Tag.user_id)) or 0)
    stats["today"] = int(
        session.scalar(
            scoped(
                select(func.count(DataItem.id)).where(
                    DataItem.created_at >= today, DataItem.is_deleted.is_(False)
                )
            )
        )
        or 0
    )
    stats["week"] = int(
        session.scalar(
            scoped(
                select(func.count(DataItem.id)).where(
                    DataItem.created_at >= week, DataItem.is_deleted.is_(False)
                )
            )
        )
        or 0
    )
    stats["storage"] = storage_usage(session, store)
    return stats


def type_breakdown(session: Session, user_id: int | None = None) -> list[dict]:
    stmt = (
        select(DataItem.type, func.count(DataItem.id), func.coalesce(func.sum(DataItem.size), 0))
        .where(DataItem.is_deleted.is_(False))
        .group_by(DataItem.type)
    )
    if user_id:
        stmt = stmt.where(DataItem.user_id == user_id)
    rows = session.execute(stmt).all()
    result = [
        {"type": type_label(row[0]), "count": int(row[1]), "size": int(row[2])}
        for row in rows
    ]
    result.sort(key=lambda row: row["count"], reverse=True)
    return result


def storage_usage(session: Session, store: BlobStore | None = None, user_id: int | None = None) -> dict:
    blob_store = store or BlobStore(store_dir())
    logical_stmt = select(func.coalesce(func.sum(DataItem.size), 0)).where(
        DataItem.is_deleted.is_(False)
    )
    if user_id:
        logical_stmt = logical_stmt.where(DataItem.user_id == user_id)
    logical = int(session.scalar(logical_stmt) or 0)
    unique_stmt = (
        select(DataItem.checksum, func.max(DataItem.size).label("size"))
        .where(DataItem.is_deleted.is_(False), DataItem.checksum != "")
        .group_by(DataItem.checksum)
    )
    if user_id:
        unique_stmt = unique_stmt.where(DataItem.user_id == user_id)
    unique_subquery = unique_stmt.subquery()
    unique = int(session.scalar(select(func.coalesce(func.sum(unique_subquery.c.size), 0))) or 0)
    return {
        "logical_size": logical,
        "unique_size": unique,
        "disk_size": blob_store.total_size(),
        "saved": max(0, logical - unique),
    }


def recent(session: Session, limit: int = 8, user_id: int | None = None) -> list[DataItem]:
    return ItemRepository(session).recent(limit, user_id=user_id)
