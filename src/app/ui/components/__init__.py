"""可复用界面组件：不持有业务状态，只依赖 `framework` 与只读数据。

约定：组件不 import 任何具体页面；需要写数据的组件只发出信号，由页面调用 service。
"""

from __future__ import annotations

from .archive_restore_dialog import RestoreDialog
from .flow_area import FlowArea
from .plugin_delegate import BADGES_ROLE, SUBTITLE_ROLE, TITLE_ROLE, PluginItemDelegate

__all__ = [
    "BADGES_ROLE",
    "SUBTITLE_ROLE",
    "TITLE_ROLE",
    "FlowArea",
    "PluginItemDelegate",
    "RestoreDialog",
]
