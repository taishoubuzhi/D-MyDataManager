from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import (db_config)


def _init_engine(db_config_item):
    global engine

    if engine is not None:
        return engine
    engine = create_engine(db_config_item.get(db_config_item.url),
                           echo=db_config_item.get(db_config.echo),
                           pool_size=db_config_item.get(db_config.pool_size),
                           max_overflow=db_config_item.get(db_config.max_overflow),
                           pool_recycle=db_config_item.get(db_config.pool_recycle),
                           pool_pre_ping=db_config_item.get(db_config.pool_pre_ping),
                           connect_args=db_config_item.get(db_config.connect_args))
    if engine is None:
        return None
    return engine


def _init_dbsession():
    global DBSession
    if DBSession is not None:
        return DBSession
    DBSession = sessionmaker(bind=engine)
    if DBSession is None:
        return None
    return DBSession


engine = _init_engine(db_config)
DBSession = _init_dbsession()
