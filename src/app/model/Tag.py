import Base

from sqlalchemy import (Column, String, Int)

class Tag(Base):
    __tablename__ = 'tags'

    id = Column(String(20), primary_key=True)
    name = Column(String(20))
    desc = Column(String(20))