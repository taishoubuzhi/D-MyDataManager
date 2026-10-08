"""应用路径解析。

所有目录都由项目根推导，代码里不再出现硬编码的绝对路径。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

_MARKERS = ("CLAUDE.md", "TODO.md")


def _find_project_root(start: Path) -> Path:
    for parent in start.parents:
        if (parent / "src").is_dir() and any((parent / m).exists() for m in _MARKERS):
            return parent
    return start.parents[3]


if getattr(sys, "frozen", False):  # 打包后以可执行文件所在目录为准
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = _find_project_root(Path(__file__).resolve())

APP_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = APP_DIR.parent

# 静态资源（随代码分发）
RESOURCE_DIR = APP_DIR / "resource"
IMAGE_DIR = RESOURCE_DIR / "images"
# 配置写在项目根目录（不随代码分发，升级 / 拉取代码时不会被覆盖）
CONFIG_DIR_NAME = ".configs"
LOG_DIR_NAME = ".logs"
# 旧版目录名：启动时由 migrate_app_dirs() 并进新目录
LEGACY_CONFIG_DIR_NAME = "config"
LEGACY_LOG_DIR_NAME = "logs"
CONFIG_DIR = ROOT / CONFIG_DIR_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

# 运行期数据：资源文件夹（默认 <根>/.resources，隐藏目录；可在设置里改到别处）
RESOURCE_ROOT_NAME = ".resources"
LEGACY_RESOURCE_DIR_NAME = "resources"
DEFAULT_RESOURCE_DIR = ROOT / RESOURCE_ROOT_NAME
LOG_DIR = ROOT / LOG_DIR_NAME
DEFAULT_EXPORT_DIR = ROOT / "exports"
#: 下载器的默认落点（用户没在「下载管理 → 下载设置」里指定下载路径时用这个）。
#: 放在程序目录下而不是资源文件夹里：资源文件夹默认是 ACL 保护 + 隐藏目录，
#: 把下载塞进去既容易被锁住，也让用户找不着自己下的东西。
DEFAULT_DOWNLOAD_DIR = ROOT / "downloads"

# 资源文件夹内部结构：数据库、库文件夹都由 apply_resource_root() 按配置重算
DATA_DIR = DEFAULT_RESOURCE_DIR
DB_FILE = DATA_DIR / "data.db"
DEFAULT_LIBRARY_DIR = DATA_DIR / "library"

# 插件：第三方插件放在项目根（打包后为可执行文件同级），随程序分发的内置插件在代码里
PLUGIN_DIR = ROOT / "plugins"
PLUGIN_STATE_FILE = CONFIG_DIR / "plugins.json"
# 查看器规则：扩展名 → 内置查看器 / 继承系统默认 / 自定义程序（由 builtin.lib.viewer 插件读写）
VIEWER_RULES_FILE = CONFIG_DIR / "viewers.json"
# 查看器规则的旧文件名：首次载入时由插件自动迁移到 VIEWER_RULES_FILE
LEGACY_VIEWER_RULES_FILE = CONFIG_DIR / "open_with.json"
# 会话标记：程序启动时写下，正常退出时删除；残留下来的就是「上次没正常退出」
SESSION_FILE = CONFIG_DIR / "session.json"
PLUGIN_MANIFEST = "plugin.json"

# 库文件夹结构（单一库）：<库>/全局/ 放全局资源，<库>/<用户名>/ 放该用户的数据
GLOBAL_DIR_NAME = "全局"
LIBRARY_STORE_DIRNAME = "store"
LIBRARY_COVER_DIRNAME = "covers"
LIBRARY_BACKUP_DIRNAME = "backups"
LIBRARY_META_DIR = ".datamanager"
UNASSIGNED_DIR_NAME = "未归属"
# 隐藏数据的物理存放目录：<分类目录>/.hiddens/
HIDDEN_DIR_NAME = ".hiddens"
# 资源文件夹下不做 ACL 保护的子目录：只有升级前留下来的旧模型目录 `.resources/models`
# 会落在这里（新版本模型在程序目录下的 `.models`，本来就不在资源文件夹里；旧位置第一次
# 用到模型目录时会被整体搬走，搬不动才会留着），而它底下
# 有运行环境 venv 那种十几万个文件，锁它会让 Windows 把 ACE 传播到整棵子树，
# 退出 / 启动都要好几分钟
UNPROTECTED_DIRS = ("models",)
LAYOUT_VERSION = 2
LAYOUT_MARKER_FILE = "layout-2.json"

# 旧版布局：仓库与封面直接放在 resources/ 下，数据平铺在库根目录；仅用于迁移与清理
LEGACY_STORE_DIR = DATA_DIR / "store"
LEGACY_COVER_DIR = DATA_DIR / "covers"


def global_subdirs(library_root: Path) -> tuple[Path, ...]:
    """库内「全局」文件夹及其子目录（仓库 / 封面 / 备份 / 元数据）。"""
    base = Path(library_root) / GLOBAL_DIR_NAME
    return (
        base,
        base / LIBRARY_STORE_DIRNAME,
        base / LIBRARY_COVER_DIRNAME,
        base / LIBRARY_BACKUP_DIRNAME,
        base / LIBRARY_META_DIR,
    )


#: 本进程是否已经放行过资源文件夹（静态保护：运行期整场放行，只需放行一次）
_released = False


def release_locked_root() -> None:
    """放行被 ACL 锁上的资源文件夹（保护未开启时什么都不做）。

    上一次会话退出时会锁上资源文件夹，此时按路径的 mkdir / 新建都会被拒绝，
    所以动文件系统前先放行。退出时用的是浅层锁（拒绝项挂在根与各直接子项上，见
    `acl.lock_tree`），所以这里也走 `acl.unlock_tree()` 连子项一起摘掉；名单里的
    `models/` 从来没被锁过，放行时同样跳过（对它执行 icacls 反而会触发全子树 ACE 传播）。
    静态保护模型下运行期不再加锁，因此同一进程里只真正放行一次（否则 `ensure_dirs()`
    会反复调用 icacls，启动要多花好几秒）。
    """
    global _released
    if _released:
        return
    from . import acl  # 延迟导入：acl 只依赖标准库
    from ..config import config, resources_root  # 延迟导入，避免 core 内部循环

    if not config.resourceProtected.value or not acl.is_supported():
        return
    for target in dict.fromkeys((resources_root(), DATA_DIR, DEFAULT_RESOURCE_DIR)):
        acl.unlock_tree(target, skip=UNPROTECTED_DIRS)
    _released = True


def make_dir(directory: str | Path) -> Path:
    """创建目录（幂等）：资源文件夹被锁上时先放行，失败只记日志不抛出。

    被锁上时 `mkdir(exist_ok=True)` 会因为无法确认「目录已存在」抛
    `FileExistsError: [WinError 183]`，所以统一走这里；真的建不出来时也不能
    让程序起不来——后面真正需要这个目录的操作会自己报错。
    """
    target = Path(directory)
    release_locked_root()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("创建目录失败：%s：%s", target, exc)
    return target


def ensure_dirs() -> None:
    """创建运行期需要的目录（幂等）。"""
    release_locked_root()
    directories = [
        DATA_DIR,
        LOG_DIR,
        CONFIG_DIR,
        DEFAULT_LIBRARY_DIR,
        PLUGIN_DIR,
        *global_subdirs(DEFAULT_LIBRARY_DIR),
    ]
    for directory in directories:
        make_dir(directory)


def resolve_dir(value: str | Path | None, fallback: Path) -> Path:
    """把配置中的路径解析为绝对路径，空值时回退到默认目录。"""
    if not value:
        return fallback
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def resource_root(value: str | Path | None = None) -> Path:
    """资源文件夹根目录。

    配置值本身就是 .resources 时直接用它；否则在其下再建一层 .resources，
    保证资源文件夹始终是隐藏目录。
    """
    path = resolve_dir(value, DEFAULT_RESOURCE_DIR)
    if path.name == RESOURCE_ROOT_NAME:
        return path
    return path / RESOURCE_ROOT_NAME


#: Windows 单条路径上限（260 字符）；目录本身超过「上限 - 60」就该提醒用户，
#: 因为真正的上限算的是整条路径（目录 + 后面的文件名）。
WINDOWS_PATH_LIMIT = 260
LONG_DIR_WARN_LENGTH = WINDOWS_PATH_LIMIT - 60


def long_path_hint(directory: str | Path) -> str:
    """目录路径过长时的提示文案（不长时返回空串）。

    新建分类会往下再建一层目录，层数一多就会顶到 Windows 的 260 字符上限，
    最后表现为「文件写不进去」。这里只给提示，不阻止操作。
    """
    target = Path(directory)
    length = len(str(target))
    if length < LONG_DIR_WARN_LENGTH:
        return ""
    return (
        f"这个目录已经 {length} 个字符，接近 Windows 单条路径 {WINDOWS_PATH_LIMIT} 字符的上限，"
        "再往里放文件就可能写不进去。建议把「资源文件夹」或分类层级改浅一些。"
    )


def apply_resource_root(root: Path) -> Path:
    """切换资源文件夹，并重算依赖它的模块级路径。"""
    global DATA_DIR, DB_FILE, DEFAULT_LIBRARY_DIR, LEGACY_STORE_DIR, LEGACY_COVER_DIR
    root = Path(root)
    DATA_DIR = root
    DB_FILE = root / "data.db"
    DEFAULT_LIBRARY_DIR = root / "library"
    LEGACY_STORE_DIR = root / "store"
    LEGACY_COVER_DIR = root / "covers"
    return root


def migrate_app_dirs() -> None:
    """把旧版的 config/、logs/ 合并进 .configs/、.logs/，再删掉空下来的旧目录。

    两个目录都在项目根（打包后是可执行文件同级）。同名条目以新目录为准（不覆盖），
    迁移失败只记日志，下次启动再试，不阻断启动。
    """
    pairs = (
        (LEGACY_CONFIG_DIR_NAME, CONFIG_DIR_NAME),
        (LEGACY_LOG_DIR_NAME, LOG_DIR_NAME),
    )
    for legacy_name, new_name in pairs:
        legacy = ROOT / legacy_name
        destination = ROOT / new_name
        if not legacy.is_dir():
            continue
        try:
            make_dir(destination)
            for child in sorted(legacy.iterdir(), key=lambda item: item.name):
                target = destination / child.name
                if not target.exists():
                    child.rename(target)
            _logger.info("已把运行期目录 %s 并入 %s", legacy.name, destination.name)
        except OSError as exc:
            _logger.warning("无法把 %s 迁移到 %s：%s", legacy, destination, exc)
            continue
        try:
            legacy.rmdir()
        except OSError:
            pass  # 还有没搬走的旧条目（与新目录重名），留给用户自己处理


def migrate_legacy_dir(target: Path | None = None) -> Path | None:
    """把旧版 resources/ 原地改名为 .resources（目标已存在或旧目录不存在时不做）。"""
    destination = Path(target) if target is not None else DEFAULT_RESOURCE_DIR
    legacy = ROOT / LEGACY_RESOURCE_DIR_NAME
    if destination == legacy or destination.exists() or not legacy.is_dir():
        return None
    try:
        make_dir(destination.parent)
        legacy.rename(destination)
    except OSError as exc:  # 跨盘 / 权限不足时不阻断启动
        _logger.warning("无法把 %s 迁移到 %s：%s", legacy, destination, exc)
        return None
    return destination
