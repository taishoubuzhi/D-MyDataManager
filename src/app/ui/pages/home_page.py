"""首页：KPI 概览、快捷操作、最近导入与类型分布。

页面骨架由 `framework.ScrollPage` 提供（标题区 + 分区卡片 + 统一间距），
本模块只负责把统计数据填进卡片，并保留 `_total_card` 等既有属性名。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
)

from ...core.config import resources_root
from ...core.shell import reveal
from ...core.signals import signalBus
from ...db import database
from ...repositories import ArchiveRepository
from ...sdk import ExtensionPoint
from ...services import ArchiveService, UserService, overview, recent
from ..components.flow_area import FlowArea
from ..dialogs import TextInputDialog
from ..framework import (
    DETAIL_MARGINS,
    KPI_MARGINS,
    ROW_SPACING,
    ScrollPage,
    clear_layout,
    empty_state,
    format_datetime,
    format_size,
    type_icon,
    type_name,
)
from ..framework.contributions import icon_of, items, text_of, title_of, value_of
from ..framework import IconTextButton, IconTextPrimaryButton, icon_label

KPI_TITLES = ("数据总量", "占用空间", "今日导入", "用户数", "分类", "标签数", "存档数")

KPI_ICONS: dict[str, FluentIcon] = {
    "数据总量": FluentIcon.FOLDER,
    "占用空间": FluentIcon.SAVE,
    "今日导入": FluentIcon.ADD,
    "用户数": FluentIcon.PEOPLE,
    "分类": FluentIcon.LIBRARY,
    "标签数": FluentIcon.TAG,
    "存档数": FluentIcon.HISTORY,
}

#: 每个 KPI 卡片的悬停说明：鼠标停在卡片（含里面的数字与标题）上才显示
KPI_HINTS: dict[str, str] = {
    "数据总量": "当前用户的数据条数；副标题是今天新增的数量",
    "占用空间": "数据本身占用的空间；副标题是资源文件夹在磁盘上的实际占用",
    "今日导入": "今天导入的数据条数；副标题是最近 7 天的导入量",
    "用户数": "本机的用户数量；副标题是当前用户的分类数量",
    "分类": "当前用户的分类数量；副标题是标签数量",
    "标签数": "当前用户的标签数量；副标题是回收站里的数据条数",
    "存档数": "当前用户的存档数量；副标题是检测到的重复内容组数",
}


# ---------------------------------------------------------------------- 纯函数
def _count_pairs(rows: Mapping[str, int] | Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    pairs = rows.items() if isinstance(rows, Mapping) else rows
    return [(str(key), int(count)) for key, count in pairs if int(count) > 0]


def type_distribution(
    rows: Mapping[str, int] | Iterable[tuple[str, int]], total: int | None = None
) -> list[tuple[str, int, float]]:
    """把各类型计数整理为 (类型键, 数量, 占比)：过滤 0、按数量降序、占比夹在 0..1。"""
    counts = _count_pairs(rows)
    base = int(total) if total is not None and int(total) > 0 else sum(count for _key, count in counts)
    ordered = sorted(counts, key=lambda kv: (-kv[1], kv[0]))
    return [
        (key, count, min(1.0, max(0.0, (count / base) if base else 0.0)))
        for key, count in ordered
    ]


def format_summary(
    stats: Mapping[str, object], *, users: int = 0, archives: int = 0
) -> list[tuple[str, str, str]]:
    """把 overview() 的统计整理成 KPI 卡片：(标题, 数值, 副标题)。顺序与 KPI_TITLES 一致。"""
    storage = stats.get("storage") or {}
    disk_size = storage.get("disk_size") if isinstance(storage, Mapping) else None
    return [
        ("数据总量", str(int(stats.get("total", 0))), f"今日 +{int(stats.get('today', 0))}"),
        (
            "占用空间",
            format_size(stats.get("total_size")),
            f"实际占用 {format_size(disk_size)}",
        ),
        ("今日导入", str(int(stats.get("today", 0))), f"最近 7 天 +{int(stats.get('week', 0))}"),
        ("用户数", str(int(users)), f"分类 {int(stats.get('categories', 0))}"),
        ("分类", str(int(stats.get("categories", 0))), f"标签 {int(stats.get('tags', 0))}"),
        ("标签数", str(int(stats.get("tags", 0))), f"回收站 {int(stats.get('trashed', 0))}"),
        ("存档数", str(int(archives)), f"重复 {int(stats.get('duplicate_groups', 0))} 组"),
    ]


# ---------------------------------------------------------------------- 控件
class StatCard(CardWidget):
    """KPI 卡片：图标 + 大号数值 + 标题与副标题。"""

    def __init__(self, title: str, icon: FluentIcon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)

        icon_label = BodyLabel("", self)
        icon_label.setPixmap(icon.icon().pixmap(20, 20))

        caption = CaptionLabel(title, self)
        self._value = TitleLabel("0", self)
        self._sub = CaptionLabel("", self)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        title_row.addWidget(caption)
        title_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DETAIL_MARGINS)
        layout.setSpacing(2)
        layout.addWidget(icon_label)
        layout.addWidget(self._value)
        layout.addLayout(title_row)
        layout.addWidget(self._sub)

    def set_hint(self, text: str) -> None:
        """卡片说明：整张卡片都带提示，鼠标停上去就弹出来。"""
        self.setToolTip(text)

    def set_value(self, value: str, sub: str = "") -> None:
        self._value.setText(value)
        self._sub.setText(sub)


class _Row(CardWidget):
    """概览页的一行最近导入：点击可跳到数据管理页并选中该项。"""

    def __init__(self, item, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item_id = int(item.id)
        icon_label = BodyLabel("", self)
        icon_label.setPixmap(type_icon(item.type).icon().pixmap(16, 16))
        left = StrongBodyLabel(item.name, self)
        right = CaptionLabel(
            f"{type_name(item.type)} · {format_size(item.size)} · {format_datetime(item.created_at)}", self
        )
        for label in (icon_label, left, right):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*KPI_MARGINS)
        layout.setSpacing(8)
        layout.addWidget(icon_label)
        layout.addWidget(left)
        layout.addStretch(1)
        layout.addWidget(right)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击跳转到「数据管理」并选中该项")
        self.clicked.connect(self._focus)

    def _focus(self) -> None:
        signalBus.focusItem.emit(self._item_id)


class _TypeBar(QWidget):
    """类型分布中的一条横向占比条。"""

    def __init__(self, key: str, count: int, ratio: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("typeBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        name = BodyLabel(type_name(key), self)
        name.setFixedWidth(56)
        bar = ProgressBar(self)
        bar.setValue(int(round(max(0.0, min(1.0, ratio)) * 100)))
        bar.setToolTip(f"{type_name(key)}：{count} 项（{ratio * 100:.1f}%）")
        layout.addWidget(name, 0)
        layout.addWidget(bar, 1)
        layout.addWidget(CaptionLabel(str(count), self), 0)


class HomePage(ScrollPage):
    page_name = "homePage"
    page_title = "数据概览"
    page_subtitle = "所有数据都保存在本机，导入时会自动去重并记录特征"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = database.new_session()
        self.kpi_cards: list[StatCard] = []
        self.plugin_cards: list[StatCard] = []
        self._type_bars: list[_TypeBar] = []

        self.user_box = ComboBox(self.header)
        self.user_box.setMinimumWidth(150)
        self.user_box.setToolTip("切换当前用户；数据管理、标签、存档等页面会随之切换")
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        self.header.add_action(icon_label(FluentIcon.PEOPLE, "当前用户", self.header))
        self.header.add_action(self.user_box)

        cards_card, cards_body = self.add_section("概览", "当前用户的数据总量、占用空间与今日导入情况")
        self.cards_host = FlowArea(
            cards_card, adaptive=True, minimum_width=180, horizontal_spacing=12, vertical_spacing=12
        )
        cards_body.addWidget(self.cards_host)

        actions_card, actions_body = self.add_section(
            "快捷操作", "常用入口：导入数据、管理数据、打开资源文件夹与新建存档"
        )
        # 普通流式布局：按钮保持自己的宽度换行，不会被等分压窄到文字显示不全
        self.actions_host = FlowArea(actions_card, horizontal_spacing=8, vertical_spacing=8)
        import_button = IconTextPrimaryButton(FluentIcon.ADD, "导入数据", self.actions_host)
        import_button.setToolTip("导入文件或文件夹，自动去重并记录特征")
        import_button.clicked.connect(lambda: signalBus.requestImport.emit())
        manage_button = IconTextButton(FluentIcon.FOLDER, "数据管理", self.actions_host)
        manage_button.setToolTip("打开数据管理页，筛选、编辑与批量操作")
        manage_button.clicked.connect(lambda: signalBus.requestManage.emit())
        folder_button = IconTextButton(FluentIcon.FOLDER_ADD, "打开资源文件夹", self.actions_host)
        folder_button.setToolTip("在资源管理器中打开资源文件夹（库内容与数据库文件都在这里）")
        folder_button.clicked.connect(self._open_resource_folder)
        archive_button = IconTextButton(FluentIcon.HISTORY, "新建存档", self.actions_host)
        archive_button.setToolTip("为当前数据创建一份存档快照")
        archive_button.clicked.connect(self._create_archive)
        self.actions_host.add_widgets((import_button, manage_button, folder_button, archive_button))
        actions_body.addWidget(self.actions_host)

        recent_card, recent_body = self.add_section(
            "最近导入", "最近导入的数据，点击一行可直接定位到数据管理页"
        )
        self._recent_host = QWidget(recent_card)
        self._recent_layout = QVBoxLayout(self._recent_host)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(ROW_SPACING)
        recent_body.addWidget(self._recent_host)

        type_card, type_body = self.add_section("类型分布", "按文件类型统计当前用户的数据占比")
        self._type_host = QWidget(type_card)
        self._type_layout = QVBoxLayout(self._type_host)
        self._type_layout.setContentsMargins(0, 0, 0, 0)
        self._type_layout.setSpacing(ROW_SPACING)
        type_body.addWidget(self._type_host)

        self.add_stretch()
        self.auto_refresh(
            signalBus.itemsChanged,
            signalBus.categoriesChanged,
            signalBus.tagsChanged,
            signalBus.userChanged,
            signalBus.archivesChanged,
            signalBus.pluginsChanged,
        )
        self.refresh()

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        self._reload_users()
        user_id = UserService(self.session).current_id()
        stats = overview(self.session, user_id=user_id)
        users = len(UserService(self.session).list_users())
        archives = ArchiveRepository(self.session).count()

        entries = format_summary(stats, users=users, archives=archives)
        self._rebuild_cards(entries)
        self._sync_card_refs(entries)
        self._rebuild_plugin_cards()

        clear_layout(self._recent_layout)
        items = recent(self.session, user_id=user_id)
        if not items:
            self._recent_layout.addWidget(empty_state(self._recent_host, "还没有导入任何数据"))
        for item in items:
            self._recent_layout.addWidget(_Row(item, self._recent_host))

        self._rebuild_type_bars(type_distribution(stats["by_type"], stats["total"]))

    def _rebuild_cards(self, entries: list[tuple[str, str, str]]) -> None:
        for widget in self.cards_host.take_widgets():
            widget.deleteLater()
        self.kpi_cards = []
        for title, value, sub in entries:
            card = StatCard(title, KPI_ICONS.get(title, FluentIcon.LABEL), self.cards_host)
            card.set_hint(KPI_HINTS.get(title, ""))
            card.set_value(value, sub)
            self.cards_host.add_widget(card)
            self.kpi_cards.append(card)

    def _sync_card_refs(self, entries: list[tuple[str, str, str]]) -> None:
        """按标题定位 KPI 卡片，保持既有属性名（供烟测与外部引用）。"""
        by_title = {title: card for (title, _value, _sub), card in zip(entries, self.kpi_cards)}
        self._total_card = by_title["数据总量"]
        self._size_card = by_title["占用空间"]
        self._today_card = by_title["今日导入"]
        self._user_card = by_title["用户数"]
        self._category_card = by_title["分类"]
        self._tag_card = by_title["标签数"]
        self._archive_card = by_title["存档数"]

    def _rebuild_plugin_cards(self) -> None:
        """插件贡献的概览卡片（扩展点 app.ui.home.kpi）：值可以给回调，每次刷新重算。"""
        self.plugin_cards = []
        for item in items(ExtensionPoint.HOME_KPI):
            data = value_of(item)
            card = StatCard(title_of(item), icon_of(data.get("icon")), self.cards_host)
            # 插件也可以在卡片数据里给一句 hint，作为悬停说明
            card.set_hint(text_of(data.get("hint")))
            card.set_value(text_of(data.get("value")), text_of(data.get("sub")))
            self.cards_host.add_widget(card)
            self.plugin_cards.append(card)

    def _rebuild_type_bars(self, distribution: list[tuple[str, int, float]]) -> None:
        clear_layout(self._type_layout)
        self._type_bars = []
        if not distribution:
            self._type_layout.addWidget(empty_state(self._type_host, "还没有导入任何数据"))
            return
        for key, count, ratio in distribution:
            bar = _TypeBar(key, count, ratio, self._type_host)
            self._type_layout.addWidget(bar)
            self._type_bars.append(bar)

    # ------------------------------------------------------------------ 快捷操作
    def _reload_users(self) -> None:
        """按当前用户刷新右上角下拉（blockSignals 防止与 userChanged 形成回环）。"""
        service = UserService(self.session)
        current = service.current_id()
        self.user_box.blockSignals(True)
        self.user_box.clear()
        for info in service.list_users():
            self.user_box.addItem(info.name, userData=info.user.id)
        index = self.user_box.findData(current)
        self.user_box.setCurrentIndex(index if index >= 0 else 0)
        self.user_box.blockSignals(False)

    def _on_user_changed(self, index: int) -> None:
        service = UserService(self.session)
        user_id = self.user_box.itemData(index)
        if not user_id or user_id == service.current_id():
            return
        user = service.by_id(user_id)
        if user is None:
            return
        if user.password_hash:
            dialog = TextInputDialog(
                "切换用户", f"请输入 {user.name} 的口令", parent=self.window(), hint="留空以取消"
            )
            if not dialog.exec() or not service.verify(user, dialog.value()):
                self.toast_warning("口令错误", f"无法切换到 {user.name}")
                self._reload_users()
                return
        service.set_current(user)
        self.session.commit()
        signalBus.userChanged.emit()
        self.toast_success("已切换用户", user.name)

    def _open_resource_folder(self) -> None:
        path = resources_root()
        if not reveal(path):
            self.toast_error("无法打开资源文件夹", str(path))

    def _create_archive(self) -> None:
        if not self.confirm("新建存档", "将为当前数据创建一份存档快照，是否继续？"):
            return
        tip = self.busy("正在创建存档", "整理数据快照…")
        try:
            ArchiveService(self.session).create()
            self.session.commit()
        except Exception as error:  # noqa: BLE001
            self.session.rollback()
            tip.finish("存档失败")
            self.toast_error("创建存档失败", str(error))
            return
        tip.finish("存档完成")
        self.toast_success("已创建存档", "可在「存档」页查看与还原")
        signalBus.archivesChanged.emit()
        self.refresh()


__all__ = ["HomePage", "StatCard", "format_summary", "type_distribution"]
