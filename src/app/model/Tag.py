from .Base import Base

from sqlalchemy import (Column, String, Integer)

class Tag(Base):
    __tablename__ = 'tags'

    id = Column(String(20), primary_key=True)
    name = Column(String(20))
    desc = Column(String(20))