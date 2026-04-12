import Base
from sqlalchemy import (Column, String, Int, Enum, ARRAY, ForeignKey)


class DataType(Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOC = "doc"
    DOCX = "docx"
    EXCEL = "excel"
    PPT = "PPT"
    UNKNOWN = "unknown"


class Data(Base):
    __tablename__ = 'datas'

    id = Column(String(20), primary_key=True)
    name = Column(String(20))
    type = Column(Enum(DataType))
    keywords = Column(ARRAY(String(10)))
    tag = Column(ARRAY(String(20)))
    size = Column(Int)

    user_id = Column(String(20), ForeignKey('users.id'))
