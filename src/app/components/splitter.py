# coding:utf-8
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QSplitter, QSplitterHandle

from qfluentwidgets import isDarkTheme


class SplitterHandle(QSplitterHandle):
    """主题自适应的分割线手柄"""

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        if isDarkTheme():
            color = QColor(255, 255, 255, 21)
        else:
            color = QColor(0, 0, 0, 15)

        pen = QPen(color)
        pen.setCosmetic(True)
        painter.setPen(pen)

        if self.orientation() == Qt.Orientation.Horizontal:
            x = self.width() // 2
            painter.drawLine(x, 0, x, self.height())
        else:
            y = self.height() // 2
            painter.drawLine(0, y, self.width(), y)


class Splitter(QSplitter):
    """主题自适应的分割器，支持拖拽调整宽度"""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(1)

    def createHandle(self):
        return SplitterHandle(self.orientation(), self)
