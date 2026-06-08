from .Base import Base
from sqlalchemy import (Column, String, Integer, JSON, Boolean, ForeignKey)


class DataBase(Base):
    __tablename__ = 'databases'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(20))
    description = Column(String(256))
    keywords = Column(JSON)
    is_hidden = Column(Boolean)

    user_id = Column(Integer, ForeignKey('users.id'))
