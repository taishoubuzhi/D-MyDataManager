"""字节数的可读格式化（core 与 SDK 共用一份实现，避免两边算出不一样的文案）。"""

from __future__ import annotations

__all__ = ["human_size"]


def human_size(value: float) -> str:
    """把字节数写成 1.2 MB 这样的可读形式。"""
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
