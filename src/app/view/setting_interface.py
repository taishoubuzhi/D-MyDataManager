# coding:utf-8
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QStandardPaths, QTime
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QLabel, QFileDialog, QTimeEdit, QDialog, QVBoxLayout, QHBoxLayout, QPushButton
import datetime

from ..common.config import app_config,pre_log_config,log_config, AUTHOR, VERSION, YEAR
from ..common.util import is_win11
from ..common.signal_bus import signalBus
from ..common.style_sheet import StyleSheet

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
            app_config.micaEnabled,
            self.personalGroup
        )
        self.themeCard = ComboBoxSettingCard(
            app_config.themeMode,
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
            app_config.themeColor,
            FIF.PALETTE,
            self.tr('Theme color'),
            self.tr('Change the theme color of you application'),
            self.personalGroup
        )
        self.zoomCard = ComboBoxSettingCard(
            app_config.dpi_scale,
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
            app_config.language,
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
            app_config.blurRadius,
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
            configItem=app_config.check_update_at_start_up,
            parent=self.updateSoftwareGroup
        )

        # log
        self.logGroup = SettingCardGroup(
            self.tr('Log'), self.scrollWidget)
        self.logLevelCard = ComboBoxSettingCard(
            log_config.log_level,
            FIF.INFO,
            self.tr('Log level'),
            self.tr('Set the log level'),
            texts=log_config.get(pre_log_config.log_levels),
            parent=self.logGroup
        )
        self.formatToJsonCard = SwitchSettingCard(
            FIF.DOCUMENT,
            self.tr('Format to JSON'),
            self.tr('Format log to JSON'),
            log_config.format_to_json,
            self.logGroup
        )
        self.catchCard = SwitchSettingCard(
            FIF.CANCEL,
            self.tr('Catch'),
            self.tr('Catch exceptions'),
            log_config.catch,
            self.logGroup
        )
        self.outputConsoleCard = SwitchSettingCard(
            FIF.COMMAND_PROMPT,
            self.tr('Output to console'),
            self.tr('Output log to console'),
            log_config.output_console,
            self.logGroup
        )
        self.enqueueCard = SwitchSettingCard(
            FIF.MORE,
            self.tr('Enqueue'),
            self.tr('Enqueue log'),
            log_config.enqueue,
            self.logGroup
        )
        self.encodingCard = ComboBoxSettingCard(
            log_config.encoding,
            FIF.FONT,
            self.tr('Encoding'),
            self.tr('Set the encoding of log'),
            texts=log_config.get(pre_log_config.encodings),
            parent=self.logGroup
        )
        self.backtraceCard = SwitchSettingCard(
            FIF.HISTORY,
            self.tr('Backtrace'),
            self.tr('Backtrace exceptions'),
            log_config.backtrace,
            self.logGroup
        )
        self.diagnoseCard = SwitchSettingCard(
            FIF.DEVELOPER_TOOLS,
            self.tr('Diagnose'),
            self.tr('Diagnose problems'),
            log_config.diagnose,
            self.logGroup
        )

        self.outputFileCard = SwitchSettingCard(
            FIF.SAVE_AS,
            self.tr('Output to file'),
            self.tr('Output log to file'),
            log_config.output_file,
            self.logGroup
        )
        self.differentLevelFileCard = SwitchSettingCard(
            FIF.FILTER,
            self.tr('Different level files'),
            self.tr('Output log to different level files'),
            log_config.different_level_file,
            self.logGroup
        )
        self.filePathCard = PushSettingCard(
            self.tr('File path'),
            FIF.FOLDER,
            self.tr('Set the path of log file'),
            log_config.get(log_config.file_path),
            self.logGroup
        )
        self.fileOverrideCard = SwitchSettingCard(
            FIF.SYNC,
            self.tr('File override'),
            self.tr('Override log file'),
            log_config.file_override,
            self.logGroup
        )
        self.rotateModeCard = ComboBoxSettingCard(
            log_config.rotate_mode,
            FIF.ROTATE,
            self.tr('Rotate mode'),
            self.tr('Set the rotate mode of log file'),
            texts=log_config.get(pre_log_config.rotate_modes),
            parent=self.logGroup
        )
        self.rotateSizeCard = RangeSettingCard(
            log_config.rotate_size,
            FIF.ZOOM,
            self.tr('Rotate size'),
            self.tr('Set the rotate size of log file'),
            self.logGroup
        )
        self.rotateTimeCard = PushSettingCard(
            self.tr('Rotate time'),
            FIF.CALENDAR,
            self.tr('Set the rotate time of log file'),
            str(log_config.get(log_config.rotate_time)),
            self.logGroup
        )

        self.rotateIntervalCard = RangeSettingCard(
            log_config.rotate_interval,
            FIF.DATE_TIME,
            self.tr('Rotate interval'),
            self.tr('Set the rotate interval of log file'),
            self.logGroup
        )
        self.rotateIntervalUnitCard = ComboBoxSettingCard(
            log_config.rotate_interval_unit,
            FIF.UNIT,
            self.tr('Rotate interval unit'),
            self.tr('Set the rotate interval unit of log file'),
            texts=log_config.get(pre_log_config.rotate_interval_units),
            parent=self.logGroup
        )

        self.retentionCard = RangeSettingCard(
            log_config.retention,
            FIF.LIBRARY,
            self.tr('Retention'),
            self.tr('Set the retention of log file'),
            self.logGroup
        )

        self.retentionUnitCard = ComboBoxSettingCard(
            log_config.retention_unit,
            FIF.UNIT,
            self.tr('Retention unit'),
            self.tr('Set the retention unit of log file'),
                texts=log_config.get(pre_log_config.retention_units),
            parent=self.logGroup
        )

        self.compressModeCard = ComboBoxSettingCard(
            log_config.compress_mode,
            FIF.ZIP_FOLDER,
            self.tr('Compress mode'),
            self.tr('Set the compress mode of log file'),
            texts=log_config.get(pre_log_config.compress_modes),
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

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group

        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
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
        if not folder or log_config.get(log_config.file_path) == folder:
            return
        log_config.set(log_config.downloadFolder, folder)
        self.filePathCard.setContent(folder)

    def __onRotateTimeCard(self):
        return

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        app_config.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        app_config.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
        self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # log
        self.filePathCard.clicked.connect(
            self.__onFilePathCardClicked)
        self.rotateTimeCard.clicked.connect(
            self.__onRotateTimeCard)
