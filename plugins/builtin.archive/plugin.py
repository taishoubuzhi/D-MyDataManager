"""内置压缩包查看器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认
该弹窗插件已经注册；viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里。
"""

from __future__ import annotations

from pathlib import Path

from app.core.viewer_data import ARCHIVE_EXTENSIONS

PLUGIN_NAME = "压缩包查看器"
VIEWER_KIND = "archive"
CAPABILITIES = ("条目列表", "压缩比", "包内文本预览")
DESCRIPTION = "列出压缩包内的条目与大小，支持预览包内文本。"


def _factory(path: Path, parent=None):
    from app.ui.viewers.archive_view import ArchiveViewer

    return ArchiveViewer(path, parent)


def register(api) -> None:
    api.require("dialog")
    api.add_viewer(
        PLUGIN_NAME,
        extensions=ARCHIVE_EXTENSIONS,
        factory=_factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
