"""首页：概览统计与最近导入。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    AdaptiveFlowLayout,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...services import UserService, overview, recent
from ..common import format_datetime, format_size, type_name


class StatCard(CardWidget):
    def __init__(self, title: str, icon: FluentIcon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(92)

        icon_label = BodyLabel("", self)
        icon_label.setPixmap(icon.icon().pixmap(20, 20))

        caption = CaptionLabel(title, self)
        self._value = TitleLabel("0", self)
        self._sub = CaptionLabel("", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        layout.addWidget(icon_label)
        layout.addWidget(self._value)
        layout.addWidget(caption)
        layout.addWidget(self._sub)

    def set_value(self, value: str, sub: str = "") -> None:
        self._value.setText(value)
        self._sub.setText(sub)


class HomePage(ScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self.session = database.new_session()

        host = QWidget(self)
        host.setObjectName("homeHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(18)

        title = TitleLabel("数据概览", host)
        subtitle = CaptionLabel("所有数据都保存在本机，导入时会自动去重并记录特征", host)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        cards_host = QWidget(host)
        cards = AdaptiveFlowLayout(cards_host, needAni=False, isTight=True)
        cards.setWidgetMinimumWidth(200)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self._total_card = StatCard("数据项", FluentIcon.FOLDER, cards_host)
        self._size_card = StatCard("占用空间", FluentIcon.SAVE, cards_host)
        self._category_card = StatCard("分类", FluentIcon.LIBRARY, cards_host)
        self._tag_card = StatCard("标签", FluentIcon.TAG, cards_host)
        for card in (self._total_card, self._size_card, self._category_card, self._tag_card):
            cards.addWidget(card)
        layout.addWidget(cards_host)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        import_button = PrimaryPushButton(FluentIcon.ADD, "导入数据", host)
        import_button.clicked.connect(lambda: signalBus.requestImport.emit())
        manage_button = PushButton(FluentIcon.FOLDER, "数据管理", host)
        manage_button.clicked.connect(lambda: signalBus.requestManage.emit())
        archive_button = PushButton(FluentIcon.HISTORY, "存档", host)
        archive_button.clicked.connect(lambda: signalBus.requestArchive.emit())
        actions.addWidget(import_button)
        actions.addWidget(manage_button)
        actions.addWidget(archive_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addWidget(SubtitleLabel("最近导入", host))
        self._recent_host = QWidget(host)
        self._recent_layout = QVBoxLayout(self._recent_host)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(6)
        layout.addWidget(self._recent_host)

        layout.addWidget(SubtitleLabel("类型分布", host))
        self._type_host = QWidget(host)
        self._type_layout = QVBoxLayout(self._type_host)
        self._type_layout.setContentsMargins(0, 0, 0, 0)
        self._type_layout.setSpacing(6)
        layout.addWidget(self._type_host)

        layout.addStretch(1)
        self.setWidget(host)
        self.setWidgetResizable(True)

        signalBus.itemsChanged.connect(self.refresh)
        signalBus.categoriesChanged.connect(self.refresh)
        signalBus.tagsChanged.connect(self.refresh)
        signalBus.userChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        user_id = UserService(self.session).current_id()
        stats = overview(self.session, user_id=user_id)
        storage = stats["storage"]
        self._total_card.set_value(str(stats["total"]), f"今日 +{stats['today']}")
        self._size_card.set_value(format_size(stats["total_size"]), f"实际占用 {format_size(storage['disk_size'])}")
        self._category_card.set_value(str(stats["categories"]), "")
        self._tag_card.set_value(str(stats["tags"]), f"回收站 {stats['trashed']}")

        self._clear(self._recent_layout)
        items = recent(self.session, user_id=user_id)
        if not items:
            self._recent_layout.addWidget(CaptionLabel("还没有导入任何数据", self._recent_host))
        for item in items:
            self._recent_layout.addWidget(_Row(item, self._recent_host))

        self._clear(self._type_layout)
        for row in _type_rows(stats):
            self._type_layout.addWidget(row)

    def _clear(self, layout) -> None:
        while layout.count():
            entry = layout.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()


class _Row(CardWidget):
    """概览页的一行最近导入：点击可跳到数据管理页并选中该项。"""

    def __init__(self, item, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item_id = int(item.id)
        left = StrongBodyLabel(item.name, self)
        right = CaptionLabel(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}", self
        )
        left.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        right.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.addWidget(left)
        layout.addStretch(1)
        layout.addWidget(right)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击跳转到「数据管理」并选中该项")
        self.clicked.connect(self._focus)

    def _focus(self) -> None:
        signalBus.focusItem.emit(self._item_id)


def _type_rows(stats: dict) -> list[QWidget]:
    from ..common import type_name

    from qfluentwidgets import ProgressBar

    rows: list[QWidget] = []
    total = max(1, stats["total"])
    for key, count in sorted(stats["by_type"].items(), key=lambda kv: kv[1], reverse=True):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(BodyLabel(type_name(key), row), 0)
        bar = ProgressBar(row)
        bar.setValue(int(count / total * 100))
        layout.addWidget(bar, 1)
        layout.addWidget(CaptionLabel(str(count), row), 0)
        rows.append(row)
    return rows


__all__ = ["HomePage"]
