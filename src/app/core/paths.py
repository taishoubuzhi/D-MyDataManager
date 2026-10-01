"""应用路径解析。

所有目录都由项目根推导，代码里不再出现硬编码的绝对路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

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

# 运行期数据
DATA_DIR = ROOT / "resources"
DB_FILE = DATA_DIR / "data.db"
LOG_DIR = ROOT / "logs"
DEFAULT_EXPORT_DIR = ROOT / "exports"
DEFAULT_LIBRARY_DIR = DATA_DIR / "library"

# 库文件夹结构（单一库）：<库>/全局/ 放全局资源，<库>/<用户名>/ 放该用户的数据
GLOBAL_DIR_NAME = "全局"
LIBRARY_STORE_DIRNAME = "store"
LIBRARY_COVER_DIRNAME = "covers"
LIBRARY_BACKUP_DIRNAME = "backups"
LIBRARY_META_DIR = ".datamanager"
UNASSIGNED_DIR_NAME = "未归属"
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


def ensure_dirs() -> None:
    """创建运行期需要的目录（幂等）。"""
    directories = [DATA_DIR, LOG_DIR, CONFIG_DIR, DEFAULT_LIBRARY_DIR, *global_subdirs(DEFAULT_LIBRARY_DIR)]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def resolve_dir(value: str | Path | None, fallback: Path) -> Path:
    """把配置中的路径解析为绝对路径，空值时回退到默认目录。"""
    if not value:
        return fallback
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path
