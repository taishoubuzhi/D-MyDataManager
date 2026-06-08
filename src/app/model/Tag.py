from .Base import Base

from sqlalchemy import (Column, String, Integer)


class Tag(Base):
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(20))
    desc = Column(String(20))