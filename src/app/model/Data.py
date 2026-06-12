from enum import Enum as PyEnum

from .Base import Base
from sqlalchemy import (Column, String, Integer, Enum, JSON, ForeignKey,
                         Boolean, DateTime)
from sqlalchemy import func


class DataType(PyEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOC = "doc"
    DOCX = "docx"
    EXCEL = "excel"
    PPT = "ppt"
    TEXT = "text"
    UNKNOWN = "unknown"


# DataType 元信息注册表（唯一标准，新增类型只需在此添加一行）
# 格式：(DataType枚举, 中文名, 是否文件资源类型)
DATA_TYPE_INFO = [
    (DataType.IMAGE,   "图片", True),
    (DataType.VIDEO,   "视频", True),
    (DataType.AUDIO,   "音频", True),
    (DataType.DOC,     "doc文档", True),
    (DataType.DOCX,    "docx文档", True),
    (DataType.EXCEL,   "excel表格", True),
    (DataType.PPT,     "ppt演示", True),
    (DataType.TEXT,    "文本", False),
    (DataType.UNKNOWN, "其他", False),
]

# 从注册表自动构建的查找字典
_DATA_TYPE_NAMES = {row[0]: row[1] for row in DATA_TYPE_INFO}
_FILE_TYPES = {row[0] for row in DATA_TYPE_INFO if row[2]}


def _normalize_type(data_type):
    """将 SQLAlchemy 可能返回的字符串统一转为 DataType 枚举"""
    if data_type is None:
        return None
    if isinstance(data_type, DataType):
        return data_type
    try:
        return DataType(data_type)
    except ValueError:
        return None


def get_type_name(data_type):
    """获取数据类型的中文名"""
    data_type = _normalize_type(data_type)
    if data_type is None:
        return "未知"
    return _DATA_TYPE_NAMES.get(data_type, "未知")


def is_file_type(data_type):
    """是否为文件资源类型"""
    data_type = _normalize_type(data_type)
    if data_type is None:
        return False
    return data_type in _FILE_TYPES


def format_size(size_bytes):
    """将字节数格式化为可读的大小字符串"""
    if size_bytes is None:
        return "未知"
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    size = float(size_bytes)
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


# 为 DataType 添加类方法（保持向后兼容）
DataType.get_name = classmethod(lambda cls, dt: get_type_name(dt))
DataType.is_file_type = classmethod(lambda cls, dt: is_file_type(dt))


class Data(Base):
    __tablename__ = 'datas'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(20))
    type = Column(Enum(DataType))
    keywords = Column(JSON)
    tag = Column(JSON)
    size = Column(Integer, default=0)
    is_hidden = Column(Boolean)
    content = Column(String(256))
    import_time = Column(DateTime, default=func.now())
    update_time = Column(DateTime, default=func.now(), onupdate=func.now())
    cover_path = Column(String(256))

    user_id = Column(Integer, ForeignKey('users.id'))
    database_id = Column(Integer, ForeignKey('databases.id'))
