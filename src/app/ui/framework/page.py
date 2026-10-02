"""页面基座：统一骨架、间距、提示与刷新约定。

两种骨架，按内容高度选择：

- `ScrollPage`：内容高度不定、需要滚动（概览、设置、标签等）。
- `Page`：正文需要占满可视高度（表格、分栏、树 + 详情等）。

两者都强制经过 `add_header()` / `add_section()` 摆布局，页面代码不再自己算边距；
`page_name` 同时是导航路由 key（`objectName`）与自检时的定位依据。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CardWidget, ScrollArea

from . import feedback as fb
from .sections import PageHeader, section_card
from .theme import clear_scroll_background
from .tokens import PAGE_MARGINS, PAGE_SPACING


class PageBase:
    """页面公共行为（mixin）：骨架、提示、刷新。"""

    #: 子类覆盖：导航路由 key，同时作为控件 objectName。
    page_name = "page"
    #: 子类覆盖：标题区文案。
    page_title = ""
    page_subtitle = ""

    def _setup_page(self, title: str, subtitle: str) -> None:
        self.setObjectName(self.page_name)
        self._page_title = title or self.page_title
        self._page_subtitle = subtitle or self.page_subtitle
        #: 页面正文布局：标题区与所有分区都加到这里。
        self.body: QVBoxLayout
        #: 标题区（由 `add_header()` 建立）。
        self.header: PageHeader | None = None

    # ------------------------------------------------------------------ 骨架
    def add_header(self, title: str = "", subtitle: str = "") -> PageHeader:
        """放置统一标题区，返回它（右侧 `header.actions` 挂主操作）。"""
        self.header = PageHeader(title or self._page_title, subtitle or self._page_subtitle, self)
        self.body.addWidget(self.header)
        return self.header

    def add_section(self, title: str, description: str = "") -> tuple[CardWidget, QVBoxLayout]:
        """添加统一样式的分区卡片，返回 (卡片, 卡片内布局)。"""
        card, layout = section_card(self, title, description)
        self.body.addWidget(card)
        return card, layout

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_row(self, row: QHBoxLayout) -> QHBoxLayout:
        """在正文里放一行水平布局（标题区之外的按钮行、筛选行）。"""
        self.body.addLayout(row)
        return row

    def add_stretch(self) -> None:
        self.body.addStretch(1)

    # ------------------------------------------------------------------ 提示
    def toast_success(self, title: str, content: str = "") -> None:
        fb.toast_success(self, title, content)

    def toast_info(self, title: str, content: str = "") -> None:
        fb.toast_info(self, title, content)

    def toast_warning(self, title: str, content: str = "") -> None:
        fb.toast_warning(self, title, content)

    def toast_error(self, title: str, content: str = "") -> None:
        fb.toast_error(self, title, content)

    def confirm(self, title: str, content: str) -> bool:
        return fb.confirm(self, title, content)

    def busy(self, title: str, content: str = "正在处理…") -> fb.BusyTip:
        return fb.BusyTip(self, title, content)

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        """子类覆盖：把数据重新填进界面。"""

    def auto_refresh(self, *signals) -> None:
        """把给定的 signalBus 信号接到 `refresh()`（页面销毁时 Qt 会自动断开）。"""
        for signal in signals:
            signal.connect(self.refresh)


class ScrollPage(ScrollArea, PageBase):
    """可滚动页面：标题区与分区卡片自上而下排列。"""

    def __init__(self, parent: QWidget | None = None, *, title: str = "", subtitle: str = "") -> None:
        ScrollArea.__init__(self, parent)
        self._setup_page(title, subtitle)
        host = QWidget(self)
        host.setObjectName(f"{self.page_name}Host")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(PAGE_SPACING)
        self.body = layout
        #: 滚动区的内层容器（自检会检查它的 objectName）。
        self.host = host
        self.setWidget(host)
        self.setWidgetResizable(True)
        clear_scroll_background(self)
        if self._page_title:
            self.add_header()


class Page(QWidget, PageBase):
    """满高页面：正文占满可视区域，适合表格、分栏与树 + 详情布局。"""

    def __init__(self, parent: QWidget | None = None, *, title: str = "", subtitle: str = "") -> None:
        QWidget.__init__(self, parent)
        self._setup_page(title, subtitle)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(PAGE_SPACING)
        self.body = layout
        if self._page_title:
            self.add_header()


__all__ = ["Page", "PageBase", "ScrollPage"]
