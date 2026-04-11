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


class PreConfig(QConfig):
    log_levels = ConfigItem("Log", "Log-Levels", ["Trace", "Debug", "Info", "Success", "Warn", "Error", "Critical"])
    encodings = ConfigItem("Log", "Encodings", ["utf-8"])
    rotate_modes = ConfigItem("File", "Rotate-Modes", ["None", "Size", "Time", "Interval"])
    rotate_size_units = ConfigItem("File", "Rotate-Size-Units", ["B", "KB", "MB", "GB"])
    rotate_interval_units = ConfigItem("File", "Rotate-Interval-Units", ["second", "minute", "hour", "day"])
    retention_units = ConfigItem("File", "Retention-Units", ["seconds", "minutes", "hours", "days"])
    compress_modes = ConfigItem("File", "Compress-Modes", ["None", "zip"])


pre_log_config = PreConfig()


class Config(QConfig):
    global pre_log_config
    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(pre_log_config.log_levels.value),
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
                                 OptionsValidator(pre_log_config.encodings.value),
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
                                    OptionsValidator(pre_log_config.rotate_modes.value),
                                    restart=True)

    rotate_size = RangeConfigItem("File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)

    rotate_size_unit = OptionsConfigItem("File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(pre_log_config.rotate_size_units.value),
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
                                             OptionsValidator(pre_log_config.rotate_interval_units.value),
                                             restart=True)

    retention = RangeConfigItem("File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)

    retention_unit = OptionsConfigItem("File", "Retention-Unit", "days",
                                       OptionsValidator(pre_log_config.retention_units.value),
                                       restart=True)

    compress_mode = OptionsConfigItem("File", "Compress-Mode", "None",
                                      OptionsValidator(pre_log_config.compress_modes.value),
                                      restart=True)


log_config = Config()


def load_log_config():
    global pre_log_config, log_config
    qconfig.load("app/config/config-log.json5", pre_log_config, log_config)


load_log_config()
