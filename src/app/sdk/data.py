"""插件的数据解析工具：文本解码、表格解析、压缩包列举、图片信息。

插件只用它：`from app.sdk import data`、`from app.sdk.data import human_size`。
这里不导入 Qt，单元测试可以脱离界面直接跑，插件控件只负责显示这些函数的结果。
支持的扩展名由各插件的 `.data/viewer.json` 决定，这里不保存扩展名表。
"""

from __future__ import annotations

import bz2
import codecs
import csv
import gzip
import lzma
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from loguru import logger

try:  # 编码嗅探（比「按顺序硬试」准得多，尤其对 gb18030 / shift_jis）；缺依赖时退回固定顺序
    from charset_normalizer import from_bytes as _detect_bytes
except ImportError:  # pragma: no cover - 取决于运行环境
    _detect_bytes = None

__all__ = [
    "DETECT_LIMIT",
    "ENCODINGS",
    "TEXT_LIMIT",
    "ArchiveMember",
    "SheetData",
    "archive_members",
    "archive_read",
    "csv_rows",
    "decode_text",
    "detect_encoding",
    "human_size",
    "image_data_url",
    "image_info",
    "looks_binary",
    "read_text",
    "suffix_of",
    "xlsx_sheets",
]
#: 编码探测顺序：UTF-8 系优先，然后中文常见编码，最后必定成功的 latin-1（嗅探猜不出时用）
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16", "latin-1")

#: 交给 charset-normalizer 的样本上限：再长收益很小，耗时却线性涨
DETECT_LIMIT = 64 * 1024

#: 文本预览的最大字节数：再大就只显示开头
TEXT_LIMIT = 512 * 1024
_MEMBER_LIMIT = 256 * 1024


def suffix_of(value: str | Path) -> str:
    """取小写、不带点的扩展名：`A.TXT` → `txt`，`a.tar.gz` → `gz`。"""
    text = str(value or "").strip().lower().replace(chr(92), "/").rsplit("/", 1)[-1]
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip(".")

def human_size(value: float) -> str:
    """把字节数写成 1.2 MB 这样的可读形式。"""
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --------------------------------------------------------------------- 文本
def detect_encoding(raw: bytes) -> str:
    """用 charset-normalizer 猜编码；没装依赖 / 猜不出 / 名字不认得都返回空串。

    规范名统一走 `codecs.lookup()`（`utf_8` → `utf-8`）；纯 ASCII 报成 `utf-8`
    （ASCII 是 UTF-8 的子集，报一个界面上认得的名字更实用）；带 UTF-8 BOM 的直接
    报 `utf-8-sig`，否则 BOM 会以 `\\ufeff` 留在正文开头。
    """
    if not raw:
        return ""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if _detect_bytes is None:
        return ""
    try:
        best = _detect_bytes(raw[:DETECT_LIMIT]).best()
    except Exception as exc:  # noqa: BLE001 - 探测失败不该影响读文件
        logger.debug("编码探测失败：{}", exc)
        return ""
    name = str(getattr(best, "encoding", "") or "")
    if not name:
        return ""
    try:
        canonical = codecs.lookup(name).name
    except LookupError:
        return ""
    return "utf-8" if canonical == "ascii" else canonical


def decode_text(raw: bytes, encoding: str = "") -> tuple[str, str]:
    """解码字节串，返回 (文本, 实际使用的编码)。

    显式给了编码就照它解；没给先用 charset-normalizer 猜一次（能正确认 gb18030 /
    shift_jis 这类），猜不出再按 `ENCODINGS` 顺序硬试，最后必定成功。
    """
    if encoding:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            logger.debug("按 {} 解码失败，改为自动探测", encoding)
    guessed = detect_encoding(raw)
    if guessed:
        try:
            return raw.decode(guessed), guessed
        except (UnicodeDecodeError, LookupError):
            logger.debug("按探测出的 {} 解码失败，改按固定顺序试", guessed)
    for candidate in ENCODINGS:
        try:
            return raw.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def looks_binary(raw: bytes) -> bool:
    """含 NUL 字节或大量不可打印字符时视为二进制。"""
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    printable = sum(1 for byte in sample if byte in (9, 10, 13) or 32 <= byte < 127 or byte >= 128)
    return printable / len(sample) < 0.85


