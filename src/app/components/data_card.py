# coding:utf-8
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame

from qfluentwidgets import (CardWidget, IconWidget, BodyLabel, CaptionLabel,
                            FluentIcon, isDarkTheme)

from ..common.style_sheet import StyleSheet
from ..model.Data import DataType


# DataType -> FluentIcon 映射
_DATA_TYPE_ICONS = {
    DataType.IMAGE: FluentIcon.PHOTO,
    DataType.VIDEO: FluentIcon.VIDEO,
    DataType.AUDIO: FluentIcon.MUSIC,
    DataType.DOC: FluentIcon.DOCUMENT,
    DataType.DOCX: FluentIcon.DOCUMENT,
    DataType.EXCEL: FluentIcon.DOCUMENT,
    DataType.PPT: FluentIcon.DOCUMENT,
    DataType.TEXT: FluentIcon.EDIT,
    DataType.UNKNOWN: FluentIcon.HELP,
}


def _get_type_icon(data_type):
    return _DATA_TYPE_ICONS.get(data_type, FluentIcon.HELP)


def _parse_json_list(value):
    """将 JSON 字段解析为字符串列表"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    return [str(value)]


class TagChip(QFrame):
    """胶囊标签组件"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setObjectName('tagChip')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(0)

        label = CaptionLabel(text, self)
        label.setObjectName('tagChipLabel')
        layout.addWidget(label)

        # 设置固定高度和最小宽度
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)


class TagContainer(QWidget):
    """胶囊标签容器（横向排列）"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setObjectName('tagContainer')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        for item in items:
            if item and str(item).strip():
                chip = TagChip(str(item), self)
                layout.addWidget(chip)

        layout.addStretch(1)


def _get_content_label(data_item):
    """根据数据类型生成 content 显示文本"""
    content = data_item.content or ""
    data_type = data_item.type

    # 文件资源类型：显示为路径
    if DataType.is_file_type(data_type):
        return content if content else "（无文件）"
    else:
        # 文本等非文件类型：显示内容本体
        if content:
            if len(content) > 60:
                return content[:57] + "..."
            return content
        return "（空）"


class DataCard(CardWidget):
    """数据卡片 - 用于卡片模式"""

    def __init__(self, data_item, parent=None):
        super().__init__(parent=parent)
        self.data_item = data_item
        self._selected = False
        self.setProperty('isSelected', False)
        self._build_ui()
        StyleSheet.DATA_CARD.apply(self)

    @property
    def isSelected(self):
        return self._selected

    def setSelected(self, selected: bool):
        """设置选中状态"""
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty('isSelected', selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self._updateBackgroundColor()

    def _normalBackgroundColor(self):
        if getattr(self, '_selected', False):
            return QColor(0, 120, 212, 50) if isDarkTheme() else QColor(0, 120, 212, 25)
        return QColor(255, 255, 255, 13 if isDarkTheme() else 170)

    def _hoverBackgroundColor(self):
        if getattr(self, '_selected', False):
            return QColor(0, 120, 212, 65) if isDarkTheme() else QColor(0, 120, 212, 40)
        return QColor(255, 255, 255, 21 if isDarkTheme() else 64)

    def _build_ui(self):
        item = self.data_item

        # 主水平布局：左图标 + 右文本信息
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(16, 14, 16, 14)
        self.hBoxLayout.setSpacing(16)

        # 左侧：类型图标
        self.iconWidget = IconWidget(_get_type_icon(item.type), self)
        self.iconWidget.setFixedSize(48, 48)
        self.hBoxLayout.addWidget(self.iconWidget)

        # 右侧：信息区
        info_widget = QWidget(self)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        # 第一行：类型 + 大小
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.typeLabel = CaptionLabel(f"类型: {DataType.get_name(item.type)}", self)
        self.typeLabel.setObjectName('typeLabel')
        title_row.addWidget(self.typeLabel)

        title_row.addStretch(1)
        size_str = str(item.size or 0)
        self.sizeLabel = CaptionLabel(f"大小: {size_str}", self)
        self.sizeLabel.setObjectName('sizeLabel')
        title_row.addWidget(self.sizeLabel)

        info_layout.addLayout(title_row)

        # 第二行：关键词胶囊
        keywords = _parse_json_list(item.keywords)
        if keywords:
            self.keywordsContainer = TagContainer(keywords[:5], self)
            info_layout.addWidget(self.keywordsContainer)

        # 第三行：标签胶囊
        tags = _parse_json_list(item.tag)
        if tags:
            self.tagContainer = TagContainer(tags[:5], self)
            info_layout.addWidget(self.tagContainer)

        # 最后：内容/路径
        content_text = _get_content_label(item)
        self.contentLabel = CaptionLabel(f"内容: {content_text}", self)
        self.contentLabel.setObjectName('contentLabel')
        self.contentLabel.setWordWrap(True)
        info_layout.addWidget(self.contentLabel)

        info_layout.addStretch(1)

        self.hBoxLayout.addWidget(info_widget, 1)

        self.setMinimumWidth(360)
        self.setMinimumHeight(120)


class DataListCard(CardWidget):
    """数据列表卡片 - 用于条目模式"""

    def __init__(self, data_item, parent=None):
        super().__init__(parent=parent)
        self.data_item = data_item
        self._build_ui()
        StyleSheet.DATA_CARD.apply(self)

    def _build_ui(self):
        item = self.data_item

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(12, 8, 12, 8)
        self.hBoxLayout.setSpacing(12)

        # 左侧：小图标
        self.iconWidget = IconWidget(_get_type_icon(item.type), self)
        self.iconWidget.setFixedSize(24, 24)
        self.hBoxLayout.addWidget(self.iconWidget)

        # 类型
        self.typeLabel = CaptionLabel(DataType.get_name(item.type), self)
        self.typeLabel.setObjectName('listTypeLabel')
        self.hBoxLayout.addWidget(self.typeLabel)

        # 标签（胶囊，最多 3 个）
        tags = _parse_json_list(item.tag)
        if tags:
            self.tagContainer = TagContainer(tags[:3], self)
            self.hBoxLayout.addWidget(self.tagContainer, 1)
        else:
            self.hBoxLayout.addStretch(1)

        # 大小
        size_str = str(item.size or 0)
        self.sizeLabel = CaptionLabel(f"{size_str}", self)
        self.sizeLabel.setObjectName('listSizeLabel')
        self.sizeLabel.setMinimumWidth(60)
        self.sizeLabel.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.hBoxLayout.addWidget(self.sizeLabel)

        # 内容/路径
        content_text = _get_content_label(item)
        self.contentLabel = CaptionLabel(content_text, self)
        self.contentLabel.setObjectName('listContentLabel')
        self.contentLabel.setMinimumWidth(120)
        self.contentLabel.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.hBoxLayout.addWidget(self.contentLabel)

        self.setMinimumHeight(60)
