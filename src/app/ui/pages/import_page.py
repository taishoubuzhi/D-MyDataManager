"""数据导入页：文本 / 文件两种模式。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    SwitchButton,
    TextEdit,
    TitleLabel,
)

from loguru import logger

from ...core.config import config
from ...core.signals import signalBus
from ...db import database
from ...repositories import TagRepository
from ...services import ArchiveService, ImportService, LibraryService, TaxonomyService, UserService
from ..common import BusyTip, elide, toast_error, toast_success, toast_warning, type_name
from ..widgets.drop_area import DropArea
from ..widgets.keyword_input import KeywordInput
from ..widgets.tag_picker import TagPicker


class ImportPage(ScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("importPage")
        self.session = database.new_session()
        self._files: list[str] = []
        self._directory: str | None = None

        host = QWidget(self)
        host.setObjectName("importHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("导入数据", host))
        layout.addWidget(CaptionLabel("文件会按内容去重后保存到本机仓库，导入后可在数据管理中查看", host))

        layout.addWidget(self._build_mode_card(host))
        layout.addWidget(self._build_form_card(host))
        layout.addStretch(1)
        self.setWidget(host)
        self.setWidgetResizable(True)

        self._reload_categories()
        self._reload_tags()
        signalBus.categoriesChanged.connect(self._reload_categories)
        signalBus.tagsChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_categories)

    # ------------------------------------------------------------------ 界面
    def _build_mode_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        title = StrongBodyLabel("数据来源", card)
        layout.addWidget(title)

        self.mode = SegmentedWidget(card)
        self.mode.addItem("text", "文本", onClick=lambda: self._set_mode("text"))
        self.mode.addItem("file", "文件 / 文件夹", onClick=lambda: self._set_mode("file"))
        self.mode.setCurrentItem("text")
        layout.addWidget(self.mode)

        self.text_edit = TextEdit(card)
        self.text_edit.setPlaceholderText("在这里粘贴或输入文本内容……")
        self.text_edit.setMinimumHeight(180)
        self.text_edit.hide()

        self.drop_area = DropArea(card)
        self.drop_area.filesSelected.connect(self._on_files)
        self.drop_area.directorySelected.connect(self._on_directory)
        self.drop_area.hide()

        self.source_hint = CaptionLabel("当前为文本模式", card)
        layout.addWidget(self.source_hint)
        layout.addWidget(self.text_edit)
        layout.addWidget(self.drop_area)

        self._file_buttons = QWidget(card)
        buttons = QHBoxLayout(self._file_buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        pick_files = PushButton(FluentIcon.FOLDER_ADD, "选择文件", self._file_buttons)
        pick_files.clicked.connect(self.drop_area.browse)
        pick_folder = PushButton(FluentIcon.FOLDER, "选择文件夹", self._file_buttons)
        pick_folder.clicked.connect(self.drop_area.browse_directory)
        clear = PushButton("清空选择", self._file_buttons)
        clear.clicked.connect(self._clear_sources)
        buttons.addWidget(pick_files)
        buttons.addWidget(pick_folder)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        self._file_buttons.hide()
        layout.addWidget(self._file_buttons)
        return card

    def _build_form_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(12)
        outer.addWidget(StrongBodyLabel("数据信息", card))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.name_edit = LineEdit(card)
        self.name_edit.setPlaceholderText("留空则使用文件名或当前时间")
        self.category_box = ComboBox(card)
        self.tag_input = TagPicker([], "输入标签后回车，或点右侧按钮选择已有标签", card)
        self.keyword_input = KeywordInput("输入关键词后回车", card)

        self.hidden_switch = SwitchButton(card)
        self.name_by_time_switch = SwitchButton(card)
        self.name_by_time_switch.setChecked(bool(config.nameByTime.value))
        self.name_by_time_switch.checkedChanged.connect(self._on_name_by_time_changed)

        grid.addWidget(BodyLabel("名称", card), 0, 0)
        grid.addWidget(self.name_edit, 0, 1, 1, 3)
        grid.addWidget(BodyLabel("分类", card), 1, 0)
        grid.addWidget(self.category_box, 1, 1, 1, 3)
        grid.addWidget(BodyLabel("标签", card), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.tag_input, 2, 1, 1, 3)
        grid.addWidget(BodyLabel("关键词", card), 3, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.keyword_input, 3, 1, 1, 3)
        grid.addWidget(BodyLabel("隐藏项", card), 4, 0)
        grid.addWidget(self.hidden_switch, 4, 1)
        grid.addWidget(BodyLabel("按时间命名", card), 4, 2)
        grid.addWidget(self.name_by_time_switch, 4, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        outer.addLayout(grid)

        self.selected_label = CaptionLabel("尚未选择文件", card)
        outer.addWidget(self.selected_label)

        actions = QHBoxLayout()
        import_button = PrimaryPushButton(FluentIcon.CLOUD, "开始导入", card)
        import_button.clicked.connect(self.import_now)
        actions.addWidget(import_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        return card

    # ------------------------------------------------------------------ 交互
    def _set_mode(self, mode: str) -> None:
        is_text = mode == "text"
        self.text_edit.setVisible(is_text)
        self.drop_area.setVisible(not is_text)
        self._file_buttons.setVisible(not is_text)
        self.name_edit.setEnabled(True)
        self.source_hint.setText(
            "当前为文本模式：直接输入或粘贴文本内容" if is_text else "当前为文件模式：拖入文件或文件夹，或使用下方按钮选择"
        )

    def _on_files(self, files: list[str]) -> None:
        self._directory = None
        for path in files:
            if path not in self._files:
                self._files.append(path)
        self._refresh_sources()

    def _on_directory(self, directory: str) -> None:
        self._files = []
        self._directory = directory
        self._refresh_sources()

    def _clear_sources(self) -> None:
        self._files = []
        self._directory = None
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        if self._directory:
            self.selected_label.setText(f"已选择文件夹：{self._directory}（将导入其中所有文件）")
        elif self._files:
            names = "，".join(Path(p).name for p in self._files[:3])
            more = f" 等 {len(self._files)} 个文件" if len(self._files) > 3 else ""
            self.selected_label.setText(f"已选择：{elide(names, 50)}{more}")
        else:
            self.selected_label.setText("尚未选择文件")

    def _on_name_by_time_changed(self, checked: bool) -> None:
        config.set(config.nameByTime, bool(checked))

    def _reload_categories(self) -> None:
        current = self.category_box.currentData()
        self.category_box.clear()
        self.category_box.addItem("未分类")
        nodes = TaxonomyService(self.session).tree(user_id=UserService(self.session).current_id())
        for node in nodes:
            prefix = "　" * node.depth
            self.category_box.addItem(f"{prefix}{node.category.name}", userData=node.category.id)
        for index in range(self.category_box.count()):
            if self.category_box.itemData(index) == current:
                self.category_box.setCurrentIndex(index)
                break

    def _reload_tags(self) -> None:
        repo = TagRepository(self.session)
        self.tag_input.set_known_tags(
            repo.names(user_id=UserService(self.session).current_id()),
            global_tags=set(repo.global_names()),
        )

    # ------------------------------------------------------------------ 导入
    def import_now(self) -> None:
        service = ImportService(self.session, library=LibraryService(self.session).ensure_default())
        user_id = UserService(self.session).current_id()
        options = {
            "category_id": self.category_box.currentData(),
            "user_id": user_id,
            "keywords": self.keyword_input.keywords(),
            "tags": self.tag_input.keywords(),
            "is_hidden": self.hidden_switch.isChecked(),
        }
        busy = None
        try:
            if self._directory:
                busy = BusyTip(self, "正在导入文件夹", self._directory.name)
                result = service.import_directory(self._directory, **options)
            elif self._files:
                busy = BusyTip(self, "正在导入文件", f"共 {len(self._files)} 个文件")
                result = service.import_files(self._files, **options)
            else:
                content = self.text_edit.toPlainText()
                if not content.strip():
                    toast_warning(self, "没有可导入的内容", "请输入文本，或切换到文件导入")
                    return
                item = service.import_text(self.name_edit.text().strip(), content, **options)
                if item is None:
                    toast_warning(self, "已跳过", "相同内容的数据已存在")
                    return
                self.session.commit()
                signalBus.itemsChanged.emit()
                signalBus.librariesChanged.emit()
                self._maybe_archive(f"导入文本「{item.name}」")
                self._reset_text()
                toast_success(self, "导入成功", f"已导入文本「{item.name}」（{type_name(item.type)}）")
                return

            if busy is not None:
                busy.finish(f"已处理 {result.added_count} 项")

            self.session.commit()
            signalBus.itemsChanged.emit()
            signalBus.librariesChanged.emit()
            self._maybe_archive(result.summary())
            self._clear_sources()
            if result.added_count:
                toast_success(self, "导入完成", result.summary())
            else:
                toast_warning(self, "没有新数据", result.summary())
            if result.failed:
                first = result.failed[0]
                toast_error(self, "部分文件导入失败", f"{Path(first[0]).name}：{first[1]}")
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            if busy is not None:
                busy.finish("导入失败")
            toast_error(self, "导入失败", str(exc))

    def _maybe_archive(self, note: str) -> None:
        """按设置决定导入后是否自动创建一次存档快照。"""
        if not config.archiveOnImport.value:
            return
        try:
            ArchiveService(self.session).create(note=note[:200])
            self.session.commit()
            signalBus.archivesChanged.emit()
            logger.info("导入后已自动创建存档：{}", note)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            logger.warning("导入后自动存档失败：{}", exc)

    def _reset_text(self) -> None:
        self.text_edit.clear()
        self.name_edit.clear()
        self.tag_input.clear()
        self.keyword_input.clear()


__all__ = ["ImportPage"]
