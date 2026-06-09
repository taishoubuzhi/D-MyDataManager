# coding:utf-8
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidgetItem, QListWidgetItem,
                             QStackedWidget, QHeaderView)

from qfluentwidgets import (SegmentedWidget, TableWidget, ListWidget,
                            FlowLayout, CheckBox, TransparentPushButton,
                            FluentIcon)

from ..common.style_sheet import StyleSheet
from ..components.data_card import DataCard, DataListCard
from ..model.Data import DataType, DATA_TYPE_INFO, _normalize_type


# 表格列定义：(列名, 取值函数)
_COLUMNS = [
    ("ID", lambda d: str(d.id)),
    ("名称", lambda d: d.name or "未命名"),
    ("类型", lambda d: DataType.get_name(d.type)),
    ("关键词", lambda d: _format_json(d.keywords)),
    ("标签", lambda d: _format_json(d.tag)),
    ("大小", lambda d: str(d.size or 0)),
    ("隐藏", lambda d: "是" if d.is_hidden else "否"),
    ("内容", lambda d: _format_content(d)),
    ("用户ID", lambda d: str(d.user_id) if d.user_id else ""),
    ("数据库ID", lambda d: str(d.database_id) if d.database_id else ""),
]


def _format_json(value):
    """格式化 JSON 字段"""
    if value is None:
        return ""
    if isinstance(value, list):
        items = [str(v) for v in value if v]
        return ", ".join(items) if items else ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _format_content(data_item):
    """根据数据类型格式化 content 字段显示"""
    content = data_item.content or ""
    data_type = data_item.type

    if DataType.is_file_type(data_type):
        return content if content else "（无文件）"
    else:
        if content:
            if len(content) > 80:
                return content[:77] + "..."
            return content
        return "（空）"


