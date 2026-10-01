"""全局信号总线：页面之间通过它解耦通信。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class SignalBus(QObject):
    itemsChanged = pyqtSignal()
    categoriesChanged = pyqtSignal()
    tagsChanged = pyqtSignal()
    userChanged = pyqtSignal()
    archivesChanged = pyqtSignal()

    requestImport = pyqtSignal()
    requestManage = pyqtSignal()
    requestArchive = pyqtSignal()
    focusItem = pyqtSignal(int)
    librariesChanged = pyqtSignal()

    micaEnableChanged = pyqtSignal(bool)
    lockedStateChanged = pyqtSignal(bool)


signalBus = SignalBus()
