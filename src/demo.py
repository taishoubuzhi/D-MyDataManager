import os
import sys

from PyQt6.QtCore import Qt, QTranslator
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

from app.common.config import app_config

from app.view.main_window import MainWindow

if app_config.get(app_config.dpi_scale) != "Auto":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = str(app_config.get(app_config.dpi_scale))

app = QApplication(sys.argv)
app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

locale = app_config.get(app_config.language).value
translator = FluentTranslator(locale)
appTranslator = QTranslator()
appTranslator.load(locale, "app", ".", ":/app/i18n")

app.installTranslator(translator)
app.installTranslator(appTranslator)

w = MainWindow()
w.show()

app.exec()