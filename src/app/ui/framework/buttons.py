"""全站统一的「图标 + 文本」按钮。

同时带图标与文本的按钮都用这里的两个类，不要再写 `PushButton(FluentIcon.X, "文本", parent)`：
开启「简化显示」（设置页 · 外观）后它们只显示图标、缩成正方形，完整文案转成工具提示，
鼠标停在按钮上仍能看懂它做什么；纯文本按钮（`PushButton("文本", parent)`）不受影响。
页面、弹窗与插件界面一律用它们，所以新增的按钮界面天然跟着这个开关走。

子类化 qfluentwidgets 按钮的坑：`PushButton.__init__` 是 `singledispatchmethod`，
内部用 `self.__init__(...)` 转调别的重载，子类若重写 `__init__` 再转调就会无限递归；
这里只调用默认重载 `super().__init__(parent)`，图标与文本随后手动设置。
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QWIDGETSIZE_MAX, QWidget
from qfluentwidgets import PrimaryPushButton, PushButton
from qfluentwidgets.common.style_sheet import setCustomStyleSheet

from ...core.config import config

# noqa: F401 - simple_display 从本模块再导出（历史入口，逐控件判定在 should_simplify）
from .simple_mode import icon_key, refresh_peers, should_simplify, simple_display
from .theme import SQUARE_BUTTON_QSS

#: 简化显示下正方形按钮相对图标多留的边距
SQUARE_PADDING = 8


class IconTextButton(PushButton):
    """图标 + 文本按钮：简化显示下缩成正方形只显示图标，完整文案留在工具提示里。"""

    def __init__(self, icon=None, text: str = "", parent: QWidget | None = None) -> None:
        # PushButton.__init__ 里就会调 setIcon -> _apply_display，状态得先备好
        self._full_text = ""
        self._auto_tip = False
        self._simple = False
        self._plain_minimum = (0, 0)
        self._icon_key: str | None = None
        self._ready = False
        super().__init__(parent)
        self.setIcon(icon)
        self.setText(text)
        config.simpleDisplay.valueChanged.connect(self._apply_display)
        self._ready = True
        # 加入容器后要按「同容器里有没有同款图标」重算（默认挡位只简化不会混淆的图标）
        refresh_peers(self)

    # ------------------------------------------------------------------ 文案
    @property
    def full_text(self) -> str:
        """完整文案：简化显示下 `text()` 是空串，这里始终是按钮本来的文字。"""
        return self._full_text

    def setIcon(self, icon) -> None:
        self._icon_key = icon_key(icon)
        super().setIcon(icon)
        self._apply_display()
        if self._ready:
            refresh_peers(self)

    @property
    def full_text(self) -> str:
        """完整文字（简化显示下按钮只显示图标，文字保存在这里）。"""
        return self._full_text

    def setText(self, text: str) -> None:
        self._full_text = str(text or "")
        self._apply_display()

    def _apply_display(self, *_args) -> None:
        # 没有图标的按钮仍然显示文字：否则简化显示下会变成一个空按钮
        key = None if self.icon().isNull() else self._icon_key
        simple = should_simplify(self, key)
        self._simple = simple
        super().setText("" if simple else self._full_text)
        self._apply_size(simple)
        self._apply_tooltip(simple)

    # ------------------------------------------------------------------ 尺寸
    # 最小宽度本来是照着文字给的；简化显示下按钮只由图标决定大小，所以先记下来、暂时不生效
    def setMinimumWidth(self, width: int) -> None:  # noqa: N802
        self._remember_minimum(int(width), self._plain_minimum[1])

    def setMinimumHeight(self, height: int) -> None:  # noqa: N802
        self._remember_minimum(self._plain_minimum[0], int(height))

    def setMinimumSize(self, *args) -> None:  # noqa: N802
        if len(args) == 2:
            width, height = int(args[0]), int(args[1])
        else:
            size = args[0]
            width, height = int(size.width()), int(size.height())
        self._remember_minimum(width, height)

    def _remember_minimum(self, width: int, height: int) -> None:
        self._plain_minimum = (max(0, width), max(0, height))
        if not self._simple:
            super().setMinimumSize(QSize(width, height))

    def _apply_size(self, simple: bool) -> None:
        """简化显示下缩成正方形（图标居中）；关掉后把尺寸交还给文字与布局。"""
        if simple:
            side = max(self.sizeHint().height(), self.iconSize().height() + 2 * SQUARE_PADDING)
            # 样式里的内边距比方形按钮还宽，会把图标裁掉：方形模式下清零。
            # 只能追加自定义样式，不能 setStyleSheet 覆盖：覆盖会把 qfluentwidgets 在
            # PushButton.__init__ 里装的整份 button.qss 冲掉 —— 主色按钮失去强调色底（深色主题下
            # 反转成深色的图标就看不见了），给图标让位的内边距也一起消失、文字会压到图标上。
            setCustomStyleSheet(self, SQUARE_BUTTON_QSS, SQUARE_BUTTON_QSS)
            self.setFixedSize(side, side)
            return
        setCustomStyleSheet(self, "", "")
        width, height = self._plain_minimum
        super().setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
        super().setMinimumSize(QSize(width, height))
        # setFixedSize 把 min/max 一起钉在方形上，页面没显示过时布局不会来重排，按钮会一直是方形，
        # 图标与文字就挤在一起：这里主动换回文字尺寸
        self.resize(self._plain_size())

    def _plain_size(self) -> QSize:
        """文字模式下的推荐尺寸：页面没显示过、布局不会来重排时也能自己恢复。"""
        hint = super().sizeHint()
        width, height = self._plain_minimum
        return QSize(max(hint.width(), width), max(hint.height(), height))

    def _apply_tooltip(self, simple: bool) -> None:
        """简化显示下把完整文案填进工具提示；原本就有提示的按钮保持原样。"""
        if simple and self._full_text and not super().toolTip():
            super().setToolTip(self._full_text)
            self._auto_tip = True
        elif self._auto_tip and not simple:
            super().setToolTip("")
            self._auto_tip = False


class IconTextPrimaryButton(PrimaryPushButton, IconTextButton):
    """主色版本的图标 + 文本按钮，用法与 `IconTextButton` 相同。

    基类顺序不能反：PyQt 的多继承只保留**第一个**基类的 QMetaObject 链，写成
    `(IconTextButton, PrimaryPushButton)` 时链上没有 `PrimaryPushButton`，
    qfluentwidgets 的 `PrimaryPushButton { background-color: --ThemeColorPrimary; }`
    永远匹配不到，按钮会掉回普通按钮的底色（深色主题下反转图标落在深色底上，就是
    「图标没适配深色主题」），图标偏移的内边距也会一起丢。
    """


__all__ = ["IconTextButton", "IconTextPrimaryButton", "SQUARE_PADDING", "simple_display"]
