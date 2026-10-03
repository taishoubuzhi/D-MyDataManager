"""整份压缩内容仓库：一份内容 = 一个文件 + 一条索引行。

用户决定（放弃分块策略）：内容不再切成固定大小的块，也不再写 pack 数据文件，
所有内容都整份落盘，压缩方案按数据类型选择：

- 文本 / 代码 / 结构化数据（txt/md/json/xml/csv/py/js/...）：`zstd` 用更高级别，压缩率优先；
- 其他可压缩内容：`zstd` 默认级别；
- 已压缩格式（视频 / 图片 / 音频 / 压缩包 / docx 之类）：原样存，省下的 CPU 比压缩率值钱；
- 压完不划算（随机 / 加密数据）：回退原样存，绝不膨胀。

落盘布局沿用原来的松散整块：`root/ab/cd/<sha256>`，文件里放的是压缩后的字节；
`Content` 行记下 `size`（原始字节数）、`codec`、`stored_size`、`name`、`mime`。
内容身份仍是原始字节的 sha256，因此同一份内容在仓库里只落一份文件。

写压缩结果走「先写 `.part`、结束后原子改名」，崩在中途只会留下 `.part`，由 `sweep()` 清掉。
`iter_content()` 对压缩编码会先整体解压再分片给出（整份存储下没有可用的流式解码器）。
"""

from __future__ import annotations

import lzma
import os
import shutil
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

try:  # Python 3.14 起标准库自带 zstd；更早的解释器回退到 deflate
    from compression import zstd as _zstd
except ImportError:  # pragma: no cover - 取决于解释器版本
    _zstd = None

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core import paths
from ..core.config import store_dir
from ..db.models import ArchiveEntry, Blob, Content, DataItem
from .blob_store import BlobStore, sha256_of, sha256_of_bytes

READ_CHUNK = 1 << 20
CODEC_RAW = "raw"
CODEC_DEFLATE = "deflate"
CODEC_ZSTD = "zstd"
CODEC_LZMA = "lzma"
# deflate 只在解释器没有 zstd 时使用；zstd 的默认级别 3 已明显优于 deflate
COMPRESSION_LEVEL = 6
ZSTD_LEVEL = 3
ZSTD_TEXT_LEVEL = 9
DEFLATE_TEXT_LEVEL = 9
# 压缩结果 ≥ 原大小 × 这个比例就回退成原始字节
COMPRESSION_FALLBACK = 0.98
PART_SUFFIX = ".part"

# 已经压缩过的格式：再压一遍几乎没有收益，直接按原始字节存（省的是 CPU）
COMPRESSED_SUFFIXES = frozenset(
    {
        # 视频 / 音频
        ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv",
        ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".opus",
        # 图片
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif",
        # 压缩包与容器
        ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2", ".zst", ".br", ".jar", ".apk",
        ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".epub",
    }
)
# 未压缩的音频（wav / aiff）压了还是有收益，不能进白名单
RAW_AUDIO_MIME = frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/aiff", "audio/x-aiff"})
# 文本类内容：同一编码用更高级别换更高压缩率（文本是压缩率收益最大的类型）
TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".rst", ".log", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".conf", ".sql", ".py", ".js", ".ts", ".tsx", ".jsx", ".java",
        ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1",
        ".html", ".htm", ".css", ".scss", ".svg", ".tex", ".m", ".pl", ".lua", ".kt", ".swift",
    }
)
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIMES = frozenset(
    {
        "application/json", "application/xml", "application/javascript", "application/x-javascript",
        "application/x-yaml", "application/yaml", "application/x-sh", "application/sql",
        "application/x-httpd-php", "application/x-python", "application/rtf",
    }
)
# 文件头就能看出「这份内容已经是压缩容器」的魔数
COMPRESSED_MAGIC = (
    b"\x89PNG\r\n\x1a\n",           # PNG
    b"\xff\xd8\xff",                # JPEG
    b"GIF87a", b"GIF89a",           # GIF
    b"RIFF",                        # WAV / AVI / WEBP（三者都是容器，压不动）
    b"\x1a\x45\xdf\xa3",            # Matroska / WebM
    b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x20ftyp", b"ftyp",  # MP4 / MOV
    b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",  # MP3
    b"fLaC",                        # FLAC
    b"OggS",                        # Ogg / Opus
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",  # ZIP 及其衍生（docx/xlsx/apk/jar）
    b"7z\xbc\xaf\x27\x1c",          # 7z
    b"Rar!\x1a\x07",                # RAR
    b"\x1f\x8b",                    # gzip
    b"BZh",                         # bzip2
    b"\xfd7zXZ\x00",                # xz
    b"(\xb5/\xfd",                  # zstd
    b"BZ0",                         # brotli（非官方魔数，仅示意）
    b"%PDF-",                       # PDF 内部多为已压缩流
)