def read_text(path: Path, limit: int = TEXT_LIMIT, encoding: str = "") -> tuple[str, str, bool]:
    """读取文本文件的开头部分，返回 (文本, 编码, 是否被截断)。"""
    target = Path(path)
    try:
        with target.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        logger.error("读取文本失败：{}（{}）", target, exc)
        return "", "", False
    truncated = len(raw) > limit
    text, used = decode_text(raw[:limit], encoding)
    return text, used, truncated


# --------------------------------------------------------------------- 表格
@dataclass
class SheetData:
    """一张表：名称 + 二维文本 + 是否因为太大被截断。"""

    name: str
    rows: list[list[str]] = field(default_factory=list)
    truncated: bool = False


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        logger.warning("sharedStrings 解析失败：{}", exc)
        return []
    return [
        "".join(node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t"))
        for item in root.iter(f"{{{_MAIN_NS}}}si")
    ]


def _xlsx_sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """返回 (工作表名, 压缩包内路径)。"""
    names = archive.namelist()
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ElementTree.ParseError):
        return [(Path(name).stem, name) for name in sorted(names) if name.startswith("xl/worksheets/")]
    targets = {rel.get("Id"): str(rel.get("Target") or "") for rel in rels}
    found: list[tuple[str, str]] = []
    for sheet in workbook.iter(f"{{{_MAIN_NS}}}sheet"):
        target = targets.get(sheet.get(f"{{{_REL_NS}}}id"), "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = f"xl/{target}"
        if path in names:
            found.append((sheet.get("name") or Path(path).stem, path))
    if not found:
        return [(Path(name).stem, name) for name in sorted(names) if name.startswith("xl/worksheets/")]
    return found


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _cell_text(cell: ElementTree.Element, kind: str, shared: list[str]) -> str:
    if kind == "s":
        node = cell.find(f"{{{_MAIN_NS}}}v")
        try:
            return shared[int(node.text or "0")] if node is not None else ""
        except (ValueError, IndexError):
            return ""
    if kind == "inlineStr":
        node = cell.find(f"{{{_MAIN_NS}}}is")
        return "" if node is None else "".join(t.text or "" for t in node.iter(f"{{{_MAIN_NS}}}t"))
    node = cell.find(f"{{{_MAIN_NS}}}v")
    return "" if node is None else (node.text or "")


def _xlsx_rows(
    archive: zipfile.ZipFile, target: str, shared: list[str], max_rows: int, max_cols: int
) -> tuple[list[list[str]], bool]:
    try:
        root = ElementTree.fromstring(archive.read(target))
    except (KeyError, ElementTree.ParseError) as exc:
        logger.warning("工作表解析失败：{}（{}）", target, exc)
        return [], False
    rows: list[list[str]] = []
    truncated = False
    for row in root.iter(f"{{{_MAIN_NS}}}row"):
        if len(rows) >= max_rows:
            truncated = True
            break
        cells: dict[int, str] = {}
        for cell in row.iter(f"{{{_MAIN_NS}}}c"):
            reference = cell.get("r") or ""
            index = _column_index(reference) if reference else len(cells)
            if index < 0:
                index = len(cells)
            if index >= max_cols:
                truncated = True
                continue
            cells[index] = _cell_text(cell, cell.get("t") or "n", shared)
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(index, "") for index in range(width)])
    return rows, truncated


def xlsx_sheets(path: Path, max_rows: int = 300, max_cols: int = 60) -> list[SheetData]:
    """解析 xlsx（zip + XML 自解析，不依赖第三方库）。"""
    target = Path(path)
    try:
        with zipfile.ZipFile(target) as archive:
            shared = _xlsx_shared_strings(archive)
            sheets = []
            for name, sheet_path in _xlsx_sheet_targets(archive):
                rows, truncated = _xlsx_rows(archive, sheet_path, shared, max_rows, max_cols)
                sheets.append(SheetData(name=name, rows=rows, truncated=truncated))
            return sheets
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        logger.error("读取表格失败：{}（{}）", target, exc)
        return []


def csv_rows(
    path: Path, max_rows: int = 300, max_cols: int = 60, delimiter: str = ""
) -> list[SheetData]:
    """解析 csv / tsv，分隔符未指定时按内容猜测。"""
    target = Path(path)
    text, _encoding, truncated = read_text(target, limit=TEXT_LIMIT * 4)
    if not text:
        return []
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = "\t" if target.suffix.lower() == ".tsv" else ","
    rows: list[list[str]] = []
    for index, row in enumerate(csv.reader(text.splitlines(), delimiter=delimiter)):
        if index >= max_rows:
            truncated = True
            break
        if len(row) > max_cols:
            truncated = True
            row = row[:max_cols]
        rows.append(row)
    return [SheetData(name=target.name, rows=rows, truncated=truncated)]


