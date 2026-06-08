import os

from PyQt6.QtCore import QLocale

from enum import Enum
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator, OptionsValidator,
                            RangeConfigItem, RangeValidator, FolderValidator, ConfigSerializer)

from .util import (is_win11)

YEAR = 2026
AUTHOR = "taishoubuzhi"
VERSION = "v0.0.0"

LOG_LEVELS = ["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
ENCODINGS = ["utf-8"]
ROTATE_MODES = ["None", "Time", "Count"]
ROTATE_INTERVAL_UNITS = ["second", "minute", "hour", "day"]

POOL_SIZE_RANGE = (0,20)
MAX_OVERFLOW_RANGE = (0,20)
POOL_RECYCLE_RANGE = (0, 86400)

class Language(Enum):
    CHINESE_SIMPLIFIED = QLocale(QLocale.Language.Chinese, QLocale.Country.China)
    ENGLISH = QLocale(QLocale.Language.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


class Config(QConfig):
    # Material
    blurRadius = RangeConfigItem("Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40))

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", is_win11(), BoolValidator())
    dpi_scale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)
    dynamic_config_display = ConfigItem("MainWindow", "DynamicConfigDisplay", False, BoolValidator())
    # software update
    check_update_at_start_up = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())

    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(LOG_LEVELS),
                                  restart=True)

    log_format = ConfigItem("Log", "Log-Format", "{time} [{level:<8}] : {message}")

    format_to_json = ConfigItem("Log", "Format-To-JSON", False,
                                BoolValidator(),
                                restart=True)

    json_format = ConfigItem("Log", "JSON-Format", "{time} | {level} | {message}")

    catch = ConfigItem("Log", "Catch", True,
                       BoolValidator(),
                       restart=True)

    output_console = ConfigItem("Log", "Output-Console", True,
                                BoolValidator(),
                                restart=True)

    enqueue = ConfigItem("Log", "Enqueue", True,
                         BoolValidator(),
                         restart=True)

    encoding = OptionsConfigItem("Log", "Encoding", "utf-8",
                                 OptionsValidator(ENCODINGS),
                                 restart=True)

    backtrace = ConfigItem("Log", "Backtrace", True,
                           BoolValidator(),
                           restart=True)

    diagnose = ConfigItem("Log", "Diagnose", True,
                          BoolValidator(),
                          restart=True)

    output_file = ConfigItem("File", "Output-File", True,
                             BoolValidator(),
                             restart=True)

    file_path = ConfigItem("Log-File", "Log-Path",
                           os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "logs")),
                           FolderValidator(),
                           restart=True)

    # 当前使用的轮转模式，默认为 "None"（不轮转）
    rotate_mode = OptionsConfigItem("Log-File", "Rotate-Mode", "None",
                                    OptionsValidator(ROTATE_MODES),
                                    restart=True)

    rotate_interval = RangeConfigItem("Log-File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)

    rotate_interval_unit = OptionsConfigItem("Log-File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(ROTATE_INTERVAL_UNITS),
                                             restart=True)

    rotate_count = RangeConfigItem("Log-File", "Rotate-Count", 10,
                                   RangeValidator(1, 100),
                                   restart=True)

    url = ConfigItem("DB", "URL",
                     "sqlite:///" + os.path.normpath(os.path.join(
                         os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                         "resources", "data.db")).replace("\\", "/"))
    echo = ConfigItem("DB", "Echo", True, BoolValidator(), restart=True)
    pool_size = RangeConfigItem("DB", "Pool-Size", 10, RangeValidator(*POOL_SIZE_RANGE))
    max_overflow = RangeConfigItem("DB", "Max-Overflow", 20,
                                   RangeValidator(*MAX_OVERFLOW_RANGE))
    pool_recycle = RangeConfigItem("DB", "Pool-Recycle", 3600,
                                   RangeValidator(*POOL_RECYCLE_RANGE))
    pool_pre_ping = ConfigItem("DB", "Pool-Pre-Ping", True, BoolValidator(), restart=True)
    connect_args = ConfigItem("DB", "Connect-Args", {})
    data_store_path = ConfigItem("Data", "Data-Store-Path",
                                  os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "resources", "datas")),
                                  FolderValidator())


def load_config():
    global config
    qconfig.load("app/config/config.json", config)


config = Config()
load_config()
