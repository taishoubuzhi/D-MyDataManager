"""页面骨架的尺寸与纯格式化工具。

这里的常量是**全站唯一的间距来源**：页面与组件只允许引用这些名字，
不允许再写死数字，否则页面之间必然再次走样。
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt
from qfluentwidgets import FluentIcon

from ...db.models import DATA_TYPE_NAMES, DataType

# ---------------------------------------------------------------------- 间距
#: 页面外边距（标题、卡片都落在这个内边距里）。
PAGE_MARGINS = (24, 20, 24, 20)
#: 页面内各分区之间的垂直间距。
PAGE_SPACING = 12
#: 面板卡片（工具条、表格容器）的内边距。
PANEL_MARGINS = (12, 12, 12, 12)
#: 明细卡片（标题 + 说明 + 内容）的内边距。
DETAIL_MARGINS = (16, 14, 16, 14)
#: 紧凑正文边距：查看器正文、筛选面板内层。
COMPACT_MARGINS = (10, 8, 10, 8)
#: KPI / 统计卡片的内边距（数字与说明更紧凑）。
KPI_MARGINS = (14, 8, 14, 8)
#: 滚动区右侧留白：避免内容贴住滚动条。
SCROLL_GUTTER = 6
#: 卡片内标题与内容之间的间距。
CARD_SPACING = 10
#: 列表行、表单行之间的间距。
ROW_SPACING = 6

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
