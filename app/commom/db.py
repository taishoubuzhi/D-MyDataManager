from loguru import logger
from init import (get_db_dbsession)


def create_session():
    db_session = get_db_dbsession()
    session = db_session()
    if session is None:
        logger.error("session create fail")
        return None
    logger.success("session create success")
    return session
