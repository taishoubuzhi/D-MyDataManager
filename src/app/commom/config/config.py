from qfluentwidgets import (qconfig)


def load(config_item, config_file, config_class):
    if config_item is not None:
        return config_item
    config_item = config_class()
    qconfig.load(config_file, config_item)
    if config_item == {}:
        return None
    return config_item
