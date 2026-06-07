from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loguru import logger
from ..config import config


engine = None
DBSession = None


def get_engine():
    global engine
    if engine is not None:
        return engine
    engine = create_engine(config.get(config.url),
                           echo=config.get(config.echo),
                           pool_size=config.get(config.pool_size),
                           max_overflow=config.get(config.max_overflow),
                           pool_recycle=config.get(config.pool_recycle),
                           pool_pre_ping=config.get(config.pool_pre_ping),
                           connect_args=config.get(config.connect_args))
    logger.info("数据库引擎创建成功")
    return engine


def get_session():
    global DBSession
    if DBSession is not None:
        return DBSession
    DBSession = sessionmaker(bind=get_engine())
    logger.info("数据库会话工厂创建成功")
    return DBSession


def init_db():
    get_engine()
    get_session()
    logger.debug(f"数据库URL: {config.get(config.url)}")
