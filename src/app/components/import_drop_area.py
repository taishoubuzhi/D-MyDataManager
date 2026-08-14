# coding: utf-8
"""文件拖拽/选择区组件：拖入文件或点击按钮选择，发出 filesSelected 信号。"""
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog

from qfluentwidgets import (CardWidget, BodyLabel, CaptionLabel, PushButton,
                            FluentIcon, setCustomStyleSheet)


class ImportDropArea(CardWidget):
    """文件拖拽/选择区

    拖入文件或点击按钮选择，通过 filesSelected 信号向外发送文件绝对路径列表。
    """

    filesSelected = pyqtSignal(list)  # list[str] 文件绝对路径

    _DARK_QSS = """
        #dropArea { border: 2px dashed rgba(255,255,255,45); border-radius: 8px; }
        #dropArea[dragOver="true"] {
            border-color: rgba(0,159,170,255);
            background: rgba(0,159,170,30);
        }
    """
    _LIGHT_QSS = """
        #dropArea { border: 2px dashed rgba(0,0,0,80); border-radius: 8px; }
        #dropArea[dragOver="true"] {
            border-color: rgba(0,159,170,255);
            background: rgba(0,159,170,25);
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setAcceptDrops(True)
        self.setObjectName('dropArea')
        self.setMinimumHeight(140)
        self._build_ui()
        # 注意：setCustomStyleSheet 参数顺序为 (widget, lightQss, darkQss)
        setCustomStyleSheet(self, self._LIGHT_QSS, self._DARK_QSS)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hintLabel = BodyLabel("拖拽文件到此处", self)
        self.hintLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hintLabel)

        self.subLabel = CaptionLabel("支持图片/视频/音频/文档等，或点击下方按钮选择", self)
        self.subLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.subLabel)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(0)
        btn_row.addStretch(1)
        self.browseBtn = PushButton(FluentIcon.FOLDER, "选择文件", self)
        self.browseBtn.clicked.connect(self._onBrowse)
        btn_row.addWidget(self.browseBtn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.selectedLabel = CaptionLabel("", self)
        self.selectedLabel.setObjectName('dropSelectedLabel')
        self.selectedLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selectedLabel.hide()
        layout.addWidget(self.selectedLabel)

    def _onBrowse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", "所有文件 (*)")
        if files:
            self._emit(files)

    def _emit(self, files):
        self.filesSelected.emit(files)
        if len(files) == 1:
            self.selectedLabel.setText(f"已选择：{os.path.basename(files[0])}")
        else:
            self.selectedLabel.setText(f"已选择 {len(files)} 个文件")
        self.selectedLabel.show()

    def reset(self):
        """清空已选文件展示"""
        self.selectedLabel.setText("")
        self.selectedLabel.hide()

    # ===== 拖拽事件 =====
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._setDragOver(True)

    def dragLeaveEvent(self, e):
        self._setDragOver(False)

    def dropEvent(self, e):
        self._setDragOver(False)
        files = [url.toLocalFile() for url in e.mimeData().urls()
                 if url.isLocalFile() and os.path.isfile(url.toLocalFile())]
        if files:
            self._emit(files)

    def _setDragOver(self, on: bool):
        self.setProperty('dragOver', on)
        self.style().unpolish(self)
        self.style().polish(self)
