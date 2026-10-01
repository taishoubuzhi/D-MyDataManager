"""内置文本查看器：页面由「内置弹窗页面」插件（builtin.dialog）承载。

协议要点：清单里 depends 声明依赖 builtin.dialog，register 时用 api.require("dialog") 确认
该弹窗插件已经注册；viewer 的 host="dialog" 让界面把内容放进该插件提供的弹窗外壳里。
"""

from __future__ import annotations

from pathlib import Path

from app.core.viewer_data import TEXT_EXTENSIONS

PLUGIN_NAME = "文本查看器"
VIEWER_KIND = "text"
CAPABILITIES = ("编码探测", "编码切换", "大文件截断", "行号统计")
DESCRIPTION = "查看纯文本与代码文件，支持编码探测、切换与大文件截断。"


def _factory(path: Path, parent=None):
    from app.ui.viewers.text_view import TextViewer

    return TextViewer(path, parent)


def register(api) -> None:
    api.require("dialog")
    api.add_viewer(
        PLUGIN_NAME,
        extensions=TEXT_EXTENSIONS,
        factory=_factory,
        kind=VIEWER_KIND,
        host="dialog",
        description=DESCRIPTION,
        capabilities=CAPABILITIES,
    )
