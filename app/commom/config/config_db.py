from loguru import logger
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator, OptionsValidator,
                            RangeConfigItem, RangeValidator, FolderValidator, ConfigSerializer)


class Config(QConfig):
    url = ConfigItem("DB", "URL", "sqlite:///data.db")
    echo = ConfigItem("DB", "Echo", True, BoolValidator(), restart=True)
    max_pool_size = RangeConfigItem("DB", "Max-Pool-Size", 256)
    min_pool_size = RangeConfigItem("DB", "Min-Pool-Size", 0)
    pool_size = RangeConfigItem("DB", "Pool-Size", 10, RangeValidator(min_pool_size, max_pool_size), restart=True)
    max_max_overflow = RangeConfigItem("DB", "Max-Max-Overflow", 256)
    min_max_overflow = RangeConfigItem("DB", "Min-Max-Overflow", 0)
    max_overflow = RangeConfigItem("DB", "Max-Overflow", 20, RangeValidator(min_max_overflow, max_max_overflow),
                                   restart=True)
    max_pool_recycle = ConfigItem("DB", "Pool-Recycle", 86400)
    min_pool_recycle = RangeConfigItem("DB", "Min-Pool-Recycle", 0)
    pool_recycle = RangeConfigItem("DB", "Pool-Recycle", 3600, RangeValidator(min_pool_recycle, max_pool_recycle),
                                   restart=True)
    pool_pre_ping = ConfigItem("DB", "Pool-Pre-Ping", True, BoolValidator(), restart=True)
    connect_args = ConfigItem("DB", "Connect-Args", {})


def load_db_config():
    global db_config
    if db_config is not None:
        logger.success("db_config is not None")
        return db_config
    db_config = Config()
    qconfig.load("app/config/config-db.json5", db_config)
    if db_config == {}:
        logger.error("load config fail")
        return None
    logger.success("load config success")
    return db_config


def get_db_config():
    if db_config is None:
        logger.error("db_config is None")
    return db_config


db_config = None
