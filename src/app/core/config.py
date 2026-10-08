"""应用配置。

配置项集中在这里定义，路径类配置留空表示使用 `paths` 推导出的默认目录。
"""

from __future__ import annotations

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

from .runtime import jsonio, paths


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


#: 「简化显示」的三个挡位：不简化 / 默认（只简化不会混淆的图标）/ 完全简化
SIMPLE_NONE = "none"
SIMPLE_DEFAULT = "default"
SIMPLE_FULL = "full"
SIMPLE_MODES = (SIMPLE_NONE, SIMPLE_DEFAULT, SIMPLE_FULL)

#: 数据管理页左键双击条目时的动作（设置页可改）
DOUBLE_CLICK_VIEWER = "viewer"
DOUBLE_CLICK_EDITOR = "editor"
DOUBLE_CLICK_ACTIONS = (DOUBLE_CLICK_VIEWER, DOUBLE_CLICK_EDITOR)


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

    # 布局：页面共用的显示偏好，页面打开时按这里的值恢复上一次的样子
    pageSize = RangeConfigItem("Layout", "Page-Size", 50, RangeValidator(10, 1000))
    showCategoryPanel = ConfigItem("Layout", "Show-Category-Panel", True, BoolValidator())
    showFilterPanel = ConfigItem("Layout", "Show-Filter-Panel", True, BoolValidator())
    expandCategories = ConfigItem("Layout", "Expand-Categories", False, BoolValidator())
    # 分类栏只显示分类；关掉后每个分类下面列出该分类文件夹里的文件
    onlyShowCategories = ConfigItem("Layout", "Only-Show-Categories", True, BoolValidator())
    expandedFilters = ConfigItem("Layout", "Expanded-Filters", [])
    simpleDisplay = OptionsConfigItem(
        "Layout", "Simple-Display", SIMPLE_DEFAULT, OptionsValidator(list(SIMPLE_MODES))
    )
    # 悬停提示：鼠标停在按钮或标题上多久才弹出说明（毫秒，0 表示立刻弹出）
    tooltipDelay = RangeConfigItem("Layout", "Tooltip-Delay", 2000, RangeValidator(0, 10_000))
    # 左键双击：数据管理页双击条目时打开查看器（默认）还是交给编辑器插件
    doubleClickAction = OptionsConfigItem(
        "Layout", "Double-Click-Action", DOUBLE_CLICK_VIEWER, OptionsValidator(list(DOUBLE_CLICK_ACTIONS))
    )

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
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[source]} | {function}:{line} | {message}",
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

    # 下载：这些是「标量」设置项，跟着 QConfig 走；镜像规则是一份结构化列表，
    # 放在清单 `core.download_mirrors` 里（见 `app.core.download.mirror_store`）。
    downloadPath = ConfigItem("Download", "Path", "")
    downloadConcurrent = RangeConfigItem("Download", "Concurrent", 2, RangeValidator(1, 16))
    # 顺序下载：同一时刻只跑一个任务（相当于把并行数锁成 1，但并行数本身保留）
    downloadSequential = ConfigItem("Download", "Sequential", False, BoolValidator())
    downloadTimeout = RangeConfigItem("Download", "Timeout", 15, RangeValidator(5, 600))
    # 每个地址的重试次数（地址之间还会按镜像规则依次回退）
    downloadRetries = RangeConfigItem("Download", "Retries", 2, RangeValidator(0, 5))
    # 全局代理，如 `http://127.0.0.1:7890`；留空表示直连、走系统默认
    downloadProxy = ConfigItem("Download", "Proxy", "")

    # 存档
    keepVersions = RangeConfigItem("Archive", "Keep-Versions", 10, RangeValidator(1, 200))
    pruneMode = OptionsConfigItem(
        "Archive", "Prune-Mode", "count", OptionsValidator(["count", "size", "age", "none"])
    )
    keepSize = RangeConfigItem("Archive", "Keep-Size-MB", 2048, RangeValidator(64, 1_048_576))
    keepDays = RangeConfigItem("Archive", "Keep-Days", 30, RangeValidator(1, 3650))
    # 回档前是否先弹「回档变更」清单确认（关闭后确认点只剩执行本身）
    restorePreview = ConfigItem("Archive", "Restore-Preview", True, BoolValidator())

    # 维护：自动清理无引用内容、残留文件与写残文件（启动后延迟一次 / 建存档后 / 删存档后）
    autoCleanup = ConfigItem("Archive", "Auto-Cleanup", True, BoolValidator())


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


