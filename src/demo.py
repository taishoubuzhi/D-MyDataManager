import os
import sys

from PyQt6.QtCore import Qt, QTranslator
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

from app.common.config import config
from app.common.init import init_log, init_db

from app.view.main_window import MainWindow

from loguru import logger

init_log()
logger.info("应用启动中...")
init_db()

if config.get(config.dpi_scale) != "Auto":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = str(config.get(config.dpi_scale))

app = QApplication(sys.argv)
app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

locale = config.get(config.language).value
translator = FluentTranslator(locale)
appTranslator = QTranslator()
appTranslator.load(locale, "app", ".", ":/app/i18n")

app.installTranslator(translator)
app.installTranslator(appTranslator)

w = MainWindow()
w.show()

app.exec()
