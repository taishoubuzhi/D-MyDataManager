# coding: utf-8
"""数据导入业务逻辑：类型识别、文件复制、Data 记录构建。

纯逻辑无 UI 依赖，便于复用与测试。
- 文本类数据：content 直接存文本
- 文件类数据：复制源文件到 Data-Store-Path，content 存新路径
"""
import os
import shutil
from datetime import datetime

from loguru import logger

from .config import config
from ..model.Data import Data, DataType


def generate_timestamp_name() -> str:
    """生成导入时间戳名称：YYYYMMDD-HHMMSS（如 20260814-163156）"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# 扩展名 -> DataType 映射表（新增类型只需在此添加一行）
EXT_TYPE_MAP = {
    ".png": DataType.IMAGE, ".jpg": DataType.IMAGE, ".jpeg": DataType.IMAGE,
    ".gif": DataType.IMAGE, ".bmp": DataType.IMAGE, ".webp": DataType.IMAGE,
    ".mp4": DataType.VIDEO, ".avi": DataType.VIDEO, ".mov": DataType.VIDEO,
    ".mkv": DataType.VIDEO, ".wmv": DataType.VIDEO,
    ".mp3": DataType.AUDIO, ".wav": DataType.AUDIO, ".flac": DataType.AUDIO,
    ".aac": DataType.AUDIO, ".ogg": DataType.AUDIO,
    ".doc": DataType.DOC,
    ".docx": DataType.DOCX,
    ".xls": DataType.EXCEL, ".xlsx": DataType.EXCEL,
    ".ppt": DataType.PPT, ".pptx": DataType.PPT,
    ".txt": DataType.TEXT, ".md": DataType.TEXT,
}


def detect_data_type(file_path: str) -> DataType:
    """根据文件扩展名识别数据类型，未知扩展名返回 UNKNOWN"""
    ext = os.path.splitext(file_path)[1].lower()
    return EXT_TYPE_MAP.get(ext, DataType.UNKNOWN)


def _get_unique_dest_path(store_path: str, filename: str) -> str:
    """生成不冲突的目标路径，同名时追加 _1/_2... 避免覆盖"""
    dest = os.path.join(store_path, filename)
    if not os.path.exists(dest):
        return dest
    name, ext = os.path.splitext(filename)
    i = 1
    while True:
        candidate = os.path.join(store_path, f"{name}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def _truncate_name(name: str, limit: int = 20) -> str:
    """截断名称以满足 Data.name(String(20)) 约束"""
    name = (name or "").strip()
    return name[:limit] if len(name) > limit else name


def import_text_data(session, name, content, keywords, tags,
                     is_hidden, user_id, database_id) -> Data:
    """文本导入：content 直接存文本内容

    Returns
    -------
    Data : 已 add 到 session 的 Data 对象（调用方负责 commit）
    """
    data = Data(
        name=_truncate_name(name) or "未命名文本",
        type=DataType.TEXT,
        keywords=keywords or [],
        tag=tags or [],
        size=len((content or "").encode("utf-8")),
        is_hidden=bool(is_hidden),
        content=content or "",
        user_id=user_id,
        database_id=database_id,
    )
    session.add(data)
    logger.info(f"文本数据待导入: name={data.name}, size={data.size}")
    return data


def import_file_data(session, source_path, name, keywords, tags,
                     is_hidden, user_id, database_id) -> Data:
    """文件导入：复制源文件到 Data-Store-Path，content 存复制后的路径

    保留源文件原件（非破坏性操作）。
    """
    store_path = config.get(config.data_store_path)
    os.makedirs(store_path, exist_ok=True)

    filename = os.path.basename(source_path)
    dest_path = _get_unique_dest_path(store_path, filename)
    shutil.copy2(source_path, dest_path)

    data_type = detect_data_type(source_path)
    file_size = os.path.getsize(source_path)
    # 图片类型顺便设置封面路径
    cover_path = dest_path if data_type == DataType.IMAGE else None
    # 名称：未指定则用去扩展名的文件名
    final_name = name if (name and name.strip()) else os.path.splitext(filename)[0]

    data = Data(
        name=_truncate_name(final_name),
        type=data_type,
        keywords=keywords or [],
        tag=tags or [],
        size=file_size,
        is_hidden=bool(is_hidden),
        content=dest_path,
        cover_path=cover_path,
        user_id=user_id,
        database_id=database_id,
    )
    session.add(data)
    logger.info(f"文件数据待导入: name={data.name}, type={data_type}, dest={dest_path}")
    return data
