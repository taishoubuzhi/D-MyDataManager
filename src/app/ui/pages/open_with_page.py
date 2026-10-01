"""打开方式设置页：为每个文件格式选择内置查看 / 继承系统默认 / 自定义程序。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    SubtitleLabel,
    TitleLabel,
)

from ...core.plugins import KIND_VIEWER
from ...core.signals import signalBus
from ...core.viewers import normalize_suffix, viewer_registry
from ...db import database
from ...repositories import ItemFilter
from ...services.item_service import ItemService
from ...services.open_with_service import (
    MODE_BUILTIN,
    MODE_CUSTOM,
    MODE_INHERIT,
    MODE_LABELS,
    open_with_service,
)
from ...services.user_service import UserService
from ..common import toast_success, toast_warning
from ..viewers.open_flow import open_path


class OpenWithPage(QWidget):
    """格式 → 打开方式 的配置页。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("openWithPage")
        self.session = database.new_session()
        self.service = open_with_service
        self.items = ItemService(self.session)
        self.users = UserService(self.session)
        self._suffixes: list[str] = []
        self._counts: dict[str, int] = {}
        self._current = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("打开方式", self))
        header.addStretch(1)
        plugin_button = PushButton(FluentIcon.APPLICATION, "管理打开方式插件", self)
        plugin_button.clicked.connect(self._on_manage_plugins)
        header.addWidget(plugin_button)
        refresh_button = PushButton(FluentIcon.SYNC, "刷新", self)
        refresh_button.clicked.connect(self._reload)
        header.addWidget(refresh_button)
        root.addLayout(header)

        root.addWidget(
            CaptionLabel("双击文件时用哪种方式打开，由这里的规则决定：内置查看器、继承系统默认程序，或指定自定义程序。", self)
        )
        root.addWidget(
            CaptionLabel(
                "没有内置查看器的格式只有「继承系统默认」与「自定义程序」两种选择；"
                "设为自定义但未指定程序时，打开文件会弹出系统的「打开方式」对话框。",
                self,
            )
        )

        body = QHBoxLayout()
        body.setSpacing(12)

        left = CardWidget(self)
        left.setFixedWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(SubtitleLabel("文件格式", left))
        self.search = SearchLineEdit(left)
        self.search.setPlaceholderText("搜索格式")
        self.search.textChanged.connect(self._fill_list)
        left_layout.addWidget(self.search)
        self.suffix_list = ListWidget(left)
        self.suffix_list.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.suffix_list, 1)
        self.count_label = CaptionLabel("", left)
        left_layout.addWidget(self.count_label)
        body.addWidget(left)

        right = CardWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(8)
        self.detail_title = SubtitleLabel("未选择格式", right)
        self.detail_meta = CaptionLabel("先在左侧选择一个文件格式", right)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_meta)

        mode_row = QHBoxLayout()
        mode_row.addWidget(CaptionLabel("打开方式", right))
        self.mode_box = ComboBox(right)
        self.mode_box.setFixedWidth(200)
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_box)
        mode_row.addStretch(1)
        right_layout.addLayout(mode_row)

        program_row = QHBoxLayout()
        program_row.addWidget(CaptionLabel("程序", right))
        self.program_edit = LineEdit(right)
        self.program_edit.setPlaceholderText("例如 C:\\Program Files\\App\\app.exe")
        program_row.addWidget(self.program_edit, 1)
        self.browse_button = PushButton(FluentIcon.FOLDER, "浏览", right)
        self.browse_button.clicked.connect(self._on_browse)
        program_row.addWidget(self.browse_button)
        right_layout.addLayout(program_row)

        args_row = QHBoxLayout()
        args_row.addWidget(CaptionLabel("参数", right))
        self.args_edit = LineEdit(right)
        self.args_edit.setPlaceholderText("可留空；用 {path} 表示文件位置，留空则把文件路径追加在最后")
        args_row.addWidget(self.args_edit, 1)
        right_layout.addLayout(args_row)

        self.hint_label = CaptionLabel("", right)
        self.hint_label.setWordWrap(True)
        right_layout.addWidget(self.hint_label)
        right_layout.addStretch(1)

        actions = QHBoxLayout()
        self.save_button = PrimaryPushButton(FluentIcon.SAVE, "保存", right)
        self.save_button.clicked.connect(self._on_save)
        actions.addWidget(self.save_button)
        self.reset_button = PushButton(FluentIcon.SYNC, "恢复默认", right)
        self.reset_button.clicked.connect(self._on_reset)
        actions.addWidget(self.reset_button)
        self.test_button = PushButton(FluentIcon.VIEW, "测试打开", right)
        self.test_button.clicked.connect(self._on_test)
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        right_layout.addLayout(actions)
        body.addWidget(right, 1)

        root.addLayout(body, 1)

        signalBus.openWithChanged.connect(self._reload)
        signalBus.pluginsChanged.connect(self._reload)
        self._reload()

    # ------------------------------------------------------------------ 数据
    def _collect_suffixes(self) -> list[str]:
        self._counts = self.items.extensions_in_use(self.users.current_id())
        found = set(viewer_registry.extensions())
        found |= set(self.service.rules())
        found |= set(self._counts)
        return sorted(suffix for suffix in found if suffix)

    def _builtin_name(self, suffix: str) -> str:
        viewer = viewer_registry.for_suffix(suffix)
        return viewer.name if viewer is not None else ""

    def _mode_of(self, suffix: str) -> str:
        rule = self.service.rule_for(suffix)
        if rule is not None and rule.mode:
            return rule.mode
        return MODE_BUILTIN if self.service.has_builtin(suffix) else MODE_INHERIT

    def _state_text(self, suffix: str) -> str:
        mode = self._mode_of(suffix)
        if mode == MODE_BUILTIN:
            name = self._builtin_name(suffix)
            return f"内置查看器（{name}）" if name else "内置查看器"
        if mode == MODE_CUSTOM:
            rule = self.service.rule_for(suffix)
            program = (rule.program if rule else "") or "未指定程序"
            return f"自定义程序（{Path(program).name}）"
        return "继承系统默认"

    def _reload(self) -> None:
        self._suffixes = self._collect_suffixes()
        self._fill_list()

    def _fill_list(self) -> None:
        keyword = self.search.text().strip().lower().lstrip(".")
        self.suffix_list.blockSignals(True)
        self.suffix_list.clear()
        shown = 0
        for suffix in self._suffixes:
            name = self._builtin_name(suffix)
            haystack = f"{suffix} {name}".lower()
            if keyword and keyword not in haystack:
                continue
            count = self._counts.get(suffix, 0)
            extra = f" · 库中 {count} 项" if count else ""
            self.suffix_list.addItem(QListWidgetItem(f".{suffix} — {self._state_text(suffix)}{extra}"))
            self.suffix_list.item(shown).setData(Qt.ItemDataRole.UserRole, suffix)
            shown += 1
        self.suffix_list.blockSignals(False)
        self.count_label.setText(f"{shown} / {len(self._suffixes)} 个格式")
        if self._suffixes:
            self.suffix_list.setCurrentRow(0)
        else:
            self._current = ""
            self.detail_title.setText("没有可配置的格式")
            self.detail_meta.setText("导入数据后这里会列出出现过的文件格式")

    # ------------------------------------------------------------------ 交互
    def _on_select(self, row: int) -> None:
        item = self.suffix_list.item(row)
        if item is None:
            return
        suffix = item.data(Qt.ItemDataRole.UserRole)
        if not suffix:
            return
        self._current = normalize_suffix(suffix)
        rule = self.service.rule_for(self._current)
        self.detail_title.setText(f".{self._current}")
        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for mode in self.service.available_modes(self._current):
            self.mode_box.addItem(MODE_LABELS.get(mode, mode), userData=mode)
        index = self.mode_box.findData(rule.mode if rule and rule.mode else self._mode_of(self._current))
        self.mode_box.setCurrentIndex(max(0, index))
        self.mode_box.blockSignals(False)
        self.program_edit.setText(rule.program if rule else "")
        self.args_edit.setText(rule.args if rule else "")
        self._on_mode_changed()

    def _on_mode_changed(self, *_args) -> None:
        mode = self.mode_box.currentData() or MODE_INHERIT
        custom = mode == MODE_CUSTOM
        self.program_edit.setEnabled(custom)
        self.browse_button.setEnabled(custom)
        self.args_edit.setEnabled(custom)
        name = self._builtin_name(self._current)
        if mode == MODE_BUILTIN and name:
            hint = f"使用内置的「{name}」在程序内显示。"
        elif mode == MODE_CUSTOM:
            hint = "保存后由指定程序打开；程序留空时改为弹出系统的「打开方式」对话框。"
        else:
            hint = "交给系统默认程序打开（与在资源管理器中双击一致）。"
        count = self._counts.get(self._current, 0)
        if count:
            hint += f" 库中现有 {count} 项该格式的数据。"
        self.hint_label.setText(hint)
        self.detail_meta.setText(self._state_text(self._current) if self._current else "")

    def _on_browse(self) -> None:
        current = self.program_edit.text().strip()
        start = str(Path(current).parent) if current else ""
        path, _filter = QFileDialog.getOpenFileName(self, "选择程序", start, "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self.program_edit.setText(path)

    def _on_save(self) -> None:
        if not self._current:
            toast_warning(self, "未选择格式", "请先在左侧选择一个文件格式")
            return
        mode = self.mode_box.currentData() or MODE_INHERIT
        program = self.program_edit.text().strip() if mode == MODE_CUSTOM else ""
        args = self.args_edit.text().strip() if mode == MODE_CUSTOM else ""
        self.service.set_rule(self._current, mode, program, args)
        signalBus.openWithChanged.emit()
        toast_success(self, "已保存打开方式", f".{self._current} → {MODE_LABELS.get(mode, mode)}")

    def _on_reset(self) -> None:
        if not self._current:
            return
        self.service.set_rule(self._current, "")
        signalBus.openWithChanged.emit()
        toast_success(self, "已恢复默认", f".{self._current} 不再有单独规则")

    def _sample_path(self, suffix: str) -> Path | None:
        for item in self.items.items.query(ItemFilter(include_hidden=True)):
            path = self.items.file_path_of(item)
            if path is not None and path.suffix.lower().lstrip(".") == suffix:
                return path
        return None

    def _on_test(self) -> None:
        if not self._current:
            return
        sample = self._sample_path(self._current)
        if sample is None:
            toast_warning(self, "没有可测试的文件", f"库里还没有 .{self._current} 格式的数据")
            return
        ok, message = open_path(sample, self.window())
        if ok:
            toast_success(self, "已打开", f"{sample.name} · {message}")
        else:
            toast_warning(self, "打开失败", message)

    def _on_manage_plugins(self) -> None:
        signalBus.requestPlugins.emit(KIND_VIEWER)
