"""分页控件：翻页、页码跳转与每页条数。"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QWidget
from PyQt6.QtCore import pyqtSignal
from qfluentwidgets import CaptionLabel, ComboBox, FluentIcon, PushButton, SpinBox

PAGE_SIZES = (50, 100, 200, 500)
DEFAULT_PAGE_SIZE = 100


class Pager(QWidget):
    """按页浏览：发出页码与每页条数变化，由页面重新取数。"""

    pageChanged = pyqtSignal(int)
    pageSizeChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        super().__init__(parent)
        self._total = 0
        self._page = 0
        self._page_size = page_size
        self._updating = False

        self.first_button = PushButton(FluentIcon.LEFT_ARROW, "首页", self)
        self.prev_button = PushButton(FluentIcon.LEFT_ARROW, "上一页", self)
        self.next_button = PushButton(FluentIcon.RIGHT_ARROW, "下一页", self)
        self.last_button = PushButton(FluentIcon.RIGHT_ARROW, "末页", self)
        self.page_box = SpinBox(self)
        self.page_box.setRange(1, 1)
        self.page_box.setFixedWidth(90)
        self.page_label = CaptionLabel("共 1 页", self)
        self.size_box = ComboBox(self)
        for size in PAGE_SIZES:
            self.size_box.addItem(f"每页 {size} 条", userData=size)
        index = self.size_box.findData(page_size)
        self.size_box.setCurrentIndex(index if index >= 0 else PAGE_SIZES.index(DEFAULT_PAGE_SIZE))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.first_button)
        layout.addWidget(self.prev_button)
        layout.addWidget(CaptionLabel("第", self))
        layout.addWidget(self.page_box)
        layout.addWidget(self.page_label)
        layout.addWidget(self.next_button)
        layout.addWidget(self.last_button)
        layout.addStretch(1)
        layout.addWidget(self.size_box)

        self.first_button.clicked.connect(lambda: self._go(0))
        self.prev_button.clicked.connect(lambda: self._go(self._page - 1))
        self.next_button.clicked.connect(lambda: self._go(self._page + 1))
        self.last_button.clicked.connect(lambda: self._go(self.pages - 1))
        self.page_box.valueChanged.connect(self._on_page_box)
        self.size_box.currentIndexChanged.connect(self._on_size_changed)
        self.set_state(0, 0, page_size)

    # ------------------------------------------------------------------ 状态
    @property
    def pages(self) -> int:
        return max(1, -(-self._total // self._page_size))

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
            self._page_size = int(page_size)
        self._page = max(0, min(int(page), self.pages - 1))
        self._updating = True
        self.page_box.setRange(1, self.pages)
        self.page_box.setValue(self._page + 1)
        self.page_label.setText(f"共 {self.pages} 页")
        self._updating = False
        self._update_buttons()

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


__all__ = ["DEFAULT_PAGE_SIZE", "PAGE_SIZES", "Pager"]
