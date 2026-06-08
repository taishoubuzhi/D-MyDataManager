# coding:utf-8
import os
import sys
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme,
                            PushButton, ExpandGroupSettingCard)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QStandardPaths, QTimer
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QLabel, QFileDialog, QHBoxLayout, QApplication

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

        # personalization (合并：外观 + 材料)
        self.personalGroup = SettingCardGroup(
            self.tr('个性化'), self.scrollWidget)
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
        self.micaCard = SwitchSettingCard(
            FIF.TRANSPARENT,
            self.tr('云母效果'),
            self.tr('窗口和表面显示半透明'),
            config.micaEnabled,
            self.personalGroup
        )
        self.blurRadiusCard = RangeSettingCard(
            config.blurRadius,
            FIF.ALBUM,
            self.tr('亚克力磨砂半径'),
            self.tr('磨砂半径越大，图像越模糊'),
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

        # data
        self.dataGroup = SettingCardGroup(
            self.tr('数据'), self.scrollWidget)
        self.dataStorePathCard = PushSettingCard(
            self.tr('选择文件夹'),
            FIF.FOLDER,
            self.tr('数据存储路径'),
            config.get(config.data_store_path),
            self.dataGroup
        )

        # log - basic
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
        self.outputConsoleCard = SwitchSettingCard(
            FIF.COMMAND_PROMPT,
            self.tr('输出到控制台'),
            self.tr('将日志输出到控制台'),
            config.output_console,
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
        self.rotateCountCard = RangeSettingCard(
            config.rotate_count,
            FIF.LIBRARY,
            self.tr('日志文件数量'),
            self.tr('当日志文件数量超过此限制时，自动删除最旧的日志文件'),
            self.logGroup
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

        # log - advanced
        self.logAdvancedGroup = SettingCardGroup(
            self.tr('日志高级'), self.scrollWidget)
        self.formatToJsonCard = SwitchSettingCard(
            FIF.DOCUMENT,
            self.tr('格式化为JSON'),
            self.tr('将日志格式化为JSON'),
            config.format_to_json,
            self.logAdvancedGroup
        )
        self.catchCard = SwitchSettingCard(
            FIF.CANCEL,
            self.tr('捕获'),
            self.tr('捕获异常'),
            config.catch,
            self.logAdvancedGroup
        )
        self.enqueueCard = SwitchSettingCard(
            FIF.MORE,
            self.tr('入队'),
            self.tr('将日志入队'),
            config.enqueue,
            self.logAdvancedGroup
        )
        self.encodingCard = ComboBoxSettingCard(
            config.encoding,
            FIF.FONT,
            self.tr('编码'),
            self.tr('设置日志编码'),
            texts=ENCODINGS,
            parent=self.logAdvancedGroup
        )
        self.backtraceCard = SwitchSettingCard(
            FIF.HISTORY,
            self.tr('回溯'),
            self.tr('回溯异常'),
            config.backtrace,
            self.logAdvancedGroup
        )
        self.diagnoseCard = SwitchSettingCard(
            FIF.DEVELOPER_TOOLS,
            self.tr('诊断'),
            self.tr('诊断问题'),
            config.diagnose,
            self.logAdvancedGroup
        )

        # developer
        self.developerGroup = SettingCardGroup(
            self.tr('开发者'), self.scrollWidget)
        self.forceInitDbCard = PushSettingCard(
            self.tr('执行'),
            FIF.SYNC,
            self.tr('强制初始化数据库'),
            self.tr('清空所有数据并重新初始化，此操作不可恢复'),
            self.developerGroup
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

        # floating buttons (bottom-right)
        self.restartButton = PushButton(FIF.CLOSE, self.tr('关闭程序'), self)
        self.restartButton.setFixedHeight(36)
        self.restartButton.clicked.connect(self.__restartApp)
        self.restartButton.hide()

        self.resetButton = PushButton(FIF.CANCEL, self.tr('还原配置'), self)
        self.resetButton.setFixedHeight(36)
        self.resetButton.clicked.connect(self.__resetConfig)
        self.resetButton.hide()

        # snapshot of all restart-required config values at startup
        self._restartConfigItems = [
            config.dpi_scale, config.language,
            config.log_level, config.format_to_json, config.catch,
            config.output_console, config.enqueue, config.encoding,
            config.backtrace, config.diagnose,
            config.output_file, config.file_path,
            config.rotate_mode, config.rotate_interval,
            config.rotate_interval_unit, config.rotate_count,
            config.echo, config.pool_pre_ping,
        ]
        self._restartConfigSnapshot = {
            item.key: config.get(item) for item in self._restartConfigItems
        }

        # snapshot of all config values at startup (for reset)
        self._allConfigItems = [
            config.micaEnabled, config.dpi_scale, config.language,
            config.blurRadius,
            config.log_level, config.format_to_json, config.catch,
            config.output_console, config.enqueue, config.encoding,
            config.backtrace, config.diagnose, config.output_file,
            config.file_path, config.rotate_mode, config.rotate_interval,
            config.rotate_interval_unit, config.rotate_count,
            config.url, config.echo, config.pool_size,
            config.max_overflow, config.pool_recycle, config.pool_pre_ping,
            config.connect_args,
            config.data_store_path,
        ]
        self._allConfigSnapshot = {
            item.key: config.get(item) for item in self._allConfigItems
        }

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

        # initialize card enabled state based on current config
        self.__updateLogCardsEnabled()

        # debounce timer for restart tooltip (prevent frequent popups from slider)
        self._restartTooltipTimer = QTimer(self)
        self._restartTooltipTimer.setSingleShot(True)
        self._restartTooltipTimer.setInterval(500)
        self._restartTooltipTimer.timeout.connect(self.__doShowRestartTooltip)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.__updateFloatingButtonsPos()

    def __updateFloatingButtonsPos(self):
        """ Position floating buttons at bottom-right """
        x = self.width() - 20
        if self.restartButton.isVisible():
            x -= self.restartButton.width()
            self.restartButton.move(x, self.height() - self.restartButton.height() - 20)
            x -= 10
        if self.resetButton.isVisible():
            x -= self.resetButton.width()
            self.resetButton.move(x, self.height() - self.resetButton.height() - 20)

    def __initLayout(self):
        self.settingLabel.move(36, 30)

        # add cards to group
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.micaCard)
        self.personalGroup.addSettingCard(self.blurRadiusCard)
        self.personalGroup.addSettingCard(self.zoomCard)
        self.personalGroup.addSettingCard(self.languageCard)

        self.dataGroup.addSettingCard(self.dataStorePathCard)

        self.logGroup.addSettingCard(self.logLevelCard)
        self.logGroup.addSettingCard(self.outputConsoleCard)
        self.logGroup.addSettingCard(self.outputFileCard)
        self.logGroup.addSettingCard(self.filePathCard)
        self.logGroup.addSettingCard(self.rotateModeCard)
        self.logGroup.addSettingCard(self.rotateCountCard)
        self.logGroup.addSettingCard(self.rotateIntervalCard)
        self.logGroup.addSettingCard(self.rotateIntervalUnitCard)

        self.logAdvancedGroup.addSettingCard(self.formatToJsonCard)
        self.logAdvancedGroup.addSettingCard(self.catchCard)
        self.logAdvancedGroup.addSettingCard(self.enqueueCard)
        self.logAdvancedGroup.addSettingCard(self.encodingCard)
        self.logAdvancedGroup.addSettingCard(self.backtraceCard)
        self.logAdvancedGroup.addSettingCard(self.diagnoseCard)

        self.developerGroup.addSettingCard(self.forceInitDbCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.dataGroup)
        self.expandLayout.addWidget(self.logGroup)
        self.expandLayout.addWidget(self.logAdvancedGroup)
        self.expandLayout.addWidget(self.developerGroup)

    def __showRestartTooltip(self):
        """防抖：延迟显示重启提示，避免滑条频繁触发"""
        self._restartTooltipTimer.start()
        self.__updateFloatingButtonsVisibility()

    def __doShowRestartTooltip(self):
        """实际显示重启提示"""
        InfoBar.success(
            self.tr('更新成功'),
            self.tr('配置在重启软件后生效'),
            duration=1500,
            parent=self
        )

    def __updateFloatingButtonsVisibility(self):
        """ Update visibility of restart and reset buttons """
        # restart button: show when any restart-required config changed
        restartChanged = any(
            config.get(item) != self._restartConfigSnapshot[item.key]
            for item in self._restartConfigItems
        )
        self.restartButton.setVisible(restartChanged)

        # reset button: show when any config changed
        anyChanged = any(
            config.get(item) != self._allConfigSnapshot[item.key]
            for item in self._allConfigItems
        )
        self.resetButton.setVisible(anyChanged)

        self.__updateFloatingButtonsPos()

    def __resetConfig(self):
        """ Reset all config values to startup snapshot """
        logger.info("Resetting all config to startup values...")
        for item in self._allConfigItems:
            original_value = self._allConfigSnapshot[item.key]
            if config.get(item) != original_value:
                config.set(item, original_value)

        # update UI cards to reflect restored values
        self.filePathCard.setContent(config.get(config.file_path))
        self.dataStorePathCard.setContent(config.get(config.data_store_path))
        self.__updateLogCardsEnabled()

        InfoBar.success(
            self.tr('还原成功'),
            self.tr('配置已还原为启动时的值，部分配置需重启生效'),
            duration=1500,
            parent=self
        )
        self.__updateFloatingButtonsVisibility()

    def __restartApp(self):
        logger.info("Closing application...")
        QApplication.quit()

    def __onFilePathCardClicked(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("选择文件夹"), "./")
        if not folder or config.get(config.file_path) == folder:
            return
        old_path = config.get(config.file_path)
        config.set(config.file_path, folder)
        self.filePathCard.setContent(folder)
        logger.info(f"Log path changed: {old_path} -> {folder}")

    def __onDataStorePathCardClicked(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("选择数据存储文件夹"), "./")
        if not folder or config.get(config.data_store_path) == folder:
            return
        old_path = config.get(config.data_store_path)
        config.set(config.data_store_path, folder)
        self.dataStorePathCard.setContent(folder)
        logger.info(f"Data store path changed: {old_path} -> {folder}")

    def __onForceInitDbClicked(self):
        """强制初始化数据库"""
        from qfluentwidgets import MessageBox
        msg = MessageBox(
            self.tr('确认强制初始化'),
            self.tr('此操作将清空所有数据并重新初始化数据库，不可恢复！\n确定要继续吗？'),
            self
        )
        msg.yesButton.setText(self.tr('确定'))
        msg.cancelButton.setText(self.tr('取消'))
        if msg.exec():
            from ..common.init.init_db import init_db
            init_db(force=True)
            logger.info("Database force initialization completed by user")
            InfoBar.success(
                self.tr('初始化完成'),
                self.tr('数据库已强制重新初始化'),
                duration=2000,
                parent=self
            )

    def __updateLogCardsEnabled(self):
        """根据 output_file 和 rotate_mode 的状态，动态启用/禁用相关设置卡片"""
        file_enabled = config.get(config.output_file)
        rotate_mode = config.get(config.rotate_mode)

        # 输出到文件关闭时，文件相关设置全部禁用
        self.filePathCard.setEnabled(file_enabled)
        self.rotateModeCard.setEnabled(file_enabled)
        self.formatToJsonCard.setEnabled(file_enabled)
        self.encodingCard.setEnabled(file_enabled)

        # 轮转相关设置：取决于 output_file 和 rotate_mode
        if not file_enabled:
            self.rotateCountCard.setEnabled(False)
            self.rotateIntervalCard.setEnabled(False)
            self.rotateIntervalUnitCard.setEnabled(False)
        else:
            match rotate_mode:
                case "None":
                    self.rotateCountCard.setEnabled(False)
                    self.rotateIntervalCard.setEnabled(False)
                    self.rotateIntervalUnitCard.setEnabled(False)
                case "Time":
                    self.rotateCountCard.setEnabled(False)
                    self.rotateIntervalCard.setEnabled(True)
                    self.rotateIntervalUnitCard.setEnabled(True)
                case "Count":
                    self.rotateCountCard.setEnabled(True)
                    self.rotateIntervalCard.setEnabled(False)
                    self.rotateIntervalUnitCard.setEnabled(False)

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        config.appRestartSig.connect(self.__showRestartTooltip)

        # personalization
        config.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: (setThemeColor(c), logger.info(f"Theme color changed: {c.name()}")))
        self.micaCard.checkedChanged.connect(lambda e: (signalBus.micaEnableChanged.emit(e), logger.info(f"Mica effect changed: {'enabled' if e else 'disabled'}")))
        self.blurRadiusCard.slider.valueChanged.connect(lambda v: logger.info(f"Acrylic blur radius changed: {v}"))
        self.zoomCard.comboBox.currentIndexChanged.connect(lambda i: logger.info(f"Interface zoom changed: {self.zoomCard.comboBox.currentText()}"))
        self.languageCard.comboBox.currentIndexChanged.connect(lambda i: logger.info(f"Language changed: {self.languageCard.comboBox.currentText()}"))

        # data
        self.dataStorePathCard.clicked.connect(self.__onDataStorePathCardClicked)

        # log - all log settings require restart
        self.logLevelCard.comboBox.currentIndexChanged.connect(lambda i: logger.info(f"Log level changed: {self.logLevelCard.comboBox.currentText()}"))
        self.outputConsoleCard.checkedChanged.connect(lambda e: logger.info(f"Output to console changed: {'enabled' if e else 'disabled'}"))
        self.outputFileCard.checkedChanged.connect(lambda e: (logger.info(f"Output to file changed: {'enabled' if e else 'disabled'}"), self.__updateLogCardsEnabled()))
        self.filePathCard.clicked.connect(self.__onFilePathCardClicked)
        self.rotateModeCard.comboBox.currentIndexChanged.connect(lambda i: (logger.info(f"Rotate mode changed: {self.rotateModeCard.comboBox.currentText()}"), self.__updateLogCardsEnabled()))
        self.rotateCountCard.slider.valueChanged.connect(lambda v: logger.info(f"Rotate count changed: {v}"))
        self.rotateIntervalCard.slider.valueChanged.connect(lambda v: logger.info(f"Rotate interval changed: {v}"))
        self.rotateIntervalUnitCard.comboBox.currentIndexChanged.connect(lambda i: logger.info(f"Rotate interval unit changed: {self.rotateIntervalUnitCard.comboBox.currentText()}"))

        # log advanced
        self.formatToJsonCard.checkedChanged.connect(lambda e: logger.info(f"Format to JSON changed: {'enabled' if e else 'disabled'}"))
        self.catchCard.checkedChanged.connect(lambda e: logger.info(f"Catch changed: {'enabled' if e else 'disabled'}"))
        self.enqueueCard.checkedChanged.connect(lambda e: logger.info(f"Enqueue changed: {'enabled' if e else 'disabled'}"))
        self.encodingCard.comboBox.currentIndexChanged.connect(lambda i: logger.info(f"Encoding changed: {self.encodingCard.comboBox.currentText()}"))
        self.backtraceCard.checkedChanged.connect(lambda e: logger.info(f"Backtrace changed: {'enabled' if e else 'disabled'}"))
        self.diagnoseCard.checkedChanged.connect(lambda e: logger.info(f"Diagnose changed: {'enabled' if e else 'disabled'}"))

        # developer
        self.forceInitDbCard.clicked.connect(self.__onForceInitDbClicked)
