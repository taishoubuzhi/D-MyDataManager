"""用户仓储。"""

from __future__ import annotations

from sqlalchemy import select

from ..db.models import User
from .base import Repository

DEFAULT_USER_NAME = "默认用户"


class UserRepository(Repository[User]):
    model = User

    def by_name(self, name: str) -> User | None:
        return self.session.scalars(select(User).where(User.name == name)).first()

    def list_all(self, include_hidden: bool = True) -> list[User]:
        stmt = select(User).order_by(User.id)
        if not include_hidden:
            stmt = stmt.where(User.is_hidden.is_(False))
        return list(self.session.scalars(stmt))

    def create(
        self,
        name: str,
        password_hash: str = "",
        is_hidden: bool = False,
        is_default: bool = False,
    ) -> User:
        return self.add(
            User(
                name=name,
                password_hash=password_hash,
                is_hidden=is_hidden,
                is_default=is_default,
            )
        )

    def default(self) -> User | None:
        """默认用户（管理员）。"""
        stmt = select(User).where(User.is_default.is_(True)).order_by(User.id)
        return self.session.scalars(stmt).first()

    def ensure_default(self) -> User:
        user = self.default() or self.by_name(DEFAULT_USER_NAME)
        return user if user is not None else self.create(DEFAULT_USER_NAME, is_default=True)
