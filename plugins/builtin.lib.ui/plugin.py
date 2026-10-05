"""内置 UI 工具库：页面骨架、控件工厂、组合构件、提示条与弹窗外壳。

包含两块内容：

* 面向**静态导入**的构建件（`dm_plugin.builtin.lib.ui.plugin`）：PageTemplate /
  ScrollPageTemplate / SplitPage / ListPanel / DetailPanel / MediaBar 等组合构件，以及
  body_label / combo_box / line_edit / list_view / read_only_table 这类**控件工厂**。
  插件页面只负责把功能函数接到工厂的回调参数上（on_change / on_click / on_select…），
  不必再自己 new Qt 或 qfluentwidgets 控件、拼布局、写间距；
* 面向**扩展接口**的服务：`dialog`（独立弹窗外壳，查看器等插件用它显示页面）
  与 `ui`（页面模板 / 组合构件工厂 + 弹窗能力的门面）。

    from app.sdk import Plugin, PluginContext
    from dm_plugin.builtin.lib.ui.plugin import SplitPage, toast_success

本插件由原先的 lib.dialog 并入而来：弹窗能力原样保留在 dialog_host.py。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtWidgets import QLabel, QScrollArea, QWidget

from app.sdk import Plugin, PluginContext

from .dialog_host import EXTENSION_NAME as DIALOG_EXTENSION, DialogApi, PopupWindow
from .ui_tools import (
    CARD_SPACING,
    COMPACT_MARGINS,
    DETAIL_MARGINS,
    DURATION_ERROR,
    DURATION_INFO,
    DURATION_SUCCESS,
    DURATION_WARNING,
    PAGE_MARGINS,
    PAGE_SPACING,
    PANEL_MARGINS,
    ROW_SPACING,
    ClickCard,
    DetailPanel,
    FormDialog,
    ListPanel,
    MediaBar,
    PageHeader,
    PageTemplate,
    PlayerPanel,
    ScrollPageTemplate,
    SplitPage,
    body_label,
    caption,
    check_box,
    check_grid,
    clear_layout,
    click_card,
    combo_box,
    confirm,
    empty_state,
    field,
    fill_table,
    form_dialog,
    form_row,
    format_time,
    icon_button,
    image_canvas,
    line_edit,
    list_item,
    list_view,
    page_header,
    page_template,
    panel_card,
    primary_button,
    progress_bar,
    push_button,
    radio_button,
    read_only_table,
    release_widget,
    scroll_area,
    search_edit,
    section_card,
    set_table_wrap,
    spin_box,
    status_label,
    strong_label,
    text_area,
    text_browser,
    text_edit,
    title_label,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
    tool_button,
    toolbar,
    view_stack,
    widget_column,
    widget_row,
)

#: 扩展接口名：UI 工具库门面（页面模板工厂 + 弹窗能力）
EXTENSION_NAME = "ui"

__all__ = [
    "body_label",
    "caption",
    "CARD_SPACING",
    "check_box",
    "check_grid",
    "clear_layout",
    "ClickCard",
    "click_card",
    "combo_box",
    "COMPACT_MARGINS",
    "confirm",
    "DETAIL_MARGINS",
    "DetailPanel",
    "DIALOG_EXTENSION",
    "DialogApi",
    "DURATION_ERROR",
    "DURATION_INFO",
    "DURATION_SUCCESS",
    "DURATION_WARNING",
    "empty_state",
    "EXTENSION_NAME",
    "field",
    "fill_table",
    "FormDialog",
    "form_dialog",
    "form_row",
    "format_time",
    "icon_button",
    "image_canvas",
    "line_edit",
    "list_item",
    "list_view",
    "ListPanel",
    "MediaBar",
    "page_header",
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "page_template",
    "PageHeader",
    "PageTemplate",
    "panel_card",
    "PANEL_MARGINS",
    "PlayerPanel",
    "PopupWindow",
    "primary_button",
    "progress_bar",
    "push_button",
    "radio_button",
    "read_only_table",
    "set_table_wrap",
    "release_widget",
    "ROW_SPACING",
    "scroll_area",
    "ScrollPageTemplate",
    "search_edit",
    "section_card",
    "spin_box",
    "SplitPage",
    "status_label",
    "strong_label",
    "text_area",
    "text_browser",
    "text_edit",
    "title_label",
    "toast_error",
    "toast_info",
    "toast_success",
    "toast_warning",
    "tool_button",
    "toolbar",
    "UiApi",
    "UiPlugin",
    "view_stack",
    "widget_row",
    "widget_column",
]


class UiApi:
    """ui 扩展接口：弹窗能力 + 页面模板 / 组合构件工厂。

    静态导入构建件更直接；需要运行时拿界面服务时用 `ctx.require("ui")`。
    """

    def __init__(self, dialog: DialogApi | None = None) -> None:
        self.dialog = dialog or DialogApi()

    def open_page(
        self,
        title: str,
        content_factory: Callable[[QWidget], QWidget],
        meta: str = "",
        buttons: Sequence[tuple[str, Callable[[], None]]] = (),
        width: int = 980,
        height: int = 700,
    ) -> PopupWindow:
        """在独立弹窗里显示一个页面（内容由插件自己构建）。"""
        return self.dialog.open_page(title, content_factory, meta, buttons, width, height)

    def windows(self) -> tuple[PopupWindow, ...]:
        return self.dialog.windows()

    def close_all(self) -> int:
        return self.dialog.close_all()

    def page(self, title: str = "", subtitle: str = "", *, scroll: bool = False) -> PageTemplate:
        """建一个标准页面（标题区 + 内容区），插件可以继续往里加分区。"""
        return PageTemplate(title, subtitle, None, scroll=scroll)

    def form_dialog(self, parent: QWidget | None = None, title: str = "", **fields) -> FormDialog:
        """建一个插件弹窗外壳（标题 + 字段区 + 确定 / 取消，字段见 FormDialog）。"""
        return FormDialog(parent, title=title, **fields)

    def split_page(self, title: str = "", subtitle: str = "", **fields) -> SplitPage:
        """建一个标准「左列表 + 右详情」页面（字段见 SplitPage）。"""
        return SplitPage(title, subtitle, None, **fields)

    def list_panel(self, parent: QWidget | None = None, **fields) -> ListPanel:
        """建一个左栏列表（标题 + 搜索 + 列表 + 计数）。"""
        return ListPanel(parent, **fields)

    def detail_panel(self, parent: QWidget | None = None, **fields) -> DetailPanel:
        """建一个右栏详情（标题 + 内容行 + 底部按钮）。"""
        return DetailPanel(parent, **fields)

    def media_bar(self, parent: QWidget | None = None, **fields) -> MediaBar:
        """建一个播放条（播放 / 进度 / 时间 / 音量）。"""
        return MediaBar(parent, **fields)

    def player_panel(self, path, parent: QWidget | None = None) -> PlayerPanel:
        """建一个通用播放控件（构造时才导入 QtMultimedia，缺依赖时抛 RuntimeError）。"""
        return PlayerPanel(path, parent)

    def image_canvas(self, parent: QWidget | None = None) -> tuple[QScrollArea, QLabel]:
        """建图片显示区，返回 (滚动画布, 图片标签)。"""
        return image_canvas(parent)

    def section(self, parent: QWidget | None = None, title: str = "", description: str = ""):
        """建一个分区卡片，返回 (卡片, 卡片内的竖直布局)。"""
        return section_card(parent, title, description)


class UiPlugin(Plugin):
    """注册 dialog（弹窗外壳）与 ui（界面工具）两个扩展接口。"""

    def setup(self, ctx: PluginContext) -> None:
        dialog = DialogApi()
        ctx.provide(DIALOG_EXTENSION, dialog)
        ctx.provide(EXTENSION_NAME, UiApi(dialog))