def download_dir() -> Path:
    """下载器的默认落点（「下载管理 → 下载设置」里的下载路径，与配置项同步）。"""
    return paths.resolve_dir(config.downloadPath.value, paths.DEFAULT_DOWNLOAD_DIR)


def db_file() -> Path:
    return paths.DB_FILE


def db_url() -> str:
    return config.dbUrl.value or f"sqlite:///{db_file().as_posix()}"


def _legacy_library_path() -> str:
    """旧版配置里的库文件夹路径（Storage/Library-Path）。"""
    try:
        data = jsonio.loads(paths.CONFIG_FILE.read_bytes())
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
        logger.info("已把库文件夹迁入资源文件夹：%s", destination)
    elif library.is_dir() and library.resolve() != destination.resolve():
        logger.warning("旧库文件夹仍在 %s，未自动迁移到 %s", library, destination)
    config.set(config.resourcePath, str(target))


def release_resource_root() -> None:
    """放行被 ACL 锁上的资源文件夹（启动、重建、搬迁前调用；保护未开启时不动）。

    静态保护下运行期整场放行，只有程序退出时才重新锁定，因此这里不需要配对加锁。
    """
    paths.release_locked_root()


#: 搬迁后旧资源文件夹可能还有被占用的文件（数据库、正在写的日志）删不掉，
#: 把待删路径记在配置目录（不在资源文件夹里，不会跟着搬），下次启动补删。
PENDING_CLEANUP_FILE = paths.CONFIG_DIR / "pending-resource-cleanup.json"


def _remember_pending_cleanup(previous: Path) -> None:
    from .manifest.kit import write_json_atomic  # 与清单机制共用原子写

    try:
        write_json_atomic(PENDING_CLEANUP_FILE, {"path": str(previous)})
    except OSError as exc:
        logger.warning("无法记录待清理的旧资源文件夹 %s：%s", previous, exc)


def cleanup_pending_resource_root() -> None:
    """启动时补删上次搬迁留下的旧资源文件夹（此时占用它的文件已经关闭）。"""
    try:
        payload = jsonio.loads(PENDING_CLEANUP_FILE.read_bytes())
    except (OSError, ValueError):
        return
    previous = str(payload.get("path", "") or "") if isinstance(payload, dict) else ""
    if not previous:
        PENDING_CLEANUP_FILE.unlink(missing_ok=True)
        return
    old = Path(previous)
    try:
        if old.resolve() == resources_root().resolve():
            PENDING_CLEANUP_FILE.unlink(missing_ok=True)
            return
    except OSError:
        pass
    if old.exists():
        try:
            shutil.rmtree(old)
        except OSError as exc:
            logger.warning("旧资源文件夹仍被占用，留着下次启动再清理：%s —— %s", old, exc)
            return
        logger.info("已清理搬迁前的旧资源文件夹：%s", old)
    PENDING_CLEANUP_FILE.unlink(missing_ok=True)


class ResourceRootExists(FileExistsError):
    """目标位置里已经有一个资源文件夹（多半是上次搬到一半留下的）。

    继承 `FileExistsError`：老的调用方和测试仍按「目标已存在」处理；界面拿到它之后可以问
    用户要不要删掉重搬。
    """

    def __init__(self, target: str | Path) -> None:
        self.target = Path(target)
        super().__init__(f"目标位置里已经有一个资源文件夹：{self.target}")


def _copied_ok(source: Path, target: Path) -> bool:
    """复制完粗查一遍：源里有的关键标记，目标里也得有（不逐个文件比对）。"""
    for name in ("data.db", "library"):
        if (source / name).exists() and not (target / name).exists():
            return False
    return True


