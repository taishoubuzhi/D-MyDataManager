"""插件可见的配置存储：读写程序配置目录（.configs/）里的 JSON 文件。

插件不能直接 import 程序内部模块（app.core / app.services），但插件自己的设置
（例如「用哪个查看器打开某种格式」）应当落在程序配置目录里，于是由 SDK 提供这层封装：

    from app.sdk import storage

    path = storage.config_file("viewers.json")
    payload = storage.read_json(path, {})
    storage.write_json(path, payload)

写失败只记日志并返回 False：插件不该因为一次落盘失败就崩掉主界面。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = ["config_dir", "config_file", "read_json", "write_json"]


def config_dir() -> Path:
    """程序配置目录（.configs）。"""
    from ..core import paths

    return paths.CONFIG_DIR


def config_file(name: str) -> Path:
    """配置目录下的一个文件名（只取末段文件名，避免越界写到别处）。"""
    return config_dir() / Path(str(name)).name


def read_json(path: str | Path, default: Any = None) -> Any:
    """读一个 JSON 文件；不存在或损坏时返回 default。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: str | Path, payload: Any) -> bool:
    """把 payload 写成 JSON 文件；成功返回 True。"""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("写入配置失败 {}：{}", target, exc)
        return False
