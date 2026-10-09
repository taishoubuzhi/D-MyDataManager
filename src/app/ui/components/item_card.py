"""数据项卡片与列表行。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, CheckBox

from ...core.config import library_root
from ...db.models import DataItem, DataType
from ..framework import accent_color, accent_name, elide, format_datetime, format_size, type_icon, type_name
from .cover_loader import cover_loader

COVER_SIZE = 48
CHECK_TIP = "勾选以多选（Ctrl / Shift + 左键也可以多选）"


def _image_cover_path(item: DataItem) -> str:
    """图片用作封面的那个文件：**优先库内那一份**，库内没有了才退回导入来源。

    注意不能直接用 `item_api._absolute_path()`：那个函数优先返回导入时的外部 `source_path`，
    原文件被移走 / 删除后封面就取不到了（用户 m01544 第 2 条：重置封面后部分图片退回默认图标）。
    图片的封面就是它自己，库内文件在就一定能显示；两处都没有才返回空串、由界面显示类型图标。
    """
    raw = str(item.file_path or "")
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            try:
                candidate = Path(library_root()) / candidate
            except Exception:  # noqa: BLE001 - 库根读不出来时按原路径试
                candidate = Path(raw)
        if candidate.is_file():
            return str(candidate)
    source = str(item.source_path or "")
    if source:
        candidate = Path(source)
        if candidate.is_absolute() and candidate.is_file():
            return source
    return ""


def cover_source(item: DataItem) -> str:
    """卡片要加载的封面路径。

    图片按规范不再存封面副本（用户 m00003 第 3 条），直接用**库内的自己**当封面；
    视频等其它类型仍用 `cover_path`（导入时抽的第一帧），没有就让界面显示类型图标。
    """
    if item.cover_path:
        return str(item.cover_path)
    if item.type is DataType.IMAGE:
        return _image_cover_path(item)
    return ""


def _accent_rgba(alpha: float) -> str:
    """主题强调色的半透明写法，用于选中态背景。"""
    color = accent_color()
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


class CoverLabel(QLabel):
    """48×48 的封面：先显示类型图标，缩略图在工作线程加载完成后替换。"""

    def __init__(self, parent: QWidget | None = None, size: int = COVER_SIZE) -> None:
        super().__init__(parent)
        self._size = size
        self._request = 0
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("border-radius: 6px; background: rgba(128, 128, 128, 0.14);")

    def set_item(self, item: DataItem) -> None:
        self._request += 1
        token = self._request
        self.clear()
        self.setPixmap(type_icon(item.type).icon().pixmap(self._size // 2, self._size // 2))
        path = cover_source(item)
        if not path:
            return
        loader = cover_loader()
        cached = loader.cached(path, self._size)
        if cached is not None:
            self._apply(cached, token)
            return
        loader.request(path, self._size, lambda pixmap: self._apply(pixmap, token))

    def _apply(self, pixmap: QPixmap | None, token: int) -> None:
        if pixmap is None or token != self._request:
            return
        self.setPixmap(pixmap)


class _TagRow(QWidget):
    def __init__(
        self, parent: QWidget | None = None, rgba: str = "0, 120, 212, 0.14", prefix: str = ""
    ) -> None:
        super().__init__(parent)
        self._rgba = rgba
        self._prefix = prefix
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        #: 复用的标签胶囊：刷新时改文案/显隐，不再销毁重建（构造标签很贵）。
        self._chips: list[CaptionLabel] = []
        self._more: CaptionLabel | None = None
        self._layout.addStretch(1)

    def set_tags(self, names: list[str]) -> None:
        shown = names[:4]
        while len(self._chips) < len(shown):
            chip = CaptionLabel(self)
            chip.setStyleSheet(
                f"background: rgba({self._rgba}); border-radius: 8px; padding: 1px 6px;"
            )
            self._layout.insertWidget(len(self._chips), chip)
            self._chips.append(chip)
        for index, chip in enumerate(self._chips):
            visible = index < len(shown)
            chip.setVisible(visible)
            if visible:
                chip.setText(f"{self._prefix}{shown[index]}")
        extra = len(names) - 4
        if extra > 0:
            if self._more is None:
                self._more = CaptionLabel(self)
                self._layout.insertWidget(len(self._chips), self._more)
            self._more.setText(f"+{extra}")
            self._more.setVisible(True)
        elif self._more is not None:
            self._more.setVisible(False)


class ItemCard(CardWidget):
    """卡片视图中的一个数据项。"""

    activated = pyqtSignal(object)
    opened = pyqtSignal(object)
    menuRequested = pyqtSignal(object, object)
    checkedChanged = pyqtSignal(object, bool)

    def __init__(self, item: DataItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._press_button = Qt.MouseButton.LeftButton

        self.check_box = CheckBox(self)
        self.check_box.setToolTip(CHECK_TIP)
        self.check_box.stateChanged.connect(self._on_check_state)

        self._indicator = QFrame(self)
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet(f"background: {accent_name()}; border-radius: 1px;")
        self._indicator.setVisible(False)

        self._cover = CoverLabel(self)
        self._cover.set_item(item)

        self._title = BodyLabel(elide(item.name, 34), self)
        self._title.setStyleSheet("font-weight: 600;")
        self._meta = CaptionLabel(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}",
            self,
        )
        tag_names = list(item.tag_names)
        keywords = [str(word) for word in (item.keywords or []) if str(word).strip()]
        self._tags = _TagRow(self, prefix="#")
        self._tags.set_tags(tag_names)
        self._tags.setToolTip("标签：" + ("、".join(tag_names) or "无"))
        self._keywords = _TagRow(self, "120, 120, 120, 0.18")

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        info.addWidget(self._title)
        info.addWidget(self._meta)
        info.addWidget(self._tags)
        info.addWidget(self._keywords)
        self._keywords.set_tags(keywords)
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))
        self._keywords.setVisible(bool(keywords))

        for flag in (Qt.WidgetAttribute.WA_TransparentForMouseEvents,):
            self._title.setAttribute(flag, True)
            self._meta.setAttribute(flag, True)
            self._tags.setAttribute(flag, True)
            self._keywords.setAttribute(flag, True)
            self._cover.setAttribute(flag, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 8, 10, 8)
        layout.setSpacing(10)
        layout.addWidget(self.check_box)
        layout.addWidget(self._indicator)
        layout.addWidget(self._cover)
        layout.addLayout(info, 1)

        self.clicked.connect(self._on_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menuRequested.emit(self._item, self.mapToGlobal(pos))
        )

    @property
    def item(self) -> DataItem:
        return self._item

    def set_item(self, item: DataItem) -> None:
        """就地换成另一条数据（刷新时复用控件，避免整页销毁重建）。"""
        self._item = item
        self._cover.set_item(item)
        self._title.setText(elide(item.name, 34))
        self._meta.setText(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}"
        )
        tag_names = list(item.tag_names)
        keywords = [str(word) for word in (item.keywords or []) if str(word).strip()]
        self._tags.set_tags(tag_names)
        self._tags.setToolTip("标签：" + ("、".join(tag_names) or "无"))
        self._keywords.set_tags(keywords)
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))
        self._keywords.setVisible(bool(keywords))

    def is_checked(self) -> bool:
        return self.check_box.isChecked()

    def set_checked(self, checked: bool) -> None:
        """由页面回写勾选状态（不发 checkedChanged，避免回环）。"""
        if self.check_box.isChecked() == bool(checked):
            return
        self.check_box.blockSignals(True)
        self.check_box.setChecked(bool(checked))
        self.check_box.blockSignals(False)

    def _on_check_state(self, _state: int) -> None:
        self.checkedChanged.emit(self._item, self.check_box.isChecked())

    def _on_clicked(self) -> None:
        """只有左键单击算「选中」，右键交给上下文菜单（不改变多选状态）。"""
        if self._press_button == Qt.MouseButton.LeftButton:
            self.activated.emit(self._item)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press_button = event.button()
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._indicator.setVisible(selected)
        self.set_checked(selected)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.opened.emit(self._item)
        super().mouseDoubleClickEvent(event)


class ItemListRow(QWidget):
    """列表视图中的一行。"""

    activated = pyqtSignal(object)
    opened = pyqtSignal(object)
    menuRequested = pyqtSignal(object, object)
    checkedChanged = pyqtSignal(object, bool)

    def __init__(self, item: DataItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._selected = False
        self.setMinimumHeight(44)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.check_box = CheckBox(self)
        self.check_box.setToolTip(CHECK_TIP)
        self.check_box.stateChanged.connect(self._on_check_state)

        self._cover = CoverLabel(self, 32)
        self._cover.set_item(item)
        self._title = BodyLabel(elide(item.name, 46), self)
        self._meta = CaptionLabel(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}",
            self,
        )
        tag_names = list(item.tag_names)
        keywords = [str(word) for word in (item.keywords or []) if str(word).strip()]
        self._tags = CaptionLabel(" ".join(f"#{name}" for name in tag_names[:3]), self)
        self._tags.setToolTip("标签：" + ("、".join(tag_names) or "无"))
        self._keywords = CaptionLabel("、".join(keywords[:3]), self)
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)
        layout.addWidget(self.check_box)
        layout.addWidget(self._cover)
        layout.addWidget(self._title, 2)
        layout.addWidget(self._meta, 2)
        layout.addWidget(self._tags, 1)
        layout.addWidget(self._keywords, 1)

    @property
    def item(self) -> DataItem:
        return self._item

    def set_item(self, item: DataItem) -> None:
        """就地换成另一条数据（刷新时复用控件，避免整页销毁重建）。"""
        self._item = item
        self._cover.set_item(item)
        self._title.setText(elide(item.name, 46))
        self._meta.setText(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}"
        )
        tag_names = list(item.tag_names)
        keywords = [str(word) for word in (item.keywords or []) if str(word).strip()]
        self._tags.setText(" ".join(f"#{name}" for name in tag_names[:3]))
        self._tags.setToolTip("标签：" + ("、".join(tag_names) or "无"))
        self._keywords.setText("、".join(keywords[:3]))
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))

    def is_checked(self) -> bool:
        return self.check_box.isChecked()

    def set_checked(self, checked: bool) -> None:
        """由页面回写勾选状态（不发 checkedChanged，避免回环）。"""
        if self.check_box.isChecked() == bool(checked):
            return
        self.check_box.blockSignals(True)
        self.check_box.setChecked(bool(checked))
        self.check_box.blockSignals(False)

    def _on_check_state(self, _state: int) -> None:
        self.checkedChanged.emit(self._item, self.check_box.isChecked())

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if self._selected != selected:
            self._selected = selected
            self.setStyleSheet(
                f"background: {_accent_rgba(0.16)}; border-radius: 6px;"
                if selected
                else "background: transparent;"
            )
        self.set_checked(selected)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """左键单击选中；右键只留给上下文菜单，不破坏已有的多选。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._item)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.opened.emit(self._item)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menuRequested.emit(self._item, event.globalPos())


__all__ = ["CHECK_TIP", "CoverLabel", "ItemCard", "ItemListRow", "cover_source"]