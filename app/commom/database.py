from loguru import logger
from init.init_database import (get_dbsession)


def create_session():
    DBSession = get_dbsession()
    if DBSession is None:
        return None
    session = DBSession()
    if session is None:
        logger.error("session create fail")
        return None
    logger.success("session create success")
    return session
