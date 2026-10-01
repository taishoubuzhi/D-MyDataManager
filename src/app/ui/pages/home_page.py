"""首页：KPI 概览、快捷操作、最近导入与类型分布。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    AdaptiveFlowLayout,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from ...core.shell import reveal
from ...core.signals import signalBus
from ...db import database
from ...repositories import ArchiveRepository
from ...services import ArchiveService, LibraryService, UserService, overview, recent
from ..common import (
    BusyTip as BusyTipWidget,
    confirm,
    format_datetime,
    format_size,
    release_widget,
    toast_error,
    toast_success,
    type_icon,
    type_name,
)

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
        layout.setContentsMargins(14, 8, 14, 8)
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


class HomePage(ScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self.session = database.new_session()
        self.kpi_cards: list[StatCard] = []
        self._type_bars: list[_TypeBar] = []

        host = QWidget(self)
        host.setObjectName("homeHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("数据概览", host))
        layout.addWidget(CaptionLabel("所有数据都保存在本机，导入时会自动去重并记录特征", host))

        cards_host = QWidget(host)
        self._cards_layout = AdaptiveFlowLayout(cards_host, needAni=False, isTight=True)
        self._cards_layout.setWidgetMinimumWidth(180)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setHorizontalSpacing(12)
        self._cards_layout.setVerticalSpacing(12)
        layout.addWidget(cards_host)

        layout.addWidget(SubtitleLabel("快捷操作", host))
        actions_host = QWidget(host)
        actions = AdaptiveFlowLayout(actions_host, needAni=False, isTight=True)
        actions.setWidgetMinimumWidth(120)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        import_button = PrimaryPushButton(FluentIcon.ADD, "导入数据", actions_host)
        import_button.setToolTip("导入文件或文件夹，自动去重并记录特征")
        import_button.clicked.connect(lambda: signalBus.requestImport.emit())
        manage_button = PushButton(FluentIcon.FOLDER, "数据管理", actions_host)
        manage_button.setToolTip("打开数据管理页，筛选、编辑与批量操作")
        manage_button.clicked.connect(lambda: signalBus.requestManage.emit())
        folder_button = PushButton(FluentIcon.FOLDER_ADD, "打开库文件夹", actions_host)
        folder_button.setToolTip("在资源管理器中打开默认库文件夹")
        folder_button.clicked.connect(self._open_library_folder)
        archive_button = PushButton(FluentIcon.HISTORY, "新建存档", actions_host)
        archive_button.setToolTip("为当前数据创建一份存档快照")
        archive_button.clicked.connect(self._create_archive)
        for button in (import_button, manage_button, folder_button, archive_button):
            actions.addWidget(button)
        layout.addWidget(actions_host)

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
        signalBus.archivesChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        user_id = UserService(self.session).current_id()
        stats = overview(self.session, user_id=user_id)
        users = len(UserService(self.session).list_users())
        archives = ArchiveRepository(self.session).count()

        entries = format_summary(stats, users=users, archives=archives)
        self._rebuild_cards(entries)
        self._sync_card_refs(entries)

        self._clear(self._recent_layout)
        items = recent(self.session, user_id=user_id)
        if not items:
            self._recent_layout.addWidget(CaptionLabel("还没有导入任何数据", self._recent_host))
        for item in items:
            self._recent_layout.addWidget(_Row(item, self._recent_host))

        self._rebuild_type_bars(type_distribution(stats["by_type"], stats["total"]))

    def _rebuild_cards(self, entries: list[tuple[str, str, str]]) -> None:
        self._clear(self._cards_layout)
        self.kpi_cards = []
        host = self._cards_layout.parentWidget()
        for title, value, sub in entries:
            card = StatCard(title, KPI_ICONS.get(title, FluentIcon.LABEL), host)
            card.set_value(value, sub)
            self._cards_layout.addWidget(card)
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

    def _rebuild_type_bars(self, distribution: list[tuple[str, int, float]]) -> None:
        self._clear(self._type_layout)
        self._type_bars = []
        if not distribution:
            self._type_layout.addWidget(CaptionLabel("还没有导入任何数据", self._type_host))
            return
        for key, count, ratio in distribution:
            bar = _TypeBar(key, count, ratio, self._type_host)
            self._type_layout.addWidget(bar)
            self._type_bars.append(bar)

    # ------------------------------------------------------------------ 快捷操作
    def _open_library_folder(self) -> None:
        path = LibraryService(self.session).ensure_default().path
        if not reveal(path):
            toast_error(self, "无法打开库文件夹", str(path))

    def _create_archive(self) -> None:
        if not confirm(self, "新建存档", "将为当前数据创建一份存档快照，是否继续？"):
            return
        tip = BusyTipWidget(self, "正在创建存档", "整理数据快照…")
        try:
            ArchiveService(self.session).create()
            self.session.commit()
        except Exception as error:  # noqa: BLE001
            self.session.rollback()
            tip.finish("存档失败")
            toast_error(self, "创建存档失败", str(error))
            return
        tip.finish("存档完成")
        toast_success(self, "已创建存档", "可在「存档」页查看与还原")
        signalBus.archivesChanged.emit()
        self.refresh()

    def _clear(self, layout) -> None:
        while layout.count():
            entry = layout.takeAt(0)
            widget = entry.widget() if hasattr(entry, "widget") else entry
            if widget is not None:
                release_widget(widget)


__all__ = ["HomePage", "StatCard", "format_summary", "type_distribution"]
