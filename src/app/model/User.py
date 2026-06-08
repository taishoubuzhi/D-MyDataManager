from .Base import Base

from sqlalchemy import (Column, String, Integer, Boolean)


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(20))
    password = Column(String(20))
    is_hidden = Column(Boolean)