def set_resource_root(folder: str | Path, *, replace: bool = False) -> Path:
    """把资源文件夹（库数据 + 数据库）迁到 folder 并立即生效。

    顺序刻意做成「先落新位置、再改配置、最后删旧目录」：同一卷上先试原子改名（瞬间完成、
    要么成功要么原样）；跨卷或目录被占用时退回整份复制，复制完粗查一遍关键标记，确认没问题
    才把配置切过去。任何一步失败都会清掉搬迁途中产生的新目录、把数据库引擎恢复到旧位置，
    然后原样抛错——绝不会留下「配置指着新位置、数据还在旧地方」或半份新目录。旧目录里若还
    有被占用的文件（数据库、正在写的日志），删除会失败——那就记下来，下次启动补删。

    目标位置已经有资源文件夹时（多半是上次没搬完留下的）：
    - `replace=False`（默认）抛 `ResourceRootExists`，交给界面去问用户；
    - `replace=True` 先把旧目标整份删掉再搬（数据以当前资源文件夹为准）。
    """
    from ..db import database  # 局部导入，避免与数据库模块循环依赖

    target = paths.resource_root(folder)
    current = resources_root()
    if target == current:
        config.set(config.resourcePath, str(target))
        paths.apply_resource_root(target)
        return target
    if target.exists():
        if not replace:
            logger.info("目标位置已存在资源文件夹，先不动任何东西：%s", target)
            raise ResourceRootExists(target)
        logger.warning("目标位置已存在资源文件夹，按用户选择先删掉再搬：%s", target)
        try:
            shutil.rmtree(target)
        except OSError as exc:
            logger.error("目标位置里那个资源文件夹删不掉：%s —— %s", target, exc)
            raise OSError(f"目标位置里那个资源文件夹删不掉（{exc}）：{target}") from exc
    database.dispose_engine()
    release_resource_root()  # 旧位置可能锁着，搬迁前先放行
    renamed = False
    created = False
    if current.exists():
        paths.make_dir(target.parent)
        try:
            current.rename(target)
            renamed = True
        except OSError as exc:
            logger.info("资源文件夹不能直接改名（%s），改为整份复制：%s → %s", exc, current, target)
            try:
                shutil.copytree(current, target)
                created = True
            except BaseException as copy_error:  # 包括 Ctrl-C / 取消：都把半份新目录清掉
                logger.error("复制资源文件夹失败：%s → %s：%s", current, target, copy_error)
                shutil.rmtree(target, ignore_errors=True)
                database.init_db()  # 让引擎恢复到旧位置，程序还能继续用
                raise OSError(f"迁移失败，原资源文件夹保持原样：{copy_error}") from copy_error
            if not _copied_ok(current, target):
                logger.error("复制过去的内容不完整，原资源文件夹保持原样：%s", target)
                shutil.rmtree(target, ignore_errors=True)
                database.init_db()
                raise OSError(f"迁移失败（复制过去的内容不完整），原资源文件夹保持原样：{target}")
    try:
        config.set(config.resourcePath, str(target))
        paths.apply_resource_root(target)
        paths.ensure_dirs()
        database.init_db()
    except BaseException:
        logger.exception("把资源文件夹切到新位置失败，正在恢复原状：%s → %s", target, current)
        try:
            if renamed and target.is_dir() and not current.exists():
                target.rename(current)  # 改过名就改回去
            elif created:
                shutil.rmtree(target, ignore_errors=True)  # 复制来的半份不要了，原目录还在
        except OSError as undo_error:
            logger.error("恢复原状也失败了，数据可能在 %s：%s", target, undo_error)
        config.set(config.resourcePath, str(current))
        paths.apply_resource_root(current)
        paths.ensure_dirs()
        database.init_db()
        raise
    _rebase_library_paths(current / "library", target / "library")
    if current.exists() and not renamed:
        try:
            shutil.rmtree(current)
        except OSError as exc:
            logger.warning("旧资源文件夹暂时删不掉（文件被占用），已记下待下次启动清理：%s —— %s", current, exc)
            _remember_pending_cleanup(current)
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


def _migrate_legacy_simple_display() -> None:
    """一次性迁移：旧版把「简化显示」存成布尔值，改成三挡位后按 不简化 / 完全简化 换算。

    只在启动读配置之前跑一次；换算结果直接落到配置文件，之后布尔值不再有特殊含义。
    """
    path = paths.CONFIG_FILE
    if not path.is_file():
        return
    try:
        payload = jsonio.loads(path.read_bytes())
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    section = payload.get("Layout")
    if not isinstance(section, dict) or not isinstance(section.get("Simple-Display"), bool):
        return
    from .manifest.kit import write_json_atomic  # 与清单机制共用原子写

    section["Simple-Display"] = SIMPLE_FULL if section["Simple-Display"] else SIMPLE_NONE
    write_json_atomic(path, payload)
    logging.getLogger(__name__).info("已把「简化显示」的旧布尔值换算成挡位：%s", section["Simple-Display"])


def load_config() -> None:
    paths.migrate_app_dirs()
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_simple_display()
    qconfig.load(str(paths.CONFIG_FILE), config)
    # 上一次退出时锁上的资源文件夹要先放行，否则迁移与建目录都会被拒绝
    release_resource_root()
    _migrate_legacy_resource_path()
    paths.apply_resource_root(resources_root())
    paths.migrate_legacy_dir(paths.DATA_DIR)
    paths.ensure_dirs()
    cleanup_pending_resource_root()


load_config()