def is_compressible(name: str, mime: str = "") -> bool:
    """这份内容值不值得尝试压缩：看扩展名与 mime 白名单。"""
    if Path(name).suffix.lower() in COMPRESSED_SUFFIXES:
        return False
    mime = (mime or "").lower()
    if mime in RAW_AUDIO_MIME:
        return True
    return not (mime.startswith("video/") or mime.startswith("audio/"))


def is_textual(name: str = "", mime: str = "") -> bool:
    """文本类内容用更高级别压缩（压缩率收益最大，且读取时不会慢到影响使用）。"""
    if Path(name).suffix.lower() in TEXT_SUFFIXES:
        return True
    mime = (mime or "").lower()
    if mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIMES:
        return True
    return mime.endswith("+json") or mime.endswith("+xml")


def zstd_available() -> bool:
    """当前解释器是否自带 zstd（Python 3.14 起的标准库 compression.zstd）。"""
    return _zstd is not None


def preferred_codec() -> str:
    """有 zstd 就用 zstd，否则退回 deflate。"""
    return CODEC_ZSTD if _zstd is not None else CODEC_DEFLATE


def policy_for(name: str = "", mime: str = "") -> str:
    """这类内容用什么编码落盘；`raw` 表示原样存，不浪费 CPU。"""
    if not is_compressible(name, mime):
        return CODEC_RAW
    return preferred_codec()


def sniff_compressed(sample: bytes) -> bool:
    """按文件头判断样本是不是已经压缩过的容器。"""
    return any(sample.startswith(magic) for magic in COMPRESSED_MAGIC)


def compress_bytes(data: bytes, *, codec: str | None = None, textual: bool = False) -> bytes:
    """压缩一段字节（内存里）；不指定编码时按 `preferred_codec()`。"""
    codec = codec or preferred_codec()
    if codec == CODEC_ZSTD:
        if _zstd is None:
            raise ValueError("当前解释器没有 zstd，无法按 zstd 压缩")
        return _zstd.compress(data, ZSTD_TEXT_LEVEL if textual else ZSTD_LEVEL)
    if codec == CODEC_DEFLATE:
        return zlib.compress(data, DEFLATE_TEXT_LEVEL if textual else COMPRESSION_LEVEL)
    if codec == CODEC_LZMA:
        return lzma.compress(data, preset=COMPRESSION_LEVEL)
    raise ValueError(f"未知的压缩编码：{codec}")


def _compressor(codec: str, textual: bool):
    if codec == CODEC_ZSTD:
        if _zstd is None:
            raise ValueError("当前解释器没有 zstd，无法按 zstd 压缩")
        return _zstd.ZstdCompressor(level=ZSTD_TEXT_LEVEL if textual else ZSTD_LEVEL)
    if codec == CODEC_DEFLATE:
        return zlib.compressobj(DEFLATE_TEXT_LEVEL if textual else COMPRESSION_LEVEL)
    if codec == CODEC_LZMA:
        return lzma.LZMACompressor(preset=COMPRESSION_LEVEL)
    raise ValueError(f"未知的压缩编码：{codec}")


