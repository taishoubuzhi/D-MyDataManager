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


class Data(Base):
    __tablename__ = 'datas'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(20))
    type = Column(Enum(DataType))
    keywords = Column(JSON)
    tag = Column(JSON)
    size = Column(Integer)
    is_hidden = Column(Boolean)
    url = Column(String(256))

    user_id = Column(Integer, ForeignKey('users.id'))
    database_id = Column(Integer, ForeignKey('databases.id'))
