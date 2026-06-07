# coding: utf-8
from PyQt6.QtCore import QObject


class Translator(QObject):

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.text = self.tr('文本')
        self.view = self.tr('视图')
        self.menus = self.tr('菜单和工具栏')
        self.icons = self.tr('图标')
        self.layout = self.tr('布局')
        self.dialogs = self.tr('对话框和弹出框')
        self.scroll = self.tr('滚动')
        self.material = self.tr('材料')
        self.dateTime = self.tr('日期和时间')
        self.navigation = self.tr('导航')
        self.basicInput = self.tr('基本输入')
        self.statusInfo = self.tr('状态和信息')
        self.price = self.tr("价格")