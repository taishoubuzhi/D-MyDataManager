from loguru import logger
from .init import get_session


def create_session():
    session_factory = get_session()
    session = session_factory()
    if session is None:
        logger.error("session create fail")
        return None
    logger.success("session create success")
    return session
