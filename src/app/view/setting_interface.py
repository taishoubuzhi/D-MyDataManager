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

from ..common.config import config,LOG_LEVELS,ENCODINGS,ROTATE_MODES,ROTATE_SIZE_UNITS,ROTATE_INTERVAL_UNITS,RETENTION_UNITS,COMPRESS_MODES
from ..common.util import is_win11
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet
from ..components import TimePickerDialog

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
        self.differentLevelFileCard = SwitchSettingCard(
            FIF.FILTER,
            self.tr('Different level files'),
            self.tr('Output log to different level files'),
            config.different_level_file,
            self.logGroup
        )
        self.filePathCard = PushSettingCard(
            self.tr('File path'),
            FIF.FOLDER,
            self.tr('Set the path of log file'),
            config.get(config.file_path),
            self.logGroup
        )
        self.fileOverrideCard = SwitchSettingCard(
            FIF.SYNC,
            self.tr('File override'),
            self.tr('Override log file'),
            config.file_override,
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
        self.rotateSizeCard = RangeSettingCard(
            config.rotate_size,
            FIF.ZOOM,
            self.tr('Rotate size'),
            self.tr('Set the rotate size of log file'),
            self.logGroup
        )

        self.rotateSizeUnitCard = ComboBoxSettingCard(
            config.rotate_size_unit,
            FIF.UNIT,
            self.tr('Rotate size unit'),
            self.tr('Set the rotate size unit of log file'),
            texts=ROTATE_SIZE_UNITS,
            parent=self.logGroup
        )
        self.rotateTimeCard = PushSettingCard(
            self.tr('Rotate time'),
            FIF.CALENDAR,
            self.tr('Set the rotate time of log file'),
            config.get(config.rotate_time).strftime("%H:%M"),
            self.logGroup
        )

        self.rotateIntervalCard = RangeSettingCard(
            config.rotate_interval,
            FIF.DATE_TIME,
            self.tr('Rotate interval'),
            self.tr('Set the rotate interval of log file'),
            self.logGroup
        )
        self.rotateIntervalUnitCard = ComboBoxSettingCard(
            config.rotate_interval_unit,
            FIF.UNIT,
            self.tr('Rotate interval unit'),
            self.tr('Set the rotate interval unit of log file'),
            texts=ROTATE_INTERVAL_UNITS,
            parent=self.logGroup
        )

        self.retentionCard = RangeSettingCard(
            config.retention,
            FIF.LIBRARY,
            self.tr('Retention'),
            self.tr('Set the retention of log file'),
            self.logGroup
        )

        self.retentionUnitCard = ComboBoxSettingCard(
            config.retention_unit,
            FIF.UNIT,
            self.tr('Retention unit'),
            self.tr('Set the retention unit of log file'),
                texts=RETENTION_UNITS,
            parent=self.logGroup
        )

        self.compressModeCard = ComboBoxSettingCard(
            config.compress_mode,
            FIF.ZIP_FOLDER,
            self.tr('Compress mode'),
            self.tr('Set the compress mode of log file'),
            texts=COMPRESS_MODES,
            parent=self.logGroup
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
        self.logGroup.addSettingCard(self.differentLevelFileCard)
        self.logGroup.addSettingCard(self.filePathCard)
        self.logGroup.addSettingCard(self.fileOverrideCard)
        self.logGroup.addSettingCard(self.rotateModeCard)
        self.logGroup.addSettingCard(self.rotateSizeCard)
        self.logGroup.addSettingCard(self.rotateSizeUnitCard)
        self.logGroup.addSettingCard(self.rotateTimeCard)
        self.logGroup.addSettingCard(self.rotateIntervalCard)
        self.logGroup.addSettingCard(self.rotateIntervalUnitCard)
        self.logGroup.addSettingCard(self.retentionCard)
        self.logGroup.addSettingCard(self.retentionUnitCard)
        self.logGroup.addSettingCard(self.compressModeCard)

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
        config.set(config.downloadFolder, folder)
        self.filePathCard.setContent(folder)

    def __onRotateTimeCardClicked(self):
        current_time = config.get(config.rotate_time)
        dialog = TimePickerDialog(
            current_time=current_time,
            title=self.tr("Select Rotate Time"),
            time_format="HH:mm",
            parent=self
        )
        
        def on_time_selected(new_time):
            config.set(config.rotate_time, new_time)
            self.rotateTimeCard.setContent(new_time.strftime("%H:%M"))
        
        dialog.timeSelected.connect(on_time_selected)
        dialog.exec()

    def __updateConfigCardVisibility(self):
        """ Update the visibility of configuration cards based on settings """
        dynamic_display = config.get(config.dynamic_config_display)
        output_file = config.get(config.output_file)
        rotate_mode = config.get(config.rotate_mode)

        # All cards
        all_cards = [
            self.differentLevelFileCard,
            self.filePathCard,
            self.fileOverrideCard,
            self.rotateModeCard,
            self.rotateSizeCard,
            self.rotateSizeUnitCard,
            self.rotateTimeCard,
            self.rotateIntervalCard,
            self.rotateIntervalUnitCard,
            self.retentionCard,
            self.retentionUnitCard,
            self.compressModeCard
        ]

        # Show all cards when dynamic_config_display is False
        if not dynamic_display:
            for card in all_cards:
                card.setVisible(True)
            return

        # When output_file is False, hide all cards after differentLevelFileCard
        if not output_file:
            self.differentLevelFileCard.setVisible(True)
            for card in all_cards[1:]:
                card.setVisible(False)
            return

        # When output_file is True, show all first few cards
        self.differentLevelFileCard.setVisible(True)
        self.filePathCard.setVisible(True)
        self.fileOverrideCard.setVisible(True)
        self.rotateModeCard.setVisible(True)

        # Rotate related cards
        self.retentionCard.setVisible(True)
        self.retentionUnitCard.setVisible(True)
        self.compressModeCard.setVisible(True)

        # Hide all rotate-specific cards first
        self.rotateSizeCard.setVisible(False)
        self.rotateSizeUnitCard.setVisible(False)
        self.rotateTimeCard.setVisible(False)
        self.rotateIntervalCard.setVisible(False)
        self.rotateIntervalUnitCard.setVisible(False)

        # Show relevant cards based on rotate_mode
        if rotate_mode == "Size":
            self.rotateSizeCard.setVisible(True)
            self.rotateSizeUnitCard.setVisible(True)
        elif rotate_mode == "Time":
            self.rotateTimeCard.setVisible(True)
        elif rotate_mode == "Interval":
            self.rotateIntervalCard.setVisible(True)
            self.rotateIntervalUnitCard.setVisible(True)

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        config.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        config.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # log
        self.filePathCard.clicked.connect(
            self.__onFilePathCardClicked)
        self.rotateTimeCard.clicked.connect(
            self.__onRotateTimeCardClicked)
        # Connect signals for dynamic config card visibility
        self.dynamicConfigDisplayCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        self.outputFileCard.checkedChanged.connect(self.__updateConfigCardVisibility)
        # Connect to internal comboBox's currentIndexChanged signal
        self.rotateModeCard.comboBox.currentIndexChanged.connect(self.__updateConfigCardVisibility)
