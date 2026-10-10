"""数据项卡片与列表行。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, CheckBox

from ...core.config import library_root
from ...db.models import DataItem, DataType
from ..framework import accent_color, accent_name, elide, format_datetime, format_size, type_icon, type_name
from .cover_loader import cover_loader
from .flow_area import FlowArea

COVER_SIZE = 48
CHECK_TIP = "勾选以多选（Ctrl / Shift + 左键也可以多选）"

#: 显示大小档位（用户 m04164 第 1 条）：列表与卡片共用一档，差别主要在封面大小。
VIEW_SIZE_KEYS = ("small", "medium", "large")
VIEW_SIZE_LABELS = {"small": "小", "medium": "中", "large": "大"}
#: 默认档与小改前的观感一致，用户不主动换档时界面不会突然变形。
DEFAULT_VIEW_SIZE = "small"


@dataclass(frozen=True)
class ViewSize:
    """一档显示大小的尺寸参数。

    `card_cover` 是卡片封面的边长，卡片视图的每行放几张由 `card_min_width` 决定；
    `card_vertical` 为真时封面放到标题下方居中（大图模式，看封面细节用）。
    """

    card_cover: int
    card_min_width: int
    card_vertical: bool
    list_cover: int
    list_row_height: int


VIEW_SIZE_PRESETS: dict[str, ViewSize] = {
    "small": ViewSize(48, 240, False, 32, 44),
    "medium": ViewSize(96, 300, False, 48, 64),
    "large": ViewSize(192, 360, True, 72, 88),
}


def view_size_key(value: str) -> str:
    """把配置值收敛成受支持的档位名：配置被手改坏时退回默认档，别让界面崩。"""
    text = str(value or "")
    return text if text in VIEW_SIZE_PRESETS else DEFAULT_VIEW_SIZE


def view_size_preset(value: str) -> ViewSize:
    """取一档尺寸参数。"""
    return VIEW_SIZE_PRESETS[view_size_key(value)]


#: 列表的列（用户 m04164 第 2 条）：名称 / 类型·大小·时间 / 标签 / 关键词。
#: 第三个值是列宽下限；实际列宽在渲染时按本页内容量出（表头与所有行取同一个值），
#: 内容过长时列会变宽、把滚动区的画布撑宽，再用左右滑动看全。
LIST_COLUMNS = (
    ("name", "名称", 200),
    ("meta", "类型 · 大小 · 时间", 220),
    ("tags", "标签", 160),
    ("keywords", "关键词", 160),
)
LIST_ROW_MARGINS = (8, 4, 8, 4)
LIST_SPACING = 10
#: 勾选框独占一格固定宽度：表头与行用同一个宽度，列才能对齐。
CHECK_CELL_WIDTH = 24


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
    """封面：先显示类型图标，缩略图在工作线程加载完成后替换。"""

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


class _TagRow(FlowArea):
    """标签 / 关键词胶囊行：按可用宽度换行，高度由 `FlowArea` 按宽度锁住。

    原先只显示前 4 个、其余折叠成「+N」，内容一多用户就看不到全（用户 m04164 第 3 条）。
    现在全部铺开、换行显示，卡片高度再跟着这行的高度长。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        rgba: str = "0, 120, 212, 0.14",
        prefix: str = "",
        spacing: int = 4,
    ) -> None:
        super().__init__(parent, horizontal_spacing=spacing, vertical_spacing=spacing)
        self._rgba = rgba
        self._prefix = prefix
        #: 复用的标签胶囊：刷新时改文案/显隐，不再销毁重建（构造标签很贵）。
        self._chips: list[CaptionLabel] = []

    def set_tags(self, names: list[str]) -> None:
        for index, name in enumerate(names):
            text = f"{self._prefix}{name}"
            if index < len(self._chips):
                chip = self._chips[index]
                chip.setText(text)
                chip.show()
            else:
                chip = CaptionLabel(self)
                chip.setStyleSheet(
                    f"background: rgba({self._rgba}); border-radius: 8px; padding: 1px 6px;"
                )
                chip.setText(text)
                self._chips.append(chip)
                self.add_widget(chip)
        # 多余的胶囊藏起来而不是摘掉：流式布局按 isTight 跳过隐藏项，可以复用
        for chip in self._chips[len(names) :]:
            chip.hide()
        # 一行内容都没有时整行收起，别在卡片里留一段空白
        self.setVisible(bool(names))
        self.sync_height()


