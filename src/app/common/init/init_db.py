import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loguru import logger
from ..config import config
from app.model.Base import Base
from app.model.Data import Data
from app.model.DataBase import DataBase
from app.model.Tag import Tag
from app.model.User import User
from .init_data_script import init_data, db_exists


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
    logger.info("Database engine created")
    return engine


def get_session():
    global DBSession
    if DBSession is not None:
        return DBSession
    DBSession = sessionmaker(bind=get_engine())
    logger.info("Database session factory created")
    return DBSession


def init_db(force=False):
    """初始化数据库

    Args:
        force: 是否强制初始化。为 True 时，无论数据库是否存在数据都会重新初始化。
    """
    # 确保数据库目录存在
    db_url = config.get(config.url)
    if db_url.startswith("sqlite:///"):
        db_dir = os.path.dirname(db_url.replace("sqlite:///", ""))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Database directory ensured: {db_dir}")

    get_engine()
    session_factory = get_session()

    if force:
        # 强制初始化：删除旧表再重建，然后注入默认数据
        Base.metadata.drop_all(get_engine())
        logger.info("Force initialization: old tables dropped")
        Base.metadata.create_all(get_engine())
        init_data(session_factory)
        logger.info("Force initialization completed")
    else:
        # 检测初始化：仅当数据库无数据时才初始化
        Base.metadata.create_all(get_engine())
        session = session_factory()
        should_init = not db_exists(session)
        session.close()

        if should_init:
            init_data(session_factory)
            logger.info("Database initialized with default data")

    logger.debug(f"Database URL: {config.get(config.url)}")
