# coding:utf-8
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPainterPath, QLinearGradient
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from qfluentwidgets import ScrollArea, isDarkTheme, FluentIcon
from ..common.config import config
from ..components import LinkCardView
from ..components import SampleCardView
from ..common.style_sheet import StyleSheet


class BannerWidget(QWidget):
    """ Banner widget """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setFixedHeight(336)

        self.vBoxLayout = QVBoxLayout(self)
        self.dLabel = QLabel('Fluent d', self)
        self.banner = QPixmap(':/app/images/header1.png')
        self.linkCardView = LinkCardView(self)

        self.dLabel.setObjectName('dLabel')

        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(0, 20, 0, 0)
        self.vBoxLayout.addWidget(self.dLabel)
        self.vBoxLayout.addWidget(self.linkCardView, 1, Qt.AlignmentFlag.AlignBottom)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def paintEvent(self, e):
        super().paintEvent(e)
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        w, h = self.width(), self.height()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        path.addRect(QRectF(0, h-50, 50, 50))
        path.addRect(QRectF(w-50, 0, 50, 50))
        path.addRect(QRectF(w-50, h-50, 50, 50))
        path = path.simplified()

        # init linear gradient effect
        gradient = QLinearGradient(0, 0, 0, h)

        # draw background color
        if not isDarkTheme():
            gradient.setColorAt(0, QColor(207, 216, 228, 255))
            gradient.setColorAt(1, QColor(207, 216, 228, 0))
        else:
            gradient.setColorAt(0, QColor(0, 0, 0, 255))
            gradient.setColorAt(1, QColor(0, 0, 0, 0))

        painter.fillPath(path, QBrush(gradient))

        # draw banner image
        pixmap = self.banner.scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.fillPath(path, QBrush(pixmap))


