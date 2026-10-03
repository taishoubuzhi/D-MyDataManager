"""图标标签：常驻文本换成图标，完整文字留在悬停提示里。

按钮走 `IconTextButton`，纯标签走这里的 `IconTextLabel`：`icon_only=True` 时永远只画图标
（「当前用户」这种一眼可辨的说明），否则平时「图标 + 文本」、简化显示下只留图标，
鼠标停在上面仍能看到完整文字（提示延迟由 `Layout/Tooltip-Delay` 决定）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
from qfluentwidgets.common.font import setFont

from ...core.config import config
from .simple_mode import icon_key, refresh_peers, should_simplify

ICON_LABEL_SIZE = 18
STRONG_FONT_SIZE = 14


def _qicon(icon) -> QIcon | None:
    """`FluentIcon`、`QIcon` 与 `None` 都收，取不到就当没有图标。"""
    if icon is None:
        return None
    if isinstance(icon, QIcon):
        return icon
    getter = getattr(icon, "icon", None)
    return getter() if callable(getter) else None


class IconTextLabel(QWidget):
    """图标 + 文本标签；简化显示下只留图标，完整文字仍在提示里。"""

    def __init__(
        self,
        icon=None,
        text: str = "",
        parent: QWidget | None = None,
        *,
        icon_only: bool = False,
        strong: bool = False,
        keep_text: bool = False,
    ) -> None:
        super().__init__(parent)
        self._full_text = str(text)
        self._icon_key: str | None = None
        self._ready = False
        self._icon = _qicon(icon)
        self._icon_key = icon_key(icon)
        self._icon_only = bool(icon_only)
        # 有些信息行只有图标看不懂（用户卡片的数据量 / 分类数 / 创建时间），它们不跟着简化显示走
        self._keep_text = bool(keep_text)

        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(ICON_LABEL_SIZE, ICON_LABEL_SIZE)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label = QLabel(self._full_text, self)
        if strong:
            setFont(self._text_label, STRONG_FONT_SIZE, QFont.Weight.DemiBold)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        # 必须传对齐参数：默认会让两个子控件按比例拉伸，宽标签里的图标与文字就被推到中间
        row.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._text_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._apply_display()
        config.simpleDisplay.valueChanged.connect(self._apply_display)
        self._ready = True
        # 加入容器后按「同容器里有没有同款图标」重算（默认挡位只简化不会混淆的图标）
        refresh_peers(self)

    def setIcon(self, icon) -> None:  # noqa: N802 - 跟随 Qt 命名
        self._icon = _qicon(icon)
        self._icon_key = icon_key(icon)
        self._apply_display()
        if self._ready:
            refresh_peers(self)

    def setText(self, text: str) -> None:  # noqa: N802 - 跟随 Qt 命名
        self._full_text = str(text)
        self._text_label.setText(self._full_text)
        self._apply_display()

    def text(self) -> str:  # noqa: N802 - 跟随 Qt 命名
        return self._full_text

    def _apply_display(self) -> None:
        simple = self._icon is not None and (
            self._icon_only or should_simplify(self, self._icon_key, keep_text=self._keep_text)
        )
        if self._icon is None:
            self._icon_label.hide()
        else:
            self._icon_label.setPixmap(self._icon.pixmap(ICON_LABEL_SIZE, ICON_LABEL_SIZE))
            self._icon_label.show()
        self._text_label.setVisible(not simple)
        self.setToolTip(self._full_text)
        self.updateGeometry()


def icon_text_label(
    icon, text: str, parent: QWidget | None = None, *, strong: bool = False, keep_text: bool = False
) -> IconTextLabel:
    """图标 + 文本标签：简化显示下只留图标；`keep_text=True` 的信息行始终保留文字。"""
    return IconTextLabel(icon, text, parent, strong=strong, keep_text=keep_text)


def icon_label(icon, text: str, parent: QWidget | None = None) -> IconTextLabel:
    """只有图标的标签：完整文字进提示。"""
    return IconTextLabel(icon, text, parent, icon_only=True)
