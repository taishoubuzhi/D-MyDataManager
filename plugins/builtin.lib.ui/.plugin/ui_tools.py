"""UI 工具库：页面骨架、卡片、工具条、提示条与确认框的统一实现。

插件不再各自手写页面结构：页面用 PageTemplate / ScrollPageTemplate 起手，
正文只用 section_card / toolbar / caption / empty_state 这几种构件拼装，
提示统一走 toast_* / confirm —— 这样不同插件生成的界面看起来才是同一个程序。

本模块只依赖 PyQt6、qfluentwidgets 与 app.sdk，可被任何插件静态导入：

    from dm_plugin.builtin.lib.ui.plugin import PageTemplate, section_card, toast_success
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    MessageBoxBase,
    PlainTextEdit,
    ProgressBar,
    PushButton,
    RadioButton,
    SearchLineEdit,
    SingleDirectionScrollArea,
    Slider,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TextEdit,
    TitleLabel,
    ToolButton,
)

from app.sdk import ui as sdk_ui
from app.sdk.data import human_size
from app.sdk.ui import ClickCard, IconTextButton, IconTextPrimaryButton

#: 下载器 / pip 安装器的视图由程序本体实现，经 SDK 转到这里：插件用它就能拿到和「下载
#: 管理」页一模一样的任务列表（不用自己画一遍，也不会两套行为）。
AddDownloadDialog = sdk_ui.AddDownloadDialog
DownloadListView = sdk_ui.DownloadListView
PipTaskView = sdk_ui.PipTaskView
add_download_dialog = sdk_ui.add_download_dialog
download_list = sdk_ui.download_list
pip_task_list = sdk_ui.pip_task_list

#: 间距与边距沿用 SDK 里的统一取值，插件不要再写死数字。
CARD_SPACING = sdk_ui.CARD_SPACING
COMPACT_MARGINS = sdk_ui.COMPACT_MARGINS
DETAIL_MARGINS = sdk_ui.DETAIL_MARGINS
PAGE_MARGINS = sdk_ui.PAGE_MARGINS
PAGE_SPACING = sdk_ui.PAGE_SPACING
PANEL_MARGINS = sdk_ui.PANEL_MARGINS
ROW_SPACING = sdk_ui.ROW_SPACING

#: 提示条停留时长（毫秒）。
DURATION_SUCCESS = 2500
DURATION_INFO = 2500
DURATION_WARNING = 3000
DURATION_ERROR = 4000


class PageHeader(QWidget):
    """统一标题区：标题 + 悬停说明，右侧 actions 行留给主操作按钮。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._title = TitleLabel(title, self)
        row.addWidget(self._title)
        row.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        outer.addLayout(row)

        self._subtitle = caption(self, subtitle)
        self._subtitle.setVisible(False)
        outer.addWidget(self._subtitle)
        self._hints: list[str] = []
        self._apply_subtitle()

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle.setText(subtitle)
        self._apply_subtitle()

    def add_hint(self, text: str) -> None:
        """追加一句说明；说明不常显，鼠标停在标题上才弹出。"""
        text = str(text).strip()
        if text:
            self._hints.append(text)
            self._apply_subtitle()

    def set_hint(self, text: str) -> None:
        """整段替换说明（用于随状态变化的说明）。"""
        text = str(text).strip()
        self._hints = [text] if text else []
        self._apply_subtitle()

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def add_actions(self, widgets: Sequence[QWidget]) -> None:
        for widget in widgets:
            self.add_action(widget)

    def _apply_subtitle(self) -> None:
        self._subtitle.setVisible(False)
        lines = [self._subtitle.text().strip(), *self._hints]
        self._title.setToolTip("\n".join(line for line in lines if line))


def page_header(parent: QWidget | None, title: str, subtitle: str = "") -> PageHeader:
    """建立统一标题区。"""
    return PageHeader(title, subtitle, parent)


def caption(parent: QWidget | None, text: str) -> CaptionLabel:
    """统一说明文字（自动换行）。"""
    label = CaptionLabel(text, parent)
    label.setWordWrap(True)
    return label


def panel_card(
    parent: QWidget | None = None,
    *,
    margins: tuple[int, int, int, int] = PANEL_MARGINS,
    spacing: int = 8,
) -> tuple[CardWidget, QVBoxLayout]:
    """统一样式的面板卡片，返回 (卡片, 卡片内的竖直布局)。"""
    card = CardWidget(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return card, layout


def section_card(
    parent: QWidget | None = None,
    title: str = "",
    description: str = "",
    *,
    margins: tuple[int, int, int, int] = DETAIL_MARGINS,
    spacing: int = CARD_SPACING,
) -> tuple[CardWidget, QVBoxLayout]:
    """统一分区卡片：标题 + 悬停说明 + 内容区。"""
    card, layout = panel_card(parent, margins=margins, spacing=spacing)
    title_label: StrongBodyLabel | None = None
    if title:
        title_label = StrongBodyLabel(title, card)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(title_label)
        row.addStretch(1)
        layout.addLayout(row)
    if description:
        hint = caption(card, description)
        hint.setVisible(False)
        layout.addWidget(hint)
        if title_label is not None:
            title_label.setToolTip(description)
        else:
            card.setToolTip(description)
    return card, layout


def toolbar(parent: QWidget | None = None, *, spacing: int = 8) -> tuple[QWidget, QHBoxLayout]:
    """统一工具条：一行放筛选、搜索与批量操作按钮。"""
    host = QWidget(parent)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return host, layout


def empty_state(
    parent: QWidget | None = None,
    text: str = "暂无数据",
    *,
    icon: FluentIcon | None = None,
) -> QWidget:
    """统一空态：可选图标 + 居中说明文字。"""
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 24, 0, 24)
    layout.setSpacing(6)
    if icon is not None:
        picture = BodyLabel("", host)
        picture.setPixmap(icon.icon().pixmap(24, 24))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
    label = CaptionLabel(text, host)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    layout.addWidget(label)
    return host