def compress_stream(source: str | Path, *, codec: str | None = None, textual: bool = False) -> bytes:
    """流式压缩整个文件：内存里只留压缩结果，不留原文副本。"""
    compressor = _compressor(codec or preferred_codec(), textual)
    parts: list[bytes] = []
    with Path(source).open("rb") as handle:
        for data in iter(lambda: handle.read(READ_CHUNK), b""):
            packed = compressor.compress(data)
            if packed:
                parts.append(packed)
    tail = compressor.flush()
    if tail:
        parts.append(tail)
    return b"".join(parts)


def encode_stored(data: bytes, *, name: str = "", mime: str = "") -> tuple[bytes, str]:
    """整份编码：先按类型与文件头定方案，再压一次，不划算就原样存。

    返回 (落盘字节, 编码名)。
    """
    if not data:
        return data, CODEC_RAW
    codec = policy_for(name, mime)
    if codec == CODEC_RAW or sniff_compressed(data[:64]):
        return data, CODEC_RAW
    packed = compress_bytes(data, codec=codec, textual=is_textual(name, mime))
    if len(packed) >= len(data) * COMPRESSION_FALLBACK:
        return data, CODEC_RAW
    return packed, codec


def decode_stored(codec: str, data: bytes) -> bytes:
    """把落盘字节按编码还原成原始字节；四种编码都要能读，旧内容才不会变成死档。"""
    if codec == CODEC_RAW:
        return data
    if codec == CODEC_DEFLATE:
        return zlib.decompress(data)
    if codec == CODEC_ZSTD:
        if _zstd is None:
            raise ValueError("这份内容以 zstd 压缩，需要 Python 3.14+ 才能读取")
        return _zstd.decompress(data)
    if codec == CODEC_LZMA:
        return lzma.decompress(data)
    raise ValueError(f"未知的内容编码：{codec}")


@dataclass
class VerifyReport:
    """完整性校验结果：计数 + 少量可读的问题描述。"""

    level: str = "quick"
    contents: int = 0
    files: int = 0
    missing: int = 0
    bad_size: int = 0
    bad_checksum: int = 0
    orphans: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.bad_size or self.bad_checksum or self.orphans)

    def summary(self) -> str:
        if self.ok:
            return f"校验通过（{self.contents} 份内容 / {self.files} 个文件 / {self.level}）"
        return (
            f"内容缺失 {self.missing} / 大小异常 {self.bad_size} / "
            f"校验和不符 {self.bad_checksum} / 无索引文件 {self.orphans}"
        )


