from loguru import logger
from app.model.Data import Data, DataType
from app.model.DataBase import DataBase
from app.model.Tag import Tag
from app.model.User import User


DEFAULT_TAGS = [
    Tag(name="图片", desc="图片类型标签"),
    Tag(name="视频", desc="视频类型标签"),
    Tag(name="音频", desc="音频类型标签"),
    Tag(name="文档", desc="文档类型标签"),
]


def db_exists(session) -> bool:
    """检测数据库是否已存在数据

    通过检查核心表是否有数据来判断数据库是否已初始化。
    只要 User 表有数据，即认为数据库已存在。
    """
    try:
        has_data = session.query(User).count() > 0
        if has_data:
            logger.debug("Database already has data, skip initialization")
            return True
        logger.debug("Database is empty, initialization required")
        return False
    except Exception:
        logger.debug("Database tables not accessible, initialization required")
        return False


def init_data(session_factory):
    """清空所有表，注入默认数据（由 init_db 统一判断是否需要调用）"""
    session = session_factory()
    try:
        _clear_all_data(session)
        _insert_default_data(session)
        session.commit()
        logger.info("Database initialized with default data")
    except Exception:
        session.rollback()
        logger.exception("Database initialization failed")
        raise
    finally:
        if session.is_active:
            session.close()


def _clear_all_data(session):
    """按外键依赖顺序清空所有表"""
    session.query(Data).delete()
    session.query(DataBase).delete()
    session.query(Tag).delete()
    session.query(User).delete()
    logger.debug("All data cleared")


def _insert_default_data(session):
    """注入默认数据，按外键依赖顺序插入"""
    default_user = User(name="默认用户", password="", is_hidden=False)
    session.add(default_user)
    session.flush()  # 获取自增 id

    default_database = DataBase(name="默认数据库", description="系统默认数据库", keywords=[], is_hidden=False, user_id=default_user.id)
    session.add(default_database)
    default_data = Data(name="示例文本", type=DataType.TEXT, keywords=["示例"], tag=["文档"], size=0, is_hidden=False, content="这是一个示例文本", user_id=default_user.id, database_id=default_database.id)
    session.add(default_data)
    session.add_all(DEFAULT_TAGS)
    logger.debug("Default data inserted")