def clear_layout(container) -> None:
    """清空普通布局中的控件。"""
    widgets = []
    while container.count():
        entry = container.takeAt(0)
        widgets.append(entry.widget() if hasattr(entry, "widget") else entry)
    for widget in widgets:
        if widget is not None:
            release_widget(widget)


def release_widget(widget) -> None:
    """把控件从界面上摘下来并排队销毁（先 hide，避免旧控件以顶层窗口闪现）。"""
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def _show(kind: str, parent, title: str, content: str, duration: int) -> None:
    getattr(InfoBar, kind)(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=duration,
        parent=parent,
    )


def toast_success(parent, title: str, content: str = "") -> None:
    _show("success", parent, title, content, DURATION_SUCCESS)


def toast_info(parent, title: str, content: str = "") -> None:
    _show("info", parent, title, content, DURATION_INFO)


def toast_warning(parent, title: str, content: str = "") -> None:
    _show("warning", parent, title, content, DURATION_WARNING)


def toast_error(parent, title: str, content: str = "") -> None:
    _show("error", parent, title, content, DURATION_ERROR)


def confirm(parent, title: str, content: str) -> bool:
    """统一样式的确认框；确认返回 True。"""
    box = MessageBox(title, content, parent.window() if parent else None)
    return bool(box.exec())


class PageTemplate(QWidget):
    """标准页面模板：标题区 + 内容区，可直接当插件页面用。

    用法：

        class MyPage(PageTemplate):
            def __init__(self, parent=None):
                super().__init__("我的页面", "一句话说明", parent)
                card, body = self.add_section("分区", "这个分区做什么")
                body.addWidget(...)
    """

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        scroll: bool = False,
        margins: tuple[int, int, int, int] = PAGE_MARGINS,
        spacing: int = PAGE_SPACING,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        outer.setSpacing(spacing)
        self.header = PageHeader(title, subtitle, self)
        outer.addWidget(self.header)

        if scroll:
            area = QScrollArea(self)
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            host = QWidget(area)
            host.setObjectName("pageScrollBody")
            area.setWidget(host)
            sdk_ui.clear_scroll_background(area)
            self.scroll_area = area
            outer.addWidget(area, 1)
        else:
            host = QWidget(self)
            host.setObjectName("pageBody")
            outer.addWidget(host, 1)

        self.body_host = host
        self.body_layout = QVBoxLayout(host)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(spacing)

    def add_section(self, title: str = "", description: str = "") -> tuple[CardWidget, QVBoxLayout]:
        """加一个分区卡片，返回 (卡片, 卡片内的竖直布局)。"""
        card, layout = section_card(self.body_host, title, description)
        self.body_layout.addWidget(card)
        return card, layout

    def add_widget(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def add_row(self, *widgets: QWidget, spacing: int = ROW_SPACING) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(spacing)
        for widget in widgets:
            row.addWidget(widget)
        self.body_layout.addLayout(row)
        return row

    def add_stretch(self, stretch: int = 1) -> None:
        self.body_layout.addStretch(stretch)

    def toast_success(self, title: str, content: str = "") -> None:
        toast_success(self, title, content)

    def toast_info(self, title: str, content: str = "") -> None:
        toast_info(self, title, content)

    def toast_warning(self, title: str, content: str = "") -> None:
        toast_warning(self, title, content)

    def toast_error(self, title: str, content: str = "") -> None:
        toast_error(self, title, content)

    def confirm(self, title: str, content: str) -> bool:
        return confirm(self, title, content)


class ScrollPageTemplate(PageTemplate):
    """可滚动的标准页面模板（长内容页用这个）。"""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = PAGE_MARGINS,
        spacing: int = PAGE_SPACING,
    ) -> None:
        super().__init__(title, subtitle, parent, scroll=True, margins=margins, spacing=spacing)


def page_template(title: str = "", subtitle: str = "", *, scroll: bool = False) -> PageTemplate:
    """快速建一个页面模板（插件页面工厂里最常用）。"""
    return PageTemplate(title, subtitle, None, scroll=scroll)



# ------------------------------------------------------------- 控件工厂
# 插件页面不再直接 new Qt / qfluentwidgets 控件：这里给出带回调的统一工厂，
# 插件只负责把功能函数接上去（on_change / on_click / on_select 等参数）。
def body_label(parent: QWidget | None, text: str = "", *, wrap: bool = False) -> BodyLabel:
    """统一正文文字。"""
    label = BodyLabel(text, parent)
    label.setWordWrap(wrap)
    return label


