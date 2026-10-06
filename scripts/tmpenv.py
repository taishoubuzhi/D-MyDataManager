"""脚本与测试共用的隔离运行环境：统一临时目录 `.tmp/` 与运行期路径重定向。

约定（规范全文见 `docs/SCRIPTS.md` 与 `docs/TESTS.md`）：

- 任何脚本 / 测试要落盘临时文件时，一律写进**所在顶层目录**下的 `.tmp/`：
  `scripts/` 下的脚本用 `scripts_tmp()`（→ `scripts/.tmp/`），`tests/` 下的测试用 `tests_tmp()`（→ `tests/.tmp/`）；
  不使用系统临时目录，也不在仓库根或被测目录里散落临时物；
- `.tmp/` 在运行期间创建，进程退出时由 `atexit` 整体删除；设置 `DM_KEEP_TMP=1` 可保留以便排查；
- `.tmp/` 已在 `.gitignore` 中，任何时候都不应进入提交与打包载荷。

`redirect_paths()` / `reset_config()` / `reset_runtime_dirs()` 把程序真实使用的
`.resources/`、`.configs/`、`.logs/` 整体改指到临时目录，脚本与测试因此不会碰到真实数据。
"""

from __future__ import annotations

import atexit
import itertools
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 两个顶层目录；临时物分别落在它们的 `.tmp/` 下（见 `tests_tmp()` / `scripts_tmp()`）
TESTS_DIR = ROOT / "tests"
SCRIPTS_DIR = ROOT / "scripts"
for _path in (ROOT, ROOT / "src", SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

#: 设置 `DM_KEEP_TMP=1` 时保留 `.tmp/`（排查失败用），否则进程退出时整体删除。
KEEP_TMP = bool(os.environ.get("DM_KEEP_TMP"))

_TMP_ROOTS: list[Path] = []
_COUNTER = itertools.count(1)


def temp_root(base: Path, *parts: str, keep: bool = False) -> Path:
    """返回 `<base>/.tmp[/parts...]` 并确保它存在；进程退出时自动删除这个 `.tmp/`。

    一般不用直接调它——按所在目录选 `tests_tmp()` / `scripts_tmp()` 即可。
    `keep=True`（或环境变量 `DM_KEEP_TMP=1`）时不登记清理，留给人工排查。
    """
    root = Path(base) / ".tmp"
    target = root.joinpath(*parts) if parts else root
    target.mkdir(parents=True, exist_ok=True)
    if not (keep or KEEP_TMP) and root not in _TMP_ROOTS:
        _TMP_ROOTS.append(root)
        atexit.register(_cleanup_tmp)
    return target


def tests_tmp(*parts: str, keep: bool = False) -> Path:
    """`tests/.tmp[/parts...]`：`tests/` 下所有测试的临时物统一落在这里。"""
    return temp_root(TESTS_DIR, *parts, keep=keep)


def scripts_tmp(*parts: str, keep: bool = False) -> Path:
    """`scripts/.tmp[/parts...]`：`scripts/` 下所有脚本的临时物统一落在这里。"""
    return temp_root(SCRIPTS_DIR, *parts, keep=keep)


def _cleanup_tmp() -> None:
    """退出时整体删除本次创建的 `.tmp/`；清理失败不影响退出码。"""
    while _TMP_ROOTS:
        shutil.rmtree(_TMP_ROOTS.pop(), ignore_errors=True)


class TempDir:
    """`<root>/.tmp/` 下的一个独立临时目录（名字带 pid 与序号，并发也不重名）。"""

    def __init__(self, root: Path, prefix: str = "case", *, keep: bool = False) -> None:
        parent = temp_root(root, keep=keep)
        self.name = str(parent / f"{prefix}-{os.getpid()}-{next(_COUNTER)}")
        Path(self.name).mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return Path(self.name)

    def cleanup(self) -> None:
        """立刻删除这个临时目录。"""
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()


def redirect_paths(root: Path) -> None:
    """把 paths 里的运行期目录整体指向临时根目录，返回资源的库文件句柄。"""
    import app.core.config  # noqa: F401 - 首次导入会执行 load_config()，必须发生在重定向之前
    from app.core.runtime import paths

    data = paths.apply_resource_root(root / paths.RESOURCE_ROOT_NAME)
    paths.LOG_DIR = root / paths.LOG_DIR_NAME
    paths.DEFAULT_EXPORT_DIR = root / "exports"
    paths.CONFIG_DIR = root / paths.CONFIG_DIR_NAME
    paths.CONFIG_FILE = paths.CONFIG_DIR / "config.json"
    paths.PLUGIN_DIR = root / "plugins"
    paths.PLUGIN_STATE_FILE = paths.CONFIG_DIR / "plugins.json"
    paths.VIEWER_RULES_FILE = paths.CONFIG_DIR / "viewers.json"
    paths.LEGACY_VIEWER_RULES_FILE = paths.CONFIG_DIR / "open_with.json"
    paths.SESSION_FILE = paths.CONFIG_DIR / "session.json"
    paths.ensure_dirs()
    _check_redirected(root)
    return data


def _check_redirected(root: Path) -> None:
    """确认重定向真的生效；否则脚本 / 测试会写进真实数据，直接拒绝继续。"""
    from app.core.runtime import paths

    expected = Path(root) / paths.RESOURCE_ROOT_NAME
    if paths.DATA_DIR != expected:
        raise RuntimeError(f"资源目录重定向失败（{paths.DATA_DIR} ≠ {expected}），拒绝在真实数据上继续")


def reset_config(root: Path) -> None:
    """把配置的保存目标改到临时目录，并把影响用例的配置项恢复为默认值。"""
    from qfluentwidgets import qconfig

    from app.core.config import config
    from app.core.runtime import paths

    target = root / paths.CONFIG_DIR_NAME / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    qconfig.load(str(target), config)
    # app.core.config 的 load_config() 会把资源根重新指回真实目录，这里再指回临时根一次
    paths.apply_resource_root(root / paths.RESOURCE_ROOT_NAME)
    config.set(config.dbUrl, "")
    config.set(config.dbEcho, False)
    config.set(config.resourcePath, str(paths.DATA_DIR))
    config.set(config.resourceProtected, False)
    config.set(config.hiddenProtected, False)
    config.set(config.exportPath, "")
    config.set(config.currentUserId, 0)
    config.set(config.nameByTime, False)
    config.set(config.duplicatePolicy, "rename")
    config.set(config.coverSize, 256)
    config.set(config.pruneMode, "none")
    config.set(config.keepVersions, 10)
    config.set(config.keepSize, 2048)
    config.set(config.keepDays, 30)
    config.set(config.pageSize, 50)
    config.set(config.showCategoryPanel, True)
    config.set(config.showFilterPanel, True)
    config.set(config.expandCategories, False)
    config.set(config.expandedFilters, [])
    config.set(config.simpleDisplay, "none")
    config.set(config.tooltipDelay, 2000)
    _check_redirected(root)


def reset_runtime_dirs() -> None:
    """清空库文件夹、内容仓库、封面与数据库文件，让每个用例都从零开始。"""
    from app.core.runtime import paths

    for folder in (
        paths.DEFAULT_LIBRARY_DIR,
        paths.LEGACY_STORE_DIR,
        paths.LEGACY_COVER_DIR,
        paths.DEFAULT_EXPORT_DIR,
    ):
        shutil.rmtree(folder, ignore_errors=True)
    for name in ("data.db", "data.db-wal", "data.db-shm"):
        (paths.DATA_DIR / name).unlink(missing_ok=True)
    paths.ensure_dirs()
