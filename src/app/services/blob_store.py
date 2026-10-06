"""内容寻址文件仓库的旧入口（薄封装）。

实现已并入 `content_store`（批 J1）：松散仓库 `BlobStore`、`sha256_of`、
`sha256_of_bytes` 都住在那一个模块里，避免两处各写一份路径与哈希逻辑。
本模块只做转发，给「只想数一数仓库目录」的调用方（`stats_service`、自检、
导入页）留一个稳定的导入名。

新代码请直接 `from .content_store import BlobStore, sha256_of`。
"""

from __future__ import annotations

from .content_store import BlobStore, sha256_of, sha256_of_bytes

__all__ = ["BlobStore", "sha256_of", "sha256_of_bytes"]
