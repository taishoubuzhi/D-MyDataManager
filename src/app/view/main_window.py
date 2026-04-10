from PyQt6.QtCore import Qt, pyqtSignal, QEasingCurve, QUrl, QSize, QTimer
from PyQt6.QtGui import QIcon, QDesktopServices, QColor
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QFrame, QWidget

from qfluentwidgets import (NavigationAvatarWidget, NavigationItemPosition, MessageBox, FluentWindow,
                            SplashScreen, SystemThemeListener, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF

from ..common.config import (app_config)
from .home_interface import HomeInterface
from .setting_interface import SettingInterface

from ..common.translator import Translator
from ..common.icon import Icon
from ..common.signal_bus import signalBus
from src.app.resource import resource


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.splashScreen = None
        self.initWindow()

        self.themeListener = SystemThemeListener(self)

        self.homeInterface = HomeInterface(self)
        self.settingInterface = SettingInterface(self)

        self.navigationInterface.setAcrylicEnabled(True)

        self.connectSignalToSlot()

        self.initNavigation()
        self.splashScreen.finish()

        self.themeListener.start()

    def connectSignalToSlot(self):
        signalBus.micaEnableChanged.connect(self.setMicaEffectEnabled)
        signalBus.switchToSampleCard.connect(self.switchToSample)
        signalBus.supportSignal.connect(self.onSupport)

    def initNavigation(self):
        t = Translator()
        self.addSubInterface(self.homeInterface, FIF.HOME, self.tr('Home'))
        self.navigationInterface.addSeparator()

        self.addSubInterface(
            self.settingInterface, FIF.SETTING, self.tr('Settings'), NavigationItemPosition.BOTTOM)


    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowIcon(QIcon(':/d/images/logo.png'))
        self.setWindowTitle("数据管理器")

        self.setMicaEffectEnabled(app_config.get(app_config.micaEnabled))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.screens()[0].availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        self.show()
        QApplication.processEvents()

    def onSupport(self):
        language = app_config.get(app_config.language).value

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())

    def closeEvent(self, e):
        self.themeListener.terminate()
        self.themeListener.deleteLater()
        super().closeEvent(e)

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

        # retry
        if self.isMicaEffectEnabled():
            QTimer.singleShot(100, lambda: self.windowEffect.setMicaEffect(self.winId(), isDarkTheme()))

    def switchToSample(self, routeKey, index):
        return

