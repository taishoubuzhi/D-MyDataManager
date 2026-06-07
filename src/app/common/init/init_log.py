import sys
import datetime
from ..config import config

from loguru import logger


def _set_logger_config(sink, rotation, config_item):
    kwargs = dict(
        sink=sink,
        level=config_item.get(config_item.log_level),
        enqueue=config_item.get(config_item.enqueue),
        backtrace=config_item.get(config_item.backtrace),
        diagnose=config_item.get(config_item.diagnose),
        format=config_item.get(config_item.json_format) if config_item.get(config_item.format_to_json) else config_item.get(config_item.log_format),
        serialize=config_item.get(config_item.format_to_json),
        catch=config_item.get(config_item.catch))
    if isinstance(sink, str):
        kwargs["encoding"] = config_item.get(config_item.encoding)
    if rotation is not None:
        kwargs["rotation"] = rotation
        kwargs["retention"] = str(config_item.get(config_item.retention)) + " " + config_item.get(config_item.retention_unit)
    logger.add(**kwargs)


def _get_log_file_path(file_name, config_item):
    if not config_item.get(config_item.file_override):
        original_file = file_name
        dot_index = file_name.rfind('.')
        if dot_index != -1:
            name_part = original_file[:dot_index]
            ext_part = original_file[dot_index:]
            file_name = f"{name_part}_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}{ext_part}"

    return config_item.get(config_item.file_path) + "/" + file_name


def init_log():
    rotation = None
    match config.get(config.rotate_mode):
        case "Size":
            rotation = str(config.get(config.rotate_size)) + " " + config.get(config.rotate_size_unit)
        case "Time":
            rotation = config.get(config.rotate_time)
        case "Interval":
            rotation = str(config.get(config.rotate_interval)) + " " + config.get(config.rotate_interval_unit)
        case _:
            rotation = None

    logger.remove()

    if config.get(config.output_console):
        _set_logger_config(sys.stderr, rotation, config)

    if config.get(config.output_file):
        file_path = _get_log_file_path(config.get(config.log_file), config)
        _set_logger_config(file_path, rotation, config)
        if config.get(config.different_level_file):
            for file in [config.get(config.trace_level_file),
                         config.get(config.debug_level_file),
                         config.get(config.info_level_file),
                         config.get(config.success_level_file),
                         config.get(config.warning_level_file),
                         config.get(config.error_level_file),
                         config.get(config.critical_level_file)]:
                file_path = _get_log_file_path(file, config)
                _set_logger_config(file_path, rotation, config)
