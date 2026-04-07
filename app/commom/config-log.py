import sys

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


def set_logger_config(sink, rotation, log_config):
    """
    配置并添加日志处理器
    根据配置参数设置 logger 的输出目标、格式、轮转等属性
    
    :param sink: 日志输出目标，可以是文件路径、流对象（如 sys.stderr）等
    :param rotation: 日志轮转策略，可以是 None、文件大小字符串、时间对象等
    :param log_config: Config 配置对象实例，包含所有日志配置项
    """
    # 调用 logger.add() 方法添加一个新的日志处理器
    logger.add(sink=sink,  # 指定日志输出目标（文件或控制台）
               level=log_config.log_level.value,  # 设置最低日志等级，只记录该等级及以上的日志
               enqueue=log_config.enqueue.value,  # 是否启用异步队列处理日志
               encoding=log_config.encoding.value,  # 设置日志文件的编码格式
               backtrace=log_config.backtrace.value,  # 是否启用扩展的异常回溯
               diagnose=log_config.diagnose.value,  # 是否在异常时显示变量值
               format=log_config.json_format.value if log_config.format_to_json.value else log_config.log_format.value,
               # 根据是否启用 JSON 格式选择对应的日志格式模板
               rotation=rotation,  # 设置日志文件轮转策略
               serialize=log_config.format_to_json.value,  # 是否将日志序列化为 JSON 格式
               retention=str(log_config.retention.value) + " " + log_config.retention_unit.value,
               # 设置日志保留时长，由数值和单位拼接而成（如 "7 days"）
               catch=log_config.catch.value)  # 是否捕获异常并记录到日志


def get_log_file_path(file_name, log_config):
    """
    生成完整的日志文件路径
    根据配置决定是否在文件名中添加时间戳以避免覆盖
    
    :param file_name: 原始日志文件名
    :param log_config: Config 配置对象实例
    :return: 完整的日志文件路径字符串
    """
    # 检查是否启用了文件覆写（添加时间戳）
    if not log_config.file_override.value:
        # 保存原始文件名
        original_file = file_name
        # 查找文件名中最后一个点号的位置（用于分离文件名和扩展名）
        dot_index = file_name.rfind('.')
        # 如果找到了点号（即文件有扩展名）
        if dot_index != -1:
            # 提取点号前的文件名部分
            name_part = original_file[:dot_index]
            # 提取点号及之后的扩展名部分
            ext_part = original_file[dot_index:]
            # 在文件名和扩展名之间插入当前时间戳，格式为 YYYY-MM-DD_HH-MM-SS
            file_name = f"{name_part}_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}{ext_part}"

    # 拼接完整的文件路径：日志目录 + "/" + 文件名
    return log_config.file_path.value + "/" + file_name


def init():
    """
    初始化日志系统
    读取配置文件，根据配置项设置 logger 的各种输出目标和行为
    这是整个日志配置的入口函数，应在程序启动时调用
    """
    # 1.加载配置文件
    # 创建 Config 配置对象实例
    log_config = Config()
    # 从 JSON5 配置文件中加载配置项到 log_config 对象
    # 配置文件路径为 "app/config/config-log.json5"
    qconfig.load("app/config/config-log.json5", log_config)

    # 2.预处理
    # 初始化轮转策略变量为 None
    rotation = None
    # 根据配置的轮转模式，计算具体的轮转策略
    match log_config.rotate_mode:
        case "Size":
            # 按文件大小轮转：拼接大小数值和单位（如 "10 MB"）
            rotation = str(log_config.rotate_size.value) + " " + log_config.rotate_size_unit.value
        case "Time":
            # 按时间点轮转：使用配置的时间对象（如每天 00:00）
            rotation = log_config.rotate_time.value
        case "Interval":
            # 按时间间隔轮转：拼接间隔数值和单位（如 "1 hour"）
            rotation = str(log_config.rotate_interval.value) + " " + log_config.rotate_interval_unit.value
        case _:
            # 其他情况（包括 "None"）：不启用轮转
            rotation = None

    # 3.输出设置
    # loguru 默认会添加一个输出到 stderr 的处理器，先将其移除
    logger.remove()

    # 3.1 控制台输出
    # 如果启用了控制台输出
    if log_config.output_console.value:
        # 添加一个输出到标准错误流（sys.stderr）的日志处理器
        # 使用之前计算的轮转策略（对控制台输出通常无效，但保持一致性）
        set_logger_config(sys.stderr, rotation, log_config)

    # 3.2 文件输出
    # 如果启用了文件输出
    if log_config.output_file.value:
        # 生成统一日志文件的完整路径
        file_path = get_log_file_path(log_config, log_config.log_file.value)
        # 添加统一日志文件的处理器
        set_logger_config(file_path, rotation, log_config)

        # 如果启用了按等级分文件
        if log_config.different_level_file.value:
            # 遍历所有日志等级对应的文件名
            for file in [log_config.trace_level_file.value,
                         log_config.debug_level_file.value,
                         log_config.info_level_file.value,
                         log_config.success_level_file.value,
                         log_config.warning_level_file.value,
                         log_config.error_level_file.value,
                         log_config.critical_level_file.value]:
                # 为每个等级的日志文件生成完整路径
                file_path = get_log_file_path(file, log_config)
                # 添加该等级日志文件的处理器
                set_logger_config(file_path, rotation, log_config)
