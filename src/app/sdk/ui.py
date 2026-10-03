"""插件的界面支持：全站共用的间距、打开文件、定位文件、滚动区透明。

插件只依赖 `app.sdk`：这里是「按程序的样子搭界面」所需的最小集合，
Qt 相关实现都在函数内部延迟导入，导入本模块不会拉起 Qt。
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CARD_SPACING",
    "IconTextButton",
    "IconTextPrimaryButton",
    "COMPACT_MARGINS",
    "DETAIL_MARGINS",
    "KPI_MARGINS",
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "PANEL_MARGINS",
    "ROW_SPACING",
    "SCROLL_GUTTER",
    "clear_scroll_background",
    "open_default",
    "reveal",
    "simple_display",
    "simple_mode",
]

#: 按钮类按需导入：插件可以直接写 `ui.IconTextButton(...)`，导入本模块仍然不拉起 Qt。
_LAZY_EXPORTS = ("IconTextButton", "IconTextPrimaryButton", "simple_display", "simple_mode")


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        from ..ui import framework

        return getattr(framework, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: 间距是全站唯一的来源：程序页面与插件界面都从这里取，不各自写死数字。
PAGE_MARGINS = (24, 20, 24, 20)
PAGE_SPACING = 12
PANEL_MARGINS = (12, 12, 12, 12)
DETAIL_MARGINS = (16, 14, 16, 14)
#: 紧凑正文边距：查看器正文、筛选面板内层。
COMPACT_MARGINS = (10, 8, 10, 8)
KPI_MARGINS = (14, 8, 14, 8)
SCROLL_GUTTER = 6
CARD_SPACING = 10
ROW_SPACING = 6

#: 滚动区自身与内层容器的透明写法，取自 qfluentwidgets 的
#: `ScrollArea.enableTransparentBackground()`：只清视口不够——滚动区自身仍会按调色板
#: 实绘底色，换肤后还留着上一个主题的颜色。
_SCROLL_AREA_QSS = "QScrollArea{border: none; background: transparent}"
_SCROLL_INNER_QSS = "QWidget{background: transparent}"


def clear_scroll_background(area, inner: bool = True) -> None:
    """滚动区不按调色板实绘底色：滚动区自身（必要时连内层容器）设为透明。"""
    from qfluentwidgets.common.style_sheet import CustomStyleSheet, setStyleSheet

    setStyleSheet(area, CustomStyleSheet(area).setCustomStyleSheet(_SCROLL_AREA_QSS, _SCROLL_AREA_QSS))
    host = area.widget()
    if inner and host is not None:
        setStyleSheet(host, CustomStyleSheet(host).setCustomStyleSheet(_SCROLL_INNER_QSS, _SCROLL_INNER_QSS))


def open_default(path: str | Path) -> bool:
    """用系统默认关联程序打开文件。"""
    from ..core import shell

    return bool(shell.open_default(path))


def reveal(path: str | Path) -> bool:
    """在系统文件管理器里定位文件。"""
    from ..core import shell

    return bool(shell.reveal(path))
