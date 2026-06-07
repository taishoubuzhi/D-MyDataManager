from loguru import logger
from .init import get_session


def create_session():
    session_factory = get_session()
    session = session_factory()
    if session is None:
        logger.error("Session create failed")
        return None
    logger.success("Session created successfully")
    return session
