# coding:utf-8
from PyQt6.QtCore import Qt, QAbstractTableModel
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTableView, QListWidget, QListWidgetItem

from qfluentwidgets import (ScrollArea, SegmentedWidget, CardWidget, FlowLayout,
                            BodyLabel, CaptionLabel)

from ..common.style_sheet import StyleSheet
from ..common.init.init_db import get_session
from app.model.Data import Data, DataType


class DataTableModel(QAbstractTableModel):
    """数据表格模型"""

    COLUMNS = ["ID", "名称", "类型", "关键词", "标签", "大小", "隐藏", "路径", "用户ID", "数据库ID"]

    def __init__(self, data_list=None, parent=None):
        super().__init__(parent)
        self._data = data_list or []

    def rowCount(self, parent=None):
        return len(self._data)

    def columnCount(self, parent=None):
        return len(self.COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._data[index.row()]
        col = index.column()
        fields = [row.id, row.name, row.type.value if row.type else "",
                  str(row.keywords) if row.keywords else "",
                  str(row.tag) if row.tag else "",
                  row.size, row.is_hidden, row.url or "",
                  row.user_id, row.database_id]
        return str(fields[col])

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def update_data(self, data_list):
        self.beginResetModel()
        self._data = data_list
        self.endResetModel()


class DataCard(CardWidget):
    """数据卡片"""

    def __init__(self, data_item, parent=None):
        super().__init__(parent)
        self.data_item = data_item
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        name_label = BodyLabel(self.data_item.name or "未命名", self)
        type_label = CaptionLabel(f"类型: {self.data_item.type.value if self.data_item.type else '未知'}", self)
        size_label = CaptionLabel(f"大小: {self.data_item.size or 0}", self)
        tag_label = CaptionLabel(f"标签: {str(self.data_item.tag) if self.data_item.tag else '无'}", self)

        layout.addWidget(name_label)
        layout.addWidget(type_label)
        layout.addWidget(size_label)
        layout.addWidget(tag_label)

        self.setFixedWidth(180)
        self.setFixedHeight(120)


class DataInterface(ScrollArea):
    """数据管理界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._data_list = []

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.__initLayout()
        self.__loadData()

    def __initWidget(self):
        self.view.setObjectName('view')
        self.setObjectName('dataInterface')
        StyleSheet.DATA_INTERFACE.apply(self)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        # 模式切换
        self.modeSwitch = SegmentedWidget(self)
        self.modeSwitch.addItem("table", self.tr("表格"), lambda: self.__switchMode("table"))
        self.modeSwitch.addItem("card", self.tr("卡片"), lambda: self.__switchMode("card"))
        self.modeSwitch.addItem("list", self.tr("条目"), lambda: self.__switchMode("list"))
        self.modeSwitch.setCurrentItem("table")

        # 表格模式
        self.tableModel = DataTableModel()
        self.tableView = QTableView(self)
        self.tableView.setModel(self.tableModel)
        self.tableView.setAlternatingRowColors(True)
        self.tableView.horizontalHeader().setStretchLastSection(True)

        # 卡片容器
        self.cardContainer = QWidget(self)
        self.cardFlowLayout = FlowLayout(self.cardContainer, needAni=True)
        self.cardFlowLayout.setSpacing(10)
        self.cardFlowLayout.setContentsMargins(0, 0, 0, 0)

        # 条目模式
        self.listView = QListWidget(self)

        # 默认显示表格
        self.currentMode = "table"
        self.cardContainer.hide()
        self.listView.hide()

    def __initLayout(self):
        self.vBoxLayout.setContentsMargins(36, 10, 36, 36)
        self.vBoxLayout.setSpacing(10)
        self.vBoxLayout.addWidget(self.modeSwitch)
        self.vBoxLayout.addWidget(self.tableView)
        self.vBoxLayout.addWidget(self.cardContainer)
        self.vBoxLayout.addWidget(self.listView)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def __loadData(self):
        """从数据库加载数据"""
        session_factory = get_session()
        session = session_factory()
        try:
            self._data_list = session.query(Data).all()
        finally:
            session.close()

        self.__updateView()

    def __updateView(self):
        """更新当前视图"""
        if self.currentMode == "table":
            self.tableModel.update_data(self._data_list)
        elif self.currentMode == "card":
            self.__updateCards()
        elif self.currentMode == "list":
            self.__updateList()

    def __updateCards(self):
        """更新卡片视图"""
        # FlowLayout.takeAt() 返回的是 widget 本身，不是 QLayoutItem
        self.cardFlowLayout.removeAllWidgets()

        for data_item in self._data_list:
            card = DataCard(data_item, self.cardContainer)
            self.cardFlowLayout.addWidget(card)

    def __updateList(self):
        """更新条目视图"""
        self.listView.clear()
        for data_item in self._data_list:
            type_str = data_item.type.value if data_item.type else "未知"
            text = f"{data_item.name or '未命名'}  |  类型: {type_str}  |  大小: {data_item.size or 0}"
            item = QListWidgetItem(text, self.listView)
            self.listView.addItem(item)

    def __switchMode(self, mode):
        """切换展示模式"""
        self.currentMode = mode
        self.tableView.setVisible(mode == "table")
        self.cardContainer.setVisible(mode == "card")
        self.listView.setVisible(mode == "list")
        self.__updateView()

    def refresh_data(self):
        """刷新数据"""
        self.__loadData()
