"""程序本体的数据接口实现：插件通过扩展接口 `items.open` 读条目、读写标签与关键词。

设计要点：

- **每次调用自建会话**：插件会在自己的工作线程里批量调用，不能共用界面线程的会话；
  写操作走 `session_scope()`（正常提交、异常回滚），并在真的改了东西之后广播一次
  `itemsChanged`，于是数据管理页 / 标签页会自动刷新；
- **只暴露快照**：插件拿到的是 `ItemRef`，改它不会影响数据库；插件要改东西只能走这里的方法；
- 写标签复用现成的批量实现（`ItemRepository.bulk_add_tags` / `bulk_remove_tags`），
  并按「是否真的新增/摘除」统计改动条数，而不是把整批都算成改动。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger

from ..core.config import library_root
from ..db.database import new_session, session_scope
from ..db.models import DataItem, DataType
from ..repositories import ItemFilter
from ..sdk import items as items_api
from ..sdk.data import read_text as read_file_text
from .item_service import ItemService
from .user_service import UserService

__all__ = ["ItemsApi", "to_ref"]


def _clean_names(values: Iterable[str]) -> list[str]:
    """去空白、去重、保持顺序。"""
    names: list[str] = []
    for raw in values or ():
        text = str(raw).strip()
        if text and text not in names:
            names.append(text)
    return names


def _clean_ids(values: Iterable[int]) -> list[int]:
    ids: list[int] = []
    for raw in values or ():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value and value not in ids:
            ids.append(value)
    return ids


def _absolute_path(item: DataItem, path: str) -> str:
    """条目在盘上的绝对路径：外部条目用自己的 `source_path`，库内条目拼库根。

    插件手里只有 `file_path`（相对库根的相对路径），直接把它交给模型 worker 会得到
    `FileNotFoundError: 默认用户/未分类/logo_6.png`（用户 m42290：图片关键词生成报错）。
    """
    source = str(item.source_path or "").strip()
    if source and Path(source).is_absolute():
        return source
    if not path:
        return ""
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    try:
        return str(Path(library_root()) / candidate)
    except Exception:  # pragma: no cover - 库根读不出来时退回原路径
        return str(candidate)


def to_ref(item: DataItem, preview: str = "") -> items_api.ItemRef:
    """把数据库条目转成插件能看的只读快照（界面侧也用它构造选中上下文）。"""
    path = str(item.file_path or item.source_path or "")
    kind = getattr(item.type, "value", item.type)
    return items_api.ItemRef(
        id=int(item.id),
        name=str(item.name or ""),
        type=str(kind or "").lower(),
        suffix=Path(path).suffix.lower().lstrip("."),
        size=int(item.size or 0),
        keywords=tuple(str(word) for word in (item.keywords or [])),
        tags=tuple(sorted(str(tag.name) for tag in (item.tags or []))),
        file_path=path,
        abs_path=_absolute_path(item, path),
        category_id=item.category_id,
        user_id=item.user_id,
        preview=preview,
    )


class ItemsApi:
    """`items.open` 的实现。"""

    # ------------------------------------------------------------------ 查询
    def current_user_id(self) -> int | None:
        with new_session() as session:
            value = UserService(session).current_id()
        return int(value) if value else None

    def list_items(
        self,
        *,
        ids: Iterable[int] | None = None,
        user_id: int | None = None,
        suffix: Iterable[str] = (),
        type: str = "",
        include_hidden: bool = False,
        include_deleted: bool = False,
        exclude_tags: Iterable[str] = (),
        preview_bytes: int = 0,
        limit: int = 0,
    ) -> list[items_api.ItemRef]:
        wanted = _clean_ids(ids or ())
        preview_limit = max(0, int(preview_bytes or 0))
        top = max(0, int(limit or 0))
        with new_session() as session:
            service = ItemService(session)
            if wanted:
                rows = service.items.by_ids(wanted)
                if not include_deleted:
                    rows = [row for row in rows if not row.is_deleted]
                if not include_hidden:
                    rows = [row for row in rows if not row.is_hidden]
                if top:
                    rows = rows[:top]
            else:
                flt = ItemFilter(include_hidden=include_hidden, include_deleted=include_deleted)
                if user_id:
                    flt.user_ids = {int(user_id)}
                suffixes = {str(item).strip().lower().lstrip(".") for item in suffix or ()}
                flt.suffixes = {item for item in suffixes if item}
                kind = self._datatype(type)
                if str(type or "").strip() and kind is None:
                    logger.warning("未知的数据类型：{}", type)
                    return []
                if kind is not None:
                    flt.types = {kind}
                excluded = {str(item).strip() for item in exclude_tags or ()}
                flt.exclude_tags = {item for item in excluded if item}
                rows = service.items.query(flt, limit=top or None)
            return [self._ref(service, row, preview_limit) for row in rows]

    def get_item(self, item_id: int) -> items_api.ItemRef | None:
        wanted = _clean_ids([item_id])
        if not wanted:
            return None
        with new_session() as session:
            service = ItemService(session)
            rows = service.items.by_ids(wanted)
            return self._ref(service, rows[0], 0) if rows else None

    def suffixes_in_use(self, user_id: int | None = None) -> dict[str, int]:
        with new_session() as session:
            counts = ItemService(session).extensions_in_use(user_id)
        return {str(key).lower(): int(value) for key, value in counts.items()}

    def tag_names(self, user_id: int | None = None) -> list[str]:
        with new_session() as session:
            return [str(name) for name in ItemService(session).tags.names(user_id)]

    # ------------------------------------------------------------------ 写入
    def ensure_tags(self, names: Iterable[str], user_id: int | None = None) -> list[str]:
        wanted = _clean_names(names)
        if not wanted:
            return []
        with session_scope() as session:
            service = ItemService(session)
            service.tags.ensure_many(wanted, user_id=user_id)
        return wanted

    def tag_items(self, item_ids: Iterable[int], names: Iterable[str]) -> int:
        wanted = _clean_names(names)
        ids = _clean_ids(item_ids)
        if not wanted or not ids:
            return 0
        changed = 0
        with session_scope() as session:
            service = ItemService(session)
            rows = service.items.by_ids(ids)
            if not rows:
                return 0
            tags = service.tags.ensure_many(wanted, user_id=rows[0].user_id)
            targets = [row for row in rows if not all(tag in row.tags for tag in tags)]
            if targets:
                service.items.bulk_add_tags(targets, tags)
                changed = len(targets)
        if changed:
            self.notify_changed()
        return changed

    def untag_items(self, item_ids: Iterable[int], names: Iterable[str]) -> int:
        wanted = _clean_names(names)
        ids = _clean_ids(item_ids)
        if not wanted or not ids:
            return 0
        changed = 0
        with session_scope() as session:
            service = ItemService(session)
            rows = service.items.by_ids(ids)
            if not rows:
                return 0
            user_id = rows[0].user_id
            tags = [
                tag
                for tag in (service.tags.by_name(name, user_id=user_id) for name in wanted)
                if tag is not None
            ]
            targets = [row for row in rows if any(tag in row.tags for tag in tags)]
            if tags and targets:
                service.items.bulk_remove_tags(targets, tags)
                changed = len(targets)
        if changed:
            self.notify_changed()
        return changed

    def add_keywords(self, item_ids: Iterable[int], words: Iterable[str]) -> int:
        wanted = _clean_names(words)
        ids = _clean_ids(item_ids)
        if not wanted or not ids:
            return 0
        with session_scope() as session:
            service = ItemService(session)
            changed = service.add_keywords(service.items.by_ids(ids), wanted)
        if changed:
            self.notify_changed()
        return changed

    def remove_keywords(self, item_ids: Iterable[int], words: Iterable[str]) -> int:
        wanted = _clean_names(words)
        ids = _clean_ids(item_ids)
        if not wanted or not ids:
            return 0
        with session_scope() as session:
            service = ItemService(session)
            changed = service.remove_keywords(service.items.by_ids(ids), wanted)
        if changed:
            self.notify_changed()
        return changed

    # ------------------------------------------------------------------ 正文
    def read_text(self, item_id: int, limit: int = 4096) -> tuple[str, str, bool]:
        wanted = _clean_ids([item_id])
        if not wanted:
            return "", "", False
        size = max(1, int(limit or 4096))
        with new_session() as session:
            service = ItemService(session)
            rows = service.items.by_ids(wanted)
            if not rows:
                return "", "", False
            item = rows[0]
            content = str(item.content or "")
            if content:
                return content[:size], "utf-8", len(content) > size
            path = service.file_path_of(item)
        if path is None:
            return "", "", False
        return read_file_text(path, size)

    def notify_changed(self) -> None:
        from ..core.signals import signalBus

        signalBus.itemsChanged.emit()

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _datatype(name: str) -> DataType | None:
        """把插件给的类型名转成枚举；空串表示不筛选。"""
        text = str(name or "").strip().upper()
        if not text:
            return None
        try:
            return DataType(text)
        except ValueError:
            return None

    @staticmethod
    def _ref(service: ItemService, item: DataItem, preview_limit: int) -> items_api.ItemRef:
        preview = ItemsApi._preview(service, item, preview_limit) if preview_limit > 0 else ""
        return to_ref(item, preview)

    @staticmethod
    def _preview(service: ItemService, item: DataItem, limit: int) -> str:
        """正文预览：文本条目用数据库里的内容，其余读磁盘文件头部。"""
        content = str(item.content or "")
        if content:
            return content[:limit]
        path = service.file_path_of(item)
        if path is None:
            return ""
        text, _encoding, _truncated = read_file_text(path, limit)
        return text
