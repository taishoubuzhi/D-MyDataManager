"""图片查看器：缩放、适应窗口、原始大小、旋转，并可在同目录图片间切换。

工具条按钮与图片画布来自 builtin.lib.ui，缩放 / 旋转逻辑仍在本模块。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QTransform
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.data import human_size, image_info
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    caption,
    image_canvas,
    status_label,
    tool_button,
    toolbar,
)

ZOOM_STEP = 1.25
ZOOM_MIN = 0.05
ZOOM_MAX = 12.0
#: 「适应窗口」允许把小图放大到铺满视口，所以上限比手动缩放的 ZOOM_MAX 宽
FIT_ZOOM_MAX = 32.0


def wanted_suffixes(extensions: Iterable[str]) -> set[str]:
    """本查看器认的扩展名（小写、去掉前导点）。"""
    return {str(item).strip().lower().lstrip(".") for item in extensions if str(item).strip()}


def sibling_images(path: Path, extensions: Iterable[str]) -> list[Path]:
    """同一目录下的其它图片（按名称排序），供上一张 / 下一张使用。

    `extensions` 由插件自己在 .data/viewer.json 里声明，程序里不再写死图片扩展名。
    """
    wanted = wanted_suffixes(extensions)
    try:
        files = [
            child
            for child in sorted(path.parent.iterdir(), key=lambda item: item.name.lower())
            if child.is_file() and child.suffix.lower().lstrip(".") in wanted
        ]
    except OSError:
        return [path]
    if path not in files:
        files.append(path)
    return files


def browse_images(path: Path, sources: Iterable[Path], extensions: Iterable[str]) -> list[Path]:
    """上一张 / 下一张要走的图片顺序：优先用调用方给的列表，否则退回同目录。

    `sources` 是宿主（数据管理页）正在显示的那一页文件，顺序就是用户看到的顺序。
    库里的图片按分类平铺、导入时还保留来源子目录，同一目录往往只有一张图，
    只看同目录等于切不动（用户 m00828 报的就是这个），所以才要宿主把列表交给查看器。
    """
    wanted = wanted_suffixes(extensions)
    files: list[Path] = []
    seen: set[Path] = set()
    for item in sources:
        candidate = Path(item)
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.suffix.lower().lstrip(".") in wanted and candidate.is_file():
            files.append(candidate)
    if len(files) > 1 and path in files:
        return files
    return sibling_images(path, extensions)


class ImageViewer(QWidget):
    """图片查看控件。

    `fit_on_open` / `zoom_step` / `smooth` 由插件选项决定（见 plugins/builtin.viewer.image/plugin.py），
    窗口标题栏的「设置」入口里也能就地改：改完立即生效，并通过 `on_option` 回写插件选项。
    """

    #: 换到另一张图时发出（「文件名 · 体积 · 尺寸」），外壳据此刷新副标题
    captionChanged = pyqtSignal(str)
    #: 换到另一张图时发出（新的绝对路径），外壳据此换标题、换「定位文件 / 用系统程序打开」的目标
    pathChanged = pyqtSignal(str)

    def __init__(
        self,
        path: Path,
        parent: QWidget | None = None,
        *,
        fit_on_open: bool = True,
        zoom_step: float = ZOOM_STEP,
        smooth: bool = True,
        extensions: Iterable[str] = (),
        on_option: Callable[[str, object], None] | None = None,
        sources: Iterable[Path] = (),
    ) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._scale = 1.0
        self._fit = bool(fit_on_open)
        self._rotation = 0
        self._step_factor = max(1.01, float(zoom_step))
        self._smooth = bool(smooth)
        self._on_option = on_option
        self._extensions = tuple(str(item) for item in extensions)
        self._siblings = browse_images(self._path, sources, self._extensions)
        self._index = self._siblings.index(self._path) if self._path in self._siblings else 0
        self._pixmap = QPixmap(str(self._path))
        self.caption = self._path.name
        self._last_caption = ""
        self._build_ui()
        self._sync_step_buttons()
        self._apply()
        if self._fit:
            self._scale = self._fit_scale()
            self._apply()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar, row = toolbar(self)
        row.addWidget(tool_button(bar, FluentIcon.ZOOM_IN, "放大", lambda: self._zoom(self._step_factor)))
        row.addWidget(tool_button(bar, FluentIcon.ZOOM_OUT, "缩小", lambda: self._zoom(1.0 / self._step_factor)))
        row.addWidget(tool_button(bar, FluentIcon.FIT_PAGE, "适应窗口", self._fit_window))
        row.addWidget(tool_button(bar, FluentIcon.ZOOM, "原始大小", self._actual_size))
        row.addWidget(tool_button(bar, FluentIcon.ROTATE, "向右旋转", lambda: self._rotate(90)))
        self._prev_button = tool_button(
            bar, FluentIcon.LEFT_ARROW, "上一张", lambda: self._step_image(-1)
        )
        row.addWidget(self._prev_button)
        self._next_button = tool_button(
            bar, FluentIcon.RIGHT_ARROW, "下一张", lambda: self._step_image(1)
        )
        row.addWidget(self._next_button)
        row.addStretch(1)
        self._zoom_label = caption(bar, "100%")
        row.addWidget(self._zoom_label)
        self.status_label = status_label(bar, "")
        row.addWidget(self.status_label)
        root.addWidget(bar)

        self._scroll, self._label = image_canvas(self)
        # 视口尺寸由外层布局决定：弹窗刚建时视口往往只有几十像素，
        # 之后变大不会再触发本控件的 resizeEvent，所以要盯住视口自己的 Resize。
        self._scroll.viewport().installEventFilter(self)
        root.addWidget(self._scroll, 1)

    # ------------------------------------------------------------------ 行为
    def _fit_scale(self) -> float:
        if self._pixmap.isNull():
            return 1.0
        available = self._scroll.viewport().size()
        width = max(1, self._pixmap.width())
        height = max(1, self._pixmap.height())
        return max(ZOOM_MIN, min(available.width() / width, available.height() / height, FIT_ZOOM_MAX))

    def _apply(self) -> None:
        transform = QTransform()
        transform.rotate(self._rotation)
        transformed = self._pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        scaled = max(1, int(transformed.width() * self._scale))
        shown = transformed.scaled(
            scaled,
            max(1, int(transformed.height() * self._scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation if self._smooth else Qt.TransformationMode.FastTransformation,
        )
        self._label.setPixmap(shown)
        self._label.resize(shown.size())
        self._update_status()

    def _viewport_size(self) -> tuple[int, int]:
        """画布视口尺寸（适应窗口时按它算比例）。"""
        size = self._scroll.viewport().size()
        return size.width(), size.height()

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

    def _update_status(self) -> None:
        self.caption = self._describe()
        self._zoom_label.setText(f"{self._scale * 100:.0f}%")
        self.status_label.setText(self._position_text())
        if self.caption != self._last_caption:  # 只在说明真的变了时通知外壳，避免来回刷副标题
            self._last_caption = self.caption
            self.captionChanged.emit(self.caption)

    def _position_text(self) -> str:
        """状态栏文字：只有一张图时不显示「第 x / y 张」。"""
        total = len(self._siblings)
        if total < 2:
            return self.caption
        return f"{self.caption}（第 {self._index + 1}/{total} 张）"

    def _zoom(self, factor: float) -> None:
        self._fit = False
        self._scale = max(ZOOM_MIN, min(self._scale * factor, ZOOM_MAX))
        self._apply()

    def _fit_window(self) -> None:
        self._fit = True
        self._scale = self._fit_scale()
        self._apply()

    def _actual_size(self) -> None:
        self._fit = False
        self._scale = 1.0
        self._apply()

    def _rotate(self, degrees: int) -> None:
        self._rotation = (self._rotation + degrees) % 360
        if self._fit:
            self._scale = self._fit_scale()
        self._apply()

    def _step_image(self, delta: int) -> None:
        if len(self._siblings) < 2:
            return
        self._index = (self._index + delta) % len(self._siblings)
        self._path = self._siblings[self._index]
        self._pixmap = QPixmap(str(self._path))
        self._rotation = 0
        if self._fit:
            self._scale = self._fit_scale()
        self.pathChanged.emit(str(self._path))
        self._apply()

    def set_sources(self, sources: Iterable[Path]) -> None:
        """宿主把「当前列表里的图片顺序」交进来时调用：上一张 / 下一张按它走。

        这是查看器内容页的可选契约（外壳用 `getattr` 探测），没实现也不影响显示。
        """
        files = browse_images(self._path, sources, self._extensions)
        if files == self._siblings:
            return
        self._siblings = files
        self._index = files.index(self._path) if self._path in files else 0
        self._sync_step_buttons()
        self._update_status()

    def _sync_step_buttons(self) -> None:
        """只有一张图时禁用上一张 / 下一张，并把原因写进提示。

        「点了没反应」比按钮灰着更难懂：库里的图片按分类平铺、导入时还保留来源子目录，
        同一目录常常只有一张图（见 `browse_images`）。
        """
        many = len(self._siblings) > 1
        for button, text in ((self._prev_button, "上一张"), (self._next_button, "下一张")):
            button.setEnabled(many)
            button.setToolTip(
                f"{text}（当前列表共 {len(self._siblings)} 张图片）"
                if many
                else f"{text}：当前列表里没有别的图片可切换"
            )

    # ------------------------------------------------------------------ 设置面板
    def settings_items(self) -> list[dict]:
        """标题栏「设置」入口里的项：打开方式、缩放步长与平滑缩放，改完立即生效。"""
        return [
            {
                "key": "fit_on_open",
                "label": "打开时适应窗口",
                "kind": "bool",
                "value": bool(self._fit),
                "description": "打开图片时先按显示区域缩放，尽量大地完整显示。",
                "on_change": self._pick_fit_on_open,
            },
            {
                "key": "zoom_step",
                "label": "缩放步长",
                "kind": "choice",
                "value": f"{self._step_factor:g}",
                "choices": {"1.1": "1.1×（细腻）", "1.25": "1.25×（默认）", "1.5": "1.5×（快速）"},
                "description": "每次点放大 / 缩小时的倍率变化幅度。",
                "on_change": self._pick_zoom_step,
            },
            {
                "key": "smooth_scaling",
                "label": "平滑缩放",
                "kind": "bool",
                "value": bool(self._smooth),
                "description": "关掉后缩放更快，但像素边缘更硬。",
                "on_change": self._pick_smooth,
            },
        ]

    def save_option(self, key: str, value: object) -> None:
        """把设置项的变化回写给插件（没有回调时只在本窗口生效）。"""
        if self._on_option is not None:
            self._on_option(key, value)

    def _pick_fit_on_open(self, value) -> None:
        if bool(value):
            self._fit_window()
        self.save_option("fit_on_open", bool(value))

    def _pick_zoom_step(self, value) -> None:
        try:
            self._step_factor = max(1.01, float(value))
        except (TypeError, ValueError):
            return
        self.save_option("zoom_step", f"{self._step_factor:g}")

    def _pick_smooth(self, value) -> None:
        self._smooth = bool(value)
        self._apply()
        self.save_option("smooth_scaling", self._smooth)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._fit:
            self._scale = self._fit_scale()
            self._apply()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        """视口尺寸变化时重算「适应窗口」：打开弹窗时视口是逐步变大的。"""
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize and self._fit:
            scale = self._fit_scale()
            if abs(scale - self._scale) > 1e-6:
                self._scale = scale
                self._apply()
        return super().eventFilter(obj, event)
