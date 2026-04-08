import sys
import datetime
from app.commom.config import (load_log_config, get_log_config)

from loguru import logger


def _set_logger_config(sink, rotation, log_config_item):
    """
    配置并添加日志处理器
    根据配置参数设置 logger 的输出目标、格式、轮转等属性

    :param sink: 日志输出目标，可以是文件路径、流对象（如 sys.stderr）等
    :param rotation: 日志轮转策略，可以是 None、文件大小字符串、时间对象等
    :param log_config_item: Config 配置对象实例，包含所有日志配置项
    """
    # 调用 logger.add() 方法添加一个新的日志处理器
    logger.add(sink=sink,  # 指定日志输出目标（文件或控制台）
               level=log_config_item.log_level.value,  # 设置最低日志等级，只记录该等级及以上的日志
               enqueue=log_config_item.enqueue.value,  # 是否启用异步队列处理日志
               encoding=log_config_item.encoding.value,  # 设置日志文件的编码格式
               backtrace=log_config_item.backtrace.value,  # 是否启用扩展的异常回溯
               diagnose=log_config_item.diagnose.value,  # 是否在异常时显示变量值
               format=log_config_item.json_format.value if log_config_item.format_to_json.value else log_config_item.log_format.value,
               # 根据是否启用 JSON 格式选择对应的日志格式模板
               rotation=rotation,  # 设置日志文件轮转策略
               serialize=log_config_item.format_to_json.value,  # 是否将日志序列化为 JSON 格式
               retention=str(log_config_item.retention.value) + " " + log_config_item.retention_unit.value,
               # 设置日志保留时长，由数值和单位拼接而成（如 "7 days"）
               catch=log_config_item.catch.value)  # 是否捕获异常并记录到日志


def _get_log_file_path(file_name, log_config_item):
    """
    生成完整的日志文件路径
    根据配置决定是否在文件名中添加时间戳以避免覆盖

    :param file_name: 原始日志文件名
    :param log_config_item: Config 配置对象实例
    :return: 完整的日志文件路径字符串
    """
    # 检查是否启用了文件覆写（添加时间戳）
    if not log_config_item.file_override.value:
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
    return log_config_item.file_path.value + "/" + file_name


def init_log():
    if load_log_config() is None:
        return None
    log_config = get_log_config()
    
    rotation = None
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

    logger.remove()

    if log_config.output_console.value:
        _set_logger_config(sys.stderr, rotation, log_config)

    if log_config.output_file.value:
        file_path = _get_log_file_path(log_config, log_config.log_file.value)
        _set_logger_config(file_path, rotation, log_config)
        if log_config.different_level_file.value:
            for file in [log_config.trace_level_file.value,
                         log_config.debug_level_file.value,
                         log_config.info_level_file.value,
                         log_config.success_level_file.value,
                         log_config.warning_level_file.value,
                         log_config.error_level_file.value,
                         log_config.critical_level_file.value]:
                file_path = _get_log_file_path(file, log_config)
                _set_logger_config(file_path, rotation, log_config)
    return None