class ItemCard(CardWidget):
    """卡片视图中的一个数据项。"""

    activated = pyqtSignal(object)
    opened = pyqtSignal(object)
    menuRequested = pyqtSignal(object, object)
    checkedChanged = pyqtSignal(object, bool)

    def __init__(
        self, item: DataItem, parent: QWidget | None = None, view_size: str = DEFAULT_VIEW_SIZE
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._view_size = view_size_key(view_size)
        self._preset = view_size_preset(self._view_size)
        self._press_button = Qt.MouseButton.LeftButton

        self.check_box = CheckBox(self)
        self.check_box.setToolTip(CHECK_TIP)
        self.check_box.stateChanged.connect(self._on_check_state)

        self._indicator = QFrame(self)
        self._indicator.setFixedWidth(3)
        self._indicator.setStyleSheet(f"background: {accent_name()}; border-radius: 1px;")
        self._indicator.setVisible(False)

        self._cover = CoverLabel(self, self._preset.card_cover)
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
        self._keywords.set_tags(keywords)
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))

        if self._preset.card_vertical:
            # 大图档：封面占上方居中，标题与勾选框独占一行，信息排在封面之下
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            head.addWidget(self.check_box)
            head.addWidget(self._indicator)
            head.addWidget(self._title, 1)
            layout: QVBoxLayout | QHBoxLayout = QVBoxLayout(self)
            layout.setContentsMargins(10, 8, 10, 10)
            layout.setSpacing(4)
            layout.addLayout(head)
            layout.addWidget(self._cover, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(self._meta)
            layout.addWidget(self._tags)
            layout.addWidget(self._keywords)
            layout.addStretch(1)
        else:
            info = QVBoxLayout()
            info.setContentsMargins(0, 0, 0, 0)
            info.setSpacing(2)
            info.addWidget(self._title)
            info.addWidget(self._meta)
            info.addWidget(self._tags)
            info.addWidget(self._keywords)
            info.addStretch(1)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(6, 8, 10, 8)
            layout.setSpacing(10)
            layout.addWidget(self.check_box)
            layout.addWidget(self._indicator)
            layout.addWidget(self._cover)
            layout.addLayout(info, 1)

        for flag in (Qt.WidgetAttribute.WA_TransparentForMouseEvents,):
            self._title.setAttribute(flag, True)
            self._meta.setAttribute(flag, True)
            self._tags.setAttribute(flag, True)
            self._keywords.setAttribute(flag, True)
            self._cover.setAttribute(flag, True)

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

    def sync_height(self) -> None:
        """让卡片按内容给出准确高度（用户 m04164 第 3 条）。

        卡片视图用的自适应流式布局按控件的 `sizeHint()` 摆行，标签行的换行高度又取决于
        卡片当前宽度，所以要先把宽度分给标签行、按宽度锁定高度、再让卡片自己的布局重算一次。
        """
        layout = self.layout()
        if layout is None:
            return
        layout.activate()  # 先让标签行拿到自己的宽度，否则它们量不到换行高度
        for row in (self._tags, self._keywords):
            row.sync_height()
        layout.activate()  # 标签行高度定了，卡片自身的高度随之更新

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """高度至少覆盖布局的最小高度，否则标签多的卡片会被下一行压住。"""
        hint = super().sizeHint()
        layout = self.layout()
        if layout is not None:
            hint.setHeight(max(hint.height(), layout.minimumSize().height()))
        return hint

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
    """列表视图中的一行。

    四个信息列都是固定宽度的单行标签（用户 m04164 第 2 条）：列宽由页面按本页内容量出后
    统一写给表头与每一行，内容超出显示区域时行会把滚动区的画布撑宽、由左右滑动看全。
    """

    activated = pyqtSignal(object)
    opened = pyqtSignal(object)
    menuRequested = pyqtSignal(object, object)
    checkedChanged = pyqtSignal(object, bool)

    def __init__(
        self, item: DataItem, parent: QWidget | None = None, view_size: str = DEFAULT_VIEW_SIZE
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._selected = False
        self._preset = view_size_preset(view_size)
        self.setMinimumHeight(self._preset.list_row_height)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.check_box = CheckBox(self)
        self.check_box.setToolTip(CHECK_TIP)
        self.check_box.stateChanged.connect(self._on_check_state)
        # 勾选框独占固定宽度的一格，表头用同样的空格子，列才对得齐
        self._check_cell = QWidget(self)
        self._check_cell.setFixedWidth(CHECK_CELL_WIDTH)
        cell_layout = QHBoxLayout(self._check_cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.addWidget(self.check_box)

        self._cover = CoverLabel(self, self._preset.list_cover)
        self._cover.set_item(item)
        self._title = BodyLabel(self)
        self._meta = CaptionLabel(self)
        self._tags = CaptionLabel(self)
        self._keywords = CaptionLabel(self)
        self._labels: dict[str, QLabel] = {
            "name": self._title,
            "meta": self._meta,
            "tags": self._tags,
            "keywords": self._keywords,
        }
        for flag in (Qt.WidgetAttribute.WA_TransparentForMouseEvents,):
            self._title.setAttribute(flag, True)
            self._meta.setAttribute(flag, True)
            self._tags.setAttribute(flag, True)
            self._keywords.setAttribute(flag, True)
            self._cover.setAttribute(flag, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*LIST_ROW_MARGINS)
        layout.setSpacing(LIST_SPACING)
        layout.addWidget(self._check_cell)
        layout.addWidget(self._cover)
        for key, _title, minimum in LIST_COLUMNS:
            label = self._labels[key]
            label.setFixedWidth(minimum)
            layout.addWidget(label)
        layout.addStretch(1)
        self.set_item(item)

    @property
    def item(self) -> DataItem:
        return self._item

    def set_item(self, item: DataItem) -> None:
        """就地换成另一条数据；文案一律写全，怎么显示交给列宽与左右滑动。"""
        self._item = item
        self._cover.set_item(item)
        tag_names = list(item.tag_names)
        keywords = [str(word) for word in (item.keywords or []) if str(word).strip()]
        # 名称也不省略：列宽会按全文长度量出来，看不全就左右滑动
        self._title.setText(item.name or "")
        self._title.setToolTip(item.name or "")
        self._meta.setText(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}"
        )
        self._tags.setText(" ".join(f"#{name}" for name in tag_names))
        self._tags.setToolTip("标签：" + ("、".join(tag_names) or "无"))
        self._keywords.setText("、".join(keywords))
        self._keywords.setToolTip("关键词：" + ("、".join(keywords) or "无"))

    def column_widths(self) -> dict[str, int]:
        """每一列需要多宽：按写全的文案量，并留出左右内边距。"""
        metrics = self.fontMetrics()
        widths: dict[str, int] = {}
        for key, label in self._labels.items():
            text = label.text() or ""
            widths[key] = max(label.sizeHint().width(), metrics.horizontalAdvance(text) + 12)
        return widths

    def apply_column_widths(self, widths: dict[str, int]) -> None:
        """接受页面统一量出的列宽（表头与每一行都用它，列才对得齐）。"""
        for key, label in self._labels.items():
            width = widths.get(key)
            if width:
                label.setFixedWidth(int(width))

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


class ListHeader(QWidget):
    """列表视图的表头（用户 m04164 第 2 条）。

    列结构与行完全一致（同样的边距、间距、勾选格与封面格宽度），列宽也由页面统一写给两边，
    所以表头文字正好落在对应列的上面。行在滚动区里可以左右拖动、表头不在滚动区里，
    于是由页面把横向滚动量转给 `set_offset()`，让表头内容做同样的位移、始终对齐。
    """

    def __init__(
        self, parent: QWidget | None = None, view_size: str = DEFAULT_VIEW_SIZE
    ) -> None:
        super().__init__(parent)
        self._preset = view_size_preset(view_size)
        self._offset = 0
        self.setFixedHeight(self._preset.list_row_height)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("listHeader")
        # 只用 id 选择器，避免样式（边框）继承到子控件上去
        self.setStyleSheet(
            "#listHeader { background: transparent;"
            " border-bottom: 1px solid rgba(128, 128, 128, 0.35); }"
        )
        self._content = QWidget(self)
        self._layout = QHBoxLayout(self._content)
        self._layout.setContentsMargins(*LIST_ROW_MARGINS)
        self._layout.setSpacing(LIST_SPACING)
        self._check_cell = QWidget(self._content)
        self._check_cell.setFixedWidth(CHECK_CELL_WIDTH)
        self._layout.addWidget(self._check_cell)
        self._cover_cell = QWidget(self._content)
        self._cover_cell.setFixedWidth(self._preset.list_cover)
        self._layout.addWidget(self._cover_cell)
        self._labels: dict[str, QLabel] = {}
        for key, title, minimum in LIST_COLUMNS:
            label = BodyLabel(title, self._content)
            label.setStyleSheet("font-weight: 600;")
            label.setFixedWidth(minimum)
            self._layout.addWidget(label)
            self._labels[key] = label
        self._layout.addStretch(1)

    def set_view_size(self, view_size: str) -> None:
        """跟随显示大小档位换封面格宽度与表头高度。"""
        self._preset = view_size_preset(view_size)
        self._cover_cell.setFixedWidth(self._preset.list_cover)
        self.setFixedHeight(self._preset.list_row_height)
        self._sync_content()

    def apply_widths(self, widths: dict[str, int]) -> None:
        """接受页面统一量出的列宽，与每一行保持一致。"""
        for key, label in self._labels.items():
            width = widths.get(key)
            if width:
                label.setFixedWidth(int(width))
        self._sync_content()

    def set_offset(self, offset: int) -> None:
        """横向滚动时把表头内容整体左移，与滚动区里的行同步。"""
        self._offset = max(0, int(offset))
        self._sync_content()

    def _sync_content(self) -> None:
        """内容按自身需要定宽（可能比表头宽），再把滚动偏移量平移过去。"""
        width = max(self.width(), self._content.sizeHint().width())
        self._content.setGeometry(-self._offset, 0, width, self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._sync_content()


__all__ = [
    "CHECK_TIP",
    "DEFAULT_VIEW_SIZE",
    "LIST_COLUMNS",
    "ListHeader",
    "VIEW_SIZE_KEYS",
    "VIEW_SIZE_LABELS",
    "VIEW_SIZE_PRESETS",
    "ViewSize",
    "CoverLabel",
    "ItemCard",
    "ItemListRow",
    "cover_source",
    "view_size_key",
    "view_size_preset",
]