def strong_label(parent: QWidget | None, text: str = "") -> StrongBodyLabel:
    """统一小标题（分区名、列名）。"""
    return StrongBodyLabel(text, parent)


def title_label(parent: QWidget | None, text: str = "") -> SubtitleLabel:
    """统一区块标题。"""
    return SubtitleLabel(text, parent)


def status_label(parent: QWidget | None, text: str = "") -> CaptionLabel:
    """统计 / 状态说明文字。"""
    label = CaptionLabel(text, parent)
    label.setWordWrap(True)
    return label


def line_edit(
    parent: QWidget | None = None,
    *,
    placeholder: str = "",
    text: str = "",
    width: int | None = None,
    read_only: bool = False,
    password: bool = False,
    on_change=None,
) -> LineEdit:
    """单行输入框；`on_change(文本)` 由插件提供，`password=True` 时打码显示。"""
    field = LineEdit(parent)
    field.setPlaceholderText(placeholder)
    field.setText(text)
    field.setReadOnly(bool(read_only))
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    if width:
        field.setFixedWidth(int(width))
    if on_change is not None:
        field.textChanged.connect(lambda value: on_change(value))
    return field


def search_edit(
    parent: QWidget | None = None,
    *,
    placeholder: str = "搜索",
    text: str = "",
    width: int | None = None,
    on_change=None,
) -> SearchLineEdit:
    """搜索框；不接 `on_change` 时可用 `ListPanel` 的默认过滤。"""
    field = SearchLineEdit(parent)
    field.setPlaceholderText(placeholder)
    field.setText(text)
    if width:
        field.setFixedWidth(int(width))
    if on_change is not None:
        field.textChanged.connect(lambda value: on_change(value))
    return field


def text_area(
    parent: QWidget | None = None,
    *,
    text: str = "",
    read_only: bool = True,
    monospace: bool = False,
    on_change=None,
) -> PlainTextEdit:
    """多行文本区；代码 / 原文用 `monospace=True`。"""
    area = PlainTextEdit(parent)
    area.setReadOnly(bool(read_only))
    area.setPlainText(text)
    if monospace:
        area.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    if on_change is not None:
        area.textChanged.connect(lambda: on_change(area.toPlainText()))
    return area


def text_browser(
    parent: QWidget | None = None,
    *,
    text: str = "",
    markdown: bool = False,
    on_link=None,
) -> QTextBrowser:
    """富文本 / Markdown 显示区；`on_link(url)` 由插件决定链接怎么处理。"""
    view = QTextBrowser(parent)
    view.setOpenExternalLinks(False)
    if markdown:
        view.setMarkdown(text)
    else:
        view.setPlainText(text)
    if on_link is not None:
        view.anchorClicked.connect(lambda url: on_link(url.toString()))
    return view


def combo_box(
    parent: QWidget | None = None,
    *,
    items: Sequence[str] = (),
    data: Sequence[object] = (),
    value: object = None,
    width: int | None = None,
    minimum_width: int | None = None,
    on_change=None,
) -> ComboBox:
    """下拉框；`on_change(当前项 data)`，没给 data 时回传文本。"""
    box = ComboBox(parent)
    labels = tuple(str(text) for text in items)
    values = tuple(data) if data else labels
    for index, text in enumerate(labels):
        box.addItem(text, userData=values[index] if index < len(values) else None)
    if value is not None:
        position = box.findData(value)
        if position < 0:
            position = box.findText(str(value))
        if position >= 0:
            box.setCurrentIndex(position)
    if width:
        box.setFixedWidth(int(width))
    if minimum_width:
        box.setMinimumWidth(int(minimum_width))
    if on_change is not None:
        box.currentIndexChanged.connect(lambda index: on_change(box.itemData(index)))
    return box


def check_box(
    parent: QWidget | None = None,
    *,
    text: str = "",
    checked: bool = False,
    on_change=None,
) -> CheckBox:
    """勾选框；`on_change(是否勾选)`。"""
    box = CheckBox(text, parent)
    box.setChecked(bool(checked))
    if on_change is not None:
        box.stateChanged.connect(lambda state: on_change(bool(state)))
    return box


def list_item(text: str, *, data: object = None, tooltip: str = "") -> QListWidgetItem:
    """列表行工厂：文本 + 附加数据（选中回调拿到的就是它）。"""
    item = QListWidgetItem(str(text))
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def list_view(
    parent: QWidget | None = None,
    *,
    width: int | None = None,
    minimum_height: int | None = None,
    on_select=None,
    spacing: int = 2,
) -> ListWidget:
    """列表控件；`on_select(数据, 文本)` 在选中行变化时调用。"""
    view = ListWidget(parent)
    view.setSpacing(spacing)
    view.setUniformItemSizes(True)
    if width:
        view.setFixedWidth(int(width))
    if minimum_height:
        view.setMinimumHeight(int(minimum_height))
    if on_select is not None:
        view.currentItemChanged.connect(
            lambda current, _previous: on_select(
                current.data(Qt.ItemDataRole.UserRole) if current is not None else None,
                current.text() if current is not None else "",
            )
        )
    return view


