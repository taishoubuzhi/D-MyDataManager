"""应用路径解析。

所有目录都由项目根推导，代码里不再出现硬编码的绝对路径。
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
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
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 运行期数据：资源文件夹（默认 <根>/.resources，隐藏目录；可在设置里改到别处）
RESOURCE_ROOT_NAME = ".resources"
LEGACY_RESOURCE_DIR_NAME = "resources"
DEFAULT_RESOURCE_DIR = ROOT / RESOURCE_ROOT_NAME
LOG_DIR = ROOT / "logs"
DEFAULT_EXPORT_DIR = ROOT / "exports"

# 资源文件夹内部结构：数据库、库文件夹都由 apply_resource_root() 按配置重算
DATA_DIR = DEFAULT_RESOURCE_DIR
DB_FILE = DATA_DIR / "data.db"
DEFAULT_LIBRARY_DIR = DATA_DIR / "library"

# 插件：第三方插件放在项目根（打包后为可执行文件同级），随程序分发的内置插件在代码里
PLUGIN_DIR = ROOT / "plugins"
PLUGIN_STATE_FILE = CONFIG_DIR / "plugins.json"
# 打开方式规则：扩展名 → 内置 / 继承 / 自定义
OPEN_WITH_FILE = CONFIG_DIR / "open_with.json"
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


def release_locked_root() -> None:
    """按设置放行被 ACL 锁上的资源文件夹（保护未开启时什么都不做）。

    上一次会话退出时会锁上资源文件夹（拒绝 Everyone 读写），此时按路径的
    新建/删除都会被拒绝，所以动文件系统前先放行；调用方负责之后恢复锁定
    （`privacy.guard()` / `privacy.lock()`）。
    """
    from . import acl  # 延迟导入：acl 只依赖标准库
    from .config import config, resources_root  # 延迟导入，避免 core 内部循环

    if not config.resourceProtected.value or not acl.is_supported():
        return
    for target in dict.fromkeys((resources_root(), DATA_DIR, DEFAULT_RESOURCE_DIR)):
        acl.unlock(target)


@contextmanager
def resource_access():
    """访问资源文件夹：被锁着时瞬时放行，退出后立即恢复锁定。"""
    from ..services.privacy_service import privacy  # 延迟导入，避免循环依赖

    with privacy.guard():
        yield


def make_dir(directory: str | Path) -> Path:
    """创建目录：资源文件夹被锁上时先瞬时放行。

    被锁上时 `mkdir(exist_ok=True)` 会因为无法确认「目录已存在」抛
    `FileExistsError: [WinError 183]`，所以统一走这里。
    """
    target = Path(directory)
    with resource_access():
        target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_dirs() -> None:
    """创建运行期需要的目录（幂等）。"""
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
        _logger.warning("无法把 {} 迁移到 {}：{}", legacy, destination, exc)
        return None
    return destination
