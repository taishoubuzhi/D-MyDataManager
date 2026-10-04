"""三态复选框清单：勾 = 全部拥有，横杠 = 部分拥有，空 = 全部没有。

批量管理标签与关键词共用：初始状态按所选数据算出来，用户改哪一行就以哪一行为准。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CheckBox, SingleDirectionScrollArea

from ..framework import clear_scroll_background

#: 横杠时的提示：初始状态可能就是这样，用户点一下应当直接变成「全有」。
_STATE_TIP = "勾 = 所选数据全部拥有，横杠 = 只有部分拥有，空 = 全都没有"
PREFERRED_HEIGHT = 186


class TriStateList(QWidget):
    """一列三态复选框；行名就是标签 / 关键词本身。"""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._boxes: dict[str, CheckBox] = {}
        self._labels: dict[str, str] = {}
        self._syncing = False

        self._host = QWidget(self)
        self._rows = QVBoxLayout(self._host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        self._rows.addStretch(1)

        self._scroll = SingleDirectionScrollArea(self)
        self._scroll.setWidget(self._host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(PREFERRED_HEIGHT)
        clear_scroll_background(self._scroll)

        self._empty = BodyLabel("暂无条目，在下面输入新的即可", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._scroll)
        layout.addWidget(self._empty)
        self._sync_empty()

    # ------------------------------------------------------------------ API
    def set_entries(self, entries: list[tuple[str, Qt.CheckState]]) -> None:
        """重建整个清单，顺序与传入一致。"""
        for box in self._boxes.values():
            self._rows.removeWidget(box)
            box.setParent(None)
            box.deleteLater()
        self._boxes.clear()
        self._labels.clear()
        for name, state in entries:
            self._append(name, state)
        self._sync_empty()

    def add_name(
        self, name: str, state: Qt.CheckState = Qt.CheckState.Checked, label: str | None = None
    ) -> bool:
        """新增一行；同名已存在时返回 False（调用方按「视为全选」处理）。"""
        name = str(name).strip()
        if not name:
            return False
        if name in self._boxes:
            return False
        self._append(name, state, label)
        self._sync_empty()
        return True

    def label_of(self, name: str) -> str:
        return self._labels.get(str(name).strip(), str(name).strip())

    def names(self) -> list[str]:
        return list(self._boxes)

    def has(self, name: str) -> bool:
        return str(name).strip() in self._boxes

    def state_of(self, name: str) -> Qt.CheckState:
        box = self._boxes.get(str(name).strip())
        return box.checkState() if box is not None else Qt.CheckState.Unchecked

    def set_state(self, name: str, state: Qt.CheckState) -> None:
        box = self._boxes.get(str(name).strip())
        if box is None:
            return
        self._syncing = True
        box.setCheckState(state)
        self._syncing = False

    def entries(self) -> list[tuple[str, Qt.CheckState]]:
        return [(name, box.checkState()) for name, box in self._boxes.items()]

    # --------------------------------------------------------------- 内部
    def _append(self, name: str, state: Qt.CheckState, label: str | None = None) -> None:
        box = CheckBox(label or name, self._host)
        box.setTristate(True)
        box.setToolTip(_STATE_TIP)
        self._syncing = True
        box.setCheckState(state)
        self._syncing = False
        box.stateChanged.connect(lambda _state, target=box: self._on_state(target))
        self._boxes[name] = box
        self._labels[name] = label or name
        self._rows.insertWidget(self._rows.count() - 1, box)

    def _on_state(self, box: CheckBox) -> None:
        if self._syncing:
            return
        if box.checkState() == Qt.CheckState.PartiallyChecked:
            # 用户点了一下带横杠的行：直接当成「全部拥有」，不要再绕一圈半选。
            self._syncing = True
            box.setCheckState(Qt.CheckState.Checked)
            self._syncing = False
        self.changed.emit()

    def _sync_empty(self) -> None:
        empty = not self._boxes
        self._scroll.setVisible(not empty)
        self._empty.setVisible(empty)


__all__ = ["PREFERRED_HEIGHT", "TriStateList"]
