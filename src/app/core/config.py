"""应用配置。

配置项集中在这里定义，路径类配置留空表示使用 `paths` 推导出的默认目录。
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    ConfigSerializer,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    qconfig,
)

from . import paths


class Language(Enum):
    CHINESE_SIMPLIFIED = "zh_CN"
    ENGLISH = "en_US"
    AUTO = "Auto"


class LanguageSerializer(ConfigSerializer):
    def serialize(self, language: Language) -> str:
        return language.value

    def deserialize(self, value: str) -> Language:
        try:
            return Language(value)
        except ValueError:
            return Language.CHINESE_SIMPLIFIED


class Config(QConfig):
    """全局配置对象。"""

    # 界面
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto",
        OptionsValidator(["Auto", "100%", "125%", "150%", "200%"]), restart=True,
    )
    language = ConfigItem(
        "MainWindow", "Language", Language.CHINESE_SIMPLIFIED,
        serializer=LanguageSerializer(), restart=True,
    )
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", True, BoolValidator())
    theme = OptionsConfigItem("MainWindow", "Theme", "auto", OptionsValidator(["light", "dark", "auto"]))
    currentUserId = RangeConfigItem("User", "Current-Id", 0, RangeValidator(0, 1_000_000_000))

    # 日志
    logLevel = OptionsConfigItem(
        "Log", "Level", "INFO", OptionsValidator(["DEBUG", "INFO", "WARNING", "ERROR"]),
    )
    logToConsole = ConfigItem("Log", "Output-Console", True, BoolValidator())
    logEnqueue = ConfigItem("Log", "Enqueue", True, BoolValidator())
    logBacktrace = ConfigItem("Log", "Backtrace", True, BoolValidator())
    logDiagnose = ConfigItem("Log", "Diagnose", True, BoolValidator())
    logAsJson = ConfigItem("Log", "Format-To-JSON", False, BoolValidator())
    logFormat = ConfigItem(
        "Log", "Log-Format",
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    )
    # single：单文件追加；session：每次启动一个文件；daily：每天一个文件；size：按大小切分
    logMode = OptionsConfigItem(
        "Log", "Mode", "daily",
        OptionsValidator(["single", "session", "daily", "size"]), restart=True,
    )
    logKeepFiles = RangeConfigItem("Log", "Keep-Files", 10, RangeValidator(1, 200))
    logKeepDays = RangeConfigItem("Log", "Keep-Days", 14, RangeValidator(1, 3650))
    logMaxFileSizeMB = RangeConfigItem("Log", "Max-File-Size-MB", 20, RangeValidator(1, 1024))
    logMaxTotalSizeMB = RangeConfigItem("Log", "Max-Total-Size-MB", 200, RangeValidator(1, 10240))

    # 数据库
    dbUrl = ConfigItem("Database", "URL", "")
    dbEcho = ConfigItem("Database", "Echo", False, BoolValidator())

    # 存储
    libraryPath = ConfigItem("Storage", "Library-Path", "")
    exportPath = ConfigItem("Storage", "Export-Path", "")
    coverSize = RangeConfigItem("Storage", "Cover-Size", 256, RangeValidator(64, 1024))

    # 导入
    nameByTime = ConfigItem("Import", "Name-By-Time", False, BoolValidator())
    duplicatePolicy = OptionsConfigItem(
        "Import", "Duplicate-Policy", "rename", OptionsValidator(["skip", "rename", "overwrite"]),
    )
    archiveOnImport = ConfigItem("Import", "Archive-On-Import", True, BoolValidator())

    # 存档
    keepVersions = RangeConfigItem("Archive", "Keep-Versions", 10, RangeValidator(1, 200))
    pruneMode = OptionsConfigItem(
        "Archive", "Prune-Mode", "count", OptionsValidator(["count", "size", "age", "none"])
    )
    keepSize = RangeConfigItem("Archive", "Keep-Size-MB", 2048, RangeValidator(64, 1_048_576))
    keepDays = RangeConfigItem("Archive", "Keep-Days", 30, RangeValidator(1, 3650))


config = Config()


# --------------------------------------------------------------------------- 路径解析
def library_root() -> Path:
    """唯一的库文件夹根目录（多库设计已取消）。"""
    return paths.resolve_dir(config.libraryPath.value, paths.DEFAULT_LIBRARY_DIR)


def global_dir() -> Path:
    """库内的全局文件夹：数据仓库、封面、备份与元数据都放在这里。"""
    return library_root() / paths.GLOBAL_DIR_NAME


def store_dir() -> Path:
    return global_dir() / paths.LIBRARY_STORE_DIRNAME


def cover_dir() -> Path:
    return global_dir() / paths.LIBRARY_COVER_DIRNAME


def backup_dir() -> Path:
    return global_dir() / paths.LIBRARY_BACKUP_DIRNAME


def export_dir() -> Path:
    return paths.resolve_dir(config.exportPath.value, paths.DEFAULT_EXPORT_DIR)


def db_file() -> Path:
    return paths.DB_FILE


def db_url() -> str:
    return config.dbUrl.value or f"sqlite:///{db_file().as_posix()}"


def load_config() -> None:
    paths.ensure_dirs()
    qconfig.load(str(paths.CONFIG_FILE), config)


load_config()
