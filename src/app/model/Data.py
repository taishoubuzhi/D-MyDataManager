import Base
from sqlalchemy import (Column, String, Int, Enum, ARRAY)


class DataType(Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    OTHER = "other"


class Data(Base):
    __tablename__ = 'data'

    id = Column(String(20), primary_key=True)
    name = Column(String(20))
    type = Column(Enum(DataType))
    keywords = Column(ARRAY(String(10)))
    tag = Column(ARRAY(String(20)))
    size = Column(Int)
