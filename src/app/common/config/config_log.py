import datetime
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator, OptionsValidator,
                            RangeConfigItem, RangeValidator, FolderValidator, ConfigSerializer)


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


LOG_LEVELS = ["Trace", "Debug", "Info", "Success", "Warn", "Error", "Critical"]
ENCODINGS = ["utf-8"]
ROTATE_MODES = ["None", "Size", "Time", "Interval"]
ROTATE_SIZE_UNITS = ["B", "KB", "MB", "GB"]
ROTATE_INTERVAL_UNITS = ["second", "minute", "hour", "day"]
RETENTION_UNITS = ["seconds", "minutes", "hours", "days"]
COMPRESS_MODES = ["None", "zip"]


class Config(QConfig):
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

    file_path = ConfigItem("File", "Log-Path", "logs",
                           FolderValidator(),
                           restart=True)

    trace_level_file = ConfigItem("File", "File-Trace", "trace.log")  # Trace 等级日志文件名
    debug_level_file = ConfigItem("File", "File-Debug", "debug.log")  # Debug 等级日志文件名
    info_level_file = ConfigItem("File", "File-Info", "info.log")  # Info 等级日志文件名
    success_level_file = ConfigItem("File", "File-Success", "success.log")  # Success 等级日志文件名
    warning_level_file = ConfigItem("File", "File-Warn", "warn.log")  # Warning 等级日志文件名
    error_level_file = ConfigItem("File", "File-Error", "error.log")  # Error 等级日志文件名
    critical_level_file = ConfigItem("File", "File-Critical", "critical.log")  # Critical 等级日志文件名

    log_file = ConfigItem("File", "File-Log", "log.log")

    file_override = ConfigItem("File", "File-Overwrite", True,
                               BoolValidator(),
                               restart=True)

    # 当前使用的轮转模式，默认为 "None"（不轮转）
    rotate_mode = OptionsConfigItem("File", "Rotate-Mode", "None",
                                    OptionsValidator(ROTATE_MODES),
                                    restart=True)

    rotate_size = RangeConfigItem("File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)

    rotate_size_unit = OptionsConfigItem("File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(ROTATE_SIZE_UNITS),
                                         restart=True)

    rotate_time = ConfigItem("File", "Rotate-Time",
                             datetime.time(0, 0),
                             serializer=FileTimeSerializer(),
                             restart=True
                             )

    rotate_interval = RangeConfigItem("File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)

    rotate_interval_unit = OptionsConfigItem("File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(ROTATE_INTERVAL_UNITS),
                                             restart=True)

    retention = RangeConfigItem("File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)

    retention_unit = OptionsConfigItem("File", "Retention-Unit", "days",
                                       OptionsValidator(RETENTION_UNITS),
                                       restart=True)

    compress_mode = OptionsConfigItem("File", "Compress-Mode", "None",
                                      OptionsValidator(COMPRESS_MODES),
                                      restart=True)


log_config = Config()


def load_log_config():
    global pre_log_config, log_config

    qconfig.load("app/config/config-log.json5", log_config)

load_log_config()