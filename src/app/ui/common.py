"""界面通用工具：格式化、图标映射、提示条。"""

from __future__ import annotations

import datetime as dt
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    CardWidget,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    StateToolTip,
    TitleLabel,
    isDarkTheme,
    qconfig,
    themeColor,
)
from qfluentwidgets.common.style_sheet import CustomStyleSheet, setStyleSheet

from ..db.models import DATA_TYPE_NAMES, DataType

TYPE_ICONS: dict[str, FluentIcon] = {
    DataType.IMAGE.value: FluentIcon.PHOTO,
    DataType.VIDEO.value: FluentIcon.VIDEO,
    DataType.AUDIO.value: FluentIcon.MUSIC,
    DataType.DOCUMENT.value: FluentIcon.DOCUMENT,
    DataType.SPREADSHEET.value: FluentIcon.DOCUMENT,
    DataType.PRESENTATION.value: FluentIcon.VIEW,
    DataType.ARCHIVE.value: FluentIcon.ZIP_FOLDER,
    DataType.CODE.value: FluentIcon.CODE,
    DataType.TEXT.value: FluentIcon.FONT,
    DataType.OTHER.value: FluentIcon.LABEL,
}


def type_key(value) -> str:
    """把 DataType 或数据库里读回的字符串统一成枚举值字符串。"""
    return str(value).split(".")[-1]


def type_name(value) -> str:
    try:
        return DATA_TYPE_NAMES[DataType(type_key(value))]
    except (ValueError, KeyError):
        return "其他"


def type_icon(value) -> FluentIcon:
    return TYPE_ICONS.get(type_key(value), FluentIcon.LABEL)


def format_size(size: int | None) -> str:
    value = float(size or 0)
    if value < 1024:
        return f"{value:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def format_datetime(value: dt.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return value.strftime(fmt) if isinstance(value, dt.datetime) else ""


def elide(text: str, limit: int = 60) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def toast_success(parent, title: str, content: str = "") -> None:
    InfoBar.success(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=2500,
        parent=parent,
    )


def toast_warning(parent, title: str, content: str = "") -> None:
    InfoBar.warning(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=3000,
        parent=parent,
    )


def toast_error(parent, title: str, content: str = "") -> None:
    InfoBar.error(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=4000,
        parent=parent,
    )


def confirm(parent, title: str, content: str) -> bool:
    box = MessageBox(title, content, parent.window() if parent else None)
    return bool(box.exec())


def release_widget(widget) -> None:
    """把控件从界面上摘下来并排队销毁。

    必须先 hide()：直接 `setParent(None)` 之后控件在 Windows 上仍是「可见的顶层窗口」，
    列表每次重渲染都会让每个旧条目以一闪而过的小窗口冒出来，所以先隐藏再断开父级。
    """
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


class BusyTip:
    """长耗时操作的进行中提示，完成时转为完成状态并自动淡出。"""

    def __init__(self, parent, title: str, content: str = "正在处理…") -> None:
        window = parent.window() if parent is not None else None
        self._tip = None if window is None else StateToolTip(title, content, window)
        if self._tip is not None:
            self._tip.move(self._tip.getSuitablePos())
            self._tip.show()

    def update(self, content: str) -> None:
        if self._tip is not None:
            self._tip.setContent(content)

    def finish(self, content: str = "已完成") -> None:
        if self._tip is not None:
            self._tip.setContent(content)
            self._tip.setState(True)


def restart_application(delay_ms: int = 400) -> None:
    """退出当前进程并用相同参数启动一个新实例（用于恢复初始化之后）。"""
    from PyQt6.QtCore import QProcess, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    def _relaunch() -> None:
        QProcess.startDetached(sys.executable, list(sys.argv))
        app.quit()

    QTimer.singleShot(delay_ms, _relaunch)


# ---------------------------------------------------------------------- 页面样式
# 全站统一的页面骨架尺寸，取自「插件管理 / 打开方式 / 存档管理 / 标签管理」这一套风格。
PAGE_MARGINS = (24, 20, 24, 20)
PAGE_SPACING = 12
PANEL_MARGINS = (12, 12, 12, 12)
DETAIL_MARGINS = (16, 14, 16, 14)


