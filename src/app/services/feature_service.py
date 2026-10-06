"""数据特征：MIME、图片尺寸与封面、感知哈希、文本统计。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from loguru import logger

from ..core.runtime import paths
from ..core.config import config, cover_dir
from ..db.models import DataItem, DataType, Feature

try:  # 内容嗅探（能认出改了扩展名的文件）；缺依赖时只看扩展名
    import puremagic as _puremagic
except ImportError:  # pragma: no cover - 取决于运行环境
    _puremagic = None

#: 扩展名判不出来时的兜底 MIME
FALLBACK_MIME = "application/octet-stream"


def mime_of_file(source: str | Path) -> str:
    """按**文件内容**猜 MIME（puremagic）；没装依赖 / 认不出 / 读不了都返回空串。

    puremagic 认不出会抛 `PureError`（纯文本、小文件常常认不出），所以这里吞掉异常：
    「内容认不出」是正常情况，交给扩展名兜底。
    """
    if _puremagic is None:
        return ""
    try:
        return str(_puremagic.from_file(str(source), mime=True) or "")
    except Exception:  # noqa: BLE001 - PureError / OSError 都表示「这条线索没用」
        return ""


def guess_mime(
    name: str, source: str | Path | None = None, fallback: str = FALLBACK_MIME
) -> str:
    """猜 MIME：扩展名与文件内容都看，跨类冲突时以内容为准。

    规则（内容嗅探能认出被改名的文件，但也会把普通 zip 当成 docx，所以不是无脑优先）：

    - 内容认不出来（纯文本、小文件）→ 用扩展名；
    - 扩展名未知或只给出 `application/octet-stream` → 用内容；
    - 两者顶层类型相同（`text/*`、`application/*`…）→ 用扩展名，它更具体
      （`.md` 比 `text/plain`、`.zip` 比 docx 准）；
    - 顶层类型不同（把 png 改名成 `.txt`）→ 用内容。
    """
    by_name, _encoding = mimetypes.guess_type(name or "")
    by_content = mime_of_file(source) if source is not None else ""
    if not by_content:
        return by_name or fallback
    if not by_name or by_name in (fallback, FALLBACK_MIME):
        return by_content
    if by_name.split("/", 1)[0] == by_content.split("/", 1)[0]:
        return by_name
    return by_content


def image_size(source: str | Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(source) as image:
            return image.size
    except Exception as exc:
        logger.debug("读取图片尺寸失败：{}", exc)
        return None


def make_cover(source: str | Path, checksum: str, size: int | None = None) -> str:
    """为图片生成方形缩略图，返回封面文件路径（失败返回空串）。"""
    if not checksum:
        return ""
    try:
        from PIL import Image

        covers = cover_dir()
        paths.make_dir(covers)
        target = covers / f"{checksum}.png"
        if target.exists():
            return str(target)
        limit = size or config.coverSize.value
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((limit, limit))
            image.save(target, "PNG")
        return str(target)
    except Exception as exc:
        logger.debug("生成封面失败：{}", exc)
        return ""


def perceptual_hash(source: str | Path, hash_size: int = 8) -> str:
    """dHash 感知哈希：用于找出内容相近的图片。"""
    try:
        from PIL import Image

        with Image.open(source) as image:
            small = image.convert("L").resize((hash_size + 1, hash_size))
            pixels = list(small.getdata())
        bits = 0
        for row in range(hash_size):
            offset = row * (hash_size + 1)
            for col in range(hash_size):
                bits = (bits << 1) | int(pixels[offset + col] > pixels[offset + col + 1])
        return f"{bits:0{hash_size * hash_size // 4}x}"
    except Exception as exc:
        logger.debug("计算感知哈希失败：{}", exc)
        return ""


def text_stats(text: str) -> dict:
    return {"chars": len(text), "lines": len(text.splitlines()), "words": len(text.split())}


def build_features(item: DataItem, source: str | Path | None = None) -> list[Feature]:
    """根据数据项生成特征行（不入库，由调用方决定是否保存）。"""
    features: list[Feature] = []
    if item.checksum:
        features.append(Feature(kind="checksum", value={"sha256": item.checksum}))
    if item.type is DataType.TEXT and item.content:
        features.append(Feature(kind="text", value=text_stats(item.content)))
    if source is not None and item.type is DataType.IMAGE:
        size = image_size(source)
        if size:
            features.append(Feature(kind="image", value={"width": size[0], "height": size[1]}))
        phash = perceptual_hash(source)
        if phash:
            features.append(Feature(kind="phash", value={"dhash": phash}))
    return features


def replace_features(session, item: DataItem, source: str | Path | None = None) -> list[Feature]:
    """重建某个数据项的特征集合。"""
    item.features.clear()
    session.flush()
    features = build_features(item, source)
    for feature in features:
        item.features.append(feature)
    session.flush()
    return features
