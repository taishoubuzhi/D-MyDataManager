"""封面规范：图片直接用原文件，视频取第一帧，统一缓存到库内 `全局/covers/`。

封面规则（三处入口共用，导入、扫描登记、设置页「重置封面」）：

- **图片**：不写 `cover_path`——卡片直接用原文件作封面，省掉一份重复的缩略图，
  也不再依赖缓存目录里的副本（用户 m00003 第 3 条）。
- **视频**：导入时用媒体引擎（`app.services.media_service`）在进程内解出第一帧，存到
  `全局/covers/<checksum>.png` 并写进 `cover_path`；解不出来（文件损坏、编码不支持）时
  退回空值，界面显示默认类型图标（用户 m00003 第 2 条）。
- 其它类型没有封面概念，`cover_path` 一律清空。
- **自定义封面**：导入或编辑数据时用户挑的那张图，会缩放成
  `全局/covers/<checksum>-<图片摘要前 8 位>.png` 并写进 `cover_path`（用户 m02499 第 2 条）。
  名字里带图片摘要，是为了「换封面」不覆盖旧文件——存档条目按文件名引用封面，覆盖同一
  路径会让存档再也还原不回原来那张。换封面后没人引用的旧文件顺手删掉，减少冗余。
- **不选择时**才按上面的默认规则来（视频抽第一帧、图片用自身、其它用默认图标）。

抽帧不再依赖外部 ffmpeg 可执行文件（`av` 的 wheel 自带 FFmpeg 库），以前那套
「PATH → imageio-ffmpeg → 模型运行环境 venv」的定位逻辑整体删掉了。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select

from ..core.config import config, cover_dir, library_root
from ..db.models import ArchiveEntry, DataItem, DataType
from . import media_service
from .blob_store import sha256_of

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


def cover_name(item: DataItem) -> str:
    """数据项当前封面的文件名（没有封面返回空串）。"""
    return Path(str(item.cover_path or "")).name


def is_default_name(item: DataItem) -> bool:
    """当前封面是不是「按默认规则生成」的那张（`<checksum>.png`）。

    「重置封面」要靠它区分：默认封面可以随便重算，用户自己挑的封面不能动。
    """
    name = cover_name(item)
    return bool(name) and name == f"{item.checksum}.png"


def custom_cover_name(checksum: str, source: str | Path) -> str:
    """自定义封面的文件名：`<checksum>-<图片摘要前 8 位>.png`。

    名字里带图片摘要有两个好处：同一张图重复选中得到同一个名字（不会越攒越多）；
    换一张图必然换一个名字，旧封面文件得以留给存档条目继续引用。
    """
    key = str(checksum or "")
    try:
        digest = sha256_of(Path(source))
    except OSError as exc:
        logger.debug("算封面摘要失败（退回固定名字）：{}", exc)
        digest = ""
    return f"{key}-{digest[:8]}.png" if digest else f"{key}-custom.png"


def set_cover(session, item: DataItem, source: str | Path = "") -> str:
    """设置数据项封面，返回新的 `cover_path`；失败时原样返回（不抛错）。

    `source` 给图片路径就是「用这张图当封面」；给空串则是「恢复默认规则」（图片与其它
    类型清空封面路径、视频重新抽第一帧）。换封面成功后，没人再引用的旧封面文件会被删掉
    减少冗余，但存档条目引用着的一律保留。
    """
    old = str(item.cover_path or "")
    chosen = str(source or "")
    if chosen:
        if not Path(chosen).is_file():
            logger.warning("封面源文件不存在，保持原封面：{}", chosen)
            return old
        target = cover_dir() / custom_cover_name(str(item.checksum or ""), chosen)
        if not _write_custom_cover(chosen, target):
            return old
        new = str(target)
    else:
        new = build_cover(_source_of(item), str(item.checksum or ""), item.type)
    item.cover_path = new
    session.flush()
    if old and Path(old).name != Path(new).name:
        release_cover(session, old, keep=Path(new).name)
    return new


def release_cover(session, path: str | Path, *, keep: str = "") -> bool:
    """旧封面没人引用时删掉文件，返回是否真的删了。

    「有人引用」= 还有数据项（`cover_path` 指向这个文件）或存档条目（按文件名记着）在
    用它。存档要能还原封面变更，所以存档记过的封面即使当前没有数据项用也留着
    （用户 m02499 第 2 条：换封面要减冗余，但不能伤到存档）。
    """
    name = Path(str(path or "")).name
    if not name or name == keep:
        return False
    if _cover_in_use(session, name):
        return False
    target = cover_dir() / name
    try:
        if not target.is_file():
            return False
        target.unlink()
    except OSError as exc:  # noqa: BLE001 - 删不掉只是多占点空间，不影响数据正确性
        logger.debug("删除旧封面失败：{}", exc)
        return False
    return True


def _cover_in_use(session, name: str) -> bool:
    """还有数据项或存档条目引用这个封面文件名吗。"""
    target = str(cover_dir() / name)
    used = session.scalars(select(DataItem.id).where(DataItem.cover_path == target).limit(1)).first()
    if used is not None:
        return True
    archived = session.scalars(select(ArchiveEntry.id).where(ArchiveEntry.cover_path == name).limit(1)).first()
    return archived is not None


def _write_custom_cover(source: str | Path, target: Path) -> bool:
    """把用户挑的图片缩放后写成 PNG 封面；失败返回 False（不抛错）。

    缩放沿用封面配置的长边上限（`Storage/Cover-Size`），和视频抽帧一个规格。
    """
    try:
        from PIL import Image

        with Image.open(source) as image:
            image.load()
            limit = max(1, int(config.coverSize.value or 256))
            if max(image.size) > limit:
                image.thumbnail((limit, limit))
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, format="PNG")
        return True
    except Exception as exc:  # noqa: BLE001 - 用户给的图打不开或格式怪，算失败
        logger.warning("自定义封面写入失败：{}：{}", Path(source).name, exc)
        _remove_partial(target)
        return False


def reset_covers(session, *, on_event=None) -> dict[str, int]:
    """按规范重建全部封面：清空封面目录与 `cover_path`，再按当前数据重新生成。

    返回 `{"items": 有封面的项数, "covers": 生成的封面数, "cleared": 清掉的旧封面数,
    "failed": 生成失败数}`。图片按新规范不再有封面文件，所以 `covers` 只会数到视频。

    **用户自定义的封面不动**：重置只重算「按默认规则生成」的那些，否则一次重置就把手工
    挑的帧也清掉了（用户 m02499 第 2 条）。
    """
    directory = cover_dir()
    items = list(session.scalars(select(DataItem).where(DataItem.is_deleted.is_(False))))
    keep = {cover_name(item) for item in items if cover_name(item) and not is_default_name(item)}
    removed = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.is_file() and entry.name not in keep:
                try:
                    entry.unlink()
                    removed += 1
                except OSError as exc:  # noqa: BLE001
                    logger.debug("删除旧封面失败：{}", exc)
    directory.mkdir(parents=True, exist_ok=True)

    total = len(items)
    kept = generated = failed = 0
    for index, item in enumerate(items, start=1):
        if on_event is not None:
            on_event("cover", index, total, str(item.name or ""))
        source = _source_of(item)
        if item.type is DataType.VIDEO:
            if cover_name(item) in keep:
                kept += 1
                continue
            cover = grab_video_frame(source, str(item.checksum or "")) if source else ""
            if cover and cover != str(item.cover_path or ""):
                item.cover_path = cover
                generated += 1
            elif not cover:
                failed += 1
            if cover:
                kept += 1
        elif item.cover_path and not is_default_name(item):
            # 自定义封面在图片类上同样保留（用户挑的就是他想要的）
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
    "cover_name",
    "custom_cover_name",
    "grab_video_frame",
    "is_default_name",
    "release_cover",
    "reset_covers",
    "set_cover",
]