class ContentStore:
    """整份压缩内容仓库：调用方只认「原始字节的 sha256」，压缩与落盘细节都在这里。"""

    def __init__(self, session: Session, root: str | Path | None = None) -> None:
        self.session = session
        self.root = Path(root or store_dir())
        paths.make_dir(self.root)
        self.loose = BlobStore(self.root)

    # ----------------------------------------------------------------- 路径
    def rel_path_for(self, checksum: str) -> str:
        return self.loose.rel_path_for(checksum)

    def loose_rel_path(self, checksum: str) -> str:
        """内容在仓库里的相对路径；文件不在就返回空串（沿用旧接口名）。"""
        if not checksum:
            return ""
        rel_path = self.rel_path_for(checksum)
        return rel_path if self.loose.exists(rel_path) else ""

    def path_of(self, rel_path: str) -> Path:
        return self.loose.path_of(rel_path)

    def exists(self, rel_path: str) -> bool:
        return self.loose.exists(rel_path)

    def remove(self, rel_path: str) -> bool:
        return self.loose.remove(rel_path)

    def iter_files(self):
        return self.loose.iter_files()

    # ----------------------------------------------------------------- 写入
    def put_file(self, source: str | Path, *, name: str = "", mime: str = "") -> tuple[str, str, int]:
        """整份登记一个已有文件，返回 (checksum, 相对路径, 原始大小)。

        同一份内容重复入库只落一份文件；`name` / `mime` 只用于挑选压缩方案。
        """
        source = Path(source)
        size = source.stat().st_size
        checksum = sha256_of(source)
        rel_path = self.loose.rel_path_for(checksum)
        row = self.content(checksum)
        if row is not None and self.loose.exists(rel_path):
            return checksum, rel_path, int(row.size)
        label = name or source.name
        codec, stored_size = self._store_file(source, checksum, label, mime)
        self._record(checksum, size, codec, stored_size, label, mime)
        logger.debug("内容已入库：{}（{} -> {} 字节 / {}）", label, size, stored_size, codec)
        return checksum, rel_path, size

    def put_bytes(self, data: bytes, *, name: str = "", mime: str = "") -> tuple[str, str, int]:
        """整份登记一段内存内容，返回 (checksum, 相对路径, 原始大小)。"""
        checksum = sha256_of_bytes(data)
        rel_path = self.loose.rel_path_for(checksum)
        row = self.content(checksum)
        if row is not None and self.loose.exists(rel_path):
            return checksum, rel_path, len(data)
        stored, codec = encode_stored(data, name=name, mime=mime)
        self._write_bytes(rel_path, stored)
        self._record(checksum, len(data), codec, len(stored), name, mime)
        return checksum, rel_path, len(data)

    def put_text(
        self, text: str, *, name: str = "", mime: str = "text/plain"
    ) -> tuple[str, str, int]:
        """整份登记一段文本（默认按 text/plain 决定压缩方案）。"""
        return self.put_bytes(text.encode("utf-8"), name=name, mime=mime)

    def _store_file(self, source: Path, checksum: str, name: str, mime: str) -> tuple[str, int]:
        """把文件整份编码落盘，返回 (编码, 落盘大小)。"""
        target = self.loose.path_of(self.loose.rel_path_for(checksum))
        size = source.stat().st_size
        codec = policy_for(name, mime)
        if codec == CODEC_RAW or sniff_compressed(self._sample(source)):
            return CODEC_RAW, self._copy_raw(source, target)
        textual = is_textual(name, mime)
        temp = target.with_name(target.name + PART_SUFFIX)
        paths.make_dir(target.parent)
        compressor = _compressor(codec, textual)
        try:
            with source.open("rb") as reader, temp.open("wb") as writer:
                for data in iter(lambda: reader.read(READ_CHUNK), b""):
                    packed = compressor.compress(data)
                    if packed:
                        writer.write(packed)
                tail = compressor.flush()
                if tail:
                    writer.write(tail)
        except Exception:  # noqa: BLE001 - 压缩失败就退回原样存，不留下半个文件
            temp.unlink(missing_ok=True)
            logger.warning("内容压缩失败，改为原样存储：{}", name)
            return CODEC_RAW, self._copy_raw(source, target)
        stored_size = temp.stat().st_size
        if stored_size >= size * COMPRESSION_FALLBACK:  # 压不动：留原始字节更省事
            temp.unlink(missing_ok=True)
            return CODEC_RAW, self._copy_raw(source, target)
        os.replace(temp, target)
        return codec, stored_size

    def _copy_raw(self, source: Path, target: Path) -> int:
        size = source.stat().st_size
        if target.exists():
            return size
        paths.make_dir(target.parent)
        temp = target.with_name(target.name + PART_SUFFIX)
        shutil.copy2(source, temp)
        os.replace(temp, target)
        return size

    def _write_bytes(self, rel_path: str, data: bytes, *, overwrite: bool = False) -> None:
        target = self.loose.path_of(rel_path)
        if target.exists() and not overwrite:
            return
        paths.make_dir(target.parent)
        temp = target.with_name(target.name + PART_SUFFIX)
        temp.write_bytes(data)
        os.replace(temp, target)

    def _sample(self, source: Path) -> bytes:
        try:
            with source.open("rb") as handle:
                return handle.read(64)
        except OSError:
            return b""

    def _record(self, checksum: str, size: int, codec: str, stored_size: int, name: str, mime: str) -> None:
        row = self.content(checksum)
        if row is None:
            row = Content(checksum=checksum)
            self.session.add(row)
        row.size = int(size)
        row.codec = codec
        row.stored_size = int(stored_size)
        row.name = name[:255]
        row.mime = (mime or "")[:127]
        self.session.flush()

    # ----------------------------------------------------------------- 读取
    def content(self, checksum: str) -> Content | None:
        if not checksum:
            return None
        return self.session.scalar(select(Content).where(Content.checksum == checksum))

    def content_available(self, checksum: str) -> bool:
        """内容可用 = 仓库里有这份文件（`ab/cd/<sha256>`）。

        索引行只记压缩元数据：老数据里的松散整块没有索引行，按原样读即可，
        所以「文件在不在」才是唯一判据。
        """
        if not checksum:
            return False
        return self.loose.exists(self.rel_path_for(checksum))

    def _stored_bytes(self, checksum: str) -> bytes | None:
        rel_path = self.rel_path_for(checksum)
        if not self.loose.exists(rel_path):
            return None
        try:
            return self.loose.read_bytes(rel_path)
        except OSError as exc:
            logger.warning("内容读取失败：{}（{}）", checksum[:12], exc)
            return None

    def _codec_of(self, checksum: str) -> str:
        row = self.content(checksum)
        return row.codec if row is not None else CODEC_RAW

    def read_content(self, checksum: str) -> bytes:
        """整份读回原始字节；文件不在或损坏时抛异常。"""
        stored = self._stored_bytes(checksum)
        if stored is None:
            raise FileNotFoundError(f"内容文件缺失：{checksum[:12]}")
        row = self.content(checksum)
        data = decode_stored(row.codec if row is not None else CODEC_RAW, stored)
        if row is not None and len(data) != int(row.size):
            raise ValueError(f"内容大小不符：{checksum[:12]}（{len(data)} != {row.size}）")
        return data

    def iter_content(self, checksum: str) -> Iterator[bytes]:
        """分片给出原始字节；原样存的内容直接流式读文件。"""
        if not self.content_available(checksum):
            raise FileNotFoundError(f"内容文件缺失：{checksum[:12]}")
        if self._codec_of(checksum) == CODEC_RAW:
            rel_path = self.rel_path_for(checksum)
            with self.loose.path_of(rel_path).open("rb") as handle:
                for data in iter(lambda: handle.read(READ_CHUNK), b""):
                    yield data
            return
        data = self.read_content(checksum)
        for start in range(0, len(data), READ_CHUNK):
            yield data[start:start + READ_CHUNK]

    def export_content(self, checksum: str, target: str | Path) -> bool:
        """把内容导出到目标路径（先写 `.part` 再原子改名）；内容不可用返回 False。"""
        if not self.content_available(checksum):
            return False
        target = Path(target)
        paths.make_dir(target.parent)
        temp = target.with_name(target.name + PART_SUFFIX)
        with temp.open("wb") as handle:
            for data in self.iter_content(checksum):
                handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return True

    # ----------------------------------------------------------------- 统计
    def used_bytes(self, checksums) -> dict[str, int]:
        """每份内容实际占用（落盘字节数）；索引缺失或文件不在的忽略掉。"""
        wanted = [value for value in dict.fromkeys(checksums) if value]
        if not wanted:
            return {}
        rows = {
            row.checksum: row
            for row in self.session.scalars(select(Content).where(Content.checksum.in_(wanted)))
        }
        result: dict[str, int] = {}
        for checksum in wanted:
            rel_path = self.rel_path_for(checksum)
            if not self.loose.exists(rel_path):
                continue
            row = rows.get(checksum)
            if row is not None:
                result[checksum] = int(row.stored_size)
                continue
            try:  # 老数据里的松散整块没有索引行，按文件大小算
                result[checksum] = self.loose.path_of(rel_path).stat().st_size
            except OSError:
                continue
        return result

    def usage(self) -> dict[str, int]:
        """仓库口径：内容条数、原始大小、落盘大小与无索引文件。"""
        rows = list(self.session.scalars(select(Content)))
        indexed = {row.checksum for row in rows}
        result = {
            "contents": len(rows),
            "raw_bytes": sum(int(row.size) for row in rows),
            "stored_bytes": sum(int(row.stored_size) for row in rows),
            "files": 0,
            "total_bytes": 0,
            "orphans": 0,
            "orphan_bytes": 0,
            "part": 0,
        }
        for path in self.loose.iter_files():
            size = path.stat().st_size
            if path.name.endswith(PART_SUFFIX):
                result["part"] += 1
                continue
            result["files"] += 1
            result["total_bytes"] += size
            if path.name not in indexed:
                result["orphans"] += 1
                result["orphan_bytes"] += size
        return result

    def total_size(self) -> int:
        return int(self.usage()["total_bytes"])

    def needs_recode(self) -> int:
        """按当前压缩方案本该用别的编码的内容条数（整理时一并升级）。"""
        return sum(
            1
            for row in self.session.scalars(select(Content))
            if policy_for(row.name, row.mime) not in (CODEC_RAW, row.codec)
        )

    # ----------------------------------------------------------------- 维护
    def _referenced_checksums(self) -> set[str]:
        referenced: set[str] = set()
        for model in (DataItem, ArchiveEntry, Blob):
            column = getattr(model, "checksum", None)
            if column is None:
                continue
            for value in self.session.scalars(select(column).where(column.is_not(None))):
                if value:
                    referenced.add(value)
        return referenced

    def _remove_file(self, path: Path) -> bool:
        try:
            path.unlink()
        except OSError:
            return False
        self.loose.prune_empty_parents(path)
        return True

    def sweep(self) -> dict[str, int]:
        """扫掉没人认领的文件：`.part` 残留与既没有索引行、也没被数据引用的文件。"""
        result = {"part": 0, "orphans": 0, "bytes": 0}
        indexed = set(self.session.scalars(select(Content.checksum)))
        referenced = self._referenced_checksums()
        for path in list(self.loose.iter_files()):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if path.name.endswith(PART_SUFFIX):
                result["part"] += 1
            elif path.name not in indexed and path.name not in referenced:
                result["orphans"] += 1
            else:
                continue
            result["bytes"] += size
            self._remove_file(path)
        if any(result.values()):
            logger.debug("内容仓库扫描：{}", result)
        return result

    def cleanup(self) -> dict[str, int]:
        """清掉没人引用的内容行与文件、无索引文件、`.part` 残留与空目录。"""
        result = {"contents": 0, "orphans": 0, "bytes": 0, "part": 0, "empty_dirs": 0}
        referenced = self._referenced_checksums()
        for row in list(self.session.scalars(select(Content))):
            if row.checksum in referenced:
                continue
            rel_path = self.rel_path_for(row.checksum)
            try:
                result["bytes"] += self.loose.path_of(rel_path).stat().st_size
            except OSError:
                pass
            result["contents"] += 1
            self.session.delete(row)
            self.loose.remove(rel_path)
        self.session.flush()
        swept = self.sweep()
        result["orphans"] += int(swept.get("orphans", 0))
        result["part"] += int(swept.get("part", 0))
        result["bytes"] += int(swept.get("bytes", 0))
        result["empty_dirs"] = self.loose.sweep_empty_dirs()
        if any(value for key, value in result.items() if key != "bytes"):
            logger.info("内容仓库清理完成：{}", result)
        return result

    def verify(self, level: str = "quick") -> VerifyReport:
        """校验索引与文件是否对得上；`deep` 会解压并复核 sha256。"""
        if level not in ("quick", "deep"):
            raise ValueError(f"未知的校验级别：{level}")
        report = VerifyReport(level=level)
        indexed: set[str] = set()
        for row in self.session.scalars(select(Content).order_by(Content.id)):
            report.contents += 1
            indexed.add(row.checksum)
            rel_path = self.rel_path_for(row.checksum)
            if not self.loose.exists(rel_path):
                report.missing += 1
                self._note(report, f"内容文件缺失：{row.checksum[:12]}")
                continue
            path = self.loose.path_of(rel_path)
            try:
                stored_size = path.stat().st_size
            except OSError as exc:
                report.missing += 1
                self._note(report, f"内容文件读不到：{row.checksum[:12]}（{exc}）")
                continue
            if stored_size != int(row.stored_size):
                report.bad_size += 1
                self._note(report, f"落盘大小不符：{row.checksum[:12]}（{stored_size} != {row.stored_size}）")
            if level == "quick":
                continue
            try:
                data = decode_stored(row.codec, path.read_bytes())
            except Exception as exc:  # noqa: BLE001 - 单份内容损坏不该中断整场校验
                report.bad_checksum += 1
                self._note(report, f"内容解压失败：{row.checksum[:12]}（{exc}）")
                continue
            if len(data) != int(row.size) or sha256_of_bytes(data) != row.checksum:
                report.bad_checksum += 1
                self._note(report, f"内容校验和不符：{row.checksum[:12]}")
        for path in self.loose.iter_files():
            if path.name.endswith(PART_SUFFIX):
                report.files += 1
                continue
            report.files += 1
            if path.name not in indexed:
                report.orphans += 1
                self._note(report, f"无索引文件：{path.name[:12]}")
        return report

    @staticmethod
    def _note(report: VerifyReport, message: str) -> None:
        if len(report.problems) < 10:
            report.problems.append(message)

    def recompress(self) -> dict[str, int]:
        """按当前压缩方案重写已有内容（旧编码升级 / 换更优方案）。

        只改落盘编码：内容身份与原始大小都不变，新结果没更小就保留原样。
        """
        result = {"contents": 0, "recoded": 0, "bytes": 0, "freed": 0}
        for row in self.session.scalars(select(Content)):
            result["contents"] += 1
            codec = policy_for(row.name, row.mime)
            if codec == CODEC_RAW or codec == row.codec:
                continue
            rel_path = self.rel_path_for(row.checksum)
            if not self.loose.exists(rel_path):
                continue
            try:
                stored = self.loose.read_bytes(rel_path)
                data = decode_stored(row.codec, stored)
            except Exception as exc:  # noqa: BLE001 - 单份内容出错不该中断整理
                logger.warning("内容重新编码失败，保持原编码：{}（{}）", row.checksum[:12], exc)
                continue
            packed = compress_bytes(data, codec=codec, textual=is_textual(row.name, row.mime))
            if len(packed) >= len(stored):
                continue
            self._write_bytes(rel_path, packed, overwrite=True)
            row.codec = codec
            row.stored_size = len(packed)
            self.session.flush()
            result["recoded"] += 1
            result["bytes"] += len(packed)
            result["freed"] += len(stored) - len(packed)
        return result

    def reset(self) -> dict[str, int]:
        """清空整个内容仓库（文件 + 索引行），返回清掉的量。"""
        result = self.usage()
        summary = {
            "contents": int(result["contents"]),
            "files": int(result["files"]),
            "blobs": int(self.session.scalar(select(func.count()).select_from(Blob)) or 0),
            "total_bytes": int(result["total_bytes"]),
        }
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        paths.make_dir(self.root)
        for model in (Content, Blob):
            self.session.execute(delete(model))
        self.session.flush()
        return summary


__all__ = [
    "CODEC_DEFLATE",
    "CODEC_LZMA",
    "CODEC_RAW",
    "CODEC_ZSTD",
    "ContentStore",
    "VerifyReport",
    "compress_bytes",
    "compress_stream",
    "decode_stored",
    "encode_stored",
    "is_compressible",
    "is_textual",
    "policy_for",
    "preferred_codec",
    "sniff_compressed",
    "zstd_available",
]
