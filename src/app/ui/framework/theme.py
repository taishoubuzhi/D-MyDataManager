"""主题底色与调色板：让按调色板绘制的控件跟随 qfluentwidgets 主题。

qfluentwidgets 换肤只替换 QSS，不会更新 `QPalette`：系统处于深色模式时
`QPalette` 一直是深色，于是按调色板绘制的控件（滚动区视口、`ScrollArea.setWidget()`
后自动填充的容器、表头等）在浅色主题下仍然画成深色，露出「发黑的底色」。
所有页面统一经这里取底色，不再各自处理。
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import isDarkTheme, qconfig, themeColor
from qfluentwidgets.common.style_sheet import CustomStyleSheet, setStyleSheet

# 滚动区底色的处理住在 app.sdk.ui（插件也要用），这里重新导出给程序侧。
from ...sdk.ui import clear_scroll_background

#: 页面底色：与 qfluentwidgets 窗口背景一致（浅色 / 深色）。
PAGE_BG_LIGHT = QColor(240, 244, 249)
PAGE_BG_DARK = QColor(32, 32, 32)

_theme_hooked = False


def accent_color() -> QColor:
    """当前主题的强调色（跟随 qfluentwidgets 主题色）。"""
    return themeColor()


def accent_name() -> str:
    """当前主题强调色的十六进制写法，用于拼 QSS。"""
    return themeColor().name()


#: 首字头像 / 徽标的通用样式：中性灰底 + 调色板文字色。
AVATAR_PLAIN_QSS = (
    "background-color: rgba(128, 128, 128, 0.25); color: palette(text);"
    "border-radius: 8px; font-size: 18px; font-weight: 600;"
)
BADGE_PLAIN_QSS = (
    "color: palette(text); background-color: rgba(128, 128, 128, 0.18);"
    "border-radius: 8px; padding: 1px 8px;"
)


def _tinted(color: QColor, alpha: int) -> QColor:
    """取同色但指定不透明度的副本。"""
    return QColor(color.red(), color.green(), color.blue(), alpha)


def avatar_style(accent: bool = False) -> str:
    """首字头像样式；`accent=True` 时用主题强调色（当前用户）。"""
    if not accent:
        return AVATAR_PLAIN_QSS
    return (
        f"background-color: {accent_name()}; color: white; border-radius: 8px;"
        "font-size: 18px; font-weight: 600;"
    )


def badge_style(accent: bool = False) -> str:
    """徽标样式；`accent=True` 时用主题强调色（当前用户）。"""
    if not accent:
        return BADGE_PLAIN_QSS
    return f"color: white; background-color: {accent_name()}; border-radius: 8px; padding: 1px 8px;"


def highlight_fill() -> QColor:
    """当前用户卡片的浅色填充（主题色 11% 不透明度）。"""
    return _tinted(accent_color(), 28)


def highlight_hover() -> QColor:
    """当前用户卡片悬停时的填充（主题色 16% 不透明度）。"""
    return _tinted(accent_color(), 40)


# clear_scroll_background 见 app.sdk.ui，本模块只重新导出。


def theme_palette(dark: bool | None = None) -> QPalette:
    """构造跟随主题的调色板（窗口底色 / 文本 / 强调色 / 禁用态）。"""
    dark = isDarkTheme() if dark is None else dark
    window = PAGE_BG_DARK if dark else PAGE_BG_LIGHT
    base = QColor(45, 45, 45) if dark else QColor(255, 255, 255)
    button = QColor(54, 54, 54) if dark else QColor(252, 252, 252)
    text = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
    accent = accent_color()

    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, window),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, button),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.ToolTipBase, base),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.PlaceholderText, QColor(text.red(), text.green(), text.blue(), 130)),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, QColor(255, 255, 255)),
    ):
        palette.setColor(role, color)

    disabled_text = QColor(text)
    disabled_text.setAlpha(110)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)
    return palette


def apply_app_palette() -> None:
    """把当前主题的调色板装到 QApplication 上。"""
    app = QApplication.instance()
    if app is None:
        return
    app.setPalette(theme_palette())


def install_app_theme() -> None:
    """启动时调用一次：装好调色板，并让它在主题切换后跟着变（幂等）。"""
    global _theme_hooked
    apply_app_palette()
    if not _theme_hooked:
        qconfig.themeChangedFinished.connect(apply_app_palette)
        _theme_hooked = True


__all__ = [
    "AVATAR_PLAIN_QSS",
    "BADGE_PLAIN_QSS",
    "PAGE_BG_DARK",
    "PAGE_BG_LIGHT",
    "accent_color",
    "accent_name",
    "apply_app_palette",
    "avatar_style",
    "badge_style",
    "clear_scroll_background",
    "highlight_fill",
    "highlight_hover",
    "install_app_theme",
    "theme_palette",
]
