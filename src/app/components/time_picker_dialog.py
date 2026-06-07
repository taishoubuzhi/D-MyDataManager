# coding:utf-8
import datetime
from PyQt6.QtCore import Qt, QTime, pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTimeEdit

from ..common.style_sheet import StyleSheet


class TimePickerDialog(QDialog):
    
    timeSelected = pyqtSignal(object)

    def __init__(self, current_time=None, title="Select Time", time_format="HH:mm:ss", parent=None):
        super().__init__(parent=parent)
        self._time_format = time_format
        self.setWindowTitle(title)
        self.setModal(True)
        
        self.__initWidget(current_time)
        StyleSheet.TIME_PICKER_DIALOG.apply(self)

    def __initWidget(self, current_time):
        layout = QVBoxLayout(self)
        
        if current_time:
            qtime = QTime(current_time.hour, current_time.minute, current_time.second)
        else:
            qtime = QTime(0, 0, 0)
        
        self.time_edit = QTimeEdit(qtime)
        self.time_edit.setDisplayFormat(self._time_format)
        self.time_edit.setObjectName("timeEdit")
        layout.addWidget(self.time_edit)
        
        button_layout = QHBoxLayout()
        
        self.ok_button = QPushButton(self.tr("确定"))
        self.ok_button.setObjectName("okButton")
        
        self.cancel_button = QPushButton(self.tr("取消"))
        self.cancel_button.setObjectName("cancelButton")
        
        self.ok_button.clicked.connect(self.__on_ok)
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

    def __on_ok(self):
        full_time = self.get_full_time()
        self.timeSelected.emit(full_time)
        self.accept()

    def get_time(self):
        selected_time = self.time_edit.time()
        format_lower = self._time_format.lower()
        
        if "hh" in format_lower and "mm" in format_lower and "ss" in format_lower:
            return datetime.time(selected_time.hour(), selected_time.minute(), selected_time.second())
        elif "hh" in format_lower and "mm" in format_lower:
            return datetime.time(selected_time.hour(), selected_time.minute())
        elif "hh" in format_lower and "ss" in format_lower:
            return datetime.time(selected_time.hour(), 0, selected_time.second())
        elif "mm" in format_lower and "ss" in format_lower:
            return datetime.time(0, selected_time.minute(), selected_time.second())
        elif "hh" in format_lower:
            return datetime.time(selected_time.hour(), 0, 0)
        elif "mm" in format_lower:
            return datetime.time(0, selected_time.minute(), 0)
        elif "ss" in format_lower:
            return datetime.time(0, 0, selected_time.second())
        else:
            return self.get_full_time()

    def get_full_time(self):
        selected_time = self.time_edit.time()
        return datetime.time(selected_time.hour(), selected_time.minute(), selected_time.second())

    def set_title(self, title):
        self.setWindowTitle(title)

    def set_time_format(self, time_format):
        self._time_format = time_format
        self.time_edit.setDisplayFormat(time_format)
