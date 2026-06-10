# coding:utf-8
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidgetItem, QListWidgetItem,
                             QStackedWidget, QHeaderView, QSplitter)

from qfluentwidgets import (SegmentedWidget, TableWidget, ListWidget,
                            FlowLayout)

from ..common.style_sheet import StyleSheet
from ..common.init.init_db import get_session
from ..components.data_card import DataCard, DataListCard, _parse_json_list
from ..components.filter_panel import FilterPanel, NameSearchPanel
from ..model.Data import DataType, DATA_TYPE_INFO, _normalize_type
from ..model.Tag import Tag


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


class DataTabPage(QWidget):
    """单个数据视图Tab页"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._all_data = []
        self._filtered_data = []
        self._current_mode = "table"
        self._selected_card = None

        self._setup_ui()
        StyleSheet.DATA_TAB_PAGE.apply(self)

    def _setup_ui(self):
        self.setObjectName('dataTabPage')

        # 根布局
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # === 左侧：顶部栏 + 视图堆栈 ===
        left_widget = QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 顶部栏：分段导航
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(16)

        self.modeSwitch = SegmentedWidget(self)
        self.modeSwitch.addItem("table", self.tr("表格"), lambda: self._switchMode("table"))
        self.modeSwitch.addItem("card", self.tr("卡片"), lambda: self._switchMode("card"))
        self.modeSwitch.addItem("list", self.tr("条目"), lambda: self._switchMode("list"))
        self.modeSwitch.setCurrentItem("table")
        top_bar.addWidget(self.modeSwitch)
        top_bar.addStretch(1)

        left_layout.addLayout(top_bar)

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
        self.cardContainer.setObjectName('cardContainer')
        self.cardFlowLayout = FlowLayout(self.cardContainer, needAni=True)
        self.cardFlowLayout.setSpacing(10)
        self.cardFlowLayout.setContentsMargins(0, 0, 0, 0)
        self.viewStack.addWidget(self.cardContainer)

        # 条目视图
        self.listView = ListWidget(self)
        self.listView.viewport().installEventFilter(self)
        self.viewStack.addWidget(self.listView)

        left_layout.addWidget(self.viewStack, 1)

        # === 右侧：筛选器区域 ===
        filter_widget = QWidget(self)
        filter_widget.setObjectName('filterArea')
        filter_widget.setMinimumWidth(180)
        filter_widget.setMaximumWidth(360)
        filter_layout = QVBoxLayout(filter_widget)
        filter_layout.setContentsMargins(8, 0, 8, 0)
        filter_layout.setSpacing(4)

        # 名称搜索筛选器
        self.nameSearch = NameSearchPanel(self)
        self.nameSearch.connect_changed(self._onFilterChanged)
        filter_layout.addWidget(self.nameSearch)

        # 类型筛选器
        self.typeFilter = FilterPanel("类型筛选", self)
        self.typeFilter.set_items({dt: name for dt, name, _ in DATA_TYPE_INFO})
        self.typeFilter.connect_changed(self._onFilterChanged)
        filter_layout.addWidget(self.typeFilter)

        # 关键词筛选器
        self.keywordFilter = FilterPanel("关键词筛选", self)
        self.keywordFilter.connect_changed(self._onFilterChanged)
        filter_layout.addWidget(self.keywordFilter)

        # 标签筛选器
        self.tagFilter = FilterPanel("标签筛选", self)
        self.tagFilter.connect_changed(self._onFilterChanged)
        filter_layout.addWidget(self.tagFilter)

        filter_layout.addStretch(1)

        # === QSplitter：可拖拽分割线 ===
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(left_widget)
        self._splitter.addWidget(filter_widget)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([800, 240])

        root_layout.addWidget(self._splitter)

    def setData(self, data_list):
        """设置全量数据"""
        self._all_data = data_list
        self._updateFilterOptions()
        self._applyFilter()

    def _updateFilterOptions(self):
        """根据当前数据更新筛选器选项"""
        # 关键词：统计当前数据中所有关键词
        keyword_items = {}
        for d in self._all_data:
            for kw in _parse_json_list(d.keywords):
                if kw and kw not in keyword_items:
                    keyword_items[kw] = kw
        self.keywordFilter.set_items(keyword_items)

        # 标签：优先从数据库 Tag 表获取，为空则从 Data.tag 统计
        tag_items = {}
        session = get_session()()
        try:
            tags = session.query(Tag).all()
            if tags:
                tag_items = {t.name: t.name for t in tags}
        finally:
            session.close()

        if not tag_items:
            for d in self._all_data:
                for t in _parse_json_list(d.tag):
                    if t and t not in tag_items:
                        tag_items[t] = t
        self.tagFilter.set_items(tag_items)

    def _applyFilter(self):
        """根据筛选条件过滤数据（多重筛选取交集）"""
        result = self._all_data

        # 名称模糊搜索
        search_text = self.nameSearch.get_search_text()
        if search_text:
            search_lower = search_text.lower()
            result = [d for d in result
                      if d.name and search_lower in d.name.lower()]

        # 类型筛选
        checked_types = self.typeFilter.get_checked()
        total_types = len(DATA_TYPE_INFO)
        if 0 < len(checked_types) < total_types:
            result = [d for d in result if _normalize_type(d.type) in checked_types]

        # 关键词筛选（OR 逻辑：数据的 keywords 与勾选关键词有交集即匹配）
        checked_keywords = self.keywordFilter.get_checked()
        if checked_keywords:
            result = [d for d in result
                      if set(_parse_json_list(d.keywords)) & checked_keywords]

        # 标签筛选（OR 逻辑）
        checked_tags = self.tagFilter.get_checked()
        if checked_tags:
            result = [d for d in result
                      if set(_parse_json_list(d.tag)) & checked_tags]

        self._filtered_data = result
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
            card.setSelected(False)
            self._selected_card = None
        else:
            if self._selected_card:
                self._selected_card.setSelected(False)
            card.setSelected(True)
            self._selected_card = card

    def _deselectCard(self):
        """取消卡片选中（点击空白区域时调用）"""
        if self._selected_card:
            self._selected_card.setSelected(False)
            self._selected_card = None

    def mousePressEvent(self, event):
        """点击空白区域取消选中"""
        if self._current_mode == "card" and self._selected_card:
            child = self.childAt(event.pos())
            if child is not self._selected_card and not self._selected_card.isAncestorOf(child):
                self._deselectCard()
        super().mousePressEvent(event)

    def eventFilter(self, obj, event):
        """事件过滤器：点击列表空白区域取消选中"""
        if obj is self.listView.viewport() and event.type() == event.Type.MouseButtonPress:
            index = self.listView.indexAt(event.pos())
            if not index.isValid():
                self.listView.clearSelection()
        return super().eventFilter(obj, event)

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
