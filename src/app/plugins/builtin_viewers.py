"""内置查看器插件：以内置插件的形式载入，可在「插件」页里启用 / 禁用。

每个内置插件只声明扩展名与控件工厂，控件模块在真正打开文件时才导入 Qt。
"""

from __future__ import annotations

from ..core.plugins import KIND_VIEWER, SOURCE_BUILTIN, PluginInfo
from ..core.viewer_data import (
    ARCHIVE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SPREADSHEET_EXTENSIONS,
    TEXT_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

VERSION = "1.0.0"


def _image_factory(path, parent):
    from ..ui.viewers.image_view import ImageViewer

    return ImageViewer(path, parent)


def _video_factory(path, parent):
    from ..ui.viewers.media_view import VideoViewer

    return VideoViewer(path, parent)


def _audio_factory(path, parent):
    from ..ui.viewers.media_view import AudioViewer

    return AudioViewer(path, parent)


def _text_factory(path, parent):
    from ..ui.viewers.text_view import TextViewer

    return TextViewer(path, parent)


def _markdown_factory(path, parent):
    from ..ui.viewers.markdown_view import MarkdownViewer

    return MarkdownViewer(path, parent)


def _archive_factory(path, parent):
    from ..ui.viewers.archive_view import ArchiveViewer

    return ArchiveViewer(path, parent)


def _sheet_factory(path, parent):
    from ..ui.viewers.sheet_view import SheetViewer

    return SheetViewer(path, parent)


#: (插件 id, 插件名, 说明, 查看器分类, 扩展名, 能力, 控件工厂)
_SPECS: tuple[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], object], ...] = (
    (
        "builtin.image",
        "内置图片查看器",
        "查看 png / jpg / gif / webp 等图片，支持缩放、适应窗口、旋转与上下张切换。",
        "image",
        IMAGE_EXTENSIONS,
        ("zoom", "rotate", "browse"),
        _image_factory,
    ),
    (
        "builtin.video",
        "内置视频播放器",
        "用 Qt Multimedia 播放 mp4 / mkv / mov 等视频，含播放、进度、音量与全屏。",
        "video",
        VIDEO_EXTENSIONS,
        ("play", "seek", "volume"),
        _video_factory,
    ),
    (
        "builtin.audio",
        "内置音频播放器",
        "播放 mp3 / wav / flac 等音频，含播放、进度与音量控制。",
        "audio",
        AUDIO_EXTENSIONS,
        ("play", "seek", "volume"),
        _audio_factory,
    ),
    (
        "builtin.text",
        "内置文本查看器",
        "查看 txt / log / json 与各类代码、配置文件，自动识别编码，可切换编码与自动换行。",
        "text",
        TEXT_EXTENSIONS,
        ("encoding", "wrap"),
        _text_factory,
    ),
    (
        "builtin.markdown",
        "内置 Markdown 查看器",
        "渲染 Markdown 的标题、列表、表格与代码块，可在渲染视图与源码之间切换。",
        "markdown",
        ("md", "markdown"),
        ("render", "source"),
        _markdown_factory,
    ),
    (
        "builtin.archive",
        "内置压缩包查看器",
        "列出 zip / tar / gz 等压缩包的成员，预览其中的文本内容，并可解压后用系统程序打开。",
        "archive",
        ARCHIVE_EXTENSIONS,
        ("list", "preview", "extract"),
        _archive_factory,
    ),
    (
        "builtin.spreadsheet",
        "内置表格查看器",
        "查看 xlsx / csv / tsv，多工作表切换；xlsx 由程序直接解析，不依赖第三方库。",
        "spreadsheet",
        SPREADSHEET_EXTENSIONS,
        ("sheets", "grid"),
        _sheet_factory,
    ),
)


def _make_register(
    name: str,
    kind: str,
    extensions: tuple[str, ...],
    description: str,
    capabilities: tuple[str, ...],
    factory: object,
):
    def register(api) -> None:
        api.add_viewer(
            name=name.replace("内置", "", 1).strip(),
            extensions=extensions,
            factory=factory,
            kind=kind,
            description=description,
            capabilities=capabilities,
        )

    return register


def builtin_plugins() -> list[tuple[PluginInfo, object]]:
    """内置插件清单：(PluginInfo, register 函数)，顺序即界面展示顺序。"""
    plugins: list[tuple[PluginInfo, object]] = []
    for plugin_id, name, description, kind, extensions, capabilities, factory in _SPECS:
        info = PluginInfo(
            id=plugin_id,
            name=name,
            version=VERSION,
            kind=KIND_VIEWER,
            description=description,
            author="内置",
            source=SOURCE_BUILTIN,
            extensions=tuple(extensions),
            capabilities=tuple(capabilities),
        )
        plugins.append((info, _make_register(name, kind, extensions, description, capabilities, factory)))
    return plugins
