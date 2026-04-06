import sys

import datetime
import json5
from loguru import logger
from qfluentwidgets import (qconfig, QConfig, ConfigItem, OptionsConfigItem, BoolValidator,
                            OptionsValidator, RangeConfigItem, RangeValidator,
                            FolderListValidator, Theme, FolderValidator, ConfigSerializer)


class FileTimeSerializer(ConfigSerializer):
    def serialize(self, value: datetime.time):
        return value.strftime("%HH:%MM")

    def deserialize(self, value: str):
        return datetime.datetime.strptime(value, "%HH:%MM").time()


class Config(QConfig):
    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(
                                      ["Trace", "Debug", "Info", "Success", "Warning", "Error", "Critical"]),
                                  restart=True)
    log_format = ConfigItem("Log", "Log-Format", "{time} | {level} | {message}")
    format_to_json = ConfigItem("Log", "Format-To-Json", False,
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
                          OptionsValidator(["utf-8"]),
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
    different_level_file = ConfigItem("File", "Different-Level-File", True,
                                      BoolValidator(),
                                      restart=True)
    file_path = ConfigItem("File", "File-Path", "logs",
                           FolderValidator(),
                           restart=True)
    trace_level_file = ConfigItem("File", "Trace-Level-File", "trace.log")
    debug_level_file = ConfigItem("File", "Debug-Level-File", "debug.log")
    info_level_file = ConfigItem("File", "Info-Level-File", "info.log")
    success_level_file = ConfigItem("File", "Success-Level-File", "success.log")
    warning_level_file = ConfigItem("File", "Warning-Level-File", "warning.log")
    error_level_file = ConfigItem("File", "Error-Level-File", "error.log")
    critical_level_file = ConfigItem("File", "Critical-Level-File", "critical.log")
    log_file = ConfigItem("File", "Log-File", "log.log")
    file_override = ConfigItem("File", "File-Override", True,
                               BoolValidator(),
                               restart=True)
    rotate_mode = OptionsConfigItem("File", "Rotate-Mode", "None",
                                    OptionsValidator(["None", "Size", "Time", "Interval"]),
                                    restart=True)
    rotate_size = RangeConfigItem("File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)
    rotate_size_unit = OptionsConfigItem("File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(["MB", "GB"]),
                                         restart=True)
    rotate_time = RangeConfigItem("File", "Rotate-Time",
                                  datetime.time(0, 0),
                                  FileTimeSerializer(),
                                  restart=True
                                  )
    rotate_interval = RangeConfigItem("File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)
    rotate_interval_unit = OptionsConfigItem("File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(["second", "minute", "hour", "day"]),
                                             restart=True)
    retention = RangeConfigItem("File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)
    retention_unit = OptionsConfigItem("File", "Retention-Unit", "days",
                                       OptionsValidator(["seconds", "minutes", "hours", "days"]),
                                       restart=True)
    file_compress = ConfigItem("File", "File-Compress", "None",
                               OptionsValidator(["None", "zip"]),
                               restart=True)


def set_logger_config(sink,log_config):
    rotation = None
    match log_config.rotate_mode:
        case "Size":
            rotation = str(log_config.rotate_size.value) + " " + log_config.rotate_size_unit.value
        case "Time":
            rotation = log_config.rotate_time.value
        case "Interval":
            rotation = str(log_config.rotate_interval.value) + " " + log_config.rotate_interval_unit.value
        case _:
            rotation = None
    logger.add(sink=sink,
               level=log_config.log_level.value,
               enqueue=log_config.enqueue.value,
               encoding=log_config.encoding.value,
               backtrace=log_config.backtrace.value,
               diagnose=log_config.diagnose.value,
               format=log_config.json_format.value if log_config.format_to_json.value else log_config.log_format.value,
               rotation=rotation,
               serialize=log_config.format_to_json.value,
               retention=str(log_config.retention.value) + " " + log_config.retention_unit.value,
               catch=log_config.catch.value)


def get_log_file_path(file_name,log_config):
    # 是否开启覆写
    if log_config.file_override.value:
        original_file = file_name
        dot_index = file_name.rfind('.')
        if dot_index != -1:
            name_part = original_file[:dot_index]
            ext_part = original_file[dot_index:]
            file_name = f"{name_part}_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}{ext_part}"
    return log_config.file_path.value + "/" + file_name


def init():
    # 读取配置文件
    log_config = Config()
    qconfig.load("app/config/log-config.json5", log_config)

    logger.remove()
    # 输出到控制台
    if log_config.output_console.value:
        set_logger_config(sys.stderr, log_config)
    # 输出到文件
    if log_config.output_file.value:
        file_path = get_log_file_path(log_config, log_config.log_file.value)
        set_logger_config(file_path, log_config)
        if log_config.different_level_file.value:
            for file in [log_config.trace_level_file.value,
                         log_config.debug_level_file.value,
                         log_config.info_level_file.value,
                         log_config.success_level_file.value,
                         log_config.warning_level_file.value,
                         log_config.error_level_file.value,
                         log_config.critical_level_file.value]:
                file_path = get_log_file_path(file,log_config)
                set_logger_config(file_path, log_config)
