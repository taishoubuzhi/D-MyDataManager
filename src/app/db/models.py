"""ORM 模型定义。

设计约定：
- 主键统一 `id`，时间统一 `created_at` / `updated_at`（本地时间）。
- 标签使用关联表（多对多），关键词保留为 JSON 列表，二者语义不同。
- 文件内容存放在内容寻址仓库（`Blob`），`DataItem.file_path` 只是相对引用。
- 存档（`Archive` / `ArchiveEntry`）记录某一时刻的库快照，用于回溯。
"""

from __future__ import annotations

import datetime as dt
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    text,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> dt.datetime:
    return dt.datetime.now()


class Base(DeclarativeBase):
    pass


class DataType(str, PyEnum):
    """数据类型（继承 str 便于直接写库与比较）。"""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    DOCUMENT = "DOCUMENT"
    SPREADSHEET = "SPREADSHEET"
    PRESENTATION = "PRESENTATION"
    ARCHIVE = "ARCHIVE"
    CODE = "CODE"
    TEXT = "TEXT"
    OTHER = "OTHER"


DATA_TYPE_NAMES: dict[DataType, str] = {
    DataType.IMAGE: "图片",
    DataType.VIDEO: "视频",
    DataType.AUDIO: "音频",
    DataType.DOCUMENT: "文档",
    DataType.SPREADSHEET: "表格",
    DataType.PRESENTATION: "演示",
    DataType.ARCHIVE: "压缩包",
    DataType.CODE: "代码",
    DataType.TEXT: "文本",
    DataType.OTHER: "其他",
}

# 需要保留原始文件的类型（TEXT 允许纯文本内容）
FILE_TYPES: frozenset[DataType] = frozenset(
    t for t in DataType if t is not DataType.TEXT
)


EXTENSION_TYPES: dict[str, DataType] = {}
for _ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tif", ".tiff", ".heic"):
    EXTENSION_TYPES[_ext] = DataType.IMAGE
for _ext in (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"):
    EXTENSION_TYPES[_ext] = DataType.VIDEO
for _ext in (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".ape"):
    EXTENSION_TYPES[_ext] = DataType.AUDIO
for _ext in (".pdf", ".doc", ".docx", ".odt", ".rtf", ".epub", ".mobi", ".md"):
    EXTENSION_TYPES[_ext] = DataType.DOCUMENT
for _ext in (".xls", ".xlsx", ".csv", ".ods", ".tsv"):
    EXTENSION_TYPES[_ext] = DataType.SPREADSHEET
for _ext in (".ppt", ".pptx", ".odp"):
    EXTENSION_TYPES[_ext] = DataType.PRESENTATION
for _ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"):
    EXTENSION_TYPES[_ext] = DataType.ARCHIVE
for _ext in (".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".sql", ".json", ".xml", ".yaml", ".yml", ".toml", ".html", ".css"):
    EXTENSION_TYPES[_ext] = DataType.CODE
for _ext in (".txt", ".log"):
    EXTENSION_TYPES[_ext] = DataType.TEXT


def guess_type(name: str) -> DataType:
    import os.path

    return EXTENSION_TYPES.get(os.path.splitext(name)[1].lower(), DataType.OTHER)


item_tags = Table(
    "item_tags",
    Base.metadata,
    Column("item_id", ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Library(Base):
    """库文件夹：数据文件在磁盘上的存放根目录。

    可以登记多个库，其中一个为默认库；导入时可选择放入任意库，
    默认库同时作为默认选项与备份目录。
    """

    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_scan_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["DataItem"]] = relationship(back_populates="library")


class User(Base):
    """用户：数据与分类相互隔离。

    is_default 标记默认用户（管理员）：只有它可以管理其他用户，且不可删除。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    categories: Mapped[list["Category"]] = relationship(back_populates="user")
    items: Mapped[list["DataItem"]] = relationship(back_populates="user")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    icon: Mapped[str] = mapped_column(String(64), default="")
    color: Mapped[str] = mapped_column(String(16), default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    user: Mapped["User | None"] = relationship(back_populates="categories")
    parent: Mapped["Category | None"] = relationship(back_populates="children", remote_side="Category.id")
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", order_by="Category.sort_order",
    )
    items: Mapped[list["DataItem"]] = relationship(back_populates="category")

    __table_args__ = (UniqueConstraint("parent_id", "name", name="uq_category_parent_name"),)


class Tag(Base):
    """标签：`is_global` 为真时对所有用户可见，否则只属于 `user_id`；`created_by` 记录创建者。"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(256), default="")
    color: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    items: Mapped[list["DataItem"]] = relationship(secondary=item_tags, back_populates="tags")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_tag_user_name"),
        Index("uq_tag_global_name", "name", unique=True, sqlite_where=text("is_global = 1")),
    )


class Blob(Base):
    """内容寻址的文件实体：同一份内容只保存一次。"""

    __tablename__ = "blobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    rel_path: Mapped[str] = mapped_column(String(512), default="")
    ref_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    versions: Mapped[list["Version"]] = relationship(back_populates="blob")


class DataItem(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[DataType] = mapped_column(default=DataType.OTHER, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)

    file_path: Mapped[str] = mapped_column(String(1024), default="")
    source_path: Mapped[str] = mapped_column(String(1024), default="")
    cover_path: Mapped[str] = mapped_column(String(512), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")
    checksum: Mapped[str] = mapped_column(String(64), default="", index=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    library_id: Mapped[int | None] = mapped_column(
        ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    category: Mapped["Category | None"] = relationship(back_populates="items")
    user: Mapped["User | None"] = relationship(back_populates="items")
    library: Mapped["Library | None"] = relationship(back_populates="items")
    tags: Mapped[list[Tag]] = relationship(secondary=item_tags, back_populates="items", lazy="selectin")
    versions: Mapped[list["Version"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="Version.created_at.desc()",
    )
    features: Mapped[list["Feature"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (Index("ix_items_type_deleted", "type", "is_deleted"),)

    @property
    def type_name(self) -> str:
        return DATA_TYPE_NAMES.get(self.type, "其他")

    @property
    def tag_names(self) -> list[str]:
        return [tag.name for tag in self.tags]


class Version(Base):
    """数据项的内容版本，指向内容寻址仓库中的某个 blob。"""

    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(256), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["DataItem"] = relationship(back_populates="versions")
    blob: Mapped["Blob | None"] = relationship(back_populates="versions")


class Feature(Base):
    """数据特征：哈希、封面、尺寸、文本统计等可检索属性。"""

    __tablename__ = "features"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["DataItem"] = relationship(back_populates="features")


class Archive(Base):
    """一次存档批次，记录当时的数据状态。

    `pinned` 为真表示已标记：自动清理（按数量 / 容量 / 时间）不会删除它，
    只能先取消标记，或由用户在存档页手动删除。
    """

    __tablename__ = "archives"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(String(512), default="")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    new_blobs: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    entries: Mapped[list["ArchiveEntry"]] = relationship(
        back_populates="archive", cascade="all, delete-orphan",
    )


class ArchiveEntry(Base):
    """存档中的一条数据记录。

    user_id / user_name 记录条目所属用户，还原时据此放回该用户的分类目录。
    """

    __tablename__ = "archive_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive_id: Mapped[int] = mapped_column(ForeignKey("archives.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_name: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(32), default="OTHER")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    category: Mapped[str] = mapped_column(String(128), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text, default="")

    archive: Mapped["Archive"] = relationship(back_populates="entries")


class AppMeta(Base):
    """键值表：schema 版本、隐私口令等全局状态。"""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
