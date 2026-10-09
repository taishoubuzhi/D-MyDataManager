"""封面规范：图片直接用原文件，视频取第一帧，统一缓存到库内 `全局/covers/`。

封面规则（三处入口共用，导入、扫描登记、设置页「重置封面」）：

- **图片**：不写 `cover_path`——卡片直接用原文件作封面，省掉一份重复的缩略图，
  也不再依赖缓存目录里的副本（用户 m00003 第 3 条）。
- **视频**：导入时用媒体引擎（`app.services.media_service`）在进程内解出第一帧，存到
  `全局/covers/<checksum>.png` 并写进 `cover_path`；解不出来（文件损坏、编码不支持）时
  退回空值，界面显示默认类型图标（用户 m00003 第 2 条）。
- 其它类型没有封面概念，`cover_path` 一律清空。

抽帧不再依赖外部 ffmpeg 可执行文件（`av` 的 wheel 自带 FFmpeg 库），以前那套
「PATH → imageio-ffmpeg → 模型运行环境 venv」的定位逻辑整体删掉了。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select

from ..core.config import config, cover_dir, library_root
from ..db.models import DataItem, DataType
from . import media_service

VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"}
)


def grab_video_frame(source: str | Path, checksum: str, size: int | None = None) -> str:
    """抽视频第一帧保存为封面，返回封面路径；失败返回空串（不抛错）。"""
    if not checksum or not source:
        return ""
    covers = cover_dir()
    covers.mkdir(parents=True, exist_ok=True)
    target = covers / f"{checksum}.png"
    if target.exists():
        return str(target)
    limit = size or config.coverSize.value
    produced = media_service.frame(source, target, size=limit)
    if not produced:
        logger.info("视频首帧抽不出来（文件损坏或编码不支持），封面退回默认图标：{}", Path(source).name)
        _remove_partial(target)
        return ""
    return produced


def _remove_partial(target: Path) -> None:
    """删掉可能残留的半张封面（写到一半失败时会有）。"""
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001 - 清理失败不影响导入
        logger.debug("清理半成品封面失败：{}", exc)


def build_cover(source: str | Path, checksum: str, data_type: DataType) -> str:
    """按类型生成封面：只有视频写封面文件，图片与其它类型都返回空串。"""
    if data_type is DataType.VIDEO:
        return grab_video_frame(source, checksum)
    return ""


def reset_covers(session, *, on_event=None) -> dict[str, int]:
    """按规范重建全部封面：清空封面目录与 `cover_path`，再按当前数据重新生成。

    返回 `{"items": 有封面的项数, "covers": 生成的封面数, "cleared": 清掉的旧封面数,
    "failed": 生成失败数}`。图片按新规范不再有封面文件，所以 `covers` 只会数到视频。
    """
    directory = cover_dir()
    removed = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.is_file():
                try:
                    entry.unlink()
                    removed += 1
                except OSError as exc:  # noqa: BLE001
                    logger.debug("删除旧封面失败：{}", exc)
    directory.mkdir(parents=True, exist_ok=True)

    items = list(session.scalars(select(DataItem).where(DataItem.is_deleted.is_(False))))
    total = len(items)
    kept = generated = failed = 0
    for index, item in enumerate(items, start=1):
        if on_event is not None:
            on_event("cover", index, total, str(item.name or ""))
        source = _source_of(item)
        if item.type is DataType.VIDEO:
            cover = grab_video_frame(source, str(item.checksum or "")) if source else ""
            if cover and cover != str(item.cover_path or ""):
                item.cover_path = cover
                generated += 1
            elif not cover:
                failed += 1
            if cover:
                kept += 1
        elif item.cover_path:
            # 图片与其它类型按新规范不再持有封面文件
            item.cover_path = ""
    session.flush()
    return {"items": kept, "covers": generated, "cleared": removed, "failed": failed}


def _source_of(item: DataItem) -> str:
    """数据项在磁盘上的真实路径：**优先库内那一份**，库内没有了才用导入来源。

    不能借 `item_api._absolute_path()` 解析 `file_path`：那个函数优先返回导入时的外部
    `source_path`，原文件一被移走，相对路径就会被解析成一个不存在的来源路径，视频抽帧、
    扫描重建封面都会白白失败（用户 m01544 第 2 条：重置封面后部分数据退回默认封面）。
    """
    for raw in (str(item.file_path or ""), str(item.source_path or "")):
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            try:
                path = Path(library_root()) / path
            except Exception:  # noqa: BLE001 - 库根读不出来时跳过这一项
                continue
        if path.is_file():
            return str(path)
    return ""


__all__ = [
    "VIDEO_SUFFIXES",
    "build_cover",
    "grab_video_frame",
    "reset_covers",
]
