"""封面规范：图片直接用原文件，视频取第一帧，统一缓存到库内 `全局/covers/`。

封面规则（三处入口共用，导入、扫描登记、设置页「重置封面」）：

- **图片**：不写 `cover_path`——卡片直接用原文件作封面，省掉一份重复的缩略图，
  也不再依赖缓存目录里的副本（用户 m00003 第 3 条）。
- **视频**：导入时用 ffmpeg 抽第一帧，存到 `全局/covers/<checksum>.png` 并写进
  `cover_path`；抽帧失败（没装 ffmpeg、编码不支持）时退回空值，界面显示默认类型图标
  （用户 m00003 第 2 条）。
- 其它类型没有封面概念，`cover_path` 一律清空。

ffmpeg 的定位顺序：PATH 上的 `ffmpeg` → `imageio-ffmpeg` 自带的 ffmpeg →
`.models/runtime` 下的 venv（「模型」页装运行环境时顺带装的 imageio-ffmpeg）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from ..core.config import config, cover_dir, library_root
from ..db.models import DataItem, DataType

#: 单次抽帧的超时（秒）；超时当作抽帧失败，不拖住导入
FRAME_TIMEOUT = 30

VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg"}
)


def _ffmpeg_from_python() -> str:
    """`imageio-ffmpeg` 自带的 ffmpeg 可执行文件路径（没装则空串）。"""
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - 没装是正常情况
        return ""
    try:
        return str(imageio_ffmpeg.get_ffmpeg_exe() or "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("定位 imageio-ffmpeg 失败：{}", exc)
        return ""


def _ffmpeg_from_runtime() -> str:
    """模型运行环境（`<模型根>/runtime/<profile>/venv`）里装的 imageio-ffmpeg。

    模型根目录由 `lib.model` 路径层决定（默认程序目录下的 `.models`，用户可改到别处），
    所以这里把该插件模块当只读依赖导入；插件不可用时直接跳过（不抛错）。
    """
    try:
        from dm_plugin.lib.model import paths as model_paths  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - 插件没装 / 没启用是正常情况
        return ""
    try:
        root = Path(model_paths.runtime_root())
    except Exception as exc:  # noqa: BLE001 - 模型目录建不出来就用别的来源
        logger.debug("定位模型运行环境目录失败：{}", exc)
        return ""
    if not root.is_dir():
        return ""
    for venv in sorted(root.glob("*/venv")):
        for candidate in (venv / "Scripts" / "ffmpeg.exe", venv / "bin" / "ffmpeg"):
            if candidate.is_file():
                return str(candidate)
    return ""


def ffmpeg_executable() -> str:
    """找到可用的 ffmpeg（PATH → 当前解释器 → 模型运行环境）；没有返回空串。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for resolver in (_ffmpeg_from_python, _ffmpeg_from_runtime):
        path = resolver()
        if path and Path(path).is_file():
            return path
    return ""


def grab_video_frame(source: str | Path, checksum: str, size: int | None = None) -> str:
    """抽视频第一帧保存为封面，返回封面路径；失败返回空串。"""
    if not checksum:
        return ""
    executable = ffmpeg_executable()
    if not executable:
        logger.info("未找到 ffmpeg，视频封面退回默认图标")
        return ""
    covers = cover_dir()
    covers.mkdir(parents=True, exist_ok=True)
    target = covers / f"{checksum}.png"
    if target.exists():
        return str(target)
    limit = size or config.coverSize.value
    command = [
        executable,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={limit}:{limit}:force_original_aspect_ratio=decrease",
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=FRAME_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 - 抽帧失败不该拖垮导入
        logger.debug("抽取视频首帧失败：{}", exc)
        return ""
    if result.returncode != 0 or not target.is_file():
        logger.debug("抽取视频首帧失败：{}", result.stderr.decode("utf-8", "replace")[:200])
        return ""
    return str(target)


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
    "ffmpeg_executable",
    "grab_video_frame",
    "reset_covers",
]