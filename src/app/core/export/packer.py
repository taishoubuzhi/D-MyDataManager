"""压缩包写入：先写 `<名字>.zip.part`，全部成功再原子改名成正式文件。

以前这套「写一半崩了就留个半成品」的顾虑散在几个服务里各写一遍，这里收成一处：

* `ZipPack` 是个上下文管理器，`with` 里往包里塞条目，正常退出才算成功；
* 中途抛异常、或显式 `abort()`，都会把 `.part` 删掉，不会留下看起来像成品的半包；
* 条目名重名自动加 `_1`、`_2`；内容既可以给整块 `bytes`，也可以给一段一段的
  迭代器（大文件流式写入，不把整份内容读进内存）。

只依赖标准库。
"""

from __future__ import annotations

import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .template import unique_name

__all__ = ["PackEntry", "PackError", "ZipPack", "write_zip"]

PART_SUFFIX = ".part"
_REPLACE_RETRY = 3
_REPLACE_WAIT = 0.3


class PackError(RuntimeError):
    """打包失败（磁盘满、目标被占用、内容读不出来都属于这一类）。"""


@dataclass(frozen=True)
class PackEntry:
    """一条要装进包里的内容：给 `data` 或给 `chunks` 二选一。"""

    name: str
    data: bytes | None = None
    chunks: Callable[[], Iterable[bytes]] | None = None


def write_zip(target: str | Path, entries: Iterable[PackEntry]) -> Path:
    """一次性把若干条目写成 zip，返回最终文件路径。"""
    with ZipPack(target) as pack:
        for entry in entries:
            if entry.data is not None:
                pack.add_bytes(entry.name, entry.data)
            elif entry.chunks is not None:
                pack.add_stream(entry.name, entry.chunks())
            else:
                pack.add_text(entry.name, "")
    return Path(target if str(target).lower().endswith(".zip") else f"{target}.zip")


class ZipPack:
    """一个正在写的 zip 包：`with` 正常退出即成功，异常即放弃。"""

    def __init__(self, target: str | Path, *, ensure_suffix: bool = True) -> None:
        text = str(target)
        if ensure_suffix and not text.lower().endswith(".zip"):
            text += ".zip"
        self.path = Path(text)
        self.part = self.path.with_name(self.path.name + PART_SUFFIX)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._used: set[str] = set()
        self._closed = False
        self._archive = zipfile.ZipFile(
            self.part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        )

    # ------------------------------------------------------------ 写条目
    def add_text(self, name: str, text: str, *, encoding: str = "utf-8") -> str:
        """写一段文本（清单、说明文件用）。"""
        return self.add_bytes(name, text.encode(encoding))

    def add_bytes(self, name: str, data: bytes) -> str:
        return self.add_stream(name, (data,))

    def add_stream(self, name: str, chunks: Iterable[bytes]) -> str:
        """流式写一份内容：`chunks` 可以是大文件的分块读取器。"""
        if self._closed:
            raise PackError("这个压缩包已经关闭了")
        member = unique_name(name, self._used)
        try:
            with self._archive.open(member, "w") as handle:
                for chunk in chunks:
                    if chunk:
                        handle.write(chunk)
        except Exception as exc:  # 内容读不出来 / 磁盘满：带上条目名更好查
            raise PackError(f"写不进压缩包：{member}（{exc}）") from exc
        return member

    def add_file(self, name: str, source: str | Path) -> str:
        """把一个磁盘文件装进去（不整份读进内存）。"""
        path = Path(source)
        return self.add_stream(name, _read_chunks(path))

    # ------------------------------------------------------------ 收尾
    def close(self) -> Path:
        """关包并原子改名；失败会尽力清理 `.part` 再抛 `PackError`。"""
        if self._closed:
            return self.path
        self._closed = True
        try:
            self._archive.close()
        except Exception as exc:
            self._discard()
            raise PackError(f"压缩包收尾失败：{exc}") from exc
        if not _replace(self.part, self.path):
            self._discard()
            raise PackError(f"挪不动压缩包：{self.part} → {self.path}")
        return self.path

    def abort(self) -> None:
        """放弃：关掉句柄并把 `.part` 删掉。"""
        if not self._closed:
            self._closed = True
            try:
                self._archive.close()
            except Exception:
                pass
        self._discard()

    def _discard(self) -> None:
        try:
            self.part.unlink()
        except OSError:
            pass

    def __enter__(self) -> "ZipPack":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self.close()
        else:
            self.abort()
        return False


def _read_chunks(path: Path, size: int = 1024 * 1024) -> Iterable[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(size)
            if not chunk:
                return
            yield chunk


def _replace(source: Path, target: Path) -> bool:
    """`os.replace` 有时候会被杀软/资源管理器占用挡一下，退几步再试。"""
    for attempt in range(_REPLACE_RETRY):
        try:
            os.replace(source, target)
            return True
        except OSError:
            if attempt == _REPLACE_RETRY - 1:
                return False
            time.sleep(_REPLACE_WAIT)
    return False
