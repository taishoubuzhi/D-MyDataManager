"""库文件夹仓储。"""

from __future__ import annotations

from sqlalchemy import select

from ..db.models import DataItem, Library
from .base import Repository


class LibraryRepository(Repository[Library]):
    model = Library

    def all(self) -> list[Library]:  # type: ignore[override]
        return list(
            self.session.scalars(
                select(Library).order_by(Library.is_default.desc(), Library.id)
            ).all()
        )

    def default(self) -> Library | None:
        return self.session.scalars(
            select(Library).where(Library.is_default.is_(True)).limit(1)
        ).first()

    def by_path(self, path: str) -> Library | None:
        return self.session.scalars(select(Library).where(Library.path == path)).first()

    def by_name(self, name: str) -> Library | None:
        return self.session.scalars(select(Library).where(Library.name == name)).first()

    def create(
        self,
        name: str,
        path: str,
        *,
        description: str = "",
        is_default: bool = False,
    ) -> Library:
        library = Library(name=name, path=path, description=description, is_default=is_default)
        self.session.add(library)
        self.session.flush()
        return library

    def set_default(self, library: Library) -> None:
        for other in self.all():
            other.is_default = other.id == library.id
        self.session.flush()

    def item_count(self, library: Library) -> int:
        return (
            self.session.query(DataItem)
            .filter(DataItem.library_id == library.id, DataItem.is_deleted.is_(False))
            .count()
        )
