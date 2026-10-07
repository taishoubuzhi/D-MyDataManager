"""插件可见的配置存储：读写程序配置目录（.configs/）里的 JSON 文件。

插件不能直接 import 程序内部模块（app.core / app.services），但插件自己的设置
（例如「用哪个查看器打开某种格式」）应当落在程序配置目录里，于是由 SDK 提供这层封装：

    from app.sdk import storage

    path = storage.config_file("viewers.json")
    payload = storage.read_json(path, {})
    storage.write_json(path, payload)

写失败只记日志并返回 False：插件不该因为一次落盘失败就崩掉主界面。
底层 JSON 走 `app.core.runtime.jsonio`（优先 orjson，缺依赖自动退回标准库）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "app_root",
    "config_dir",
    "config_file",
    "data_dir",
    "dumps",
    "loads",
    "read_json",
    "resources_dir",
    "write_json",
]


def _jsonio():
    """底层 JSON 实现（延迟导入，避免 SDK 在 import 期就依赖 core）。"""
    from ..core.runtime import jsonio

    return jsonio


def loads(payload: str | bytes) -> Any:
    """解析 JSON 文本 / 字节（优先 orjson）。"""
    return _jsonio().loads(payload)


def dumps(payload: Any, *, indent: int | None = None) -> str:
    """序列化成 JSON 文本（默认紧凑单行；写文件才需要缩进）。

    进程间协议（worker 的 JSON-Lines、SSE 分段）必须单行，所以这里默认不缩进——
    需要好看的文件内容请用 `write_json()`。
    """
    return _jsonio().dumps(payload, indent=indent)


def app_root() -> Path:
    """程序文件根目录（放程序自己那一堆文件的地方，资源文件夹之外）。

    改「资源文件夹」不会动它，所以放「跟着程序走、不该被资源迁移牵连」的大文件时用它。
    """
    from ..core.runtime import paths

    return paths.ROOT


def config_dir() -> Path:
    """程序配置目录（.configs）。"""
    from ..core.runtime import paths

    return paths.CONFIG_DIR


def config_file(name: str) -> Path:
    """配置目录下的一个文件名（只取末段文件名，避免越界写到别处）。"""
    return config_dir() / Path(str(name)).name


def resources_dir() -> Path:
    """资源文件夹根目录（`.resources`，用户可在设置里改到别处）。

    插件需要放大文件（模型权重、缓存等）时用它，别写进插件自己的目录：
    插件目录在覆盖安装时会被整个删掉重建。
    """
    from ..core.runtime import paths

    return paths.DATA_DIR


def data_dir(name: str) -> Path:
    """资源文件夹下的一个子目录（按需创建）；只取目录名，避免越界写到别处。"""
    target = resources_dir() / Path(str(name)).name
    target.mkdir(parents=True, exist_ok=True)
    return target


def read_json(path: str | Path, default: Any = None) -> Any:
    """读一个 JSON 文件；不存在或损坏时返回 default。"""
    return _jsonio().read_json(path, default)


def write_json(path: str | Path, payload: Any) -> bool:
    """把 payload 写成 JSON 文件；成功返回 True。"""
    ok = _jsonio().write_json(path, payload)
    if not ok:
        logger.warning("写入配置失败：{}", path)
    return ok
