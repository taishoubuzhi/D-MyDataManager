import Base

from sqlalchemy import (Column, String)

class User(Base):
    __tablename__ = 'user'

    id = Column(String(20), primary_key=True)
    name = Column(String(20),)
    password = Column(String(20))