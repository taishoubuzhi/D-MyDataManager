"""悬停提示：统一的等待时间，以及「没写提示的按钮用它的文字兜底」。

鼠标停在按钮或标题上多久才弹出提示由配置项 `Layout/Tooltip-Delay` 决定
（设置页 · 外观 →「悬停提示延迟」），改完立即生效，不用重启。

页面上原本直接铺开的说明文字（页面副标题、分区说明）不再常显，
改成挂在标题上的悬停提示，见 `sections.py`；插件给自定义控件加说明时，
要么 `setToolTip()`，要么设 `hoverHint` 属性。

提示框里的文字先经 `wrap_hint()` 按显示宽度折行，宽度不再随说明长短无限延长；
`hint_badge()` 是挂在说明性控件右侧的小问号，用来告诉用户「这里可以悬停」。
"""

from __future__ import annotations

import unicodedata

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QProxyStyle, QStyle, QToolTip, QWidget

from ...core.config import config
from .theme import HINT_BADGE_QSS

#: 配置项缺失或损坏时用的等待时间（毫秒）
DEFAULT_DELAY = 2000
#: 等待时间上限，避免填出「等不到」的值
MAX_DELAY = 10_000
#: 控件属性名：设了它就当作悬停提示（给自定义控件补说明用）
HINT_PROPERTY = "hoverHint"
#: 往上找几层父控件：卡片上的说明也好让里面的子控件用上
MAX_PARENT_HOPS = 4
#: 提示框一行最宽多少（半角字符宽度，中文算两个）：超出就折行，提示框始终近似矩形
HINT_COLUMNS = 44
#: 小问号标记的边长（像素）
HINT_BADGE_SIZE = 14
#: 折行时，行尾这段短于多少字符就不再拆词、整段挪到下一行
_WORD_KEEP = 12


def hover_delay() -> int:
    """悬停多久后弹出提示（毫秒）；每次弹提示都重新读配置，改完立即生效。"""
    try:
        value = int(config.tooltipDelay.value)
    except (TypeError, ValueError):
        return DEFAULT_DELAY
    return max(0, min(MAX_DELAY, value))


class HoverStyle(QProxyStyle):
    """只改 Qt 工具提示的等待时间，其余画法交给原来那个样式。"""

    def styleHint(self, hint, option=None, widget=None, data=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return hover_delay()
        if hint == QStyle.StyleHint.SH_ToolTip_FallAsleepDelay:
            # 0：每次都重新等满配置的时间，扫过一排按钮时不会立刻弹出一片提示
            return 0
        return super().styleHint(hint, option, widget, data)


def own_hint(widget: QWidget) -> str:
    """控件自己身上的提示：显式提示 > `hoverHint` 属性 > 按钮文字。"""
    text = str(widget.toolTip() or "").strip()
    if text:
        return text
    hint = widget.property(HINT_PROPERTY)
    if hint:
        return str(hint).strip()
    if isinstance(widget, QAbstractButton):
        return str(widget.text() or "").replace("&", "").strip()
    return ""


def describe(widget: QWidget) -> str:
    """控件该显示的悬停提示：自己的提示优先，没有就借最近一层父控件的提示。

    卡片、列表行这类把说明写在容器上的地方，鼠标落在里面的标题或数字上
    也能看到同一句说明，不用逐个控件再写一遍。
    """
    text = own_hint(widget)
    if text:
        return text
    parent = widget.parentWidget()
    for _hop in range(MAX_PARENT_HOPS):
        if parent is None:
            break
        text = own_hint(parent)
        if text:
            return text
        parent = parent.parentWidget()
    return ""


def display_width(text: str) -> int:
    """文本的显示宽度：中西文混排时按半角字符折算（中文算两个）。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def wrap_hint(text: str, columns: int = HINT_COLUMNS) -> str:
    """把长提示折成近似矩形的多行文本，提示框宽度不再随说明长短变化。

    已有换行照旧保留；英文单词尽量整词换行，实在放不下才从中间断开，行首的空格丢掉。
    """
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        current = ""
        width = 0
        for char in paragraph:
            if not current and char == " ":
                continue  # 行首的空格丢掉：折行后不会缩进一格
            step = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
            if current and width + step > columns:
                head, _space, tail = current.rpartition(" ")
                if head and len(tail) < _WORD_KEEP:
                    lines.append(head.rstrip())
                    current, width = tail, display_width(tail)
                else:
                    lines.append(current.rstrip())
                    current, width = "", 0
                if char == " ":
                    continue  # 换行处的空格丢掉，下一行不会缩进一格
            current += char
            width += step
        lines.append(current.rstrip())
    return "\n".join(lines).strip("\n")


class HintBadge(QLabel):
    """小问号标记：挂在会弹说明的控件右侧，提示用户这里可以悬停。

    标记自己带同一段说明，鼠标停在问号上也能看到；没有说明时自己藏起来，不留空位。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("?", parent)
        self.setObjectName("hintBadge")
        self.setFixedSize(HINT_BADGE_SIZE, HINT_BADGE_SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setStyleSheet(HINT_BADGE_QSS)
        self._hint = ""
        self.set_hint(text)

    @property
    def hint(self) -> str:
        """标记挂着的说明。"""
        return self._hint

    def set_hint(self, text: str) -> None:
        """换掉说明；空说明时整枚标记隐藏。"""
        self._hint = str(text or "").strip()
        self.setProperty(HINT_PROPERTY, self._hint)
        self.setToolTip(self._hint)
        self.setVisible(bool(self._hint))


def hint_badge(text: str = "", parent: QWidget | None = None) -> HintBadge:
    """造一枚小问号标记，放在带说明的控件右侧。"""
    return HintBadge(text, parent)


class TooltipFilter(QObject):
    """鼠标进入控件时补上它自己的文字；弹提示时统一折行。"""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            return self._show(obj, event)
        if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
            self._fill(obj)
        return False

    @staticmethod
    def _show(obj, event) -> bool:
        """自己弹提示：折好行再显示，长说明不会拉出一条超宽的提示框。"""
        if not isinstance(obj, QWidget):
            return False
        text = describe(obj)
        if not text:
            return False
        QToolTip.showText(event.globalPos(), wrap_hint(text), obj)
        return True

    @staticmethod
    def _fill(obj) -> None:
        if not isinstance(obj, QWidget) or obj.toolTip():
            return
        text = describe(obj)
        if text:
            obj.setToolTip(text)


_style: HoverStyle | None = None
_filter: TooltipFilter | None = None


def install_tooltips(app: QApplication | None = None) -> None:
    """启动时调用一次（幂等）：装好延迟样式与补提示的事件过滤器。"""
    global _style, _filter
    target = app or QApplication.instance()
    if target is None:
        return
    if _style is None:
        # 拿当前样式当基底：只动工具提示的等待时间，别改别的画法
        _style = HoverStyle(target.style())
        target.setStyle(_style)
    if _filter is None:
        _filter = TooltipFilter(target)
        target.installEventFilter(_filter)


__all__ = [
    "DEFAULT_DELAY",
    "HINT_BADGE_SIZE",
    "HINT_COLUMNS",
    "HINT_PROPERTY",
    "MAX_DELAY",
    "MAX_PARENT_HOPS",
    "HintBadge",
    "HoverStyle",
    "TooltipFilter",
    "describe",
    "display_width",
    "hint_badge",
    "hover_delay",
    "install_tooltips",
    "own_hint",
    "wrap_hint",
]
