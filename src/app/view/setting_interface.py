# coding:utf-8
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QStandardPaths
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QLabel, QFileDialog

from ..common.config import config,LOG_LEVELS,ENCODINGS,ROTATE_MODES,ROTATE_INTERVAL_UNITS
from ..common.util import is_win11
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet
from loguru import logger

class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.settingLabel = QLabel(self.tr("设置"), self)


        # personalization
        self.personalGroup = SettingCardGroup(
            self.tr('个性化'), self.scrollWidget)
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT,
            self.tr('云母效果'),
            self.tr('窗口和表面显示半透明'),
            config.micaEnabled,
            self.personalGroup
        )
        self.themeCard = ComboBoxSettingCard(
            config.themeMode,
            FIF.BRUSH,
            self.tr('应用主题'),
            self.tr("调整应用的外观"),
            texts=[
                self.tr('浅色'), self.tr('深色'),
                self.tr('跟随系统设置')
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            config.themeColor,
            FIF.PALETTE,
            self.tr('主题色'),
            self.tr('调整应用的主题色'),
            self.personalGroup
        )
        self.dynamicConfigDisplayCard = SwitchSettingCard(
            FIF.SYNC,
            self.tr('动态配置显示'),
            self.tr('动态显示配置变更'),
            config.dynamic_config_display,
            self.personalGroup
        )
        self.zoomCard = ComboBoxSettingCard(
            config.dpi_scale,
            FIF.ZOOM,
            self.tr("界面缩放"),
            self.tr("调整小部件和字体的大小"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("跟随系统设置")
            ],
            parent=self.personalGroup
        )
        self.languageCard = ComboBoxSettingCard(
            config.language,
            FIF.LANGUAGE,
            self.tr('语言'),
            self.tr('选择界面所使用的语言'),
            texts=['简体中文', 'English', self.tr('跟随系统设置')],
            parent=self.personalGroup
        )

        # material
        self.materialGroup = SettingCardGroup(
            self.tr('材料'), self.scrollWidget)
        self.blurRadiusCard = RangeSettingCard(
            config.blurRadius,
            FIF.ALBUM,
            self.tr('亚克力磨砂半径'),
            self.tr('磨砂半径越大，图像越模糊'),
            self.materialGroup
        )

        # update software
        self.updateSoftwareGroup = SettingCardGroup(
            self.tr("软件更新"), self.scrollWidget)
        self.updateOnStartUpCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('应用启动时检查更新'),
            self.tr('新版本将更加稳定并拥有更多功能'),
            configItem=config.check_update_at_start_up,
            parent=self.updateSoftwareGroup
        )

        # log
        self.logGroup = SettingCardGroup(
            self.tr('日志'), self.scrollWidget)
        self.logLevelCard = ComboBoxSettingCard(
            config.log_level,
            FIF.INFO,
            self.tr('日志级别'),
            self.tr('设置日志级别'),
            texts=LOG_LEVELS,
            parent=self.logGroup
        )
        self.formatToJsonCard = SwitchSettingCard(
            FIF.DOCUMENT,
            self.tr('格式化为JSON'),
            self.tr('将日志格式化为JSON'),
            config.format_to_json,
            self.logGroup
        )
        self.catchCard = SwitchSettingCard(
            FIF.CANCEL,
            self.tr('捕获'),
            self.tr('捕获异常'),
            config.catch,
            self.logGroup
        )
        self.outputConsoleCard = SwitchSettingCard(
            FIF.COMMAND_PROMPT,
            self.tr('输出到控制台'),
            self.tr('将日志输出到控制台'),
            config.output_console,
            self.logGroup
        )
        self.enqueueCard = SwitchSettingCard(
            FIF.MORE,
            self.tr('入队'),
            self.tr('将日志入队'),
            config.enqueue,
            self.logGroup
        )
        self.encodingCard = ComboBoxSettingCard(
            config.encoding,
            FIF.FONT,
            self.tr('编码'),
            self.tr('设置日志编码'),
            texts=ENCODINGS,
            parent=self.logGroup
        )
        self.backtraceCard = SwitchSettingCard(
            FIF.HISTORY,
            self.tr('回溯'),
            self.tr('回溯异常'),
            config.backtrace,
            self.logGroup
        )
        self.diagnoseCard = SwitchSettingCard(
            FIF.DEVELOPER_TOOLS,
            self.tr('诊断'),
            self.tr('诊断问题'),
            config.diagnose,
            self.logGroup
        )

        self.outputFileCard = SwitchSettingCard(
            FIF.SAVE_AS,
            self.tr('输出到文件'),
            self.tr('将日志输出到文件'),
            config.output_file,
            self.logGroup
        )
        self.filePathCard = PushSettingCard(
            self.tr('文件路径'),
            FIF.FOLDER,
            self.tr('设置日志文件路径'),
            config.get(config.file_path),
            self.logGroup
        )
        self.rotateModeCard = ComboBoxSettingCard(
            config.rotate_mode,
            FIF.ROTATE,
            self.tr('轮转模式'),
            self.tr('设置日志文件轮转模式'),
            texts=ROTATE_MODES,
            parent=self.logGroup
        )
        self.rotateIntervalCard = RangeSettingCard(
            config.rotate_interval,
            FIF.DATE_TIME,
            self.tr('轮转间隔'),
            self.tr('按指定时间间隔创建新的日志文件'),
            self.logGroup
        )
        self.rotateIntervalUnitCard = ComboBoxSettingCard(
            config.rotate_interval_unit,
            FIF.UNIT,
            self.tr('轮转间隔单位'),
            self.tr('设置轮转间隔的时间单位'),
            texts=ROTATE_INTERVAL_UNITS,
            parent=self.logGroup
        )
        self.rotateCountCard = RangeSettingCard(
            config.rotate_count,
            FIF.LIBRARY,
            self.tr('日志文件数量'),
            self.tr('当日志文件数量超过此限制时，自动删除最旧的日志文件'),
            self.logGroup
        )

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName('settingInterface')

        # initialize style sheet
        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)

        self.micaCard.setEnabled(is_win11())

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()
        
        # Update card visibility initially
        self.__updateConfigCardVisibility()

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group

        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.dynamicConfigDisplayCard)
        self.personalGroup.addSettingCard(self.zoomCard)
        self.personalGroup.addSettingCard(self.languageCard)

        self.materialGroup.addSettingCard(self.blurRadiusCard)

        self.updateSoftwareGroup.addSettingCard(self.updateOnStartUpCard)

        self.logGroup.addSettingCard(self.logLevelCard)
        self.logGroup.addSettingCard(self.formatToJsonCard)
        self.logGroup.addSettingCard(self.catchCard)
        self.logGroup.addSettingCard(self.outputConsoleCard)
        self.logGroup.addSettingCard(self.enqueueCard)
        self.logGroup.addSettingCard(self.encodingCard)
        self.logGroup.addSettingCard(self.backtraceCard)
        self.logGroup.addSettingCard(self.diagnoseCard)
        self.logGroup.addSettingCard(self.outputFileCard)
        self.logGroup.addSettingCard(self.filePathCard)
        self.logGroup.addSettingCard(self.rotateModeCard)
        self.logGroup.addSettingCard(self.rotateIntervalCard)
        self.logGroup.addSettingCard(self.rotateIntervalUnitCard)
        self.logGroup.addSettingCard(self.rotateCountCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.materialGroup)
        self.expandLayout.addWidget(self.updateSoftwareGroup)
        self.expandLayout.addWidget(self.logGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('更新成功'),
            self.tr('配置在重启软件后生效'),
            duration=1500,
            parent=self
        )

    def __onFilePathCardClicked(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("选择文件夹"), "./")
        if not folder or config.get(config.file_path) == folder:
            return
        old_path = config.get(config.file_path)
        config.set(config.file_path, folder)
        self.filePathCard.setContent(folder)
        logger.info(f"Log path changed: {old_path} -> {folder}")

    def __updateConfigCardVisibility(self):
        """ Update the visibility of configuration cards based on settings """
        dynamic_display = config.get(config.dynamic_config_display)
        output_file = config.get(config.output_file)
        rotate_mode = config.get(config.rotate_mode)

        # All cards
        all_cards = [
            self.filePathCard,
            self.rotateModeCard,
            self.rotateIntervalCard,
            self.rotateIntervalUnitCard,
            self.rotateCountCard
        ]

        # Show all cards when dynamic_config_display is False
        if not dynamic_display:
            for card in all_cards:
                card.setVisible(True)
            return

        # When output_file is False, hide all file-related cards
        if not output_file:
            for card in all_cards:
                card.setVisible(False)
            return

        # When output_file is True, show common cards
        self.filePathCard.setVisible(True)
        self.rotateModeCard.setVisible(True)

        # Hide mode-specific cards first
        self.rotateIntervalCard.setVisible(False)
        self.rotateIntervalUnitCard.setVisible(False)
        self.rotateCountCard.setVisible(False)

        # Show relevant cards based on rotate_mode
        if rotate_mode == "Time":
            self.rotateIntervalCard.setVisible(True)
            self.rotateIntervalUnitCard.setVisible(True)
        elif rotate_mode == "Count":
            self.rotateCountCard.setVisible(True)

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        config.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        config.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: (setThemeColor(c), logger.info(f"Theme color changed: {c.name()}")))
        self.micaCard.checkedChanged.connect(lambda e: (signalBus.micaEnableChanged.emit(e), logger.info(f"Mica effect changed: {'enabled' if e else 'disabled'}")))

        # log
        self.filePathCard.clicked.connect(
            self.__onFilePathCardClicked)
        # Connect signals for dynamic config card visibility
        self.dynamicConfigDisplayCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        self.outputFileCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        # Connect to internal comboBox's currentIndexChanged signal
        self.rotateModeCard.comboBox.currentIndexChanged.connect(self.__updateConfigCardVisibility)
