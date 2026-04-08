from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from loguru import logger

from app.commom.config import (load_db_config, get_db_config)


def _init_engine(db_config):
    global engine

    if engine is not None:
        logger.success("engine is not None")
        return engine
    engine = create_engine(db_config.url.value,
                           echo=db_config.echo.value,
                           pool_size=db_config.pool_size.value,
                           max_overflow=db_config.max_overflow.value,
                           pool_recycle=db_config.pool_recycle.value,
                           pool_pre_ping=db_config.pool_pre_ping.value,
                           connect_args=db_config.connect_args.value)
    if engine is None:
        logger.error("engine create fail")
        return None
    logger.success("engine create success")
    return engine


def _init_dbsession():
    global DBSession
    if DBSession is not None:
        logger.success("DBSession is not None")
        return DBSession
    DBSession = sessionmaker(bind=engine)
    if DBSession is None:
        logger.error("DBSession create fail")
        return None
    logger.success("DBSession create success")
    return DBSession


def init_db():
    if load_db_config() is None:
        return None
    db_config = get_db_config()
    logger.info("init database")
    logger.info("init database engine")
    if _init_engine(db_config) is None:
        logger.error("init engine fail")
        return None
    logger.info("init DBSession")
    if _init_dbsession() is None:
        logger.error("init DBSession fail")
        return None
    logger.info("init database success")
    return None


def get_db_engine():
    if engine is None:
        logger.error("engine is None")
    return engine


def get_db_dbsession():
    if DBSession is None:
        logger.error("DBSession is None")
    return DBSession


engine = None
DBSession = None
