"""首次运行时写入的默认数据。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import library_root
from .models import Category, Library, Tag, User

DEFAULT_USER = "默认用户"
DEFAULT_CATEGORIES = ("学习资料", "工作文档", "图片素材", "影音资料")
DEFAULT_TAGS = ("重要", "待整理", "收藏")
DEFAULT_LIBRARY_NAME = "默认库"


def is_initialized(session: Session) -> bool:
    return bool(session.scalar(select(func.count(User.id))))


def seed_user_defaults(session: Session, user: User) -> None:
    """为新用户写入默认分类与标签（每个用户拥有自己的分类与标签）。"""
    session.add_all(
        [
            Category(name=name, user_id=user.id, sort_order=index)
            for index, name in enumerate(DEFAULT_CATEGORIES)
        ]
    )
    session.add_all(
        [Tag(name=name, user_id=user.id, created_by=user.id) for name in DEFAULT_TAGS]
    )
    session.flush()


def seed(session: Session) -> bool:
    """库为空时写入默认用户、分类与标签；返回是否执行了写入。"""
    if is_initialized(session):
        return False

    user = User(name=DEFAULT_USER, is_default=True)
    session.add(user)
    session.flush()
    seed_user_defaults(session, user)

    root = library_root()
    for directory in paths.global_subdirs(root):
        directory.mkdir(parents=True, exist_ok=True)
    session.add(
        Library(
            name=DEFAULT_LIBRARY_NAME,
            path=str(root),
            description="唯一的库文件夹，用于存放用户数据与全局资源",
            is_default=True,
        )
    )
    session.flush()
    logger.info(
        "已写入默认数据：用户、{} 个分类、{} 个标签、库文件夹 {}",
        len(DEFAULT_CATEGORIES),
        len(DEFAULT_TAGS),
        root,
    )
    return True
