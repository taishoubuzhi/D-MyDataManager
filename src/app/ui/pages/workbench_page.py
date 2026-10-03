"""页面管理：列出全部页面，从这里打开任意一页。

左侧导航的顺序是固定的：内置页面按内置顺序（设置恒在最下面），插件页面按载入顺序追加；
追加不下的插件页面只在这里出现。这一页是只读的，不做任何布局改动。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PushButton,
    StrongBodyLabel,
)

from ...core.signals import signalBus
from ..framework import COMPACT_MARGINS, ROW_SPACING, ScrollPage, clear_layout
from ..framework import IconTextButton

#: 本页自己的路由名。
WORKBENCH_ROUTE = "workbenchPage"


@dataclass(frozen=True)
class NavEntry:
    """一个页面在导航里的样子（由主窗口列出全部页面）。"""

    widget: QWidget
    icon: FluentIcon
    title: str
    builtin: bool = True
    plugin_id: str = ""
    bottom: bool = False
    in_sidebar: bool = True

    @property
    def route(self) -> str:
        """路由名：页面控件的 objectName，导航项也用它。"""
        return self.widget.objectName()

    @property
    def kind_text(self) -> str:
        """页面来源，用于行内副标题。"""
        if self.builtin:
            return "内置页面"
        return f"插件页面 · {self.plugin_id}" if self.plugin_id else "插件页面"

    @property
    def place_text(self) -> str:
        """这一页现在从哪里能点到。"""
        return "显示在左侧导航" if self.in_sidebar else "插件页面过多，只在页面管理里打开"


class WorkbenchPage(ScrollPage):
    page_name = WORKBENCH_ROUTE
    page_title = "页面管理"
    page_subtitle = "全部页面一览；左侧导航顺序固定，插件页面过多时多出来的只在这里打开。"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.section_card, self.rows_layout = self.add_section(
            "页面",
            "内置页面与插件页面都在这里列出，点「打开」即可切过去；左侧导航顺序固定：设置恒在最下面，"
            "插件页面按载入顺序追加，追加不下的只在这里打开。",
        )
        self.rows_layout.setSpacing(ROW_SPACING)
        self.rows: list[QWidget] = []
        self.auto_refresh(signalBus.pluginsChanged)

    # -------------------------------------------------------------- 主窗口
    def _host(self):
        """主窗口（提供页面清单与打开操作）；不在主窗口里时返回 None。"""
        window = self.window()
        return window if hasattr(window, "page_entries") else None

    # -------------------------------------------------------------- 渲染
    def refresh(self) -> None:
        clear_layout(self.rows_layout)
        self.rows = []
        host = self._host()
        if host is None:
            return
        for entry in host.page_entries():
            row = self._build_row(entry)
            self.rows_layout.addWidget(row)
            self.rows.append(row)
        self.rows_layout.addStretch(1)

    def _build_row(self, entry: NavEntry) -> QWidget:
        row = CardWidget(self)
        row.setObjectName(f"navRow.{entry.route}")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(*COMPACT_MARGINS)
        layout.setSpacing(ROW_SPACING)

        icon = BodyLabel(row)
        icon.setPixmap(entry.icon.icon().pixmap(20, 20))
        layout.addWidget(icon)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addWidget(StrongBodyLabel(entry.title, row))
        text.addWidget(CaptionLabel(f"{entry.kind_text} · {entry.place_text}", row))
        layout.addLayout(text, 1)

        open_button = IconTextButton(FluentIcon.VIEW, "打开", row)
        open_button.clicked.connect(lambda: self._on_open(entry.route))
        layout.addWidget(open_button)

        row.route = entry.route
        row.title_text = entry.title
        row.open_button = open_button
        return row

    def row_for(self, route: str) -> QWidget | None:
        """按路由找行（检查与联调用）。"""
        for row in self.rows:
            if getattr(row, "route", None) == route:
                return row
        return None

    # -------------------------------------------------------------- 交互
    def _on_open(self, route: str) -> None:
        host = self._host()
        if host is not None:
            host.open_navigation(route)


__all__ = ["NavEntry", "WORKBENCH_ROUTE", "WorkbenchPage"]
