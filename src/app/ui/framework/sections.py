"""页面骨架构件：标题区、分区卡片、工具条、空态。

页面的正文只能是这几种构件的组合，不允许把「裸」控件直接铺在页面上——
这是「每个页面看起来是同一个程序」的最低要求。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, FluentIcon, StrongBodyLabel, TitleLabel

from .tokens import CARD_SPACING, DETAIL_MARGINS, PANEL_MARGINS
from .tooltips import hint_badge


class ClickCard(CardWidget):
    """整块可点的卡片：按下与抬起都在卡片自己身上时发出 `clicked`。

    卡片里的按钮等子控件自己吃掉鼠标事件，所以点它们不会连带触发整块点击。
    """

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        inside = self.rect().contains(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if inside and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class PageHeader(QWidget):
    """统一标题区：标题 + 悬停说明，右侧 `actions` 行留给主操作按钮。

    说明文字不常显、直接铺在标题下面：它挂在标题上做悬停提示，
    鼠标停住才弹出来，页面看起来更干净。
    """

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
        # 小问号：告诉用户「这个标题上有说明，停一下就能看到」
        self.hint_badge = hint_badge("", self)
        row.addWidget(self.hint_badge)
        row.addStretch(1)

        #: 右侧操作区：挂 PushButton / 下拉框等主操作。
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        outer.addLayout(row)

        # 说明留在控件里（提示文案的唯一来源），但不显示在页面上
        self._subtitle = caption(self, subtitle)
        self._subtitle.setVisible(False)
        outer.addWidget(self._subtitle)
        self._hints: list[str] = []
        self._apply_subtitle()

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.setText(subtitle)
        self._apply_subtitle()

    def add_hint(self, text: str) -> None:
        """追加一句说明，同样只在鼠标停在标题上时显示。

        页面上原本铺开的整段说明（例如某一页的操作注意事项）改挂在这里。
        """
        text = str(text).strip()
        if not text:
            return
        self._hints.append(text)
        self._apply_subtitle()

    def set_hint(self, text: str) -> None:
        """整段替换说明（用于随状态变化的说明），同样只在悬停标题时显示。"""
        text = str(text).strip()
        self._hints = [text] if text else []
        self._apply_subtitle()

    def _apply_subtitle(self) -> None:
        """说明文字不常显：挂到标题上，鼠标停住才弹出提示。"""
        self._subtitle.setVisible(False)
        lines = [self._subtitle.text().strip(), *self._hints]
        text = "\n".join(line for line in lines if line)
        self._title.setToolTip(text)
        self.hint_badge.set_hint(text)

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
    """统一样式的分区卡片：标题 + 悬停说明 + 内容区。

    说明文字与页面副标题一样不常显：鼠标停在分区标题上才弹提示。
    """
    card, layout = panel_card(parent, margins=margins, spacing=spacing)
    title_label: StrongBodyLabel | None = None
    if title:
        title_label = StrongBodyLabel(title, card)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        layout.addLayout(title_row)
    if description:
        # 说明文字只留在控件里供提示使用，不占版面
        hint = caption(card, description)
        hint.setVisible(False)
        layout.addWidget(hint)
        if title_label is not None:
            title_label.setToolTip(description)
        else:
            card.setToolTip(description)
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
    # 上下留弹簧：外面给多了高度就把图标与文字整体居中，而不是把图标拉成一大块空白。
    layout.addStretch(1)
    if icon is not None:
        picture = BodyLabel("", host)
        picture.setPixmap(icon.icon().pixmap(24, 24))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
    label = CaptionLabel(text, host)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    layout.addWidget(label)
    layout.addStretch(1)
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
