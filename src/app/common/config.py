import datetime

from PyQt6.QtCore import QLocale

from enum import Enum
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator, OptionsValidator,
                            RangeConfigItem, RangeValidator, FolderValidator, ConfigSerializer)

from .util import (is_win11)

YEAR = 2026
AUTHOR = "taishoubuzhi"
VERSION = "v0.0.0"

LOG_LEVELS = ["Trace", "Debug", "Info", "Success", "Warn", "Error", "Critical"]
ENCODINGS = ["utf-8"]
ROTATE_MODES = ["None", "Size", "Time", "Interval"]
ROTATE_SIZE_UNITS = ["B", "KB", "MB", "GB"]
ROTATE_INTERVAL_UNITS = ["second", "minute", "hour", "day"]
RETENTION_UNITS = ["seconds", "minutes", "hours", "days"]
COMPRESS_MODES = ["None", "zip"]

POOL_SIZE_RANGE = (0,20)
MAX_OVERFLOW_RANGE = (0,20)
POOL_RECYCLE_RANGE = (0, 86400)

class Language(Enum):
    CHINESE_SIMPLIFIED = QLocale(QLocale.Language.Chinese, QLocale.Country.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Language.Chinese, QLocale.Country.HongKong)
    ENGLISH = QLocale(QLocale.Language.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


class FileTimeSerializer(ConfigSerializer):

    def serialize(self, value: datetime.time):
        return value.strftime("%H:%M")

    def deserialize(self, value: str):
        try:
            return datetime.datetime.strptime(value, "%H:%M").time()
        except ValueError:
            try:
                return datetime.datetime.strptime(value, "%HH:%MM").time()
            except ValueError:
                return datetime.time(0, 0)


class Config(QConfig):
    # Material
    blurRadius = RangeConfigItem("Material", "AcrylicBlurRadius", 15, RangeValidator(0, 40))

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", is_win11(), BoolValidator())
    dpi_scale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)
    # software update
    check_update_at_start_up = ConfigItem("Update", "CheckUpdateAtStartUp", True, BoolValidator())

    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(LOG_LEVELS),
                                  restart=True)

    log_format = ConfigItem("Log", "Log-Format", "{time} | {level} | {message}")

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

    different_level_file = ConfigItem("File", "Different-Level-Files", True,
                                      BoolValidator(),
                                      restart=True)

    file_path = ConfigItem("Log-File", "Log-Path", "logs",
                           FolderValidator(),
                           restart=True)

    trace_level_file = ConfigItem("Log-File", "File-Trace", "trace.log")  # Trace 等级日志文件名
    debug_level_file = ConfigItem("Log-File", "File-Debug", "debug.log")  # Debug 等级日志文件名
    info_level_file = ConfigItem("Log-File", "File-Info", "info.log")  # Info 等级日志文件名
    success_level_file = ConfigItem("Log-File", "File-Success", "success.log")  # Success 等级日志文件名
    warning_level_file = ConfigItem("Log-File", "File-Warn", "warn.log")  # Warning 等级日志文件名
    error_level_file = ConfigItem("Log-File", "File-Error", "error.log")  # Error 等级日志文件名
    critical_level_file = ConfigItem("Log-File", "File-Critical", "critical.log")  # Critical 等级日志文件名

    log_file = ConfigItem("Log-File", "File-Log", "log.log")

    file_override = ConfigItem("Log-File", "File-Overwrite", True,
                               BoolValidator(),
                               restart=True)

    # 当前使用的轮转模式，默认为 "None"（不轮转）
    rotate_mode = OptionsConfigItem("Log-File", "Rotate-Mode", "None",
                                    OptionsValidator(ROTATE_MODES),
                                    restart=True)

    rotate_size = RangeConfigItem("Log-File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)

    rotate_size_unit = OptionsConfigItem("Log-File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(ROTATE_SIZE_UNITS),
                                         restart=True)

    rotate_time = ConfigItem("Log-File", "Rotate-Time",
                             datetime.time(0, 0),
                             serializer=FileTimeSerializer(),
                             restart=True
                             )

    rotate_interval = RangeConfigItem("Log-File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)

    rotate_interval_unit = OptionsConfigItem("Log-File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(ROTATE_INTERVAL_UNITS),
                                             restart=True)

    retention = RangeConfigItem("Log-File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)

    retention_unit = OptionsConfigItem("Log-File", "Retention-Unit", "days",
                                       OptionsValidator(RETENTION_UNITS),
                                       restart=True)

    compress_mode = OptionsConfigItem("Log-File", "Compress-Mode", "None",
                                      OptionsValidator(COMPRESS_MODES),
                                      restart=True)

    url = ConfigItem("DB", "URL", "sqlite:///data.db")
    echo = ConfigItem("DB", "Echo", True, BoolValidator(), restart=True)
    pool_size = RangeConfigItem("DB", "Pool-Size", 10, RangeValidator(*POOL_SIZE_RANGE),
                                restart=True)
    max_overflow = RangeConfigItem("DB", "Max-Overflow", 20,
                                   RangeValidator(*MAX_OVERFLOW_RANGE),
                                   restart=True)
    pool_recycle = RangeConfigItem("DB", "Pool-Recycle", 3600,
                                   RangeValidator(*POOL_RECYCLE_RANGE),
                                   restart=True)
    pool_pre_ping = ConfigItem("DB", "Pool-Pre-Ping", True, BoolValidator(), restart=True)
    connect_args = ConfigItem("DB", "Connect-Args", {})


def load_config():
    global config
    qconfig.load("app/config/config.json", config)


config = Config()
load_config()