def read_only_table(
    parent: QWidget | None = None,
    *,
    headers: Sequence[str] = (),
    wrap: bool = True,
    fit_rows: bool = True,
) -> TableWidget:
    """只读表格控件（内容用 `fill_table()` 填）。

    列宽按内容自适应（夹在 88～320 px 之间）、末列拉满剩余宽度；`wrap` 打开时单元格
    自动换行且不省略，配合 `fit_rows` 让行高随内容长高；每格的完整文本还会挂 tooltip
    （见 `fill_table()`）——这样长内容不会被无声截断。数据网格（如表格查看器）可以关掉
    `wrap` / `fit_rows` 换回单行省略与固定行高。
    （qfluentwidgets 的表格把原生滚动条换成自己的，原生策略固定 AlwaysOff 是它的
    正常行为，超出可视区的行 / 列照常能滚。）
    """
    table = TableWidget(parent)
    table.setBorderVisible(True)
    table.setBorderRadius(8)
    table.setWordWrap(wrap)
    table.setTextElideMode(Qt.TextElideMode.ElideNone if wrap else Qt.TextElideMode.ElideRight)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    if fit_rows:
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setMinimumSectionSize(88)
    header.setMaximumSectionSize(320)
    header.setStretchLastSection(True)
    if headers:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels([str(item) for item in headers])
    return table


def resize_table_height(table: TableWidget, *, max_height: int = 560) -> None:
    """按当前内容重算只读表格高度（换行开关变了之后必须重算，否则多出来的行会被裁掉）。

    先按每行的内容提示高度兜底（`sizeHintForRow()` 认换行后的高度），再让 Qt 自己
    `resizeRowsToContents()`；最后把表格最小高度按总高设好，长内容才不会被裁在可视区外。
    """
    for index in range(table.rowCount()):
        current = table.rowHeight(index)
        hint = max(current, table.sizeHintForRow(index))
        if hint > current:
            table.setRowHeight(index, hint)
    table.resizeRowsToContents()
    content = table.horizontalHeader().height() + sum(
        table.rowHeight(index) for index in range(table.rowCount())
    )
    table.setMinimumHeight(min(content + 8, int(max_height)))
    table.updateGeometry()


def fill_table(
    table: TableWidget,
    rows: Sequence[Sequence[object]],
    *,
    headers: Sequence[str] = (),
    max_height: int = 560,
) -> None:
    """用二维数据填满只读表格（None 显示为空）。

    每格挂上完整文本 tooltip，并按内容重算行高、把表格最小高度撑到能放下所有行
    （超过 `max_height` 就交给滚动），避免表格默认高度只露几行。
    """
    if headers:
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels([str(item) for item in headers])
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            text = "" if value is None else str(value)
            item = QTableWidgetItem(text)
            if text:
                item.setToolTip(text)
            table.setItem(index, column, item)
    resize_table_height(table, max_height=max_height)


def set_table_wrap(table: TableWidget, wrap: bool) -> None:
    """运行期切换只读表格的「自动换行 / 单行省略」。

    长内容（比如对齐表的「系统方案」列）默认单行省略、行高固定，表格不会被撑开；
    需要看全文时切成自动换行 + 行高随内容，再长也完整显示。
    """
    table.setWordWrap(bool(wrap))
    table.setTextElideMode(Qt.TextElideMode.ElideNone if wrap else Qt.TextElideMode.ElideRight)
    table.verticalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.ResizeToContents if wrap else QHeaderView.ResizeMode.Fixed
    )
    # 行高变了必须重算表格高度：不重算的话换行后多出来的行会被裁掉，看着还是「显示不全」。
    resize_table_height(table)


def tool_button(parent: QWidget | None, icon, tooltip: str = "", on_click=None) -> ToolButton:
    """图标按钮（工具条里那种）；`on_click()`。"""
    button = ToolButton(icon, parent)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click is not None:
        button.clicked.connect(lambda *_: on_click())
    return button


def icon_button(parent: QWidget | None, icon, text: str = "", on_click=None) -> IconTextButton:
    """图标 + 文字按钮；`on_click()`。"""
    button = IconTextButton(icon, text, parent)
    if on_click is not None:
        button.clicked.connect(lambda *_: on_click())
    return button


def primary_button(parent: QWidget | None, icon, text: str = "", on_click=None) -> IconTextPrimaryButton:
    """强调按钮（每页最多一个）；`on_click()`。"""
    button = IconTextPrimaryButton(icon, text, parent)
    if on_click is not None:
        button.clicked.connect(lambda *_: on_click())
    return button


def push_button(parent: QWidget | None, text: str = "", on_click=None) -> PushButton:
    """普通按钮（无图标时用）；`on_click()`。"""
    button = PushButton(text, parent)
    if on_click is not None:
        button.clicked.connect(lambda *_: on_click())
    return button


