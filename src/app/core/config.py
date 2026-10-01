"""应用配置。

配置项集中在这里定义，路径类配置留空表示使用 `paths` 推导出的默认目录。
"""

from __future__ import annotations

import json
import logging
import shutil
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
    resourcePath = ConfigItem("Storage", "Resource-Path", "")
    resourceProtected = ConfigItem("Storage", "Resource-Protected", False, BoolValidator())
    hiddenProtected = ConfigItem("Storage", "Hidden-Protected", False, BoolValidator())
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

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- 路径解析
def resources_root() -> Path:
    """资源文件夹：库数据、数据库、内容仓库与封面都在里面。"""
    return paths.resource_root(config.resourcePath.value)


def library_root() -> Path:
    """唯一的库文件夹根目录（资源文件夹下的 library/）。"""
    return resources_root() / "library"


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


def _legacy_library_path() -> str:
    """旧版配置里的库文件夹路径（Storage/Library-Path）。"""
    try:
        data = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    storage = data.get("Storage")
    if not isinstance(storage, dict):
        return ""
    return str(storage.get("Library-Path", "") or "")


def _migrate_legacy_resource_path() -> None:
    """把旧版「库文件夹」配置换算成新的「资源文件夹」配置。"""
    legacy = _legacy_library_path()
    if config.resourcePath.value or not legacy:
        return
    library = paths.resolve_dir(legacy, paths.DEFAULT_LIBRARY_DIR)
    folder = library.parent if library.name == "library" else library
    if folder == paths.ROOT / paths.LEGACY_RESOURCE_DIR_NAME:
        return  # 旧默认位置：由 migrate_legacy_dir() 原地改名
    target = paths.resource_root(folder)
    paths.migrate_legacy_dir(target)
    destination = target / "library"
    if library.is_dir() and not destination.exists():
        paths.make_dir(target)
        shutil.move(str(library), str(destination))
        logger.info("已把库文件夹迁入资源文件夹：{}", destination)
    elif library.is_dir() and library.resolve() != destination.resolve():
        logger.warning("旧库文件夹仍在 {}，未自动迁移到 {}", library, destination)
    config.set(config.resourcePath, str(target))


def release_resource_root() -> None:
    """放行被 ACL 锁上的资源文件夹（启动、重建、搬迁前调用；保护未开启时不动）。"""
    paths.release_locked_root()


def lock_resource_root() -> None:
    """按当前设置重新锁定资源文件夹（保护未开启时什么都不做）。"""
    if not config.resourceProtected.value:
        return
    from ..services.privacy_service import privacy  # 延迟导入，避免循环依赖

    privacy.lock()


def set_resource_root(folder: str | Path) -> Path:
    """把资源文件夹（库数据 + 数据库）迁到 folder 并立即生效。"""
    from ..db import database  # 局部导入，避免与数据库模块循环依赖

    target = paths.resource_root(folder)
    current = resources_root()
    if target == current:
        config.set(config.resourcePath, str(target))
        paths.apply_resource_root(target)
        return target
    if target.exists():
        raise FileExistsError(f"目标资源文件夹已存在：{target}")
    database.dispose_engine()
    release_resource_root()  # 旧位置可能锁着，搬迁前先放行
    if current.exists():
        paths.make_dir(target.parent)
        shutil.move(str(current), str(target))
    config.set(config.resourcePath, str(target))
    paths.apply_resource_root(target)
    paths.ensure_dirs()
    database.init_db()
    _rebase_library_paths(current / "library", target / "library")
    lock_resource_root()  # 换了位置：按设置把新位置锁上
    return target


def _rebase_library_paths(old_root: Path, new_root: Path) -> None:
    """资源文件夹整体搬迁后，把库里记录的老路径改到新位置。"""
    from ..db import database
    from ..db.models import Library

    with database.session_scope() as session:
        for library in session.query(Library).all():
            try:
                relative = Path(library.path).relative_to(old_root)
            except ValueError:
                continue
            library.path = str(new_root / relative)


def load_config() -> None:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    qconfig.load(str(paths.CONFIG_FILE), config)
    # 上一次退出时锁上的资源文件夹要先放行，否则迁移与建目录都会被拒绝
    release_resource_root()
    _migrate_legacy_resource_path()
    paths.apply_resource_root(resources_root())
    paths.migrate_legacy_dir(paths.DATA_DIR)
    paths.ensure_dirs()


load_config()
