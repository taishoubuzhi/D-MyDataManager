"""文件拖放区域：支持拖入文件或文件夹。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QFrame, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, PushButton

from ..common import accent_color


def _frame_qss() -> str:
    """拖放框样式：普通状态是灰色虚线，拖入时换成主题强调色。"""
    color = accent_color()
    return f"""
#dropFrame {{
    border: 2px dashed rgba(128, 128, 128, 0.55);
    border-radius: 8px;
    background: rgba(128, 128, 128, 0.06);
}}
#dropFrame[dropActive="true"] {{
    border: 2px dashed {color.name()};
    background: rgba({color.red()}, {color.green()}, {color.blue()}, 0.12);
}}
"""


class DropArea(QWidget):
    """点击或拖放选择文件的区域。"""

    filesSelected = pyqtSignal(list)
    directorySelected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, allow_directory: bool = True) -> None:
        super().__init__(parent)
        self._allow_directory = allow_directory

        self._frame = QFrame(self)
        self._frame.setObjectName("dropFrame")
        self._frame.setStyleSheet(_frame_qss())
        self._frame.setMinimumHeight(140)

        icon_label = QFrame(self._frame)
        icon_label.setFixedSize(40, 40)
        icon_layout = QVBoxLayout(icon_label)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_view = BodyLabel("", icon_label)
        icon_view.setPixmap(FluentIcon.FOLDER.icon().pixmap(32, 32))
        icon_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout.addWidget(icon_view)

        title = BodyLabel("把文件拖到这里", self._frame)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = CaptionLabel(
            "支持任意类型文件，也可以直接拖入整个文件夹" if allow_directory else "支持任意类型文件",
            self._frame,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        browse = PushButton(FluentIcon.FOLDER_ADD, "选择文件", self._frame)
        browse.clicked.connect(self.browse)

        inner = QVBoxLayout(self._frame)
        inner.setContentsMargins(16, 18, 16, 18)
        inner.setSpacing(8)
        inner.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        inner.addWidget(title)
        inner.addWidget(hint)
        inner.addWidget(browse, alignment=Qt.AlignmentFlag.AlignHCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._frame)

        self.setAcceptDrops(True)

    # ------------------------------------------------------------------ 交互
    def browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择要导入的文件", "", "所有文件 (*)")
        if files:
            self.filesSelected.emit(files)

    def browse_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹")
        if directory:
            self.directorySelected.emit(directory)

    # ------------------------------------------------------------------ 拖放
    def _set_active(self, active: bool) -> None:
        self._frame.setProperty("dropActive", "true" if active else "false")
        self._frame.style().unpolish(self._frame)
        self._frame.style().polish(self._frame)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)
        paths: list[str] = []
        directories: list[str] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            (directories if path.is_dir() else paths).append(str(path))
        if paths:
            self.filesSelected.emit(paths)
        elif directories and self._allow_directory:
            self.directorySelected.emit(directories[0])
        event.acceptProposedAction()


__all__ = ["DropArea"]
