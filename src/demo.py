import os
import sys

from PyQt6.QtCore import Qt, QTranslator
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

from app.common.config import config, load_config
from app.common.init import init_log, init_db

from app.view.main_window import MainWindow

from loguru import logger

init_log()
logger.info("Application starting...")
init_db()

if config.get(config.dpi_scale) != "Auto":
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = str(config.get(config.dpi_scale))

app = QApplication(sys.argv)
app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)

_translator = None
_appTranslator = None
_window = None


def _setup_translators():
    global _translator, _appTranslator
    if _translator:
        app.removeTranslator(_translator)
    if _appTranslator:
        app.removeTranslator(_appTranslator)
    locale = config.get(config.language).value
    _translator = FluentTranslator(locale)
    _appTranslator = QTranslator()
    _appTranslator.load(locale, "app", ".", ":/app/i18n")
    app.installTranslator(_translator)
    app.installTranslator(_appTranslator)


_setup_translators()
_window = MainWindow()
_window.show()

app.exec()
