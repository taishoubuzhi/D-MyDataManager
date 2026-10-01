"""数据特征：MIME、图片尺寸与封面、感知哈希、文本统计。"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from loguru import logger

from ..core.config import config, cover_dir
from ..db.models import DataItem, DataType, Feature


def guess_mime(name: str, fallback: str = "application/octet-stream") -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or fallback


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
        covers.mkdir(parents=True, exist_ok=True)
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
