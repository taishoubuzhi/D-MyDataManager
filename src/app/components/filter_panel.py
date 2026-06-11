# coding:utf-8
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QCompleter, QFrame, QSizePolicy)

from qfluentwidgets import (CheckBox, TransparentPushButton, FluentIcon,
                            SearchLineEdit, LineEdit, PillPushButton,
                            FlowLayout, ScrollArea, isDarkTheme,
                            setCustomStyleSheet)

from ..common.style_sheet import StyleSheet


class NameSearchPanel(QWidget):
    """名称模糊搜索筛选器（直接显示搜索框，无折叠）"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._search_text = ""
        self._changed_callbacks = []

        self.setObjectName('nameSearchPanel')
        self._setup_ui()
        StyleSheet.FILTER_PANEL.apply(self)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 面板本身：根据内容大小自适应
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # 标题
        from qfluentwidgets import BodyLabel
        titleLabel = BodyLabel("名称搜索", self)
        layout.addWidget(titleLabel)

        # 可清空的搜索输入框
        self._searchEdit = LineEdit(self)
        self._searchEdit.setPlaceholderText("输入名称关键词...")
        self._searchEdit.setClearButtonEnabled(True)
        self._searchEdit.setFixedHeight(30)
        self._searchEdit.textChanged.connect(self._onSearchTextChanged)
        layout.addWidget(self._searchEdit)

    def _onSearchTextChanged(self, text):
        """搜索框文本变化"""
        self._search_text = text.strip()
        for callback in self._changed_callbacks:
            callback()

    def get_search_text(self):
        """获取当前搜索文本"""
        return self._search_text

    def connect_changed(self, callback):
        """连接变更信号"""
        self._changed_callbacks.append(callback)


class FilterPanel(QWidget):
    """通用筛选器面板：折叠框 + 三态复选框 + 搜索框 + PillPushButton 列表"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent=parent)
        self._title = title
        self._buttons = {}      # key -> PillPushButton
        self._is_collapsed = True
        self._changed_callbacks = []
        self._updating = False  # 防止三态复选框循环触发

        self.setObjectName('filterPanel')
        self._setup_ui()
        StyleSheet.FILTER_PANEL.apply(self)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 面板本身：根据内容大小自适应，不随父布局无限扩张
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # 折叠头部：标题 + 折叠按钮
        self._header = QWidget(self)
        self._header.setObjectName('filterHeader')
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(8)

        self._toggleBtn = TransparentPushButton(FluentIcon.DOWN, self._title, self)
        self._toggleBtn.clicked.connect(self._toggleCollapse)
        header_layout.addWidget(self._toggleBtn)

        header_layout.addStretch(1)

        main_layout.addWidget(self._header)

        # 折叠内容区
        self._content = QWidget(self)
        self._content.setObjectName('filterContent')
        self._content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(6)

        # 三态复选框（全选/全不选/部分选中）
        self._triStateCb = CheckBox("全选", self._content)
        self._triStateCb.setTristate(True)
        self._triStateCb.setCheckState(Qt.CheckState.Checked)
        self._triStateCb.stateChanged.connect(self._onTriStateChanged)
        content_layout.addWidget(self._triStateCb)

        # 搜索框（带补全）
        self._searchEdit = SearchLineEdit(self)
        self._searchEdit.setPlaceholderText("搜索...")
        self._searchEdit.setFixedHeight(30)
        self._searchEdit.textChanged.connect(self._onSearchTextChanged)
        content_layout.addWidget(self._searchEdit)

        # PillPushButton 滚动区域
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        self._scrollArea.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._scrollArea.setMaximumHeight(200)

        self._scrollWidget = QWidget(self)
        self._buttonLayout = FlowLayout(self._scrollWidget, needAni=True)
        self._buttonLayout.setSpacing(6)
        self._buttonLayout.setContentsMargins(0, 0, 0, 0)

        self._scrollArea.setWidget(self._scrollWidget)
        self._scrollArea.enableTransparentBackground()
        self._scrollArea.viewport().setStyleSheet("background: transparent")
        content_layout.addWidget(self._scrollArea)  # 不加 stretch 因子

        self._content.setVisible(False)
        main_layout.addWidget(self._content)

    def _toggleCollapse(self):
        """切换折叠/展开，根据内容大小自动调整高度"""
        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            self._content.setVisible(False)
            self._toggleBtn.setIcon(FluentIcon.DOWN)
        else:
            self._content.setVisible(True)
            self._toggleBtn.setIcon(FluentIcon.UP)
        # 强制刷新父布局，让面板根据内容大小重新计算
        self.updateGeometry()
        if self.parent():
            self.parent().updateGeometry()

    def set_items(self, items: dict):
        """设置筛选选项 {key: display_name}，动态创建 PillPushButton"""
        # 保存旧的勾选状态
        old_checked = self.get_checked()

        # 清除旧按钮
        for btn in self._buttons.values():
            self._buttonLayout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()

        # 创建新 PillPushButton
        for key, display_name in items.items():
            btn = PillPushButton(str(display_name), self._scrollWidget)
            btn.setChecked(key in old_checked)
            btn.setProperty('filterKey', key)
            self._buttons[key] = btn
            self._buttonLayout.addWidget(btn)

            btn.toggled.connect(lambda checked, k=key: self._onButtonToggled(k, checked))

        # 更新搜索框补全
        self._updateCompleter()

        # 同步三态复选框
        self._syncTriState()

    def _onButtonToggled(self, key, checked):
        """PillPushButton 切换时触发筛选变更"""
        if self._updating:
            return
        self._syncTriState()
        for callback in self._changed_callbacks:
            callback()

    def _updateCompleter(self):
        """更新搜索框的 QCompleter"""
        names = [btn.text() for btn in self._buttons.values()]
        completer = QCompleter(names, self._searchEdit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(10)
        self._searchEdit.setCompleter(completer)

    def _onSearchTextChanged(self, text):
        """搜索框文本变化时过滤按钮可见性"""
        search = text.strip().lower()
        for key, btn in self._buttons.items():
            if not search:
                btn.setVisible(True)
            else:
                btn.setVisible(search in btn.text().lower())
        self._syncTriState()

    def _onTriStateChanged(self, state):
        """三态复选框状态变化：控制子按钮"""
        if self._updating:
            return
        self._updating = True
        if state == Qt.CheckState.Checked.value:
            self.selectAll()
        elif state == Qt.CheckState.Unchecked.value:
            self.deselectAll()
        # PartiallyChecked 时不改变子按钮
        self._updating = False

    def _syncTriState(self):
        """根据子按钮状态同步三态复选框"""
        visible_btns = [btn for btn in self._buttons.values() if btn.isVisible()]
        if not visible_btns:
            return
        checked_count = sum(1 for btn in visible_btns if btn.isChecked())
        self._updating = True
        if checked_count == 0:
            self._triStateCb.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == len(visible_btns):
            self._triStateCb.setCheckState(Qt.CheckState.Checked)
        else:
            self._triStateCb.setCheckState(Qt.CheckState.PartiallyChecked)
        self._updating = False

    def selectAll(self):
        """全选可见的按钮"""
        for btn in self._buttons.values():
            if btn.isVisible():
                btn.setChecked(True)
        for callback in self._changed_callbacks:
            callback()

    def deselectAll(self):
        """全不选可见的按钮"""
        for btn in self._buttons.values():
            if btn.isVisible():
                btn.setChecked(False)
        for callback in self._changed_callbacks:
            callback()

    def get_checked(self):
        """获取当前勾选的 key 集合"""
        return {key for key, btn in self._buttons.items() if btn.isChecked()}

    def connect_changed(self, callback):
        """连接筛选状态变化信号到回调"""
        self._changed_callbacks.append(callback)
