import datetime
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator, OptionsValidator,
                            RangeConfigItem, RangeValidator, FolderValidator, ConfigSerializer)


class FileTimeSerializer(ConfigSerializer):
    """
    文件时间序列化器类
    用于将 datetime.time 对象与字符串之间进行序列化和反序列化转换
    继承自 ConfigSerializer，实现自定义的时间格式转换
    """

    def serialize(self, value: datetime.time):
        """
        序列化方法：将 time 对象转换为字符串格式
        :param value: datetime.time 对象
        :return: 格式化后的时间字符串，格式为 "%HH:%MM"
        """
        # 将 time 对象格式化为 "小时:分钟" 格式的字符串
        return value.strftime("%HH:%MM")

    def deserialize(self, value: str):
        """
        反序列化方法：将字符串转换为 time 对象
        :param value: 时间字符串，格式为 "%HH:%MM"
        :return: datetime.time 对象
        """
        # 将字符串解析为 datetime 对象，然后提取 time 部分
        return datetime.datetime.strptime(value, "%HH:%MM").time()


class Config(QConfig):
    log_levels = ConfigItem("Log", "Log-Levels", ["Trace", "Debug", "Info", "Success", "Warn", "Error", "Critical"])

    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(log_levels.value),
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

    encodings = ConfigItem("Log", "Encodings", ["utf-8"])

    encoding = OptionsConfigItem("Log", "Encoding", "utf-8",
                                 OptionsValidator(encodings.value),
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

    rotate_modes = ConfigItem("File", "Rotate-Modes", ["None", "Size", "Time", "Interval"])

    # 当前使用的轮转模式，默认为 "None"（不轮转）
    rotate_mode = OptionsConfigItem("File", "Rotate-Mode", "None",
                                    OptionsValidator(rotate_modes.value),
                                    restart=True)

    rotate_size = RangeConfigItem("File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)

    rotate_size_units = ConfigItem("File", "Rotate-Size-Units", ["B", "KB", "MB", "GB"])

    rotate_size_unit = OptionsConfigItem("File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(rotate_size_units.value),
                                         restart=True)

    rotate_time = ConfigItem("File", "Rotate-Time",
                             datetime.time(0, 0),
                             serializer=FileTimeSerializer(),
                             restart=True
                             )

    rotate_interval = RangeConfigItem("File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)

    rotate_interval_units = ConfigItem("File", "Rotate-Interval-Units", ["second", "minute", "hour", "day"])

    rotate_interval_unit = OptionsConfigItem("File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(rotate_interval_units.value),
                                             restart=True)

    retention = RangeConfigItem("File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)

    retention_units = ConfigItem("File", "Retention-Units", ["seconds", "minutes", "hours", "days"])

    retention_unit = OptionsConfigItem("File", "Retention-Unit", "days",
                                       OptionsValidator(retention_units.value),
                                       restart=True)

    compress_modes = ConfigItem("File", "Compress-Modes", ["None", "zip"])

    compress_mode = OptionsConfigItem("File", "Compress-Mode", "None",
                                      OptionsValidator(compress_modes.value),
                                      restart=True)


def load_log_config():
    global log_config
    qconfig.load("app/config/config-log.json5", log_config)

log_config = Config()
load_log_config()