def form_row(
    parent: QWidget | None,
    label: str,
    widget: QWidget,
    *,
    label_width: int | None = None,
    strong: bool = False,
    spacing: int = ROW_SPACING,
    align_top: bool = False,
) -> QWidget:
    """一行「标签 + 控件」；标签宽度固定时多行能对齐，`strong=True` 时标签加粗。

    `align_top=True` 让标签贴着行顶：控件是多行高块（选择框 + 提示 + 列表）时，
    默认的垂直居中会把标签顶到中间，看起来跟选择项没对齐。
    """
    host = QWidget(parent)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    if label:
        text = (StrongBodyLabel if strong else BodyLabel)(label, host)
        text.setWordWrap(True)
        if label_width:
            text.setFixedWidth(int(label_width))
        if align_top:
            row.addWidget(text, 0, Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(text)
    row.addWidget(widget, 1)
    return host


def view_stack(parent: QWidget | None, *widgets: QWidget) -> QStackedWidget:
    """多视图叠放（比如「渲染视图 / 原文视图」切换）。"""
    stack = QStackedWidget(parent)
    for widget in widgets:
        stack.addWidget(widget)
    return stack


def widget_row(
    parent: QWidget | None = None,
    *widgets: QWidget,
    spacing: int = ROW_SPACING,
    stretches: Sequence[int] | None = None,
) -> QWidget:
    """横排一行控件（工具按钮条、按钮 + 说明文字这类）；`stretches` 按位给伸缩比例。"""
    host = QWidget(parent)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(int(spacing))
    for index, widget in enumerate(widgets):
        stretch = int(stretches[index]) if stretches is not None and index < len(stretches) else 0
        layout.addWidget(widget, stretch)
    return host


def widget_column(
    parent: QWidget | None = None,
    *widgets: QWidget,
    spacing: int = 2,
    stretches: Sequence[int] | None = None,
) -> QWidget:
    """竖排一列控件（「一行控件 + 底下的小字说明」这类）；`stretches` 按位给伸缩比例。"""
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(int(spacing))
    for index, widget in enumerate(widgets):
        stretch = int(stretches[index]) if stretches is not None and index < len(stretches) else 0
        layout.addWidget(widget, stretch)
    return host


def progress_bar(
    parent: QWidget | None = None,
    *,
    value: int = 0,
    indeterminate: bool = False,
    width: int | None = None,
    height: int | None = None,
) -> ProgressBar | IndeterminateProgressBar:
    """进度条；拿不到总量时用 `indeterminate=True`（来回滚动的那种）。"""
    bar: ProgressBar | IndeterminateProgressBar = (
        IndeterminateProgressBar(parent) if indeterminate else ProgressBar(parent)
    )
    if not indeterminate:
        bar.setValue(int(value))
    if width:
        bar.setFixedWidth(int(width))
    if height:
        bar.setFixedHeight(int(height))
    return bar


def spin_box(
    parent: QWidget | None = None,
    *,
    value: int = 0,
    minimum: int = 0,
    maximum: int = 100,
    step: int = 1,
    suffix: str = "",
    width: int | None = None,
    on_change=None,
) -> SpinBox:
    """数字输入框；`on_change(新值)`，`suffix` 是单位（如「秒」）。"""
    box = SpinBox(parent)
    box.setRange(int(minimum), int(maximum))
    box.setSingleStep(int(step))
    box.setValue(int(value))
    if suffix:
        box.setSuffix(f" {suffix}")
    if width:
        box.setFixedWidth(int(width))
    if on_change is not None:
        box.valueChanged.connect(lambda number: on_change(number))
    return box


def radio_button(
    parent: QWidget | None = None,
    *,
    text: str = "",
    checked: bool = False,
    on_change=None,
) -> RadioButton:
    """单选按钮（同一父容器内互斥）；`on_change(是否选中)`。"""
    button = RadioButton(text, parent)
    button.setChecked(bool(checked))
    if on_change is not None:
        button.toggled.connect(lambda state: on_change(bool(state)))
    return button


def text_edit(
    parent: QWidget | None = None,
    *,
    text: str = "",
    placeholder: str = "",
    read_only: bool = False,
    height: int | None = None,
    on_change=None,
) -> TextEdit:
    """多行富文本输入区；`on_change(文本)`，`height` 用来限制高度。"""
    area = TextEdit(parent)
    area.setPlaceholderText(placeholder)
    area.setPlainText(text)
    area.setReadOnly(bool(read_only))
    if height:
        area.setFixedHeight(int(height))
    if on_change is not None:
        area.textChanged.connect(lambda: on_change(area.toPlainText()))
    return area


def check_grid(
    parent: QWidget | None,
    items: Sequence[tuple[str, str]],
    *,
    columns: int = 3,
    checked: Sequence[str] = (),
    on_change=None,
) -> tuple[QWidget, dict[str, CheckBox]]:
    """多列勾选网格（能力、标签这类多选项）；返回 (容器, {值: 勾选框})。

    `items` 是 `(值, 显示文本)`；`on_change(值, 是否勾选)`。
    """
    holder = QWidget(parent)
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(ROW_SPACING)
    boxes: dict[str, CheckBox] = {}
    chosen = tuple(checked)
    column_count = max(1, int(columns))
    for index, (value, text) in enumerate(items):
        box = CheckBox(str(text), holder)
        box.setChecked(value in chosen)
        if on_change is not None:
            box.toggled.connect(lambda state, key=value: on_change(key, bool(state)))
        grid.addWidget(box, index // column_count, index % column_count)
        boxes[str(value)] = box
    return holder, boxes


def field(parent: QWidget | None, label: str, widget: QWidget, *, spacing: int = 2) -> QWidget:
    """竖排的一项：小标签在上、控件在下（弹窗表单用这种）。"""
    holder = QWidget(parent)
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    if label:
        layout.addWidget(caption(holder, label))
    layout.addWidget(widget)
    return holder


def scroll_area(
    parent: QWidget | None = None,
    *,
    widget: QWidget | None = None,
    minimum_height: int | None = None,
    maximum_height: int | None = None,
) -> SingleDirectionScrollArea:
    """只竖着滚的滚动区（横向永远不出滚动条）。"""
    area = SingleDirectionScrollArea(parent)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if widget is not None:
        area.setWidget(widget)
    if minimum_height:
        area.setMinimumHeight(int(minimum_height))
    if maximum_height:
        area.setMaximumHeight(int(maximum_height))
    area.enableTransparentBackground()
    return area


def click_card(parent: QWidget | None = None, *, on_click=None) -> ClickCard:
    """整块可点的卡片；`on_click()`（内部按钮的点击不会冒泡到这里）。"""
    card = ClickCard(parent)
    if on_click is not None:
        card.clicked.connect(lambda: on_click())
    return card


# --------------------------------------------------------------- 组合构件
class ListPanel:
    """左栏列表：标题 + 搜索 + 列表 + 计数。

    搜索与选中都通过回调交给插件（不接 `on_search` 时自动按文本过滤行）。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        placeholder: str = "搜索",
        width: int = 340,
        count_text: str = "",
        on_select=None,
        on_search=None,
    ) -> None:
        self.card, layout = section_card(parent, title)
        if width:
            self.card.setFixedWidth(int(width))
        self._on_select = on_select
        self._on_search = on_search
        self.search = search_edit(self.card, placeholder=placeholder, on_change=self._search_changed)
        layout.addWidget(self.search)
        self.list = list_view(self.card, on_select=self._selection_changed)
        layout.addWidget(self.list, 1)
        self.count_label = status_label(self.card, count_text)
        layout.addWidget(self.count_label)

    def _search_changed(self, text: str) -> None:
        if self._on_search is not None:
            self._on_search(text)
        else:
            self.filter(text)

    def _selection_changed(self, data, text: str) -> None:
        if self._on_select is not None:
            self._on_select(data, text)

    def add_item(self, text: str, *, data: object = None, tooltip: str = "") -> QListWidgetItem:
        item = list_item(text, data=data, tooltip=tooltip)
        self.list.addItem(item)
        return item

    def set_items(self, items: Sequence[tuple], *, select: object = None) -> None:
        """重建列表；`items` 是 `(data, 文本[, 气泡])` 序列。"""
        self.list.clear()
        for entry in items:
            data = entry[0] if len(entry) > 0 else None
            text = entry[1] if len(entry) > 1 else ""
            tooltip = entry[2] if len(entry) > 2 else ""
            self.add_item(str(text), data=data, tooltip=str(tooltip))
        if select is not None:
            self.select(select)

    def clear(self) -> None:
        self.list.clear()

    def count(self) -> int:
        """当前可见行数。"""
        return sum(1 for index in range(self.list.count()) if not self.list.item(index).isHidden())

    def current_data(self) -> object:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select(self, data: object) -> bool:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == data:
                self.list.setCurrentItem(item)
                return True
        return False

    def filter(self, keyword: str) -> int:
        """按文本过滤行（大小写不敏感），返回可见行数。"""
        wanted = str(keyword).strip().lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            hidden = bool(wanted) and wanted not in item.text().lower()
            item.setHidden(hidden)
        return self.count()

    def set_count_text(self, text: str) -> None:
        self.count_label.setText(text)


class DetailPanel:
    """右栏详情：标题 + 副标题 + 内容行 + 底部操作按钮。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "未选择",
        subtitle: str = "",
        margins: tuple[int, int, int, int] = DETAIL_MARGINS,
        spacing: int = CARD_SPACING,
    ) -> None:
        self.card, layout = panel_card(parent, margins=margins, spacing=spacing)
        self.title_label = title_label(self.card, title)
        layout.addWidget(self.title_label)
        self.meta_label = caption(self.card, subtitle)
        layout.addWidget(self.meta_label)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)
        layout.addLayout(self.body)
        layout.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        layout.addLayout(self.actions)

    def add_row(self, label: str, widget: QWidget, *, label_width: int | None = None) -> QWidget:
        """加一行「标签 + 控件」。"""
        row = form_row(self.card, label, widget, label_width=label_width)
        self.body.addWidget(row)
        return row

    def add_widget(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_stretch(self, stretch: int = 1) -> None:
        self.body.addStretch(stretch)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def add_actions(self, widgets: Sequence[QWidget]) -> None:
        for widget in widgets:
            self.add_action(widget)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_meta(self, text: str) -> None:
        self.meta_label.setText(text)


class SplitPage(PageTemplate):
    """标准「左列表 + 右详情」页面：插件只填数据与回调，不再自己拼两栏结构。"""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        *,
        list_title: str = "",
        list_placeholder: str = "搜索",
        list_width: int = 340,
        list_count_text: str = "",
        detail_title: str = "未选择",
        detail_subtitle: str = "",
        on_select=None,
        on_search=None,
        margins: tuple[int, int, int, int] = PAGE_MARGINS,
        spacing: int = PAGE_SPACING,
    ) -> None:
        super().__init__(title, subtitle, parent, margins=margins, spacing=spacing)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(PAGE_SPACING)
        self.list_panel = ListPanel(
            self.body_host,
            title=list_title,
            placeholder=list_placeholder,
            width=list_width,
            count_text=list_count_text,
            on_select=on_select,
            on_search=on_search,
        )
        self.detail_panel = DetailPanel(
            self.body_host,
            title=detail_title,
            subtitle=detail_subtitle,
        )
        row.addWidget(self.list_panel.card)
        row.addWidget(self.detail_panel.card, 1)
        self.body_layout.addLayout(row, 1)


