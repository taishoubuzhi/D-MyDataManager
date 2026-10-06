"""JSON 读写：优先 `orjson`（快 3–10 倍、对 bytes 友好），缺依赖时退回标准库。

`dumps()` 一律返回 `str`（orjson 返回 bytes，这里解码掉），所以调用方不需要为了
换实现改代码；`ensure_ascii` 只对标准库兜底有意义——orjson 永远输出 UTF-8 原文。
异常类型与标准库保持兼容：`orjson.JSONDecodeError` 是 `ValueError` 子类、
`orjson.JSONEncodeError` 是 `TypeError` 子类，`except (ValueError, TypeError)` 照样管用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

try:  # 推荐依赖（批 K 写进 requirements）；缺失不影响功能
    import orjson as _orjson
except ImportError:  # pragma: no cover - 取决于运行环境
    _orjson = None

import json as _json

__all__ = ["dump_bytes", "dumps", "loads", "orjson_available", "read_json", "write_json"]


def orjson_available() -> bool:
    """当前环境有没有 orjson（没有就走标准库，语义一致）。"""
    return _orjson is not None


def loads(payload: str | bytes | bytearray) -> Any:
    """解析 JSON 文本 / 字节。"""
    if _orjson is not None:
        return _orjson.loads(payload)
    return _json.loads(payload)


def dumps(
    payload: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    default=None,
) -> str:
    """序列化成 JSON 文本（UTF-8 原文，不转义非 ASCII）。"""
    return dump_bytes(
        payload, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii, default=default
    ).decode("utf-8")


def dump_bytes(
    payload: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    default=None,
) -> bytes:
    """序列化成 UTF-8 JSON 字节（写文件时可直接落盘）。"""
    if _orjson is not None:
        option = 0
        if indent:
            option |= _orjson.OPT_INDENT_2
        if sort_keys:
            option |= _orjson.OPT_SORT_KEYS
        return _orjson.dumps(payload, option=option, default=default)
    text = _json.dumps(
        payload, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys, default=default
    )
    return text.encode("utf-8")


def read_json(path: str | Path, default: Any = None) -> Any:
    """读一个 JSON 文件；不存在或损坏时返回 `default`（不抛异常）。"""
    try:
        return loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return default


def write_json(path: str | Path, payload: Any, *, indent: int | None = 2) -> bool:
    """写一个 JSON 文件；成功返回 True，失败只记日志并返回 False。"""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dump_bytes(payload, indent=indent))
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("写入 JSON 失败 {}：{}", target, exc)
        return False
