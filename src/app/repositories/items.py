"""数据项仓储：负责查询过滤、批量操作与软删除。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import Select, exists, false, func, or_, select, text as sql_text
from sqlalchemy.exc import SQLAlchemyError

from ..db.database import FTS_TABLE
from ..db.models import DataItem, DataType, Tag
from .base import Repository

_SORT_COLUMNS = {
    "name": DataItem.name,
    "created_at": DataItem.created_at,
    "updated_at": DataItem.updated_at,
    "size": DataItem.size,
    "type": DataItem.type,
}


@dataclass
class ItemFilter:
    """数据管理页的查询条件。"""

    text: str = ""
    text_ids: set[int] | None = None
    types: set[DataType] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    keywords: set[str] = field(default_factory=set)
    category_ids: set[int] = field(default_factory=set)
    library_ids: set[int] = field(default_factory=set)
    user_ids: set[int] = field(default_factory=set)
    suffixes: set[str] = field(default_factory=set)
    exclude_tags: set[str] = field(default_factory=set)
    include_hidden: bool = False
    only_hidden: bool = False
    include_deleted: bool = False
    only_deleted: bool = False
    min_size: int = 0
    max_size: int = 0
    date_from: dt.datetime | None = None
    date_to: dt.datetime | None = None
    sort_by: str = "created_at"
    descending: bool = True


class ItemRepository(Repository[DataItem]):
    model = DataItem

    # ---------------------------------------------------------------- 查询
    def build_query(self, flt: ItemFilter) -> Select:
        stmt = select(DataItem)
        if flt.only_deleted:
            stmt = stmt.where(DataItem.is_deleted.is_(True))
        elif not flt.include_deleted:
            stmt = stmt.where(DataItem.is_deleted.is_(False))

        if flt.only_hidden:
            stmt = stmt.where(DataItem.is_hidden.is_(True))
        elif not flt.include_hidden:
            stmt = stmt.where(DataItem.is_hidden.is_(False))

        if flt.text:
            like = f"%{flt.text}%"
            # keywords 以 JSON 存储（ASCII 转义），须在展开后的成员值上匹配
            keyword_values = func.json_each(DataItem.keywords).table_valued("value")
            stmt = stmt.where(
                or_(
                    DataItem.name.like(like),
                    DataItem.content.like(like),
                    DataItem.source_path.like(like),
                    exists(
                        select(1)
                        .select_from(keyword_values)
                        .where(keyword_values.c.value.like(like))
                    ),
                )
            )
        if flt.text_ids is not None:
            stmt = stmt.where(DataItem.id.in_(list(flt.text_ids)) if flt.text_ids else false())
        if flt.types:
            stmt = stmt.where(DataItem.type.in_(list(flt.types)))
        if flt.category_ids:
            stmt = stmt.where(DataItem.category_id.in_(list(flt.category_ids)))
        if flt.library_ids:
            stmt = stmt.where(DataItem.library_id.in_(list(flt.library_ids)))
        if flt.user_ids:
            stmt = stmt.where(DataItem.user_id.in_(list(flt.user_ids)))
        if flt.tags:
            stmt = stmt.where(DataItem.tags.any(Tag.name.in_(list(flt.tags))))
        if flt.exclude_tags:
            # 一条都不要带上这些标签（批量打标签时用来跳过已经做过的条目）
            stmt = stmt.where(~DataItem.tags.any(Tag.name.in_(list(flt.exclude_tags))))
        if flt.suffixes:
            # 后缀按「文件名结尾」匹配：存库路径与原始路径都算（SQLite 的 LIKE 对 ASCII 不区分大小写）
            endings = []
            for suffix in flt.suffixes:
                text = str(suffix).strip().lower().lstrip(".")
                if text:
                    endings.append(DataItem.file_path.like(f"%.{text}"))
                    endings.append(DataItem.source_path.like(f"%.{text}"))
            if endings:
                stmt = stmt.where(or_(*endings))
        for keyword in flt.keywords:
            values = func.json_each(DataItem.keywords).table_valued("value")
            stmt = stmt.where(
                exists(select(1).select_from(values).where(values.c.value == str(keyword)))
            )
        if flt.min_size:
            stmt = stmt.where(DataItem.size >= flt.min_size)
        if flt.max_size:
            stmt = stmt.where(DataItem.size <= flt.max_size)
        if flt.date_from:
            stmt = stmt.where(DataItem.created_at >= flt.date_from)
        if flt.date_to:
            stmt = stmt.where(DataItem.created_at <= flt.date_to)

        column = _SORT_COLUMNS.get(flt.sort_by, DataItem.created_at)
        return stmt.order_by(column.desc() if flt.descending else column.asc())

    def query(self, flt: ItemFilter, limit: int | None = None, offset: int = 0) -> list[DataItem]:
        stmt = self.build_query(flt)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def count(self, flt: ItemFilter) -> int:
        return self.session.scalar(select(func.count()).select_from(self.build_query(flt).subquery())) or 0

    def recent(self, limit: int = 8, user_id: int | None = None) -> list[DataItem]:
        filters = ItemFilter(sort_by="created_at", descending=True)
        if user_id:
            filters.user_ids = {int(user_id)}
        return self.query(filters, limit=limit)

    def by_checksum(self, checksum: str) -> list[DataItem]:
        if not checksum:
            return []
        stmt = select(DataItem).where(DataItem.checksum == checksum, DataItem.is_deleted.is_(False))
        return list(self.session.scalars(stmt))

    def by_checksums(self, checksums, *, include_deleted: bool = False) -> list[DataItem]:
        """按内容校验和批量取数据项；`include_deleted=True` 时连回收站项一起返回。"""
        wanted = [value for value in dict.fromkeys(checksums) if value]
        if not wanted:
            return []
        stmt = select(DataItem).where(DataItem.checksum.in_(wanted)).order_by(DataItem.id)
        if not include_deleted:
            stmt = stmt.where(DataItem.is_deleted.is_(False))
        return list(self.session.scalars(stmt))

    def by_names(self, names, *, include_deleted: bool = False, user_id: int | None = None) -> list[DataItem]:
        """按文件名批量取数据项（存档条目按「归属 + 文件名」找回对应项时用）。

        `include_deleted=True` 连回收站项一起返回；`user_id` 限定归属用户。
        """
        wanted = [value for value in dict.fromkeys(names) if value]
        if not wanted:
            return []
        stmt = select(DataItem).where(DataItem.name.in_(wanted)).order_by(DataItem.id)
        if not include_deleted:
            stmt = stmt.where(DataItem.is_deleted.is_(False))
        if user_id is not None:
            stmt = stmt.where(DataItem.user_id == user_id)
        return list(self.session.scalars(stmt))

    def by_ids(self, ids) -> list[DataItem]:
        wanted = [int(value) for value in ids]
        if not wanted:
            return []
        stmt = select(DataItem).where(DataItem.id.in_(wanted)).order_by(DataItem.id)
        return list(self.session.scalars(stmt))

    def in_category(self, category_id: int | None) -> list[DataItem]:
        return self.query(ItemFilter(category_ids={category_id} if category_id else set()))

    def by_library_path(self, library_id: int, rel_path: str) -> DataItem | None:
        if not rel_path:
            return None
        stmt = select(DataItem).where(
            DataItem.library_id == library_id, DataItem.file_path == rel_path,
        )
        return self.session.scalars(stmt).first()

    def library_paths(self, library_id: int) -> set[str]:
        stmt = select(DataItem.file_path).where(DataItem.library_id == library_id)
        return {path for path in self.session.scalars(stmt) if path}

    # ------------------------------------------------------------- 全文检索
    def search_ids(self, text: str, limit: int = 5000) -> set[int]:
        """全文检索命中的数据项 id 集合；多词为 AND 语义。

        3 字符以上的词走 FTS5 trigram 索引，更短的词退回 LIKE 兜底；名称始终按 LIKE 匹配。
        """
        terms = [term for term in (text or "").split() if term]
        if not terms:
            return set()

        matched: set[int] | None = None
        long_terms = [term for term in terms if len(term) >= 3]
        if long_terms:
            expr = " AND ".join('"' + term.replace('"', '""') + '"' for term in long_terms)
            try:
                rows = self.session.execute(
                    sql_text(f"SELECT rowid FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH :expr LIMIT {int(limit)}"),
                    {"expr": expr},
                )
                matched = {int(row[0]) for row in rows}
            except SQLAlchemyError:
                logger.warning("全文检索不可用，改为名称匹配：{}", text)
                matched = None
        if matched is None:
            matched = self._like_ids(terms)

        for term in (term for term in terms if len(term) < 3):
            matched &= self._like_ids([term])
        return matched | self._like_ids([text.strip()], columns=(DataItem.name,))

    def _like_ids(self, terms: list[str], columns=None) -> set[int]:
        targets = columns or (DataItem.name, DataItem.content, DataItem.source_path)
        conditions = []
        for term in terms:
            like = f"%{term}%"
            conditions.extend(column.like(like) for column in targets)
            keyword_values = func.json_each(DataItem.keywords).table_valued("value")
            conditions.append(
                exists(select(1).select_from(keyword_values).where(keyword_values.c.value.like(like)))
            )
        stmt = select(DataItem.id).where(or_(*conditions))
        return {int(value) for value in self.session.scalars(stmt)}

    # ---------------------------------------------------------------- 写入
    def create(self, **kwargs) -> DataItem:
        keywords = kwargs.pop("keywords", None) or []
        item = DataItem(keywords=list(keywords), **kwargs)
        return self.add(item)

    def update(self, item: DataItem, **fields) -> DataItem:
        for key, value in fields.items():
            setattr(item, key, value)
        item.updated_at = dt.datetime.now()
        self.session.flush()
        return item

    def soft_delete(self, items: list[DataItem]) -> int:
        for item in items:
            item.is_deleted = True
            item.deleted_at = dt.datetime.now()
        self.session.flush()
        return len(items)

    def restore(self, items: list[DataItem]) -> int:
        for item in items:
            item.is_deleted = False
            item.deleted_at = None
        self.session.flush()
        return len(items)

    def purge(self, items: list[DataItem]) -> int:
        for item in items:
            self.session.delete(item)
        self.session.flush()
        return len(items)

    # ---------------------------------------------------------------- 批量
    def bulk_set_field(self, items: list[DataItem], field_name: str, value) -> int:
        for item in items:
            setattr(item, field_name, value)
            item.updated_at = dt.datetime.now()
        self.session.flush()
        return len(items)

    def bulk_add_tags(self, items: list[DataItem], tags: list[Tag]) -> int:
        for item in items:
            for tag in tags:
                if tag not in item.tags:
                    item.tags.append(tag)
        self.session.flush()
        return len(items)

    def bulk_remove_tags(self, items: list[DataItem], tags: list[Tag]) -> int:
        for item in items:
            for tag in tags:
                if tag in item.tags:
                    item.tags.remove(tag)
        self.session.flush()
        return len(items)

    # ---------------------------------------------------------------- 统计
    def stats(self, user_id: int | None = None) -> dict:
        def scoped(stmt):
            return stmt.where(DataItem.user_id == user_id) if user_id else stmt

        total = self.session.scalar(
            scoped(select(func.count()).select_from(DataItem).where(DataItem.is_deleted.is_(False)))
        ) or 0
        total_size = self.session.scalar(
            scoped(select(func.coalesce(func.sum(DataItem.size), 0)).where(DataItem.is_deleted.is_(False)))
        ) or 0
        by_type = {
            str(t).split(".")[-1]: count
            for t, count in self.session.execute(
                scoped(
                    select(DataItem.type, func.count(DataItem.id))
                    .where(DataItem.is_deleted.is_(False))
                    .group_by(DataItem.type)
                )
            )
        }
        hidden = self.session.scalar(
            scoped(
                select(func.count()).select_from(DataItem)
                .where(DataItem.is_hidden.is_(True), DataItem.is_deleted.is_(False))
            )
        ) or 0
        trashed = self.session.scalar(
            scoped(select(func.count()).select_from(DataItem).where(DataItem.is_deleted.is_(True)))
        ) or 0
        duplicates = self.session.execute(
            scoped(
                select(DataItem.checksum, func.count(DataItem.id))
                .where(DataItem.checksum != "", DataItem.is_deleted.is_(False))
                .group_by(DataItem.checksum)
                .having(func.count(DataItem.id) > 1)
            )
        ).all()
        return {
            "total": total,
            "total_size": int(total_size),
            "by_type": by_type,
            "hidden": hidden,
            "trashed": trashed,
            "duplicate_groups": len(duplicates),
            "duplicate_extra": sum(count - 1 for _, count in duplicates),
        }
