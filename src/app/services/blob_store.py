"""内容寻址的文件仓库。

文件按 SHA-256 存放为 `root/ab/cd/<checksum>`，相同内容只落盘一次，
因此天然支持增量存档（只有内容变化才会产生新文件）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from loguru import logger

from ..core import paths

CHUNK = 1 << 20


def sha256_of(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        paths.make_dir(self.root)

    # ----------------------------------------------------------------- 路径
    def rel_path_for(self, checksum: str) -> str:
        return f"{checksum[:2]}/{checksum[2:4]}/{checksum}"

    def path_of(self, rel_path: str) -> Path:
        return self.root / rel_path

    def exists(self, rel_path: str) -> bool:
        return bool(rel_path) and self.path_of(rel_path).exists()

    # ----------------------------------------------------------------- 写入
    def put_file(self, source: str | Path) -> tuple[str, str, int]:
        """登记一个已有文件，返回 (checksum, rel_path, size)。"""
        source = Path(source)
        checksum = sha256_of(source)
        rel_path = self.rel_path_for(checksum)
        target = self.path_of(rel_path)
        size = source.stat().st_size
        if target.exists():
            return checksum, rel_path, size

        paths.make_dir(target.parent)
        temp = target.with_name(target.name + ".part")
        shutil.copy2(source, temp)
        temp.replace(target)
        logger.debug("文件已入库：{} -> {}", source.name, rel_path)
        return checksum, rel_path, size

    def put_bytes(self, data: bytes) -> tuple[str, str, int]:
        checksum = sha256_of_bytes(data)
        rel_path = self.rel_path_for(checksum)
        target = self.path_of(rel_path)
        if not target.exists():
            paths.make_dir(target.parent)
            target.write_bytes(data)
        return checksum, rel_path, len(data)

    def put_text(self, text: str) -> tuple[str, str, int]:
        return self.put_bytes(text.encode("utf-8"))

    # ----------------------------------------------------------------- 读取
    def read_bytes(self, rel_path: str) -> bytes:
        return self.path_of(rel_path).read_bytes()

    def read_text(self, rel_path: str) -> str:
        return self.path_of(rel_path).read_text(encoding="utf-8", errors="replace")

    def remove(self, rel_path: str) -> bool:
        if not rel_path:
            return False
        path = self.path_of(rel_path)
        if path.exists():
            path.unlink()
            self.prune_empty_parents(path)
            logger.debug("已删除仓库文件：{}", rel_path)
            return True
        return False

    # ------------------------------------------------------------- 空目录清理
    def prune_empty_parents(self, path: str | Path) -> int:
        """删掉文件后顺手收掉空掉的哈希目录（`ab/cd`），返回删掉的目录数。

        只往上走到仓库根为止：根目录本身、非空目录、被占用的目录都不动。
        """
        removed = 0
        folder = Path(path).parent
        while folder != self.root and self.root in folder.parents:
            try:
                if any(folder.iterdir()):
                    break
                folder.rmdir()
            except OSError as exc:
                logger.debug("空目录删除失败，留待下次清理：{}（{}）", folder, exc)
                break
            removed += 1
            folder = folder.parent
        return removed

    def sweep_empty_dirs(self) -> int:
        """收掉仓库里所有空目录（删内容留下的 `ab/cd`、空的 pack 世代目录）。"""
        removed = 0
        folders = sorted(
            (p for p in self.root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for folder in folders:
            try:
                if any(folder.iterdir()):
                    continue
                folder.rmdir()
            except OSError as exc:
                logger.debug("空目录删除失败，留待下次清理：{}（{}）", folder, exc)
                continue
            removed += 1
        return removed

    # ----------------------------------------------------------------- 统计
    def iter_files(self):
        return (p for p in self.root.rglob("*") if p.is_file())

    def total_size(self) -> int:
        return sum(path.stat().st_size for path in self.iter_files())
