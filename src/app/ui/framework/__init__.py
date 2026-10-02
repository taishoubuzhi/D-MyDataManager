"""页面基座：统一骨架、间距、提示与主题。

页面的写法固定为：

```python
class HomePage(ScrollPage):
    page_name = "homePage"
    page_title = "数据概览"
    page_subtitle = "所有数据都保存在本机"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.add_header().add_action(self.user_box)
        card, body = self.add_section("概览", "当前用户的数据总量")
        ...
```

页面代码只从这里取间距、卡片、提示与底色，不再各自 `setStyleSheet` 或写死数字。
"""

from __future__ import annotations

from .app import open_path, restart_application
from .feedback import (
    BusyTip,
    clear_layout,
    confirm,
    release_widget,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
)
from .page import Page, PageBase, ScrollPage
from .sections import PageHeader, caption, empty_state, page_header, panel_card, section_card, toolbar
from .theme import (
    AVATAR_PLAIN_QSS,
    BADGE_PLAIN_QSS,
    PAGE_BG_DARK,
    PAGE_BG_LIGHT,
    accent_color,
    accent_name,
    apply_app_palette,
    avatar_style,
    badge_style,
    clear_scroll_background,
    highlight_fill,
    highlight_hover,
    install_app_theme,
    theme_palette,
)
from .tokens import (
    CARD_SPACING,
    COMPACT_MARGINS,
    DETAIL_MARGINS,
    KPI_MARGINS,
    PAGE_MARGINS,
    PAGE_SPACING,
    PANEL_MARGINS,
    ROW_SPACING,
    SCROLL_GUTTER,
    TYPE_ICONS,
    elide,
    format_datetime,
    format_size,
    tri_state,
    type_icon,
    type_key,
    type_name,
)

__all__ = [
    "AVATAR_PLAIN_QSS",
    "BADGE_PLAIN_QSS",
    "CARD_SPACING",
    "COMPACT_MARGINS",
    "DETAIL_MARGINS",
    "KPI_MARGINS",
    "PAGE_BG_DARK",
    "PAGE_BG_LIGHT",
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "PANEL_MARGINS",
    "ROW_SPACING",
    "SCROLL_GUTTER",
    "TYPE_ICONS",
    "BusyTip",
    "Page",
    "PageBase",
    "PageHeader",
    "ScrollPage",
    "accent_color",
    "accent_name",
    "apply_app_palette",
    "avatar_style",
    "badge_style",
    "caption",
    "clear_layout",
    "clear_scroll_background",
    "confirm",
    "elide",
    "empty_state",
    "format_datetime",
    "format_size",
    "highlight_fill",
    "highlight_hover",
    "install_app_theme",
    "open_path",
    "page_header",
    "panel_card",
    "release_widget",
    "restart_application",
    "section_card",
    "theme_palette",
    "toast_error",
    "toast_info",
    "toast_success",
    "toast_warning",
    "toolbar",
    "tri_state",
    "type_icon",
    "type_key",
    "type_name",
]