class MediaBar:
    """播放条：播放 / 暂停、进度、时间与音量，播放逻辑由插件绑定回调。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_toggle=None,
        on_seek=None,
        on_volume=None,
        volume: float = 0.8,
    ) -> None:
        self.host, layout = toolbar(parent, spacing=8)
        self.button = tool_button(self.host, FluentIcon.PLAY, "播放 / 暂停", on_toggle)
        layout.addWidget(self.button)
        self.slider = Slider(Qt.Orientation.Horizontal, self.host)
        self.slider.setRange(0, 0)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if on_seek is not None:
            self.slider.sliderMoved.connect(lambda value: on_seek(value))
            self.slider.sliderReleased.connect(lambda: on_seek(self.slider.value()))
        layout.addWidget(self.slider, 1)
        self.time_label = status_label(self.host, "")
        layout.addWidget(self.time_label)
        layout.addWidget(caption(self.host, "音量"))
        self.volume = Slider(Qt.Orientation.Horizontal, self.host)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(120)
        self.volume.setValue(int(max(0.0, min(1.0, float(volume))) * 100))
        if on_volume is not None:
            self.volume.valueChanged.connect(lambda value: on_volume(value / 100.0))
        layout.addWidget(self.volume)

    def set_playing(self, playing: bool) -> None:
        self.button.setIcon(FluentIcon.PAUSE if playing else FluentIcon.PLAY)

    def set_duration(self, milliseconds: int) -> None:
        self.slider.setRange(0, max(0, int(milliseconds)))

    def set_position(self, milliseconds: int) -> None:
        self.slider.setValue(max(0, int(milliseconds)))

    def block_signals(self, blocked: bool) -> None:
        """拖动进度条时挡住回写，避免自己触发自己的 on_seek。"""
        self.slider.blockSignals(bool(blocked))


def format_time(milliseconds: int) -> str:
    """毫秒 → `m:ss` / `h:mm:ss`。"""
    seconds = max(0, int(milliseconds // 1000))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _multimedia():
    """延迟导入 QtMultimedia：模块级不导入，没有它的环境仍可导入本库。"""
    try:
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError("当前环境缺少 QtMultimedia，无法播放音视频") from exc
    return QMediaPlayer, QAudioOutput, QVideoWidget


class PlayerPanel(QWidget):
    """通用播放控件：播放 / 暂停、进度、时长、音量与错误提示。

    QtMultimedia 延迟到构造时才导入（模块级导入会让没有它的环境连本库都进不来），
    缺依赖时抛 RuntimeError。子类置 `shows_video = True` 即改用 QVideoWidget 承载画面，
    否则显示音频占位区；播放条复用 MediaBar。
    """

    shows_video = False

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        player_cls, audio_cls, video_cls = _multimedia()
        self._player_cls = player_cls
        self._video_cls = video_cls
        self._path = Path(path)
        size = 0
        try:
            size = self._path.stat().st_size
        except OSError:
            pass
        self.caption = f"{self._path.name} · {human_size(size)}"
        self._player = player_cls(self)
        self._audio = audio_cls(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        self._surface: QWidget | None = None
        self._build_ui()
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        if self.shows_video:
            video = self._video_cls(self)
            self._player.setVideoOutput(video)
            self._surface = video
            root.addWidget(video, 1)
        else:
            stage = QWidget(self)
            stage_layout = QVBoxLayout(stage)
            stage_layout.setContentsMargins(0, 0, 0, 0)
            stage_layout.setSpacing(6)
            stage_layout.addStretch(1)
            title = title_label(stage, self._path.stem)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage_layout.addWidget(title)
            self._hint = status_label(stage, "音频播放中")
            self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stage_layout.addWidget(self._hint)
            stage_layout.addStretch(1)
            root.addWidget(stage, 1)

        self._bar = MediaBar(
            self,
            on_toggle=self._toggle,
            on_seek=self._on_seek,
            on_volume=self._audio.setVolume,
            volume=0.8,
        )
        root.addWidget(self._bar.host)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------ 行为
    def _toggle(self) -> None:
        if self._player.playbackState() == self._player_cls.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, state) -> None:
        self._bar.set_playing(state == self._player_cls.PlaybackState.PlayingState)

    def _on_position(self, position: int) -> None:
        self._bar.set_position(position)
        self._bar.time_label.setText(f"{format_time(position)} / {format_time(self._player.duration())}")

    def _on_duration(self, duration: int) -> None:
        self._bar.set_duration(duration)
        self._bar.time_label.setText(f"{format_time(self._player.position())} / {format_time(duration)}")

    def _on_seek(self, value: int) -> None:
        if self._player.duration() > 0 and abs(self._player.position() - value) > 800:
            self._player.setPosition(value)

    def _on_error(self, error, message: str = "") -> None:
        if error == self._player_cls.Error.NoError:
            return
        text = message or "该格式无法播放"
        self._bar.button.setEnabled(False)
        self.caption = f"{self._path.name} · 播放失败：{text}"
        if self._surface is None:
            self._hint.setText(f"无法播放：{text}（可在「查看器」页改为继承系统默认程序）")

    def stop(self) -> None:
        self._player.stop()


def image_canvas(parent: QWidget | None = None) -> tuple[QScrollArea, QLabel]:
    """图片显示区：返回 (滚动画布, 图片标签)，缩放逻辑仍由插件决定。"""
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sdk_ui.clear_scroll_background(area)
    label = QLabel(area)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    area.setWidget(label)
    return area, label


# --------------------------------------------------------------- 弹窗外壳
class FormDialog(MessageBoxBase):
    """插件弹窗外壳：标题 + 字段区（长了就滚动）+ 底部「确定 / 取消」。

    插件不再自己 new MessageBoxBase、也不再手算滚动区高度：

        dialog = form_dialog(parent, "新增模型", width=560)
        dialog.add_field("名称", line_edit(dialog, placeholder="给这张卡片起的名字"))
        dialog.set_buttons(yes="保存", cancel="取消")
        if dialog.exec():
            name = dialog.values()["name"]
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        width: int | None = 560,
        scroll: bool = True,
        minimum_height: int = 320,
        maximum_height: int | None = None,
        spacing: int = PAGE_SPACING,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        if width:
            self.widget.setFixedWidth(int(width))
        self.titleLabel = strong_label(self, title)
        if title:
            self.viewLayout.addWidget(self.titleLabel)

        self.area: SingleDirectionScrollArea | None = None
        if scroll:
            content = QWidget(self)
            body = QVBoxLayout(content)
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(int(spacing))
            self.area = scroll_area(
                self,
                widget=content,
                minimum_height=minimum_height,
                maximum_height=maximum_height or self._height_limit(minimum_height),
            )
            self.viewLayout.addWidget(self.area)
            self.body = body
        else:
            # 不滚动时字段直接进 viewLayout（标题下面那一层）。
            self.body = self.viewLayout

    #: 字段区布局（和 `viewLayout` 同一种用法，插件也可以往里 addWidget）。
    body: QVBoxLayout

    def _height_limit(self, minimum: int) -> int:
        """字段区最高到「窗口高度 - 260」，免得弹窗比屏幕还高。"""
        window = self.parentWidget()
        height = window.height() if window is not None else 720
        return max(int(minimum), int(height) - 260)

    def add_widget(self, widget: QWidget) -> QWidget:
        """往字段区加一个控件。"""
        self.body.addWidget(widget)
        return widget

    def add_field(self, label: str, widget: QWidget) -> QWidget:
        """加一项「小标签在上、控件在下」的字段。"""
        return self.add_widget(field(self, label, widget))

    def add_row(self, label: str, widget: QWidget, *, label_width: int | None = None) -> QWidget:
        """加一行「标签 + 控件」（横排，标签加粗）。"""
        return self.add_widget(form_row(self, label, widget, label_width=label_width, strong=True))

    def add_hint(self, text: str) -> CaptionLabel:
        """加一句说明文字。"""
        return self.add_widget(status_label(self, text))

    def set_buttons(self, *, yes: str | None = None, cancel: str | None = None) -> None:
        """改底部按钮文字。"""
        if yes:
            self.yesButton.setText(yes)
        if cancel:
            self.cancelButton.setText(cancel)


def form_dialog(parent: QWidget | None = None, title: str = "", **fields) -> FormDialog:
    """弹窗外壳工厂（字段见 `FormDialog`）。"""
    return FormDialog(parent, title=title, **fields)



__all__ = [
    "add_download_dialog",
    "AddDownloadDialog",
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
    "DURATION_ERROR",
    "DURATION_INFO",
    "DURATION_SUCCESS",
    "DURATION_WARNING",
    "download_list",
    "DownloadListView",
    "empty_state",
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
    "pip_task_list",
    "PipTaskView",
    "PlayerPanel",
    "primary_button",
    "progress_bar",
    "push_button",
    "radio_button",
    "read_only_table",
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
    "view_stack",
    "widget_column",
    "widget_row",
]
