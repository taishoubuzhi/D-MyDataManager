"""仓储基类：把常用的 Session 操作收敛到一处。"""

from __future__ import annotations

from typing import Generic, Sequence, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Repository(Generic[T]):
    model: type

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, obj_id: int) -> T | None:
        return self.session.get(self.model, obj_id)

    def all(self) -> Sequence[T]:
        return self.session.scalars(select(self.model)).all()

    def add(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete(self, obj: T) -> None:
        self.session.delete(obj)
        self.session.flush()

    def count(self) -> int:
        return self.session.query(self.model).count()

    def commit(self) -> None:
        self.session.commit()
