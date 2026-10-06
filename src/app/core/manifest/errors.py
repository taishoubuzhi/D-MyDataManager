"""清单机制的异常。

都继承 `ManifestError`，插件侧可以一次捕获，也可以按类型分别处理。
"""

from __future__ import annotations

__all__ = [
    "ManifestBackupError",
    "ManifestError",
    "ManifestFormatError",
    "ManifestNotFoundError",
    "ManifestValidationError",
]


class ManifestError(Exception):
    """清单操作的基类异常。"""


class ManifestNotFoundError(ManifestError):
    """清单 id 没登记，或登记的文件不存在。"""


class ManifestFormatError(ManifestError):
    """文件内容不符合清单协议（必须项缺失、items 不是数组、键重复等）。"""


class ManifestValidationError(ManifestError):
    """内容通过了格式解析，但没过 schema 校验。"""


class ManifestBackupError(ManifestError):
    """备份相关的失败（没有备份可恢复、备份目录写不进去等）。"""
