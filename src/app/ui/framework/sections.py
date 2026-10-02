"""页面骨架构件：标题区、分区卡片、工具条、空态。

页面的正文只能是这几种构件的组合，不允许把「裸」控件直接铺在页面上——
这是「每个页面看起来是同一个程序」的最低要求。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, FluentIcon, StrongBodyLabel, TitleLabel

from .tokens import CARD_SPACING, DETAIL_MARGINS, PANEL_MARGINS


class PageHeader(QWidget):
    """统一标题区：标题 + 说明，右侧 `actions` 行留给主操作按钮。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._title = TitleLabel(title, self)
        row.addWidget(self._title)
        row.addStretch(1)

        #: 右侧操作区：挂 PushButton / 下拉框等主操作。
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        outer.addLayout(row)

        self._subtitle = CaptionLabel(subtitle, self)
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))
        outer.addWidget(self._subtitle)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


def page_header(parent: QWidget | None, title: str, subtitle: str = "") -> PageHeader:
    """建立统一标题区（由 `PageBase.add_header()` 调用）。"""
    return PageHeader(title, subtitle, parent)


def panel_card(
    parent: QWidget | None = None,
    *,
    margins: tuple[int, int, int, int] = PANEL_MARGINS,
    spacing: int = 8,
) -> tuple[CardWidget, QVBoxLayout]:
    """统一样式的面板卡片，返回 (卡片, 卡片内的竖直布局)。"""
    card = CardWidget(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return card, layout


def section_card(
    parent: QWidget | None = None,
    title: str = "",
    description: str = "",
    *,
    margins: tuple[int, int, int, int] = DETAIL_MARGINS,
    spacing: int = CARD_SPACING,
) -> tuple[CardWidget, QVBoxLayout]:
    """统一样式的分区卡片：标题 + 可选说明 + 内容区。"""
    card, layout = panel_card(parent, margins=margins, spacing=spacing)
    if title:
        layout.addWidget(StrongBodyLabel(title, card))
    if description:
        layout.addWidget(caption(card, description))
    return card, layout


def caption(parent: QWidget | None, text: str) -> CaptionLabel:
    """统一说明文字（自动换行）。"""
    label = CaptionLabel(text, parent)
    label.setWordWrap(True)
    return label


def empty_state(
    parent: QWidget | None = None,
    text: str = "暂无数据",
    *,
    icon: FluentIcon | None = None,
) -> QWidget:
    """统一空态：可选的图标 + 居中的说明文字。"""
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 24, 0, 24)
    layout.setSpacing(6)
    if icon is not None:
        picture = BodyLabel("", host)
        picture.setPixmap(icon.icon().pixmap(24, 24))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
    label = CaptionLabel(text, host)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    layout.addWidget(label)
    return host


def toolbar(parent: QWidget | None = None, *, spacing: int = 8) -> tuple[QWidget, QHBoxLayout]:
    """统一工具条：一行放筛选、搜索与批量操作按钮。"""
    host = QWidget(parent)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return host, layout


__all__ = [
    "PageHeader",
    "caption",
    "empty_state",
    "page_header",
    "panel_card",
    "section_card",
    "toolbar",
]