class HomeInterface(ScrollArea):
    """ Home interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.banner = BannerWidget(self)
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.loadSamples()

    def __initWidget(self):
        self.view.setObjectName('view')
        self.setObjectName('homeInterface')
        StyleSheet.HOME_INTERFACE.apply(self)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 36)
        self.vBoxLayout.setSpacing(40)
        self.vBoxLayout.addWidget(self.banner)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def loadSamples(self):
        """ load samples """
        # basic input samples
        basicInputView = SampleCardView(
            self.tr("基本输入示例"), self.view)
        basicInputView.addSampleCard(
            icon=":/app/images/controls/Button.png",
            title="Button",
            content=self.tr(
                "响应用户输入并发出点击信号的控件。"),
            routeKey="basicInputInterface",
            index=0
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/Checkbox.png",
            title="CheckBox",
            content=self.tr("用户可以选择或清除的控件。"),
            routeKey="basicInputInterface",
            index=8
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/ComboBox.png",
            title="ComboBox",
            content=self.tr(
                "用户可以从中选择项的下拉列表。"),
            routeKey="basicInputInterface",
            index=10
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/DropDownButton.png",
            title="DropDownButton",
            content=self.tr(
                "点击时显示选项弹出框的按钮。"),
            routeKey="basicInputInterface",
            index=12
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/HyperlinkButton.png",
            title="HyperlinkButton",
            content=self.tr(
                "显示为超链接文本的按钮，可以导航到URI或处理点击事件。"),
            routeKey="basicInputInterface",
            index=18
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/RadioButton.png",
            title="RadioButton",
            content=self.tr(
                "允许用户从一组选项中选择单个选项的控件。"),
            routeKey="basicInputInterface",
            index=19
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/Slider.png",
            title="Slider",
            content=self.tr(
                "通过沿轨道移动滑块来让用户从值范围中选择的控件。"),
            routeKey="basicInputInterface",
            index=20
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/SplitButton.png",
            title="SplitButton",
            content=self.tr(
                "由两部分组成的按钮，点击次要部分时显示弹出框。"),
            routeKey="basicInputInterface",
            index=21
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/ToggleSwitch.png",
            title="SwitchButton",
            content=self.tr(
                "可以在两种状态之间切换的开关。"),
            routeKey="basicInputInterface",
            index=25
        )
        basicInputView.addSampleCard(
            icon=":/app/images/controls/ToggleButton.png",
            title="ToggleButton",
            content=self.tr(
                "可以像复选框一样在两种状态之间切换的按钮。"),
            routeKey="basicInputInterface",
            index=26
        )
        self.vBoxLayout.addWidget(basicInputView)

        # date time samples
        dateTimeView = SampleCardView(self.tr('日期和时间示例'), self.view)
        dateTimeView.addSampleCard(
            icon=":/app/images/controls/CalendarDatePicker.png",
            title="CalendarPicker",
            content=self.tr("允许用户使用日历选择日期值的控件。"),
            routeKey="dateTimeInterface",
            index=0
        )
        dateTimeView.addSampleCard(
            icon=":/app/images/controls/DatePicker.png",
            title="DatePicker",
            content=self.tr("允许用户选择日期值的控件。"),
            routeKey="dateTimeInterface",
            index=2
        )
        dateTimeView.addSampleCard(
            icon=":/app/images/controls/TimePicker.png",
            title="TimePicker",
            content=self.tr(
                "可配置的允许用户选择时间值的控件。"),
            routeKey="dateTimeInterface",
            index=4
        )
        self.vBoxLayout.addWidget(dateTimeView)

        # dialog samples
        dialogView = SampleCardView(self.tr('对话框示例'), self.view)
        dialogView.addSampleCard(
            icon=":/app/images/controls/Flyout.png",
            title="Dialog",
            content=self.tr("无边框消息对话框。"),
            routeKey="dialogInterface",
            index=0
        )
        dialogView.addSampleCard(
            icon=":/app/images/controls/ContentDialog.png",
            title="MessageBox",
            content=self.tr("带遮罩的消息对话框。"),
            routeKey="dialogInterface",
            index=1
        )
        dialogView.addSampleCard(
            icon=":/app/images/controls/ColorPicker.png",
            title="ColorDialog",
            content=self.tr("允许用户选择颜色的对话框。"),
            routeKey="dialogInterface",
            index=2
        )
        dialogView.addSampleCard(
            icon=":/app/images/controls/Flyout.png",
            title="Flyout",
            content=self.tr("显示上下文信息并启用用户交互。"),
            routeKey="dialogInterface",
            index=3
        )
        dialogView.addSampleCard(
            icon=":/app/images/controls/TeachingTip.png",
            title="TeachingTip",
            content=self.tr("内容丰富的弹出框，用于引导用户和实现教学时刻。"),
            routeKey="dialogInterface",
            index=5
        )
        self.vBoxLayout.addWidget(dialogView)

        # layout samples
        layoutView = SampleCardView(self.tr('布局示例'), self.view)
        layoutView.addSampleCard(
            icon=":/app/images/controls/Grid.png",
            title="FlowLayout",
            content=self.tr(
                "从左到右排列组件的布局，当当前行满时换行到下一行。"),
            routeKey="layoutInterface",
            index=0
        )
        self.vBoxLayout.addWidget(layoutView)

        # material samples
        materialView = SampleCardView(self.tr('材料示例'), self.view)
        materialView.addSampleCard(
            icon=":/app/images/controls/Acrylic.png",
            title="AcrylicLabel",
            content=self.tr(
                "推荐用于面板背景的半透明材料。"),
            routeKey="materialInterface",
            index=0
        )
        self.vBoxLayout.addWidget(materialView)

        # menu samples
        menuView = SampleCardView(self.tr('菜单和工具栏示例'), self.view)
        menuView.addSampleCard(
            icon=":/app/images/controls/MenuFlyout.png",
            title="RoundMenu",
            content=self.tr(
                "显示简单命令或选项的上下文列表。"),
            routeKey="menuInterface",
            index=0
        )
        menuView.addSampleCard(
            icon=":/app/images/controls/CommandBar.png",
            title="CommandBar",
            content=self.tr(
                "显示简单命令或选项的上下文列表。"),
            routeKey="menuInterface",
            index=2
        )
        menuView.addSampleCard(
            icon=":/app/images/controls/CommandBarFlyout.png",
            title="CommandBarFlyout",
            content=self.tr(
                "显示主动命令的迷你工具栏，以及可选的命令菜单。"),
            routeKey="menuInterface",
            index=3
        )
        self.vBoxLayout.addWidget(menuView)

        # navigation
        navigationView = SampleCardView(self.tr('导航'), self.view)
        navigationView.addSampleCard(
            icon=":/app/images/controls/BreadcrumbBar.png",
            title="BreadcrumbBar",
            content=self.tr(
                "显示到达当前位置的导航路径。"),
            routeKey="navigationViewInterface",
            index=0
        )
        navigationView.addSampleCard(
            icon=":/app/images/controls/Pivot.png",
            title="Pivot",
            content=self.tr(
                "在选项卡视图中呈现来自不同来源的信息。"),
            routeKey="navigationViewInterface",
            index=1
        )
        navigationView.addSampleCard(
            icon=":/app/images/controls/TabView.png",
            title="TabView",
            content=self.tr(
                "在选项卡视图中呈现来自不同来源的信息。"),
            routeKey="navigationViewInterface",
            index=3
        )
        self.vBoxLayout.addWidget(navigationView)

        # scroll samples
        scrollView = SampleCardView(self.tr('滚动示例'), self.view)
        scrollView.addSampleCard(
            icon=":/app/images/controls/ScrollViewer.png",
            title="ScrollArea",
            content=self.tr(
                "允许用户平滑平移和缩放内容的容器控件。"),
            routeKey="scrollInterface",
            index=0
        )
        scrollView.addSampleCard(
            icon=":/app/images/controls/PipsPager.png",
            title="PipsPager",
            content=self.tr(
                "当页码不需要视觉显示时，允许用户浏览分页集合的控件。"),
            routeKey="scrollInterface",
            index=3
        )
        self.vBoxLayout.addWidget(scrollView)

        # state info samples
        stateInfoView = SampleCardView(self.tr('状态和信息示例'), self.view)
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/ProgressRing.png",
            title="StateToolTip",
            content=self.tr(
                "显示应用在任务上的进度，或应用正在执行阻止用户交互的持续工作。"),
            routeKey="statusInfoInterface",
            index=0
        )
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/InfoBadge.png",
            title="InfoBadge",
            content=self.tr(
                "一种非侵入式UI，用于显示通知或将焦点带到某个区域。"),
            routeKey="statusInfoInterface",
            index=3
        )
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/InfoBar.png",
            title="InfoBar",
            content=self.tr(
                "用于显示应用范围状态变更信息的内联消息。"),
            routeKey="statusInfoInterface",
            index=4
        )
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/ProgressBar.png",
            title="ProgressBar",
            content=self.tr(
                "显示应用在任务上的进度，或应用正在执行不阻止用户交互的持续工作。"),
            routeKey="statusInfoInterface",
            index=8
        )
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/ProgressRing.png",
            title="ProgressRing",
            content=self.tr(
                "显示应用在任务上的进度，或应用正在执行不阻止用户交互的持续工作。"),
            routeKey="statusInfoInterface",
            index=10
        )
        stateInfoView.addSampleCard(
            icon=":/app/images/controls/ToolTip.png",
            title="ToolTip",
            content=self.tr(
                "在弹出窗口中显示元素的信息。"),
            routeKey="statusInfoInterface",
            index=1
        )
        self.vBoxLayout.addWidget(stateInfoView)

        # text samples
        textView = SampleCardView(self.tr('文本示例'), self.view)
        textView.addSampleCard(
            icon=":/app/images/controls/TextBox.png",
            title="LineEdit",
            content=self.tr("单行纯文本字段。"),
            routeKey="textInterface",
            index=0
        )
        textView.addSampleCard(
            icon=":/app/images/controls/PasswordBox.png",
            title="PasswordLineEdit",
            content=self.tr("用于输入密码的控件。"),
            routeKey="textInterface",
            index=2
        )
        textView.addSampleCard(
            icon=":/app/images/controls/NumberBox.png",
            title="SpinBox",
            content=self.tr(
                "用于数字输入和代数方程求值的文本控件。"),
            routeKey="textInterface",
            index=3
        )
        textView.addSampleCard(
            icon=":/app/images/controls/RichEditBox.png",
            title="TextEdit",
            content=self.tr(
                "支持格式化文本、超链接和其他富内容的富文本编辑控件。"),
            routeKey="textInterface",
            index=8
        )
        self.vBoxLayout.addWidget(textView)

        # view samples
        collectionView = SampleCardView(self.tr('视图示例'), self.view)
        collectionView.addSampleCard(
            icon=":/app/images/controls/ListView.png",
            title="ListView",
            content=self.tr(
                "在垂直列表中呈现项目集合的控件。"),
            routeKey="viewInterface",
            index=0
        )
        collectionView.addSampleCard(
            icon=":/app/images/controls/DataGrid.png",
            title="TableView",
            content=self.tr(
                "DataGrid控件提供了以行和列显示数据集合的灵活方式。"),
            routeKey="viewInterface",
            index=1
        )
        collectionView.addSampleCard(
            icon=":/app/images/controls/TreeView.png",
            title="TreeView",
            content=self.tr(
                "TreeView控件是具有展开和折叠节点的分层列表模式，包含嵌套项。"),
            routeKey="viewInterface",
            index=2
        )
        collectionView.addSampleCard(
            icon=":/app/images/controls/FlipView.png",
            title="FlipView",
            content=self.tr(
                "呈现用户可以逐项翻转的项目集合。"),
            routeKey="viewInterface",
            index=4
        )
        self.vBoxLayout.addWidget(collectionView)
