"""图片查看器：缩放、适应窗口、旋转，并可在同目录图片间切换。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, ToolButton

from ...core.viewer_data import IMAGE_EXTENSIONS, human_size, image_info

ZOOM_STEP = 1.25
ZOOM_MIN = 0.05
ZOOM_MAX = 12.0


def sibling_images(path: Path) -> list[Path]:
    """同一目录下的其它图片（按名称排序），供上一张 / 下一张使用。"""
    try:
        files = [
            child
            for child in sorted(path.parent.iterdir(), key=lambda item: item.name.lower())
            if child.is_file() and child.suffix.lower().lstrip(".") in IMAGE_EXTENSIONS
        ]
    except OSError:
        return [path]
    if path not in files:
        files.append(path)
    return files


class ImageViewer(QWidget):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._scale = 1.0
        self._fit = True
        self._rotation = 0
        self._siblings = sibling_images(self._path)
        self._index = self._siblings.index(self._path) if self._path in self._siblings else 0
        self._pixmap = QPixmap(str(self._path))
        self.caption = self._describe()
        self._build_ui()
        self._apply()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._prev_button = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._prev_button.clicked.connect(lambda: self._step(-1))
        self._next_button = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._next_button.clicked.connect(lambda: self._step(1))
        self._zoom_out_button = ToolButton(FluentIcon.ZOOM_OUT, self)
        self._zoom_out_button.clicked.connect(lambda: self._zoom(1 / ZOOM_STEP))
        self._zoom_in_button = ToolButton(FluentIcon.ZOOM_IN, self)
        self._zoom_in_button.clicked.connect(lambda: self._zoom(ZOOM_STEP))
        self._fit_button = ToolButton(FluentIcon.FIT_PAGE, self)
        self._fit_button.clicked.connect(self._fit_window)
        self._rotate_button = ToolButton(FluentIcon.ROTATE, self)
        self._rotate_button.clicked.connect(self._rotate)
        for button in (
            self._prev_button,
            self._next_button,
            self._zoom_out_button,
            self._zoom_in_button,
            self._fit_button,
            self._rotate_button,
        ):
            bar.addWidget(button)
        bar.addStretch(1)
        self._zoom_label = CaptionLabel("100%", self)
        bar.addWidget(self._zoom_label)
        self._index_label = CaptionLabel("", self)
        bar.addWidget(self._index_label)
        root.addLayout(bar)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll = QScrollArea(self)
        self._scroll.setWidget(self._label)
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scroll, 1)

        single = len(self._siblings) <= 1
        self._prev_button.setEnabled(not single)
        self._next_button.setEnabled(not single)
        if not single:
            self._index_label.setText(f"{self._index + 1}/{len(self._siblings)}")

    # ------------------------------------------------------------------ 行为
    def _describe(self) -> str:
        pieces = [self._path.name]
        try:
            pieces.append(human_size(self._path.stat().st_size))
        except OSError:
            pass
        info = image_info(self._path)
        if info.get("width"):
            pieces.append(f"{info['width']}×{info['height']}")
        if info.get("frames", 1) and info["frames"] > 1:
            pieces.append(f"{info['frames']} 帧（显示首帧）")
        return " · ".join(pieces)

    def _load(self, path: Path) -> None:
        self._path = path
        self._pixmap = QPixmap(str(path))
        self._rotation = 0
        self._scale = 1.0
        self._fit = True
        self.caption = self._describe()
        self._index_label.setText(f"{self._index + 1}/{len(self._siblings)}")
        self._apply()

    def _step(self, delta: int) -> None:
        if len(self._siblings) <= 1:
            return
        self._index = (self._index + delta) % len(self._siblings)
        self._load(self._siblings[self._index])

    def _zoom(self, factor: float) -> None:
        self._fit = False
        self._scale = max(ZOOM_MIN, min(ZOOM_MAX, self._scale * factor))
        self._apply()

    def _fit_window(self) -> None:
        self._fit = True
        self._scale = 1.0
        self._apply()

    def _rotate(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._apply()

    def _apply(self) -> None:
        if self._pixmap.isNull():
            self._label.setText("无法显示该图片（格式可能不受支持）")
            self._label.adjustSize()
            return
        pixmap = self._pixmap
        if self._rotation:
            pixmap = pixmap.transformed(QTransform().rotate(self._rotation), Qt.TransformationMode.SmoothTransformation)
        scale = self._scale
        if self._fit:
            area = self._scroll.viewport().size()
            if pixmap.width() and pixmap.height():
                scale = min(area.width() / pixmap.width(), area.height() / pixmap.height(), 1.0)
        self._scale = scale
        size = pixmap.size() * scale
        target = pixmap.scaled(
            max(1, int(size.width())),
            max(1, int(size.height())),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(target)
        self._label.resize(target.size())
        self._zoom_label.setText(f"{int(round(scale * 100))}%")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._fit:
            self._apply()
