from .Base import Base
from sqlalchemy import (Column, String, Integer, JSON, Boolean, ForeignKey)


class DataBase(Base):
    __tablename__ = 'databases'

    id = Column(String(20), primary_key=True)
    name = Column(String(20))
    description = Column(String(256))
    keywords = Column(JSON)
    is_hidden = Column(Boolean)

    user_id = Column(String(20), ForeignKey('users.id'))
