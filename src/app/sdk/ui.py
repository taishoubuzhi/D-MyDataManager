"""插件的界面支持：全站共用的间距、打开文件、定位文件、滚动区透明。

插件只依赖 `app.sdk`：这里是「按程序的样子搭界面」所需的最小集合，
Qt 相关实现都在函数内部延迟导入，导入本模块不会拉起 Qt。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

__all__ = [
    "CARD_SPACING",
    "ClickCard",
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
    "ask_open_with",
    "clear_scroll_background",
    "open_default",
    "open_page",
    "open_with_program",
    "notify_items_changed",
    "reveal",
    "simple_display",
    "simple_mode",
]

#: 按钮与卡片类按需导入：插件可以直接写 `ui.IconTextButton(...)` / `ui.ClickCard(...)`，
#: 导入本模块仍然不拉起 Qt。
_LAZY_EXPORTS = (
    "ClickCard",
    "IconTextButton",
    "IconTextPrimaryButton",
    "simple_display",
    "simple_mode",
)


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
    from ..core.runtime import shell

    return bool(shell.open_default(path))


def ask_open_with(path: str | Path) -> bool:
    """弹出系统的「选择程序」对话框，让用户挑一个程序打开文件。"""
    from ..core.runtime import shell

    return bool(shell.ask_open_with(path))


def open_with_program(program: str | Path, args: str, path: str | Path) -> bool:
    """用指定程序打开文件；`args` 里可以用 `{path}` 占位。"""
    from ..core.runtime import shell

    return bool(shell.open_with_program(str(program), args, path))


def reveal(path: str | Path) -> bool:
    """在系统文件管理器里定位文件。"""
    from ..core.runtime import shell

    return bool(shell.reveal(path))


def open_page(route: str) -> bool:
    """跳到某个页面：插件页面路由用模型工具库的 `page_route()` 这类值，跳不过去返回 False。

    本体没装界面（脚本、服务层自检）或页面不在时返回 False，插件据此提示用户手动打开。
    """
    try:
        from ..core.plugins.app_ui import APP_UI_EXTENSION
        from ..core.plugins.extensions import extension_registry
    except Exception:
        return False
    api = extension_registry.provider(APP_UI_EXTENSION)
    opener = getattr(api, "open_page", None)
    if not callable(opener):
        return False
    try:
        return bool(opener(route))
    except Exception:
        logger.exception("跳转页面失败：{}", route)
        return False


def notify_items_changed() -> None:
    """库内文件被外部（如内置编辑器）改写后广播一次条目变更，让界面刷新列表。

    程序只提供这条公开信号：插件改完库内文件调用它，不要直接写库。
    """
    from ..core.runtime.signals import signalBus

    signalBus.itemsChanged.emit()
