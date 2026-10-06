"""通用 JSON 清单基础设施（TODO 1.1 / 方案 §4.2）。

- `kit.ManifestKit`：读取 / 查询 / 对照 / 变更 / 重置 / 备份的统一门面；
- `registry`：清单 id → 文件路径 + 归属 + 格式的登记表；
- `schema/`：JSON Schema（统一格式一份、历史格式一份）；
- 协议细节见 `docs/MANIFEST_PROTOCOL.md`。

与 `.configs/config.json` 的边界：QConfig 配置项是用户运行设置，不进清单机制；
清单管的是「固定、但可能需要被外部替换/扩展」的注册表、列表与映射。
"""

from __future__ import annotations

from .errors import (
    ManifestBackupError,
    ManifestError,
    ManifestFormatError,
    ManifestNotFoundError,
    ManifestValidationError,
)
from .kit import (
    BACKUP_KEEP,
    KINDS,
    MANIFEST_VERSION,
    REQUIRED_KEYS,
    ManifestData,
    ManifestKit,
    manifest_kit,
    read_json,
    write_json_atomic,
)
from .registry import (
    FORMAT_LEGACY,
    FORMAT_MANIFEST,
    ManifestEntry,
    ManifestRegistry,
    builtin_entries,
)

__all__ = [
    "BACKUP_KEEP",
    "FORMAT_LEGACY",
    "FORMAT_MANIFEST",
    "KINDS",
    "MANIFEST_VERSION",
    "REQUIRED_KEYS",
    "ManifestBackupError",
    "ManifestData",
    "ManifestEntry",
    "ManifestError",
    "ManifestFormatError",
    "ManifestKit",
    "ManifestNotFoundError",
    "ManifestRegistry",
    "ManifestValidationError",
    "builtin_entries",
    "manifest_kit",
    "read_json",
    "write_json_atomic",
]
