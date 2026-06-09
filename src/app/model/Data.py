from enum import Enum as PyEnum

from .Base import Base
from sqlalchemy import (Column, String, Integer, Enum, JSON, ForeignKey, Boolean)


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
    size = Column(Integer)
    is_hidden = Column(Boolean)
    content = Column(String(256))

    user_id = Column(Integer, ForeignKey('users.id'))
    database_id = Column(Integer, ForeignKey('databases.id'))
