from qfluentwidgets import (qconfig, QConfig, ConfigItem, BoolValidator, RangeConfigItem, RangeValidator)
import config


class Config(QConfig):
    url = ConfigItem("DB", "URL", "sqlite:///data.db")
    echo = ConfigItem("DB", "Echo", True, BoolValidator(), restart=True)
    max_pool_size = RangeConfigItem("DB", "Max-Pool-Size", 256)
    min_pool_size = RangeConfigItem("DB", "Min-Pool-Size", 0)
    pool_size = RangeConfigItem("DB", "Pool-Size", 10, RangeValidator(min_pool_size.value, max_pool_size.value), restart=True)
    max_max_overflow = RangeConfigItem("DB", "Max-Max-Overflow", 256)
    min_max_overflow = RangeConfigItem("DB", "Min-Max-Overflow", 0)
    max_overflow = RangeConfigItem("DB", "Max-Overflow", 20, RangeValidator(min_max_overflow.value, max_max_overflow.value),
                                   restart=True)
    max_pool_recycle = ConfigItem("DB", "Pool-Recycle", 86400)
    min_pool_recycle = RangeConfigItem("DB", "Min-Pool-Recycle", 0)
    pool_recycle = RangeConfigItem("DB", "Pool-Recycle", 3600, RangeValidator(min_pool_recycle.value, max_pool_recycle.value),
                                   restart=True)
    pool_pre_ping = ConfigItem("DB", "Pool-Pre-Ping", True, BoolValidator(), restart=True)
    connect_args = ConfigItem("DB", "Connect-Args", {})


def load_db_config():
    global db_config
    return config.load(db_config, "app/config/config-db.json5", Config)


db_config = load_db_config()
