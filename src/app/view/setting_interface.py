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
        self.settingLabel = QLabel(self.tr("Settings"), self)


        # personalization
        self.personalGroup = SettingCardGroup(
            self.tr('Personalization'), self.scrollWidget)
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT,
            self.tr('Mica effect'),
            self.tr('Apply semi transparent to windows and surfaces'),
            config.micaEnabled,
            self.personalGroup
        )
        self.themeCard = ComboBoxSettingCard(
            config.themeMode,
            FIF.BRUSH,
            self.tr('Application theme'),
            self.tr("Change the appearance of your application"),
            texts=[
                self.tr('Light'), self.tr('Dark'),
                self.tr('Use system setting')
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            config.themeColor,
            FIF.PALETTE,
            self.tr('Theme color'),
            self.tr('Change the theme color of you application'),
            self.personalGroup
        )
        self.dynamicConfigDisplayCard = SwitchSettingCard(
            FIF.SYNC,
            self.tr('Dynamic config display'),
            self.tr('Dynamically display configuration changes'),
            config.dynamic_config_display,
            self.personalGroup
        )
        self.zoomCard = ComboBoxSettingCard(
            config.dpi_scale,
            FIF.ZOOM,
            self.tr("Interface zoom"),
            self.tr("Change the size of widgets and fonts"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                self.tr("Use system setting")
            ],
            parent=self.personalGroup
        )
        self.languageCard = ComboBoxSettingCard(
            config.language,
            FIF.LANGUAGE,
            self.tr('Language'),
            self.tr('Set your preferred language for UI'),
            texts=['简体中文', 'English', self.tr('Use system setting')],
            parent=self.personalGroup
        )

        # material
        self.materialGroup = SettingCardGroup(
            self.tr('Material'), self.scrollWidget)
        self.blurRadiusCard = RangeSettingCard(
            config.blurRadius,
            FIF.ALBUM,
            self.tr('Acrylic blur radius'),
            self.tr('The greater the radius, the more blurred the image'),
            self.materialGroup
        )

        # update software
        self.updateSoftwareGroup = SettingCardGroup(
            self.tr("Software update"), self.scrollWidget)
        self.updateOnStartUpCard = SwitchSettingCard(
            FIF.UPDATE,
            self.tr('Check for updates when the application starts'),
            self.tr('The new version will be more stable and have more features'),
            configItem=config.check_update_at_start_up,
            parent=self.updateSoftwareGroup
        )

        # log
        self.logGroup = SettingCardGroup(
            self.tr('Log'), self.scrollWidget)
        self.logLevelCard = ComboBoxSettingCard(
            config.log_level,
            FIF.INFO,
            self.tr('Log level'),
            self.tr('Set the log level'),
            texts=LOG_LEVELS,
            parent=self.logGroup
        )
        self.formatToJsonCard = SwitchSettingCard(
            FIF.DOCUMENT,
            self.tr('Format to JSON'),
            self.tr('Format log to JSON'),
            config.format_to_json,
            self.logGroup
        )
        self.catchCard = SwitchSettingCard(
            FIF.CANCEL,
            self.tr('Catch'),
            self.tr('Catch exceptions'),
            config.catch,
            self.logGroup
        )
        self.outputConsoleCard = SwitchSettingCard(
            FIF.COMMAND_PROMPT,
            self.tr('Output to console'),
            self.tr('Output log to console'),
            config.output_console,
            self.logGroup
        )
        self.enqueueCard = SwitchSettingCard(
            FIF.MORE,
            self.tr('Enqueue'),
            self.tr('Enqueue log'),
            config.enqueue,
            self.logGroup
        )
        self.encodingCard = ComboBoxSettingCard(
            config.encoding,
            FIF.FONT,
            self.tr('Encoding'),
            self.tr('Set the encoding of log'),
            texts=ENCODINGS,
            parent=self.logGroup
        )
        self.backtraceCard = SwitchSettingCard(
            FIF.HISTORY,
            self.tr('Backtrace'),
            self.tr('Backtrace exceptions'),
            config.backtrace,
            self.logGroup
        )
        self.diagnoseCard = SwitchSettingCard(
            FIF.DEVELOPER_TOOLS,
            self.tr('Diagnose'),
            self.tr('Diagnose problems'),
            config.diagnose,
            self.logGroup
        )

        self.outputFileCard = SwitchSettingCard(
            FIF.SAVE_AS,
            self.tr('Output to file'),
            self.tr('Output log to file'),
            config.output_file,
            self.logGroup
        )
        self.filePathCard = PushSettingCard(
            self.tr('File path'),
            FIF.FOLDER,
            self.tr('Set the path of log file'),
            config.get(config.file_path),
            self.logGroup
        )
        self.rotateModeCard = ComboBoxSettingCard(
            config.rotate_mode,
            FIF.ROTATE,
            self.tr('Rotate mode'),
            self.tr('Set the rotate mode of log file'),
            texts=ROTATE_MODES,
            parent=self.logGroup
        )
        self.rotateIntervalCard = RangeSettingCard(
            config.rotate_interval,
            FIF.DATE_TIME,
            self.tr('Rotate interval'),
            self.tr('Create a new log file at the specified time interval'),
            self.logGroup
        )
        self.rotateIntervalUnitCard = ComboBoxSettingCard(
            config.rotate_interval_unit,
            FIF.UNIT,
            self.tr('Rotate interval unit'),
            self.tr('Set the time unit for rotation interval'),
            texts=ROTATE_INTERVAL_UNITS,
            parent=self.logGroup
        )
        self.rotateCountCard = RangeSettingCard(
            config.rotate_count,
            FIF.LIBRARY,
            self.tr('Log file count'),
            self.tr('Automatically delete oldest log files when count exceeds this limit'),
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
            self.tr('Updated successfully'),
            self.tr('Configuration takes effect after restart'),
            duration=1500,
            parent=self
        )

    def __onFilePathCardClicked(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose folder"), "./")
        if not folder or config.get(config.file_path) == folder:
            return
        old_path = config.get(config.file_path)
        config.set(config.file_path, folder)
        self.filePathCard.setContent(folder)
        logger.info(f"日志路径变更: {old_path} -> {folder}")

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
        self.themeColorCard.colorChanged.connect(lambda c: (setThemeColor(c), logger.info(f"主题颜色变更: {c.name()}")))
        self.micaCard.checkedChanged.connect(lambda e: (signalBus.micaEnableChanged.emit(e), logger.info(f"Mica效果变更: {'开启' if e else '关闭'}")))

        # log
        self.filePathCard.clicked.connect(
            self.__onFilePathCardClicked)
        # Connect signals for dynamic config card visibility
        self.dynamicConfigDisplayCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        self.outputFileCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        # Connect to internal comboBox's currentIndexChanged signal
        self.rotateModeCard.comboBox.currentIndexChanged.connect(self.__updateConfigCardVisibility)
