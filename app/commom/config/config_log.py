import datetime
from loguru import logger
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
    """
    日志配置类
    继承自 QConfig，用于管理日志相关的所有配置项
    每个配置项对应 config-log.json5 中的一个配置字段
    """

    # 1.Log 部分配置

    # 定义所有可用的日志等级列表
    # 包括：Trace(追踪)、Debug(调试)、Info(信息)、Success(成功)、Warn(警告)、Error(错误)、Critical(严重错误)
    log_levels = ConfigItem("Log", "Log-Levels", ["Trace", "Debug", "Info", "Success", "Warn", "Error", "Critical"])

    # 当前生效的日志等级配置项
    # 使用 OptionsConfigItem 限制只能从预定义的日志等级中选择
    # restart=True 表示修改此配置后需要重启才能生效
    log_level = OptionsConfigItem("Log", "Log-Level", "Debug",
                                  OptionsValidator(log_levels.value),
                                  restart=True)

    # 日志输出格式配置
    # 支持变量：{time} 时间、{level} 等级、{message} 消息内容等
    log_format = ConfigItem("Log", "Log-Format", "{time} | {level} | {message}")

    # 是否将日志格式化为 JSON 格式输出
    # 使用 BoolValidator 确保值只能是 True 或 False
    format_to_json = ConfigItem("Log", "Format-To-JSON", False,
                                BoolValidator(),
                                restart=True)

    # JSON 格式下的日志输出格式模板
    json_format = ConfigItem("Log", "JSON-Format", "{time} | {level} | {message}")

    # 是否捕获异常并记录到日志中
    catch = ConfigItem("Log", "Catch", True,
                       BoolValidator(),
                       restart=True)

    # 是否将日志输出到控制台（终端）
    output_console = ConfigItem("Log", "Output-Console", True,
                                BoolValidator(),
                                restart=True)

    # 是否将日志记录放入队列异步处理
    # 启用后可提高性能，避免阻塞主线程
    enqueue = ConfigItem("Log", "Enqueue", True,
                         BoolValidator(),
                         restart=True)

    # 支持的编码格式列表
    encodings = ConfigItem("Log", "Encodings", ["utf-8"])

    # 当前使用的编码格式，默认为 utf-8
    encoding = OptionsConfigItem("Log", "Encoding", "utf-8",
                                 OptionsValidator(encodings.value),
                                 restart=True)

    # 是否在异常跟踪中包含调用栈的更深层信息
    # 启用后可以追溯到捕获错误的更外层位置
    backtrace = ConfigItem("Log", "Backtrace", True,
                           BoolValidator(),
                           restart=True)

    # 是否在异常跟踪中显示局部变量的值
    # 启用后有助于调试，但可能泄露敏感信息
    diagnose = ConfigItem("Log", "Diagnose", True,
                          BoolValidator(),
                          restart=True)

    # 2.File 部分配置

    # 是否将日志输出到文件
    output_file = ConfigItem("File", "Output-File", True,
                             BoolValidator(),
                             restart=True)

    # 是否为不同日志等级创建独立的日志文件
    # 启用后会将不同等级的日志分别写入不同的文件
    different_level_file = ConfigItem("File", "Different-Level-Files", True,
                                      BoolValidator(),
                                      restart=True)

    # 日志文件存储路径，默认为 "logs" 目录
    # 使用 FolderValidator 验证路径是否为有效的文件夹路径
    file_path = ConfigItem("File", "Log-Path", "logs",
                           FolderValidator(),
                           restart=True)

    # 各个日志等级对应的文件名配置
    trace_level_file = ConfigItem("File", "File-Trace", "trace.log")  # Trace 等级日志文件名
    debug_level_file = ConfigItem("File", "File-Debug", "debug.log")  # Debug 等级日志文件名
    info_level_file = ConfigItem("File", "File-Info", "info.log")  # Info 等级日志文件名
    success_level_file = ConfigItem("File", "File-Success", "success.log")  # Success 等级日志文件名
    warning_level_file = ConfigItem("File", "File-Warn", "warn.log")  # Warning 等级日志文件名
    error_level_file = ConfigItem("File", "File-Error", "error.log")  # Error 等级日志文件名
    critical_level_file = ConfigItem("File", "File-Critical", "critical.log")  # Critical 等级日志文件名

    # 默认的统一日志文件名（当不区分等级时使用）
    log_file = ConfigItem("File", "File-Log", "log.log")

    # 是否覆盖已存在的日志文件
    # 不启用后会在文件名中添加时间戳，避免覆盖旧文件
    file_override = ConfigItem("File", "File-Overwrite", True,
                               BoolValidator(),
                               restart=True)

    # 定义所有可用的日志文件轮转模式
    # None: 不轮转、Size: 按文件大小、Time: 按时间点、Interval: 按时间间隔
    rotate_modes = ConfigItem("File", "Rotate-Modes", ["None", "Size", "Time", "Interval"])

    # 当前使用的轮转模式，默认为 "None"（不轮转）
    rotate_mode = OptionsConfigItem("File", "Rotate-Mode", "None",
                                    OptionsValidator(rotate_modes.value),
                                    restart=True)

    # 按文件大小轮转时的阈值大小
    # 使用 RangeValidator 限制范围在 1-1024 之间
    rotate_size = RangeConfigItem("File", "Rotate-Size", 1,
                                  RangeValidator(1, 1024),
                                  restart=True)

    # 文件大小单位列表：B(字节)、KB(千字节)、MB(兆字节)、GB(吉字节)
    rotate_size_units = ConfigItem("File", "Rotate-Size-Units", ["B", "KB", "MB", "GB"])

    # 当前使用的文件大小单位，默认为 "MB"
    rotate_size_unit = OptionsConfigItem("File", "Rotate-Size-Unit", "MB",
                                         OptionsValidator(rotate_size_units.value),
                                         restart=True)

    # 按时间点轮转的具体时间
    # 使用自定义的 FileTimeSerializer 进行序列化/反序列化
    # 默认为每天 00:00 生成新文件
    rotate_time = RangeConfigItem("File", "Rotate-Time",
                                  datetime.time(0, 0),
                                  FileTimeSerializer(),
                                  restart=True
                                  )

    # 按时间间隔轮转的间隔数值
    # 使用 RangeValidator 限制范围在 1-24 之间
    rotate_interval = RangeConfigItem("File", "Rotate-Interval", 1,
                                      RangeValidator(1, 24),
                                      restart=True)

    # 时间间隔单位列表：second(秒)、minute(分钟)、hour(小时)、day(天)
    rotate_interval_units = ConfigItem("File", "Rotate-Interval-Units", ["second", "minute", "hour", "day"])

    # 当前使用的时间间隔单位，默认为 "hour"
    rotate_interval_unit = OptionsConfigItem("File", "Rotate-Interval-Unit", "hour",
                                             OptionsValidator(rotate_interval_units.value),
                                             restart=True)

    # 日志保留时长数值
    # 超过此时长的旧日志会被自动清理
    # 使用 RangeValidator 限制范围在 1-365 之间
    retention = RangeConfigItem("File", "Retention", 7,
                                RangeValidator(1, 365),
                                restart=True)

    # 保留时间单位列表：seconds(秒)、minutes(分钟)、hours(小时)、days(天)
    retention_units = ConfigItem("File", "Retention-Units", ["seconds", "minutes", "hours", "days"])

    # 当前使用的保留时间单位，默认为 "days"
    retention_unit = OptionsConfigItem("File", "Retention-Unit", "days",
                                       OptionsValidator(retention_units.value),
                                       restart=True)

    # 压缩方式列表：None(不压缩)、zip(zip 压缩)
    compress_modes = ConfigItem("File", "Compress-Modes", ["None", "zip"])

    # 当前使用的压缩方式，默认为 "None"（不压缩）
    compress_mode = OptionsConfigItem("File", "Compress-Mode", "None",
                                      OptionsValidator(compress_modes.value),
                                      restart=True)


def load_config():
    # 1.加载配置文件
    # 创建 Config 配置对象实例
    log_config = None
    if log_config is not None:
        logger.success("log_config is not None")
        return log_config
    log_config = Config()
    qconfig.load("app/config/config-log.json5", log_config)
    if log_config == {}:
        logger.error("load config fail")
        return None
    logger.success("load config success")
    return log_config