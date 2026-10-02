"""图片查看器：缩放、适应窗口、原始大小、旋转，并可在同目录图片间切换。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, ToolButton

from ...core.viewer_data import IMAGE_EXTENSIONS, human_size, image_info
from ..framework import COMPACT_MARGINS, clear_scroll_background

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
    """图片查看控件。

    `fit_on_open` / `zoom_step` / `smooth` 由插件选项决定（见 plugins/builtin.image/plugin.py），
    插件页改动选项后重新载入插件即可生效。
    """

    def __init__(
        self,
        path: Path,
        parent: QWidget | None = None,
        *,
        fit_on_open: bool = True,
        zoom_step: float = ZOOM_STEP,
        smooth: bool = True,
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._scale = 1.0
        self._fit = bool(fit_on_open)
        self._rotation = 0
        self._step_factor = max(1.01, float(zoom_step))
        self._smooth = bool(smooth)
        self._siblings = sibling_images(self._path)
        self._index = self._siblings.index(self._path) if self._path in self._siblings else 0
        self._pixmap = QPixmap(str(self._path))
        self.caption = self._describe()
        self._build_ui()
        self._apply()

    # ------------------------------------------------------------------ 界面
    def _mode(self) -> Qt.TransformationMode:
        return Qt.TransformationMode.SmoothTransformation if self._smooth else Qt.TransformationMode.FastTransformation

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._prev_button = ToolButton(FluentIcon.LEFT_ARROW, self)
        self._prev_button.clicked.connect(lambda: self._step(-1))
        self._next_button = ToolButton(FluentIcon.RIGHT_ARROW, self)
        self._next_button.clicked.connect(lambda: self._step(1))
        self._zoom_out_button = ToolButton(FluentIcon.ZOOM_OUT, self)
        self._zoom_out_button.clicked.connect(lambda: self._zoom(1 / self._step_factor))
        self._zoom_in_button = ToolButton(FluentIcon.ZOOM_IN, self)
        self._zoom_in_button.clicked.connect(lambda: self._zoom(self._step_factor))
        self._fit_button = ToolButton(FluentIcon.FIT_PAGE, self)
        self._fit_button.clicked.connect(self._fit_window)
        self._actual_button = ToolButton(FluentIcon.ZOOM, self)
        self._actual_button.clicked.connect(self._actual_size)
        self._rotate_button = ToolButton(FluentIcon.ROTATE, self)
        self._rotate_button.clicked.connect(self._rotate)
        for button in (
            self._prev_button,
            self._next_button,
            self._zoom_out_button,
            self._zoom_in_button,
            self._fit_button,
            self._actual_button,
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
        clear_scroll_background(self._scroll)
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scroll, 1)

        for button, tip in (
            (self._prev_button, "上一张"),
            (self._next_button, "下一张"),
            (self._zoom_out_button, "缩小"),
            (self._zoom_in_button, "放大"),
            (self._fit_button, "适应窗口"),
            (self._actual_button, "原始大小"),
            (self._rotate_button, "旋转 90°"),
        ):
            button.setToolTip(tip)

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
        self._fit = True  # 切图后重新适应窗口
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

    def _actual_size(self) -> None:
        self._fit = False
        self._scale = 1.0
        self._apply()

    def _rotate(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._apply()

    def _viewport_size(self) -> tuple[int, int]:
        area = self._scroll.viewport().size()
        return area.width(), area.height()

    def _apply(self) -> None:
        if self._pixmap.isNull():
            self._label.setText("无法显示该图片（格式可能不受支持）")
            self._label.adjustSize()
            return
        pixmap = self._pixmap
        if self._rotation:
            pixmap = pixmap.transformed(QTransform().rotate(self._rotation), self._mode())
        scale = self._scale
        if self._fit and pixmap.width() and pixmap.height():
            width, height = self._viewport_size()
            # 视口尚未布局完成时先不缩放，避免首帧把图片算得过小；showEvent / resizeEvent 会再算一次
            if width > 8 and height > 8:
                scale = min(width / pixmap.width(), height / pixmap.height())
                scale = max(ZOOM_MIN, min(ZOOM_MAX, scale))
        self._scale = scale
        size = pixmap.size() * scale
        target = pixmap.scaled(
            max(1, int(size.width())),
            max(1, int(size.height())),
            Qt.AspectRatioMode.KeepAspectRatio,
            self._mode(),
        )
        self._label.setPixmap(target)
        self._label.resize(target.size())
        self._zoom_label.setText(f"{int(round(scale * 100))}%")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._fit:
            self._apply()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._fit:
            self._apply()
