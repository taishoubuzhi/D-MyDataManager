from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from loguru import logger


def _init_engine():
    global engine
    if engine is not None:
        logger.success("engine is not None")
        return engine
    engine = create_engine('sqlite:///example.db')
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


def init():
    logger.info("init database")
    logger.info("init database engine")
    if _init_engine() is None:
        logger.error("init engine fail")
        return None
    logger.info("init DBSession")
    if _init_dbsession() is None:
        logger.error("init DBSession fail")
        return None
    logger.info("init database success")
    return None

def get_engine():
    if engine is None:
        logger.error("engine is None")
    return engine

def get_dbsession():
    if DBSession is None:
        logger.error("DBSession is None")
    return DBSession

engine = None
DBSession = None
