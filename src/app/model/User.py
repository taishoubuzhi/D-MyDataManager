import Base

from sqlalchemy import (Column, String, Boolean)


class User(Base):
    __tablename__ = 'users'

    id = Column(String(20), primary_key=True)
    name = Column(String(20), )
    password = Column(String(20))
    is_hidden = Column(Boolean)