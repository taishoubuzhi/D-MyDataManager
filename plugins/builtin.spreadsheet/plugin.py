"""内置表格查看器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认
该弹窗插件已经注册；viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里。
"""

from __future__ import annotations

from pathlib import Path

from app.core.viewer_data import SPREADSHEET_EXTENSIONS

PLUGIN_NAME = "表格查看器"
VIEWER_KIND = "spreadsheet"
CAPABILITIES = ("工作表切换", "前 300 行预览", "分隔符识别")
DESCRIPTION = "查看 xlsx / csv 表格，支持工作表切换与前若干行预览。"


def _factory(path: Path, parent=None):
    from app.ui.viewers.sheet_view import SheetViewer

    return SheetViewer(path, parent)


def register(api) -> None:
    api.require("dialog")
    api.add_viewer(
        PLUGIN_NAME,
        extensions=SPREADSHEET_EXTENSIONS,
        factory=_factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