def page_layout(page) -> QVBoxLayout:
    """给页面（或滚动区的内层容器）建立统一的外层布局。"""
    layout = QVBoxLayout(page)
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)
    return layout


def page_header(root: QVBoxLayout, page, title: str, description: str = "") -> QHBoxLayout:
    """在页面顶部放统一的标题行（右侧留给主操作按钮），并在下面放说明文字。"""
    header = QHBoxLayout()
    header.addWidget(TitleLabel(title, page))
    header.addStretch(1)
    root.addLayout(header)
    if description:
        root.addWidget(CaptionLabel(description, page))
    return header


def panel_card(parent=None, margins: tuple[int, int, int, int] = PANEL_MARGINS, spacing: int = 8):
    """建立一个统一样式的面板卡片，返回 (卡片, 卡片内的竖直布局)。"""
    card = CardWidget(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return card, layout


def clear_background(widget) -> None:
    """让控件不按调色板实绘底色（透明），露出父级或主题背景。

    `QScrollArea.setWidget()` 会把内层容器重新设成 `autoFillBackground=True`，
    于是它按 Qt 调色板填充；调色板不随 qfluentwidgets 主题变化，运行时切主题后
    就会留下一块深色。卡片内部的滚动容器用这个函数保持透明。
    """
    widget.setAutoFillBackground(False)
    setStyleSheet(
        widget,
        CustomStyleSheet(widget).setCustomStyleSheet("background: transparent;", "background: transparent;"),
    )


def clear_scroll_background(area, inner: bool = True) -> None:
    """滚动区域不按调色板实绘底色：视口（必要时连内层容器）设为透明。

    视口是 `qt_scrollarea_viewport`，默认 `autoFillBackground=True`；调色板不随主题
    变化，运行时切浅色后它会留下一整块深色。
    """
    viewport = area.viewport()
    if viewport is not None:
        clear_background(viewport)
    if inner and area.widget() is not None:
        clear_background(area.widget())


def accent_color() -> QColor:
    """当前主题的强调色（跟随 qfluentwidgets 主题色）。"""
    return themeColor()


def accent_name() -> str:
    """当前主题强调色的十六进制写法，用于拼 QSS。"""
    return themeColor().name()


# ---------------------------------------------------------------------- 主题底色
#: 页面底色：与 qfluentwidgets 窗口背景一致（浅色 / 深色）。
PAGE_BG_LIGHT = QColor(240, 244, 249)
PAGE_BG_DARK = QColor(32, 32, 32)

_theme_hooked = False


def theme_palette(dark: bool | None = None) -> QPalette:
    """构造跟随主题的调色板。

    qfluentwidgets 换肤只替换 QSS，不会更新 QPalette：系统处于深色模式时
    QPalette 一直是深色，于是按调色板绘制的控件（滚动区视口、
    `QScrollArea.setWidget()` 后自动填充的容器、表格表头等）在浅色主题下
    仍然画成深色，露出「发黑的底色」。
    """
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
    """启动时调用一次：装好调色板，并让它在主题切换后跟着变。"""
    global _theme_hooked
    apply_app_palette()
    if not _theme_hooked:
        qconfig.themeChangedFinished.connect(apply_app_palette)
        _theme_hooked = True


def page_background(widget, name: str = "") -> None:
    """给页面（或滚动内容容器）铺一层跟随主题的不透明底色。

    纯 QWidget 页面本身是透明的，露出的是窗口背面（云母材质）；显式画上
    主题底色后，浅色 / 深色模式都不会出现发黑的分块。

    注意必须走 `setStyleSheet` 而不是 `setCustomStyleSheet`：后者只写动态
    属性，未注册的控件没有样式监听器，等同于什么都没做。
    """
    widget.setAutoFillBackground(False)
    object_name = name or widget.objectName() or f"{widget.__class__.__name__.lower()}Bg"
    widget.setObjectName(object_name)
    style = CustomStyleSheet(widget).setCustomStyleSheet(
        f"#{object_name} {{ background-color: {PAGE_BG_LIGHT.name()}; }}",
        f"#{object_name} {{ background-color: {PAGE_BG_DARK.name()}; }}",
    )
    setStyleSheet(widget, style)
