"""模型工具库的路径：全部落在资源文件夹 `.resources/models/` 下。

权重、下载临时文件、日志、运行环境都放这里；插件自己的目录只放只读模板，
因为插件覆盖安装时插件目录会被整个删掉重建。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.sdk import storage

__all__ = [
    "clear_model_logs",
    "download_dir",
    "local_dir",
    "local_root",
    "logs_dir",
    "model_log_file",
    "model_log_files",
    "models_root",
    "registry_file",
    "runtime_root",
    "settings_file",
    "slug_dir_name",
]

MODELS_DIR_NAME = "models"


def models_root() -> Path:
    """模型根目录（`.resources/models`，随「资源文件夹」设置变化）。"""
    return storage.data_dir(MODELS_DIR_NAME)


def registry_file() -> Path:
    """模型登记表。"""
    return models_root() / "registry.json"


def local_root() -> Path:
    """本地模型权重根目录。"""
    target = models_root() / "local"
    target.mkdir(parents=True, exist_ok=True)
    return target


def slug_dir_name(model_id: str) -> str:
    """`local/<slug>` 取末段当目录名，避免越界；没有名字时返回空串（不编一个假名字）。"""
    text = str(model_id or "").replace("\\", "/").strip()
    return Path(text).name if text else ""


def local_dir(model_id: str) -> Path:
    """某个本地模型的权重目录（按需创建）。

    空 id 直接返回权重根目录本身：以前会退回 `local/model`，凭空造一个空目录，
    还会被「清理未使用权重」当成垃圾列出来。
    """
    slug = slug_dir_name(model_id)
    if not slug:
        return local_root()
    target = local_root() / slug
    target.mkdir(parents=True, exist_ok=True)
    return target


def download_dir(model_id: str = "") -> Path:
    """下载临时目录（`.part` 与断点信息）。"""
    target = models_root() / "download"
    if model_id:
        target = target / slug_dir_name(model_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def logs_dir() -> Path:
    """模型与 worker 日志目录。"""
    target = models_root() / "logs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def runtime_root() -> Path:
    """运行环境根目录（每个 profile 一个 venv）。"""
    target = models_root() / "runtime"
    target.mkdir(parents=True, exist_ok=True)
    return target


def settings_file() -> Path:
    """插件设置文件（`.configs/models.json`）。"""
    return storage.config_file("models.json")

_LEGACY_LOG = re.compile(r"^(?P<slug>.+)-(?P<stamp>\d{8}-\d{6})\.log$")


def _log_slug(model_id: str) -> str:
    """日志文件名里的 slug（`local/qwen` → `local-qwen`）：和卡片、登记表里的叫法一致。"""
    from .record import slugify  # 延迟导入：record 依赖 paths，顶层 import 会成环

    return slugify(str(model_id or "worker"))


def model_log_file(model_id: str) -> Path:
    """一条模型的运行日志（一个模型只这一个文件，每次运行重写）。"""
    return logs_dir() / f"{_log_slug(model_id)}.log"


def model_log_files(model_id: str) -> list[Path]:
    """这条模型现在有的日志，最新的排在最后。

    新版本一个模型只有一个文件；老版本按时间戳攒过一堆（`<slug>-20260101-010101.log`），
    这里也一并认出来，免得界面上看不到、删模型时又漏掉。
    """
    slug = _log_slug(model_id)
    stable = logs_dir() / f"{slug}.log"
    legacy: list[Path] = []
    for path in logs_dir().glob(f"{slug}-*.log"):
        match = _LEGACY_LOG.match(path.name)
        if match and match.group("slug") == slug and path.is_file():
            legacy.append(path)
    found = sorted(legacy)
    if stable.is_file():
        found.append(stable)
    return found


def clear_model_logs(model_id: str) -> list[Path]:
    """删掉一条模型的日志（新单文件 + 老版本的时间戳文件），返回删掉的文件。"""
    removed: list[Path] = []
    for path in model_log_files(model_id):
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed
