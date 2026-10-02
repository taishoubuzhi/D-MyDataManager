"""用户管理页：默认用户（管理员）管理其他用户，普通用户只能修改自己。

展示层为响应式卡片网格：每个用户一张卡片，当前用户卡片高亮并带「当前用户」徽标。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...services import LibraryService, UserService
from ..common import (
    DETAIL_MARGINS,
    accent_color,
    accent_name,
    confirm,
    format_datetime,
    clear_scroll_background,
    page_layout,
    release_widget,
    toast_success,
    toast_warning,
    page_header,
    section_card,
)
from ..dialogs import TextInputDialog

# 卡片网格参数：卡片固定宽度，窄窗口 1 列，宽窗口最多 4 列。
CARD_WIDTH = 320
CARD_MIN_WIDTH = 300
CARD_MAX_COLUMNS = 4
CARD_SPACING = 12

# 卡片内部结构：首字头像 + 两列操作按钮网格。
CARD_BUTTON_COLUMNS = 2
AVATAR_SIZE = 40
AVATAR_PLAIN_STYLE = (
    "background-color: rgba(128, 128, 128, 0.25); color: palette(text);"
    "border-radius: 8px; font-size: 18px; font-weight: 600;"
)
BADGE_PLAIN_STYLE = (
    "color: palette(text); background-color: rgba(128, 128, 128, 0.18);"
    "border-radius: 8px; padding: 1px 8px;"
)


def avatar_accent_style() -> str:
    """首字头像的强调色样式（跟随主题色）。"""
    return (
        f"background-color: {accent_name()}; color: white; border-radius: 8px;"
        "font-size: 18px; font-weight: 600;"
    )


def badge_accent_style() -> str:
    """「当前用户」徽标的强调色样式（跟随主题色）。"""
    return f"color: white; background-color: {accent_name()}; border-radius: 8px; padding: 1px 8px;"


def highlight_fill() -> QColor:
    """当前用户卡片的浅色填充（主题色 11% 不透明度）。"""
    color = accent_color()
    return QColor(color.red(), color.green(), color.blue(), 28)


def highlight_hover() -> QColor:
    """当前用户卡片悬停时的填充。"""
    color = accent_color()
    return QColor(color.red(), color.green(), color.blue(), 40)


# ---------------------------------------------------------------------- 纯逻辑
def grid_columns(
    available_width: int,
    *,
    card_width: int = CARD_MIN_WIDTH,
    spacing: int = CARD_SPACING,
    max_columns: int = CARD_MAX_COLUMNS,
) -> int:
    """按可用宽度计算卡片网格列数：至少 1 列，最多 max_columns 列。"""
    if available_width <= 0 or card_width <= 0:
        return 1
    columns = (int(available_width) + spacing) // (card_width + spacing)
    return max(1, min(int(max_columns), int(columns)))


def card_natural_width(cards, *, minimum: int = CARD_MIN_WIDTH) -> int:
    """卡片自然宽度：所有卡片「刚好容纳内容」所需宽度中的最大值。

    卡片已改为固定宽度（CARD_WIDTH），本函数保留用于自检与兼容。
    """
    widths = [int(card.minimumSizeHint().width()) for card in cards]
    return max([int(minimum), *widths])


def is_current_user(user_id: int, current_id: int) -> bool:
    """该用户是否为当前用户。"""
    return int(user_id) == int(current_id)


def card_badges(*, is_current: bool, is_default: bool, protected: bool) -> list[str]:
    """卡片徽标文本：当前用户 / 默认用户 / 已设口令。"""
    badges: list[str] = []
    if is_current:
        badges.append("当前用户")
    if is_default:
        badges.append("默认用户")
    if protected:
        badges.append("已设口令")
    return badges


def card_summary(*, item_count: int, category_count: int, created_at: dt.datetime | None) -> str:
    """卡片摘要：数据项数量、分类数量与创建时间。"""
    parts = [f"{int(item_count)} 项数据", f"{int(category_count)} 个分类"]
    created = format_datetime(created_at, "%Y-%m-%d %H:%M")
    if created:
        parts.append(f"创建于 {created}")
    return " · ".join(parts)


def card_permissions(
    *, is_current: bool, is_default: bool, protected: bool, is_admin: bool
) -> dict[str, bool]:
    """卡片按钮可见性：切换 / 重命名 / 口令 / 清除口令 / 删除。

    只能改自己或由默认用户操作；默认用户不可删除；当前用户也不能删除自己
    （删号后当前用户会被悄悄切换成默认用户，等于给自己提权），删除只对默认用户开放。
    """
    mine = bool(is_current)
    rename = bool(is_admin or mine)
    return {
        "switch": not mine,
        "rename": rename,
        "password": rename,
        "clear_password": rename and bool(protected),
        "delete": bool(is_admin) and not mine and not bool(is_default),
    }


# ---------------------------------------------------------------------- 卡片
class UserCard(CardWidget):
    """用户卡片：当前用户高亮描边并着色背景。"""

    _highlighted = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._highlighted = False
        self.user_id = 0
        self._info = None

    def set_highlighted(self, highlighted: bool) -> None:
        highlighted = bool(highlighted)
        if highlighted != self._highlighted:
            self._highlighted = highlighted
            self.update()

    def is_highlighted(self) -> bool:
        return self._highlighted

    def _normalBackgroundColor(self) -> QColor:  # noqa: N802 - Qt 命名
        if self._highlighted:
            return highlight_fill()
        return super()._normalBackgroundColor()

    def _hoverBackgroundColor(self) -> QColor:  # noqa: N802 - Qt 命名
        if self._highlighted:
            return highlight_hover()
        return super()._hoverBackgroundColor()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().paintEvent(event)
        if not self._highlighted:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(accent_color())
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -3, -3), 5, 5)


# ---------------------------------------------------------------------- 页面
class UserPage(QWidget):
    """用户管理：默认用户可以管理其他用户，其他用户只能修改自己。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("userPage")
        self.session = database.new_session()
        self.service = UserService(self.session)
        self._user_id = 0
        self._is_admin = False
        self._cards: list[UserCard] = []
        self._columns = 0

        root = page_layout(self)

        header = page_header(
            root, self, "用户", "每位用户拥有独立的数据、标签与存档，切换用户即可查看各自内容"
        )
        self.create_button = PrimaryPushButton(FluentIcon.ADD, "新建用户", self)
        self.create_button.setToolTip("新建一个独立用户，数据与其他用户互不影响")
        self.create_button.clicked.connect(self._create_user)
        header.addWidget(self.create_button)

        self.caption = CaptionLabel("", self)
        self.caption.setWordWrap(True)
        root.addWidget(self.caption)

        card, card_layout = section_card(
            self, "全部用户", "卡片上可直接切换用户、重命名、设置口令或删除；每人的数据互不影响", spacing=10
        )
        card.setMinimumHeight(420)
        self.scroll = ScrollArea(card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_host = QWidget(self.scroll)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(CARD_SPACING)
        self.grid.setVerticalSpacing(CARD_SPACING)
        self.scroll.setWidget(self.grid_host)
        clear_scroll_background(self.scroll)
        self.scroll.setObjectName("userScroll")
        self.grid_host.setObjectName("userGridHost")
        self.scroll.viewport().installEventFilter(self)
        card_layout.addWidget(self.scroll, 1)
        root.addWidget(card, 1)

        signalBus.userChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ 卡片网格
    def cards(self) -> list[UserCard]:
        """当前展示的卡片列表（按用户顺序）。"""
        return list(self._cards)

    def current_card(self) -> UserCard | None:
        """当前用户对应的卡片。"""
        for card in self._cards:
            if card.user_id == self._user_id:
                return card
        return None

    def refresh(self) -> None:
        self._user_id = self.service.current_id()
        self._is_admin = self.service.is_admin()
        self.create_button.setVisible(self._is_admin)
        self.caption.setText(
            "默认用户可以新建、重命名、删除任意用户，并为任意用户设置或清除口令；"
            "被删除用户的数据与标签会并入默认用户。"
            if self._is_admin
            else "当前用户可以修改自己的用户名与口令（口令用于解锁隐藏数据），"
            "也可以清除自己的口令或删除自己——数据与标签会并入默认用户。"
        )
        for card in self._cards:
            self.grid.removeWidget(card)
            release_widget(card)
        self._cards = []
        for info in self.service.list_users():
            card = self._user_card(info)
            self._cards.append(card)
        self._columns = 0
        self._equalize_card_heights()
        self._layout_cards(force=True)

    def _available_width(self) -> int:
        """网格可用宽度：优先取滚动区域视口宽度，减去左右留白。"""
        viewport = self.scroll.viewport().width() if hasattr(self, "scroll") else 0
        width = viewport or self.grid_host.width() or self.width()
        return max(CARD_MIN_WIDTH, int(width) - 24)

    def _natural_width(self) -> int:
        """卡片自然宽度（保留导出/自检用；网格已按固定宽度排布）。"""
        return card_natural_width(self._cards)

    def _layout_cards(self, *, force: bool = False) -> None:
        """按可用宽度排布固定尺寸卡片：卡片靠左，窗口变宽只增加列数。"""
        available = self._available_width()
        columns = grid_columns(available, card_width=CARD_WIDTH)
        columns = max(1, min(columns, len(self._cards) or 1))
        if not force and columns == self._columns and self.grid.count():
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)
        # 卡片列不伸缩；在其后追加一个占位伸缩列，让卡片左对齐且不被拉宽。
        for column in range(columns):
            self.grid.setColumnStretch(column, 0)
        self.grid.setColumnStretch(columns, 1)
        for column in range(columns + 1, CARD_MAX_COLUMNS + 2):
            self.grid.setColumnStretch(column, 0)
        # 行不伸缩：多余高度全部给末行占位，卡片始终从左上角开始排列。
        rows = (len(self._cards) + columns - 1) // columns
        for row in range(rows):
            self.grid.setRowStretch(row, 0)
        self.grid.setRowStretch(rows, 1)
        for row in range(rows + 1, CARD_MAX_COLUMNS + 2):
            self.grid.setRowStretch(row, 0)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        """视口尺寸变化时重排卡片，保证卡片始终随可用宽度自适应。"""
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._layout_cards()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._layout_cards()

    def _user_card(self, info) -> UserCard:
        card = UserCard(self.grid_host)
        card.user_id = int(info.user.id)
        card._info = info
        mine = is_current_user(info.user.id, self._user_id)
        card.set_highlighted(mine)
        # 固定尺寸卡片：宽度固定，高度按内容自适应。
        card.setFixedWidth(CARD_WIDTH)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(*DETAIL_MARGINS)
        layout.setSpacing(10)

        # ① 首字头像 + 用户名与徽标，② 摘要。
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self._avatar(info, mine), 0, Qt.AlignmentFlag.AlignTop)

        info_box = QVBoxLayout()
        info_box.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        name_label = StrongBodyLabel(info.name, card)
        name_label.setToolTip(info.name)
        title_row.addWidget(name_label)
        for badge in card_badges(
            is_current=mine, is_default=info.is_default, protected=info.protected
        ):
            label = CaptionLabel(badge, card)
            label.setStyleSheet(badge_accent_style() if badge == "当前用户" else BADGE_PLAIN_STYLE)
            title_row.addWidget(label)
        title_row.addStretch(1)
        info_box.addLayout(title_row)

        summary = BodyLabel(
            card_summary(
                item_count=info.item_count,
                category_count=info.category_count,
                created_at=info.user.created_at,
            ),
            card,
        )
        summary.setToolTip(summary.text())
        info_box.addWidget(summary)
        header.addLayout(info_box, 1)
        layout.addLayout(header)

        # ③ 操作区：两列按钮网格，保证 CARD_WIDTH 下按钮完整显示。
        allow = card_permissions(
            is_current=mine,
            is_default=info.is_default,
            protected=info.protected,
            is_admin=self._is_admin,
        )
        buttons: list[PushButton] = []

        switch_button = PushButton(
            FluentIcon.SYNC, "当前用户" if mine else "切换为当前用户", card
        )
        switch_button.setEnabled(allow["switch"])
        switch_button.setToolTip("这已经是当前用户" if mine else "把该用户切换为当前用户")
        if not mine:
            switch_button.clicked.connect(lambda _=False, item=info: self._switch_user(item))
        buttons.append(switch_button)
        card.switch_button = switch_button

        if allow["rename"]:
            rename_button = PushButton(FluentIcon.EDIT, "重命名", card)
            rename_button.clicked.connect(lambda _=False, item=info: self._rename_user(item))
            buttons.append(rename_button)

        if allow["password"]:
            password_button = PushButton(FluentIcon.FINGERPRINT, "口令", card)
            password_button.clicked.connect(lambda _=False, item=info: self._set_password(item))
            buttons.append(password_button)

        if allow["clear_password"]:
            clear_button = PushButton(FluentIcon.BROOM, "清除口令", card)
            clear_button.clicked.connect(lambda _=False, item=info: self._clear_password(item))
            buttons.append(clear_button)

        card.delete_button = None
        if allow["delete"]:
            delete_button = PushButton(FluentIcon.DELETE, "删除", card)
            delete_button.clicked.connect(lambda _=False, item=info: self._delete_user(item))
            buttons.append(delete_button)
            card.delete_button = delete_button
        elif mine and not info.is_default:
            # 当前用户不允许删除自己：按钮保留但禁用，说明要先切换用户。
            delete_button = PushButton(FluentIcon.DELETE, "删除", card)
            delete_button.setEnabled(False)
            delete_button.setToolTip("不能删除当前用户，请先切换到其他用户再删除")
            buttons.append(delete_button)
            card.delete_button = delete_button

        buttons_grid = QGridLayout()
        buttons_grid.setHorizontalSpacing(6)
        buttons_grid.setVerticalSpacing(6)
        for index, button in enumerate(buttons):
            buttons_grid.addWidget(
                button, index // CARD_BUTTON_COLUMNS, index % CARD_BUTTON_COLUMNS
            )
        for column in range(CARD_BUTTON_COLUMNS):
            buttons_grid.setColumnStretch(column, 1)
        layout.addLayout(buttons_grid)
        return card

    def _avatar(self, info, highlighted: bool) -> QLabel:
        """首字头像：圆角方块内的用户名首字。"""
        avatar = QLabel((info.name or "?")[:1], self)
        avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(avatar_accent_style() if highlighted else AVATAR_PLAIN_STYLE)
        return avatar

    def _equalize_card_heights(self) -> None:
        """所有卡片统一为最大内容高度，保证网格行列对齐。"""
        heights = [card.sizeHint().height() for card in self._cards]
        if not heights:
            return
        height = max(heights)
        for card in self._cards:
            card.setFixedHeight(height)

    # ------------------------------------------------------------------ 操作
    def _ask(self, title: str, placeholder: str = "", hint: str = "", text: str = "") -> str | None:
        dialog = TextInputDialog(title, placeholder, text=text, parent=self.window(), hint=hint)
        return dialog.value() if dialog.exec() else None

    def _create_user(self) -> None:
        name = self._ask("新建用户", "用户名", "每个用户拥有独立的数据项与分类，互不可见")
        if name is None:
            return
        if not name:
            toast_warning(self, "名称不能为空", "")
            return
        user = self.service.create(name)
        if user is None:
            toast_warning(self, "无法创建", "已存在同名用户")
            return
        self.session.commit()
        signalBus.userChanged.emit()
        signalBus.librariesChanged.emit()
        toast_success(self, "已创建用户", name)

    def _switch_user(self, info) -> None:
        if info.protected:
            password = self._ask("切换用户", f"请输入 {info.name} 的口令", "留空以取消")
            if password is None or not self.service.verify(info.user, password):
                toast_warning(self, "口令错误", f"无法切换到 {info.name}")
                return
        self.service.set_current(info.user)
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已切换用户", info.name)

    def _rename_user(self, info) -> None:
        mine = is_current_user(info.user.id, self._user_id)
        if not mine and not self._is_admin:
            toast_warning(self, "没有权限", "只有默认用户可以重命名其他用户")
            return
        name = self._ask("重命名用户", "新的用户名", text=info.name)
        if name is None:
            return
        old_name = info.name
        if not self.service.rename(info.user, name):
            toast_warning(self, "无法重命名", "名称为空或已存在同名用户")
            return
        LibraryService(self.session).rename_user_dir(info.user, old_name)
        self.session.commit()
        signalBus.userChanged.emit()
        signalBus.librariesChanged.emit()
        toast_success(self, "已重命名用户", f"{old_name} -> {info.user.name}")

    def _set_password(self, info) -> None:
        mine = is_current_user(info.user.id, self._user_id)
        if not mine and not self._is_admin:
            toast_warning(self, "没有权限", "只有默认用户可以修改其他用户的口令")
            return
        if mine and info.protected:
            password = self._ask("验证当前口令", f"请输入 {info.name} 当前的口令", "留空以取消")
            if password is None or not self.service.verify(info.user, password):
                toast_warning(self, "口令错误", "未能修改口令")
                return
        value = self._ask("设置用户口令", f"{info.name} 的口令（留空以清除）", "切换用户时需要输入")
        if value is None:
            return
        self.service.set_password(info.user, value)
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已更新口令", info.name if value else f"已清除 {info.name} 的口令")

    def _clear_password(self, info) -> None:
        """清除口令：默认用户可以清除任何已设口令的用户，其他用户只能清除自己的。"""
        mine = is_current_user(info.user.id, self._user_id)
        if not (self._is_admin or mine):
            toast_warning(self, "没有权限", "只有默认用户可以清除其他用户的口令")
            return
        if not info.protected:
            toast_warning(self, "无需清除", "该用户没有设置口令")
            return
        if not confirm(
            self,
            "清除口令",
            f"确定清除用户「{info.name}」的口令吗？\n"
            "清除后切换用户与解锁隐藏数据都不再需要口令。",
        ):
            return
        self.service.set_password(info.user, "")
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已清除口令", info.name)

    def _delete_user(self, info) -> None:
        if is_current_user(info.user.id, self._user_id):
            toast_warning(self, "无法删除", "不能删除当前用户，请先切换到其他用户再删除自己的账号")
            return
        if not self._is_admin:
            toast_warning(self, "没有权限", "只有默认用户可以删除其他用户")
            return
        if info.is_default:
            toast_warning(self, "无法删除", "默认用户不可删除")
            return
        if not confirm(
            self,
            "删除用户",
            f"确定删除用户「{info.name}」吗？\n"
            "该用户的数据文件、标签（同名标签会合并）、它创建的全局标签与存档条目都会转移到默认用户；\n"
            "它的分类会以用户名作为一级分类并入默认用户，没有数据时只清理其分类与用户文件夹。",
        ):
            return
        if not self.service.delete(info.user):
            toast_warning(self, "无法删除", "默认用户与当前用户不可删除，或找不到可接收数据的目标用户")
            return
        self.session.commit()
        for signal in (
            signalBus.userChanged,
            signalBus.librariesChanged,
            signalBus.itemsChanged,
            signalBus.categoriesChanged,
            signalBus.archivesChanged,
        ):
            signal.emit()
        toast_success(self, "已删除用户", f"{info.name} 的数据已并入默认用户")


__all__ = [
    "CARD_MAX_COLUMNS",
    "CARD_MIN_WIDTH",
    "CARD_SPACING",
    "CARD_WIDTH",
    "UserCard",
    "UserPage",
    "card_badges",
    "card_natural_width",
    "card_permissions",
    "card_summary",
    "grid_columns",
    "is_current_user",
]