# --------------------------------------------------------------------- 压缩包
@dataclass(frozen=True)
class ArchiveMember:
    """压缩包里的一个成员。"""

    name: str
    size: int
    compressed: int = 0
    is_dir: bool = False


def _open_single(path: Path):
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "gz":
        return gzip.open(path, "rb")
    if suffix == "bz2":
        return bz2.open(path, "rb")
    if suffix == "xz":
        return lzma.open(path, "rb")
    return path.open("rb")


def _single_member(path: Path) -> list[ArchiveMember]:
    """非 tar 的 .gz / .bz2 / .xz：只有一条解压后的数据流。"""
    total = 0
    try:
        with _open_single(path) as handle:
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > (512 << 20):
                    total = 0
                    break
        compressed = path.stat().st_size
    except (OSError, EOFError, lzma.LZMAError, ValueError) as exc:
        logger.error("读取压缩文件失败：{}（{}）", path, exc)
        return []
    return [ArchiveMember(name=path.stem or path.name, size=total, compressed=compressed)]


def archive_members(path: Path) -> list[ArchiveMember]:
    """列出压缩包成员；不支持的格式返回空列表。"""
    target = Path(path)
    try:
        if zipfile.is_zipfile(target):
            with zipfile.ZipFile(target) as archive:
                return [
                    ArchiveMember(info.filename, info.file_size, info.compress_size, info.is_dir())
                    for info in archive.infolist()
                ]
        if tarfile.is_tarfile(target):
            with tarfile.open(target) as archive:
                return [
                    ArchiveMember(member.name, member.size, 0, member.isdir())
                    for member in archive.getmembers()
                ]
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        logger.error("读取压缩包失败：{}（{}）", target, exc)
        return []
    if suffix_of(target) in ("gz", "bz2", "xz"):
        return _single_member(target)
    return []


def archive_read(path: Path, name: str, limit: int = _MEMBER_LIMIT) -> bytes:
    """读取压缩包里某个成员的开头部分（用于文本预览）。"""
    target = Path(path)
    try:
        if zipfile.is_zipfile(target):
            with zipfile.ZipFile(target) as archive:
                with archive.open(name) as handle:
                    return handle.read(limit)
        if tarfile.is_tarfile(target):
            with tarfile.open(target) as archive:
                handle = archive.extractfile(name)
                return b"" if handle is None else handle.read(limit)
    except (OSError, KeyError, zipfile.BadZipFile, tarfile.TarError, RuntimeError) as exc:
        logger.error("读取压缩包成员失败：{} → {}（{}）", target, name, exc)
        return b""
    return b""


# --------------------------------------------------------------------- 图片
def image_info(path: Path) -> dict:
    """读取图片基本信息（Pillow 已随程序分发）。"""
    from PIL import Image

    try:
        with Image.open(path) as image:
            return {
                "format": image.format or "",
                "width": image.size[0],
                "height": image.size[1],
                "mode": image.mode,
                "frames": int(getattr(image, "n_frames", 1)),
            }
    except (OSError, ValueError) as exc:
        logger.error("读取图片信息失败：{}（{}）", path, exc)
        return {}


def image_data_url(path: Path, max_side: int = 1024) -> str:
    """把图片压成一段 `data:` 地址，供插件塞进模型请求（视觉模型多要求 base64 图片）。

    有透明通道的图存 PNG，其余存 JPEG；最长边不超过 `max_side`；
    读不了或不是图片时返回空串（调用方自己跳过这条数据）。
    """
    import base64
    import io

    from PIL import Image, ImageOps

    try:
        with Image.open(Path(path)) as image:
            frame = ImageOps.exif_transpose(image)
            frame.thumbnail((int(max_side), int(max_side)))
            has_alpha = frame.mode in ("RGBA", "LA", "P")
            if has_alpha:
                buffer = io.BytesIO()
                frame.convert("RGBA").save(buffer, format="PNG", optimize=True)
                mime = "image/png"
            else:
                buffer = io.BytesIO()
                frame.convert("RGB").save(buffer, format="JPEG", quality=85)
                mime = "image/jpeg"
    except (OSError, ValueError) as exc:
        logger.error("转换图片为 data 地址失败：{}（{}）", path, exc)
        return ""
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{payload}"
