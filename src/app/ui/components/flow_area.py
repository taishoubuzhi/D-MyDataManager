"""自适应高度的流式容器：按当前宽度锁住换行后的高度。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QTimer
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import AdaptiveFlowLayout, FlowLayout


class FlowArea(QWidget):
    """把流式布局包进普通容器，并按当前宽度锁住自身高度。

    直接把 FlowLayout 放进普通容器时有两类问题：

    1. 父布局只询问 sizeHint（不含换行后的高度），容器容易被压成 0 高，内容看起来消失了；
    2. 新增控件不会改变容器尺寸，父布局因此不会重跑，新控件停留在默认位置盖住第一个。

    因此这里自行承担高度与内部几何：加入控件后立即试算一次，并在事件循环稳定后再算一次。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        adaptive: bool = False,
        minimum_width: int = 180,
        horizontal_spacing: int = 8,
        vertical_spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        if adaptive:
            flow: FlowLayout = AdaptiveFlowLayout(self, needAni=False, isTight=True)
            flow.setWidgetMinimumWidth(minimum_width)
        else:
            flow = FlowLayout(self, needAni=False, isTight=True)
        flow.setContentsMargins(0, 0, 0, 0)
        flow.setHorizontalSpacing(horizontal_spacing)
        flow.setVerticalSpacing(vertical_spacing)
        self._flow = flow
        self._syncing = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    # ------------------------------------------------------------------ 内容
    def flow(self) -> FlowLayout:
        """暴露内部流式布局，便于调用方调整间距或列宽策略。"""
        return self._flow

    def widgets(self) -> list[QWidget]:
        return [item.widget() for item in self._flow_items() if item.widget() is not None]

    def add_widget(self, widget: QWidget) -> None:
        self._flow.addWidget(widget)
        self._sync_soon()

    def add_widgets(self, widgets) -> None:
        for widget in widgets:
            self._flow.addWidget(widget)
        self._sync_soon()

    def take_widgets(self) -> list[QWidget]:
        """摘出全部控件并返回，交由调用方重新安置或释放。"""
        widgets: list[QWidget] = []
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if hasattr(item, "widget") else item
            if widget is not None:
                widgets.append(widget)
        self._sync_soon()
        return widgets

    def clear_widgets(self) -> None:
        for widget in self.take_widgets():
            widget.setParent(None)
            widget.deleteLater()

    # ------------------------------------------------------------------ 高度
    def _flow_items(self) -> list:
        return [self._flow.itemAt(index) for index in range(self._flow.count())]

    def _sync_soon(self) -> None:
        """立即试算一次，并让事件循环在布局稳定后再算一次。"""
        self.sync_height()
        QTimer.singleShot(0, self.sync_height)

    def sync_height(self) -> None:
        """按当前宽度重算换行后的高度并锁定；不可见或宽度未知时保持原高度。"""
        if self._syncing or not self.isVisible():
            return
        width = self.width()
        if width <= 0:
            return
        self._syncing = True
        try:
            height = self._flow.heightForWidth(width)
            if height <= 0:
                height = self._flow.sizeHint().height()
            height = max(height, 0)
            if height != self.height():
                self.setFixedHeight(height)
            self._flow.setGeometry(QRect(0, 0, width, max(height, 1)))
            self.updateGeometry()
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 事件
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_soon()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.sync_height()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:
            self.sync_height()
        return super().event(event)


__all__ = ["FlowArea"]
