from loguru import logger
from .init import (DBSession)


def create_session():
    session = DBSession()
    if session is None:
        logger.error("session create fail")
        return None
    logger.success("session create success")
    return session
