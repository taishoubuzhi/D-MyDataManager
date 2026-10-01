"""关键词输入：以标签块（chip）的形式收集自由词。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, FlowLayout, FluentIcon, LineEdit, ToolButton, ToolTipFilter

from ..common import accent_color


def _chip_qss() -> str:
    """关键词块的样式：底色与描边跟随主题强调色。"""
    color = accent_color()
    fill = f"rgba({color.red()}, {color.green()}, {color.blue()}, 0.12)"
    line = f"rgba({color.red()}, {color.green()}, {color.blue()}, 0.35)"
    return f"""
#keywordChip {{
    background: {fill};
    border: 1px solid {line};
    border-radius: 10px;
}}
#keywordChip QLabel {{ background: transparent; border: none; }}
"""


class KeywordChip(QFrame):
    """单个关键词 / 标签块，右侧带移除按钮。"""

    removeRequested = pyqtSignal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setObjectName("keywordChip")
        self.setStyleSheet(_chip_qss())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(2)

        label = BodyLabel(text, self)
        layout.addWidget(label)

        button = ToolButton(FluentIcon.CLOSE, self)
        button.setFixedSize(18, 18)
        button.setIconSize(button.iconSize() * 0.6)
        button.setToolTip("移除该关键词")
        button.installEventFilter(ToolTipFilter(button))
        button.clicked.connect(lambda: self.removeRequested.emit(self._text))
        layout.addWidget(button)


class ChipArea(QWidget):
    """流式排列标签块的容器，按当前宽度锁住自身高度。

    直接把 FlowLayout 放进普通容器时，父布局只会询问 sizeHint（不含换行后的高度），
    容器经常被压成 0 高：标签块其实已经加入，但用户看不到，像是"回车没反应"。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, needAni=False, isTight=True)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(0)

    def add_widget(self, widget: QWidget) -> None:
        self._flow.addWidget(widget)
        self._sync_soon()

    def clear_widgets(self) -> None:
        self._flow.takeAllWidgets()
        self._sync_soon()

    def _sync_soon(self) -> None:
        """立即试算一次，并让事件循环在布局稳定后再算一次。"""
        self.sync_height()
        QTimer.singleShot(0, self.sync_height)

    def sync_height(self) -> None:
        """按当前宽度重算换行后的高度并锁定，宽度为 0（尚未布局）时保持 0。"""
        width = self.width()
        height = self._flow.heightForWidth(width) if width > 0 else 0
        if height != self.height():
            self.setFixedHeight(height)
        # 新增标签块不会改变容器尺寸，父布局因此不会重跑，新块会停留在默认位置盖住第一个。
        self._flow.setGeometry(QRect(0, 0, width, max(height, 1)))
        self.updateGeometry()

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


class KeywordInput(QWidget):
    """带标签块展示的关键词输入控件。"""

    changed = pyqtSignal()

    def __init__(self, placeholder: str = "输入关键词后回车添加", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keywords: list[str] = []

        self._area = ChipArea(self)

        self._edit = LineEdit(self)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setClearButtonEnabled(True)
        self._edit.returnPressed.connect(self._on_submit)

        self._edit_row = QHBoxLayout()
        self._edit_row.setContentsMargins(0, 0, 0, 0)
        self._edit_row.setSpacing(6)
        self._edit_row.addWidget(self._edit, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._area)
        layout.addLayout(self._edit_row)

    # ------------------------------------------------------------------ API
    def keywords(self) -> list[str]:
        return list(self._keywords)

    def set_keywords(self, keywords: list[str] | None) -> None:
        self._keywords = []
        self._rebuild()
        for word in keywords or []:
            self._append(str(word))

    def clear(self) -> None:
        self._keywords = []
        self._edit.clear()
        self._rebuild()

    def chips_height(self) -> int:
        """标签块区域的高度；为 0 说明标签块不可见。"""
        return self._area.height()

    def add_trailing_widget(self, widget: QWidget) -> None:
        """在输入框右侧追加控件（例如"选择已有标签"按钮）。"""
        self._edit_row.addWidget(widget)

    def set_placeholder(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

    # --------------------------------------------------------------- 内部
    def _on_submit(self) -> None:
        text = self._edit.text().strip()
        if not text:
            return
        self._edit.clear()
        for part in text.replace("，", ",").split(","):
            self._append(part.strip())
        self.changed.emit()

    def _append(self, word: str) -> None:
        word = word.strip()
        if not word or word in self._keywords:
            return
        self._keywords.append(word)
        chip = KeywordChip(word, self._area)
        chip.removeRequested.connect(self._remove)
        self._area.add_widget(chip)

    def _remove(self, word: str) -> None:
        if word in self._keywords:
            self._keywords.remove(word)
        self._rebuild()
        self.changed.emit()

    def _rebuild(self) -> None:
        self._area.clear_widgets()
        for word in self._keywords:
            chip = KeywordChip(word, self._area)
            chip.removeRequested.connect(self._remove)
            self._area.add_widget(chip)


__all__ = ["ChipArea", "KeywordChip", "KeywordInput"]
