"""分页控件：翻页、页码跳转、每页条数与选择摘要。

翻页区用流式布局排列，宽度不足时自动换行、绝不被裁切；`summary_label` 显示总数/选中数，
`hint_label` 在跨页多选时给出提示。分页文案与每页条数选项抽成模块级纯函数，便于单测。
"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel, ComboBox, FluentIcon, FlowLayout, SpinBox
from ..framework import IconTextButton

PAGE_SIZES = (50, 100, 200, 500)
DEFAULT_PAGE_SIZE = 50


def pages_of(total: int, page_size: int) -> int:
    """按每页条数计算总页数（至少 1 页）。"""
    size = max(1, int(page_size))
    return max(1, -(-max(0, int(total)) // size))


def normalize_page_size(size: int | None) -> int:
    """把每页条数收敛到可选档位：取相差最小的档位，越界与垃圾值都退回默认值。"""
    try:
        value = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return min(PAGE_SIZES, key=lambda option: (abs(option - value), option))


def page_size_options(sizes: Iterable[int] = PAGE_SIZES) -> list[tuple[int, str]]:
    """每页条数选项：(条数, 文案)，默认给出 50/100/200/500 四档。"""
    return [(int(size), f"每页 {int(size)} 条") for size in sizes]


def selection_summary(total: int, selected: int) -> str:
    """总数与选中数文案，例如「共 128 项 · 已选 3 项」。"""
    return f"共 {max(0, int(total))} 项 · 已选 {max(0, int(selected))} 项"


def selection_hint(selected: int, visible: int) -> str:
    """跨页多选提示：选中数超过当前页可见数时才返回文案，否则返回空串。"""
    count = max(0, int(selected))
    if count > max(0, int(visible)):
        return f"已跨页选择 {count} 项"
    return ""


class Pager(QWidget):
    """按页浏览：发出页码与每页条数变化，由页面重新取数。"""

    pageChanged = pyqtSignal(int)
    pageSizeChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        super().__init__(parent)
        self._total = 0
        self._page = 0
        self._page_size = normalize_page_size(page_size)
        self._selected = 0
        self._visible = 0
        self._updating = False

        self.first_button = IconTextButton(FluentIcon.LEFT_ARROW, "首页", self)
        self.prev_button = IconTextButton(FluentIcon.LEFT_ARROW, "上一页", self)
        self.next_button = IconTextButton(FluentIcon.RIGHT_ARROW, "下一页", self)
        self.last_button = IconTextButton(FluentIcon.RIGHT_ARROW, "末页", self)
        for button, tip in (
            (self.first_button, "跳到第一页"),
            (self.prev_button, "上一页"),
            (self.next_button, "下一页"),
            (self.last_button, "跳到最后一页"),
        ):
            button.setToolTip(tip)

        # 页码框按自身需要的宽度给足：写死宽度会把页码数字挤掉，只剩两个箭头
        self.page_box = SpinBox(self)
        self.page_box.setRange(1, 1)
        self.page_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_box.setMinimumWidth(self.page_box.sizeHint().width())
        self.page_box.setToolTip("输入页码快速跳转")
        self.page_label = CaptionLabel("/ 共 1 页", self)

        self.size_box = ComboBox(self)
        self.size_box.setToolTip("每页显示条数")
        for size, label in page_size_options():
            self.size_box.addItem(label, userData=size)
        index = self.size_box.findData(self._page_size)
        self.size_box.setCurrentIndex(index if index >= 0 else PAGE_SIZES.index(DEFAULT_PAGE_SIZE))

        self.summary_label = CaptionLabel("", self)
        self.hint_label = CaptionLabel("", self)
        self.hint_label.setToolTip("选择会跨页保留")
        self.hint_label.hide()

        self.page_prefix = CaptionLabel("第", self)

        # 首页 / 上一页 / 下一页 / 末页 等宽：换行时每一行看起来才整齐
        nav_buttons = (self.first_button, self.prev_button, self.next_button, self.last_button)
        nav_width = max(button.sizeHint().width() for button in nav_buttons)
        for button in nav_buttons:
            button.setMinimumWidth(nav_width)

        self.flow = FlowLayout(self, needAni=False, isTight=True)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(6)
        self.flow.setVerticalSpacing(4)
        for widget in (
            self.first_button,
            self.prev_button,
            self.page_prefix,
            self.page_box,
            self.page_label,
            self.next_button,
            self.last_button,
            self.size_box,
            self.summary_label,
            self.hint_label,
        ):
            self.flow.addWidget(widget)

        self.first_button.clicked.connect(lambda: self._go(0))
        self.prev_button.clicked.connect(lambda: self._go(self._page - 1))
        self.next_button.clicked.connect(lambda: self._go(self._page + 1))
        self.last_button.clicked.connect(lambda: self._go(self.pages - 1))
        self.page_box.valueChanged.connect(self._on_page_box)
        self.size_box.currentIndexChanged.connect(self._on_size_changed)
        self.set_state(0, 0, self._page_size)

    # ------------------------------------------------------------------ 状态
    @property
    def pages(self) -> int:
        return pages_of(self._total, self._page_size)

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def offset(self) -> int:
        return self._page * self._page_size

    def set_state(self, total: int, page: int, page_size: int | None = None) -> None:
        """同步显示状态（不触发信号）；page 会被裁剪到有效范围。"""
        self._total = max(0, int(total))
        if page_size is not None and page_size > 0:
            wanted = normalize_page_size(page_size)
            if wanted != self._page_size:
                self._page_size = wanted
                index = self.size_box.findData(wanted)
                if index >= 0:
                    self.size_box.blockSignals(True)
                    self.size_box.setCurrentIndex(index)
                    self.size_box.blockSignals(False)
        self._page = max(0, min(int(page), self.pages - 1))
        self._updating = True
        self.page_box.setRange(1, self.pages)
        self.page_box.setValue(self._page + 1)
        self.page_label.setText(f"/ 共 {self.pages} 页")
        self._updating = False
        self._update_buttons()
        self._sync_summary()

    def set_selection(self, selected: int, visible: int | None = None) -> None:
        """同步选中数（visible 为本页可见条数，用于判断是否跨页多选）。"""
        self._selected = max(0, int(selected))
        if visible is not None:
            self._visible = max(0, int(visible))
        self._sync_summary()

    # ------------------------------------------------------------------ 布局
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        """按当前宽度计算流式布局所需高度，换行后自动增高。"""
        needed = self.flow.heightForWidth(max(1, self.width()))
        if needed <= 0:
            needed = self.flow.sizeHint().height()
        if needed > 0 and self.height() != needed:
            self.setFixedHeight(needed)

    def _sync_summary(self) -> None:
        self.summary_label.setText(selection_summary(self._total, self._selected))
        hint = selection_hint(self._selected, self._visible)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self._fit_height()

    # ------------------------------------------------------------------ 交互
    def _go(self, page: int) -> None:
        if self._updating:
            return
        target = max(0, min(page, self.pages - 1))
        if target == self._page:
            self.set_state(self._total, self._page)
            return
        self.set_state(self._total, target)
        self.pageChanged.emit(self._page)

    def _on_page_box(self, value: int) -> None:
        self._go(value - 1)

    def _on_size_changed(self, index: int) -> None:
        if self._updating:
            return
        size = self.size_box.itemData(index)
        if not size or int(size) == self._page_size:
            return
        self._page_size = int(size)
        self.set_state(self._total, 0)
        self.pageSizeChanged.emit(self._page_size)

    def _update_buttons(self) -> None:
        first = self._page <= 0
        last = self._page >= self.pages - 1
        self.first_button.setEnabled(not first)
        self.prev_button.setEnabled(not first)
        self.next_button.setEnabled(not last)
        self.last_button.setEnabled(not last)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "PAGE_SIZES",
    "Pager",
    "normalize_page_size",
    "page_size_options",
    "pages_of",
    "selection_hint",
    "selection_summary",
]