class TypeFilterPanel(QWidget):
    """类型筛选面板：折叠框 + 复选框 + 全选/全不选按钮"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._checkboxes = {}  # DataType -> CheckBox
        self._is_collapsed = True
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName('typeFilterPanel')
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 折叠头部：标题 + 折叠按钮
        self._header = QWidget(self)
        self._header.setObjectName('filterHeader')
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(8)

        self._toggleBtn = TransparentPushButton(FluentIcon.DOWN, "类型筛选", self)
        self._toggleBtn.clicked.connect(self._toggleCollapse)
        header_layout.addWidget(self._toggleBtn)

        header_layout.addStretch(1)

        # 全选/全不选按钮
        self._selectAllBtn = TransparentPushButton("全选", self)
        self._selectAllBtn.clicked.connect(self.selectAll)
        header_layout.addWidget(self._selectAllBtn)

        self._deselectAllBtn = TransparentPushButton("全不选", self)
        self._deselectAllBtn.clicked.connect(self.deselectAll)
        header_layout.addWidget(self._deselectAllBtn)

        main_layout.addWidget(self._header)

        # 折叠内容区：复选框
        self._content = QWidget(self)
        self._content.setObjectName('filterContent')
        content_layout = QHBoxLayout(self._content)
        content_layout.setContentsMargins(20, 4, 4, 4)
        content_layout.setSpacing(12)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        for data_type, name, _ in DATA_TYPE_INFO:
            cb = CheckBox(name, self._content)
            cb.setChecked(True)
            cb.setProperty('dataType', data_type)
            self._checkboxes[data_type] = cb
            content_layout.addWidget(cb)

        content_layout.addStretch(1)
        self._content.setMaximumHeight(0)
        self._content.setVisible(False)
        main_layout.addWidget(self._content)

    def _toggleCollapse(self):
        """切换折叠/展开"""
        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            self._content.setVisible(False)
            self._content.setMaximumHeight(0)
            self._toggleBtn.setIcon(FluentIcon.DOWN)
        else:
            self._content.setVisible(True)
            self._content.setMaximumHeight(16777215)
            self._toggleBtn.setIcon(FluentIcon.UP)

    def selectAll(self):
        """全选"""
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def deselectAll(self):
        """全不选"""
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def get_checked_types(self):
        """获取当前勾选的 DataType 集合"""
        return {dt for dt, cb in self._checkboxes.items() if cb.isChecked()}

    def connect_changed(self, callback):
        """连接所有复选框状态变化信号到回调"""
        for cb in self._checkboxes.values():
            cb.stateChanged.connect(callback)


class DataTabPage(QWidget):
    """单个数据视图Tab页"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._all_data = []
        self._filtered_data = []
        self._current_mode = "table"
        self._selected_card = None  # 当前选中的卡片

        self._setup_ui()
        StyleSheet.DATA_TAB_PAGE.apply(self)

    def _setup_ui(self):
        self.setObjectName('dataTabPage')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 顶部栏：分段导航
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(16)

        # 分段导航：表格/卡片/条目
        self.modeSwitch = SegmentedWidget(self)
        self.modeSwitch.addItem("table", self.tr("表格"), lambda: self._switchMode("table"))
        self.modeSwitch.addItem("card", self.tr("卡片"), lambda: self._switchMode("card"))
        self.modeSwitch.addItem("list", self.tr("条目"), lambda: self._switchMode("list"))
        self.modeSwitch.setCurrentItem("table")
        top_bar.addWidget(self.modeSwitch)

        top_bar.addStretch(1)

        layout.addLayout(top_bar)

        # 类型筛选面板
        self.typeFilterPanel = TypeFilterPanel(self)
        self.typeFilterPanel.connect_changed(self._onFilterChanged)
        layout.addWidget(self.typeFilterPanel)

        # 视图堆栈
        self.viewStack = QStackedWidget(self)

        # 表格视图
        self.tableView = TableWidget(self)
        self.tableView.verticalHeader().hide()
        self.tableView.setBorderRadius(8)
        self.tableView.setBorderVisible(True)
        self.tableView.setAlternatingRowColors(True)
        self.tableView.setColumnCount(len(_COLUMNS))
        self.tableView.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        header = self.tableView.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tableView.verticalHeader().setDefaultSectionSize(38)
        self.viewStack.addWidget(self.tableView)

        # 卡片容器
        self.cardContainer = QWidget(self)
        self.cardFlowLayout = FlowLayout(self.cardContainer, needAni=True)
        self.cardFlowLayout.setSpacing(10)
        self.cardFlowLayout.setContentsMargins(0, 0, 0, 0)
        self.viewStack.addWidget(self.cardContainer)

        # 条目视图
        self.listView = ListWidget(self)
        self.viewStack.addWidget(self.listView)

        layout.addWidget(self.viewStack, 1)

    def setData(self, data_list):
        """设置全量数据"""
        self._all_data = data_list
        self._applyFilter()

    def _applyFilter(self):
        """根据筛选条件过滤数据"""
        checked = self.typeFilterPanel.get_checked_types()

        # 全选或全不选 → 显示全部
        if len(checked) == 0 or len(checked) == len(DATA_TYPE_INFO):
            self._filtered_data = list(self._all_data)
        else:
            self._filtered_data = [
                d for d in self._all_data
                if _normalize_type(d.type) in checked
            ]
        self._refreshView()

    def _refreshView(self):
        """刷新当前视图"""
        if self._current_mode == "table":
            self._updateTable()
        elif self._current_mode == "card":
            self._updateCards()
        elif self._current_mode == "list":
            self._updateList()

    def _updateTable(self):
        self.tableView.setRowCount(len(self._filtered_data))
        for row, item in enumerate(self._filtered_data):
            for col, (_, getter) in enumerate(_COLUMNS):
                self.tableView.setItem(row, col, QTableWidgetItem(getter(item)))
        self.tableView.resizeColumnsToContents()
        self.tableView.horizontalHeader().setStretchLastSection(True)

    def _updateCards(self):
        self.cardFlowLayout.takeAllWidgets()
        self._selected_card = None
        for data_item in self._filtered_data:
            card = DataCard(data_item, self.cardContainer)
            card.clicked.connect(lambda checked=False, c=card: self._onCardClicked(c))
            self.cardFlowLayout.addWidget(card)

    def _onCardClicked(self, card):
        """卡片点击选中"""
        if self._selected_card is card:
            # 取消选中
            card.setSelected(False)
            self._selected_card = None
        else:
            # 切换选中
            if self._selected_card:
                self._selected_card.setSelected(False)
            card.setSelected(True)
            self._selected_card = card

    def _updateList(self):
        self.listView.clear()
        for data_item in self._filtered_data:
            item = QListWidgetItem(self.listView)
            card = DataListCard(data_item, self.listView)
            item.setSizeHint(card.sizeHint())
            self.listView.addItem(item)
            self.listView.setItemWidget(item, card)

    def _switchMode(self, mode):
        self._current_mode = mode
        mode_index = {"table": 0, "card": 1, "list": 2}
        self.viewStack.setCurrentIndex(mode_index[mode])
        self._refreshView()

        if mode == "table":
            self.tableView.resizeColumnsToContents()
            self.tableView.horizontalHeader().setStretchLastSection(True)
            self.tableView.updateGeometry()

    def _onFilterChanged(self):
        """筛选条件变更"""
        self._applyFilter()
