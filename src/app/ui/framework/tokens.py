"""页面骨架的尺寸与纯格式化工具。

间距常量住在 app.sdk.ui：它是**全站唯一的间距来源**，程序页面、组件与插件界面
都只引用这些名字，不写死数字，否则界面之间必然再次走样。
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt
from qfluentwidgets import FluentIcon

from ...db.models import DATA_TYPE_NAMES, DataType

from ...sdk.ui import (
    CARD_SPACING,
    COMPACT_MARGINS,
    DETAIL_MARGINS,
    KPI_MARGINS,
    PAGE_MARGINS,
    PAGE_SPACING,
    PANEL_MARGINS,
    ROW_SPACING,
    SCROLL_GUTTER,
)

# ---------------------------------------------------------------------- 间距
# 间距常量定义在 app.sdk.ui（插件界面与程序页面共用同一套），这里重新导出。

# ---------------------------------------------------------------------- 类型
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


# ---------------------------------------------------------------------- 格式化
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


def tri_state(checked: int, total: int) -> Qt.CheckState:
    """全选框的三态：空 = 全不选，横杠 = 部分选中，勾 = 全选。"""
    if total <= 0 or checked <= 0:
        return Qt.CheckState.Unchecked
    if checked >= total:
        return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked


__all__ = [
    "CARD_SPACING",
    "COMPACT_MARGINS",
    "DETAIL_MARGINS",
    "KPI_MARGINS",
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "PANEL_MARGINS",
    "ROW_SPACING",
    "SCROLL_GUTTER",
    "TYPE_ICONS",
    "elide",
    "format_datetime",
    "format_size",
    "tri_state",
    "type_icon",
    "type_key",
    "type_name",
]
