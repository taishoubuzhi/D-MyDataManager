# coding:utf-8
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget

from qfluentwidgets import (ScrollArea, TabBar, TabCloseButtonDisplayMode,
                            FluentIcon, qrouter)

from ..common.style_sheet import StyleSheet
from ..common.init.init_db import get_session
from ..components.data_tab_page import DataTabPage
from app.model.Data import Data


class DataInterface(ScrollArea):
    """数据管理界面 - TabBar + SegmentedWidget 架构"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._data_list = []
        self._tab_pages = {}  # routeKey -> DataTabPage
        self._tab_count = 0

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

        # TabBar
        self.tabBar = TabBar(self)
        self.tabBar.setTabMaximumWidth(200)
        self.tabBar.setTabShadowEnabled(True)
        self.tabBar.setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ON_HOVER)
        self.tabBar.tabAddRequested.connect(self._addTab)
        self.tabBar.tabCloseRequested.connect(self._removeTab)

        # 内容堆栈
        self.stackedWidget = QStackedWidget(self)
        self.stackedWidget.currentChanged.connect(self._onCurrentChanged)

        # 默认创建一个"全部数据"Tab
        self._addTab(self.tr("全部数据"))

    def __initLayout(self):
        self.vBoxLayout.setContentsMargins(36, 10, 36, 36)
        self.vBoxLayout.setSpacing(8)
        self.vBoxLayout.addWidget(self.tabBar)
        self.vBoxLayout.addWidget(self.stackedWidget, 1)

    def _addTab(self, title=None):
        """新增Tab页"""
        self._tab_count += 1
        route_key = f"data_tab_{self._tab_count}"

        if title is None:
            title = self.tr("数据视图") + f" {self._tab_count}"

        page = DataTabPage(self)
        page.setObjectName(route_key)
        page.setData(self._data_list)

        self._tab_pages[route_key] = page
        self.stackedWidget.addWidget(page)
        self.tabBar.addTab(
            routeKey=route_key,
            text=title,
            icon=FluentIcon.DOCUMENT,
            onClick=lambda: self.stackedWidget.setCurrentWidget(page)
        )
        self.stackedWidget.setCurrentWidget(page)
        self.tabBar.setCurrentTab(route_key)
        qrouter.setDefaultRouteKey(self.stackedWidget, route_key)

    def _removeTab(self, index):
        """关闭Tab页（至少保留1个）"""
        if self.tabBar.count() <= 1:
            return

        item = self.tabBar.tabItem(index)
        route_key = item.routeKey()
        page = self._tab_pages.pop(route_key, None)

        self.stackedWidget.removeWidget(page)
        self.tabBar.removeTab(index)

        if page:
            page.deleteLater()

    def _onCurrentChanged(self, index):
        """Tab切换时同步TabBar选中状态"""
        widget = self.stackedWidget.widget(index)
        if widget:
            self.tabBar.setCurrentTab(widget.objectName() if widget.objectName() else "")
            # 切换后刷新表格宽度
            if hasattr(widget, 'tableView') and widget._current_mode == "table":
                widget.tableView.resizeColumnsToContents()
                widget.tableView.horizontalHeader().setStretchLastSection(True)
                widget.tableView.updateGeometry()

    def __loadData(self):
        """从数据库加载数据"""
        session_factory = get_session()
        session = session_factory()
        try:
            self._data_list = session.query(Data).all()
        finally:
            session.close()

        # 分发数据到所有Tab页
        for page in self._tab_pages.values():
            page.setData(self._data_list)

    def refresh_data(self):
        """刷新数据"""
        self.__loadData()
