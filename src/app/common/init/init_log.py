import sys
import os
import datetime
from ..config import config

from loguru import logger


def _cleanup_old_logs(log_dir, max_count):
    """启动时检查日志目录，删除超出数量限制的旧日志文件"""
    log_files = [f for f in os.listdir(log_dir) if f.endswith(".log")]
    if len(log_files) <= max_count:
        return
    # 按修改时间排序，最旧的在前
    log_files.sort(key=lambda f: os.path.getmtime(os.path.join(log_dir, f)))
    delete_count = len(log_files) - max_count
    for f in log_files[:delete_count]:
        os.remove(os.path.join(log_dir, f))
        logger.debug(f"Deleted old log file: {f}")


def _set_logger_config(sink, rotation, retention, config_item):
    fmt = config_item.get(config_item.json_format) if config_item.get(config_item.format_to_json) else config_item.get(config_item.log_format)
    # 控制台使用整行带颜色的格式
    if not isinstance(sink, str):
        fmt = "<level>" + fmt + "</level>"
    is_file = isinstance(sink, str)
    kwargs = dict(
        sink=sink,
        level=config_item.get(config_item.log_level),
        # 文件 handler 关闭 enqueue，确保崩溃时日志不丢失
        enqueue=config_item.get(config_item.enqueue) if not is_file else False,
        backtrace=config_item.get(config_item.backtrace),
        diagnose=config_item.get(config_item.diagnose),
        format=fmt,
        serialize=config_item.get(config_item.format_to_json),
        catch=config_item.get(config_item.catch))
    if is_file:
        kwargs["encoding"] = config_item.get(config_item.encoding)
        if rotation is not None:
            kwargs["rotation"] = rotation
        if retention is not None:
            kwargs["retention"] = retention
    logger.add(**kwargs)


def init_log():
    rotation = None
    retention = None
    match config.get(config.rotate_mode):
        case "Time":
            rotation = str(config.get(config.rotate_interval)) + " " + config.get(config.rotate_interval_unit)
        case "Count":
            # 每日轮转触发 + retention 控制保留数量
            rotation = "1 day"
            retention = config.get(config.rotate_count)
        case _:
            rotation = None

    logger.remove()

    if config.get(config.output_file):
        log_dir = config.get(config.file_path)
        os.makedirs(log_dir, exist_ok=True)

        # 轮转模式不为 None 时，启动时额外进行一次手动清理
        if config.get(config.rotate_mode) != "None":
            _cleanup_old_logs(log_dir, config.get(config.rotate_count))

        file_name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log"
        file_path = os.path.join(log_dir, file_name)
        _set_logger_config(file_path, rotation, retention, config)

    if config.get(config.output_console):
        _set_logger_config(sys.stderr, rotation, retention, config)

    logger.info("Log system initialized")
    logger.debug(f"Log level: {config.get(config.log_level)}")
    logger.debug(f"Console output: {config.get(config.output_console)}")
    logger.debug(f"File output: {config.get(config.output_file)}")
    if config.get(config.output_file):
        logger.debug(f"Log directory: {config.get(config.file_path)}")
    if rotation is not None:
        logger.debug(f"Log rotation: {rotation}")
    if retention is not None:
        logger.debug(f"Log retention count: {retention}")
