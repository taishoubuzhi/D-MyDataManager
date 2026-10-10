"""数据导入页：文本 / 批量（文件、文件夹）三种来源，并显示导入进度与逐文件结果。"""

from __future__ import annotations

import datetime as dt
from functools import partial
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QLabel, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
    SwitchButton,
    TableWidget,
    TextEdit,
)

from loguru import logger

from ...core.config import config
from ...core.runtime.signals import signalBus
from ...db import database
from ...db.models import guess_type
from ...repositories import ItemRepository, TagRepository
from ...sdk.items import ImportContext
from ...sdk.points import ExtensionPoint
from ...services import ArchiveService, ImportService, LibraryService, TaxonomyService, UserService
from ...services.blob_store import sha256_of
from ...services.import_job import ImportPlanItem, import_store, open_journals, resume_job
from ..components import FlowArea
from ..components.import_worker import ImportWorker
from ..framework import (
    IconTextButton,
    IconTextPrimaryButton,
    ScrollPage,
    elide,
    format_datetime,
    format_size,
    icon_text_label,
    release_widget,
    type_name,
)
from ..framework import contributions
from ..framework.contributions import path_filters
from ..components.data_table import fit_columns, fit_table_height, prepare_table, wrap_table
from ..components.drop_area import DropArea
from ..components.keyword_input import KeywordInput
from ..components.tag_picker import TagPicker
from ..dialogs import CategoryPickerComboBox
from ..framework import IconTextButton, IconTextPrimaryButton, icon_text_label

_STATUS_LABELS = {"added": "已导入", "skipped": "已跳过", "failed": "失败", "cancelled": "已取消"}
_MAX_DETAIL_ROWS = 500
_DEDUPE_SCAN_LIMIT = 200
#: 结果表格里「文件 / 说明」两列放的是文件名与库内路径，都比较长：换行显示、不省略。
_RESULT_TABLE_MAX_WIDTH = 360
#: 结果表格长高的上限（超过就靠表格自己的滚动条）、保底高度，以及导入过程中每隔几行重算一次。
_RESULT_TABLE_MAX_HEIGHT = 460
_RESULT_TABLE_MIN_HEIGHT = 200
_FIT_HEIGHT_EVERY = 20


class ImportPage(ScrollPage):
    page_name = "importPage"
    page_title = "导入数据"
    page_subtitle = "文件会按内容去重后保存到本机仓库，可单个文件导入，也可整个文件夹批量导入"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = database.new_session()
        self._files: list[str] = []
        self._directory: str | None = None
        self._tree_files: list[tuple[Path, str]] = []
        self._worker: ImportWorker | None = None
        #: 这一批统一使用的自定义封面（用户挑的图片路径）；空串 = 按默认规则
        self._cover_source: str = ""
        self.selected_label_text = "尚未选择文件"
        self._started_at: dt.datetime | None = None
        #: 当前这份「待导入清单」的 id（暂停后还在，用来继续 / 逐项取消）
        self._job_id: str = ""
        self._job_total = 0
        #: 上次异常退出留下的清单
        self._recovery_journals: list = []

        self._build_mode_card()
        self._build_target_card()
        self._build_details_card()
        self._build_progress_card()
        self._build_recovery_card()
        self.add_stretch()

        self._reload_users()
        self._reload_categories()
        self._reload_tags()
        signalBus.categoriesChanged.connect(self._reload_categories)
        signalBus.tagsChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_users)
        signalBus.userChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_categories)
        # 插件启用 / 禁用后，导入页的功能按钮跟着出现或消失
        signalBus.pluginsChanged.connect(self._sync_plugin_actions)
        # 启动时找残留的导入清单：上次异常退出后没做完的批量导入
        self._check_recovery()

    # ------------------------------------------------------------------ 界面
    def _build_mode_card(self) -> CardWidget:
        card, layout = self.add_section("数据来源", "文本内容或文件 / 文件夹，可随时切换")

        # 已选摘要：先建好——构造期 setCurrentItem 就会触发 _set_mode。
        self.selected_label = CaptionLabel(self.selected_label_text, card)
        self.selected_label.setWordWrap(True)
        self.selected_label.hide()

        self.mode = SegmentedWidget(card)
        self.mode.addItem("text", "文本", onClick=lambda: self._set_mode("text"))
        self.mode.addItem("file", "批量导入（文件 / 文件夹）", onClick=lambda: self._set_mode("file"))
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

        # 来源按钮用流式布局：窄窗口下换行，不再被裁掉，也不会在隐藏时塌成 0 高。
        self._file_buttons = FlowArea(card, horizontal_spacing=8, vertical_spacing=8)
        pick_files = IconTextButton(FluentIcon.FOLDER_ADD, "选择多个文件", self._file_buttons)
        pick_files.clicked.connect(self.drop_area.browse)
        pick_folder = IconTextButton(FluentIcon.FOLDER, "选择文件夹", self._file_buttons)
        pick_folder.clicked.connect(self.drop_area.browse_directory)
        clear = IconTextButton(FluentIcon.CLEAR_SELECTION, "清空选择", self._file_buttons)
        clear.clicked.connect(self._clear_sources)
        self._file_buttons.add_widgets((pick_files, pick_folder, clear))
        self._file_buttons.hide()
        layout.addWidget(self._file_buttons)

        # 已选摘要：此前只写进 selected_label_text 属性，界面上看不到。
        layout.addWidget(self.selected_label)
        return card

    def _build_target_card(self) -> CardWidget:
        card, outer = self.add_section(
            "导入目标与数据信息", "选择导入到哪个用户与分类，并按需补充名称、标签与关键词"
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.user_box = ComboBox(card)
        self.user_box.setMinimumWidth(180)
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        # 说明改挂在对应的输入控件上：鼠标停住才弹出来
        self.user_hint = CaptionLabel("数据会复制到该用户的用户名文件夹下", card)
        self.user_hint.setVisible(False)
        self.user_box.setToolTip(self.user_hint.text())
        self.name_edit = LineEdit(card)
        self.name_edit.setPlaceholderText("留空则使用文件名或当前时间")
        self.category_box = CategoryPickerComboBox(card, title="选择导入分类")
        self.category_hint = CaptionLabel("文件导入到所选分类下", card)
        self.category_hint.setVisible(False)
        self.category_box.setToolTip(self.category_hint.text())
        self.tag_input = TagPicker([], "输入标签后回车，或点右侧按钮选择已有标签", card)
        self.keyword_input = KeywordInput("输入关键词后回车", card)

        self.hidden_switch = SwitchButton(card)
        self.name_by_time_switch = SwitchButton(card)
        self.name_by_time_switch.setChecked(bool(config.nameByTime.value))
        self.name_by_time_switch.checkedChanged.connect(self._on_name_by_time_changed)

        # 封面：这一批统一用用户挑的那张图；不挑就按默认规则（用户 m02499 第 2 条）
        self.cover_button = PushButton("选择封面图片", card)
        self.cover_button.clicked.connect(self._on_pick_cover)
        self.cover_clear_button = PushButton("用默认封面", card)
        self.cover_clear_button.clicked.connect(self._clear_cover)
        self.cover_clear_button.setEnabled(False)
        self.cover_preview = QLabel(card)
        self.cover_preview.setFixedSize(40, 40)
        self.cover_preview.setScaledContents(True)
        self.cover_preview.setVisible(False)
        self.cover_name_label = CaptionLabel("使用默认封面", card)
        self.cover_hint = CaptionLabel(
            "不选封面时按默认规则：视频取第一帧，图片用自身，其它类型用默认图标", card
        )
        self.cover_hint.setVisible(False)
        self.cover_button.setToolTip(self.cover_hint.text())
        cover_row = QHBoxLayout()
        cover_row.setSpacing(8)
        cover_row.addWidget(self.cover_preview)
        cover_row.addWidget(self.cover_name_label, 1)

        grid.addWidget(icon_text_label(FluentIcon.PEOPLE, "导入用户", card), 0, 0)
        grid.addWidget(self.user_box, 0, 1, 1, 3)
        grid.addWidget(self.user_hint, 1, 1, 1, 3)
        grid.addWidget(icon_text_label(FluentIcon.EDIT, "名称", card), 2, 0)
        grid.addWidget(self.name_edit, 2, 1, 1, 3)
        grid.addWidget(icon_text_label(FluentIcon.TILES, "分类", card), 3, 0)
        grid.addWidget(self.category_box, 3, 1, 1, 3)
        grid.addWidget(self.category_hint, 4, 1, 1, 3)
        # 输入框是单行时居中对齐，标签与后面的控件在同一条基线上：
        # 用 AlignTop 会把标签顶到行首，看起来比输入框高 11px。
        grid.addWidget(
            icon_text_label(FluentIcon.TAG, "标签", card), 5, 0, Qt.AlignmentFlag.AlignVCenter
        )
        grid.addWidget(self.tag_input, 5, 1, 1, 3)
        grid.addWidget(
            icon_text_label(FluentIcon.FONT, "关键词", card), 6, 0, Qt.AlignmentFlag.AlignVCenter
        )
        grid.addWidget(self.keyword_input, 6, 1, 1, 3)
        grid.addWidget(icon_text_label(FluentIcon.PHOTO, "封面", card), 7, 0)
        grid.addWidget(self.cover_button, 7, 1)
        grid.addWidget(self.cover_clear_button, 7, 2)
        grid.addLayout(cover_row, 7, 3)
        grid.addWidget(self.cover_hint, 8, 1, 1, 3)
        grid.addWidget(icon_text_label(FluentIcon.VIEW, "隐藏项", card), 9, 0)
        grid.addWidget(self.hidden_switch, 9, 1)
        grid.addWidget(icon_text_label(FluentIcon.DATE_TIME, "按时间命名", card), 9, 2)
        grid.addWidget(self.name_by_time_switch, 9, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        import_button = IconTextPrimaryButton(FluentIcon.CLOUD, "开始导入", card)
        import_button.clicked.connect(self.import_now)
        actions.addWidget(import_button)
        self._action_row = actions
        self._action_buttons: list[QWidget] = []
        actions.addStretch(1)
        self._sync_plugin_actions()
        outer.addLayout(actions)
        return card

    # ------------------------------------------------------- 插件贡献的按钮
    def _sync_plugin_actions(self) -> None:
        """插件贡献的导入页按钮（`app.ui.import.action`）：只有载入插件后才出现。"""
        for button in getattr(self, "_action_buttons", []):
            release_widget(button)
        self._action_buttons = []
        row = getattr(self, "_action_row", None)
        if row is None:
            return
        for item in contributions.items(ExtensionPoint.IMPORT_ACTION):
            data = contributions.value_of(item)
            callback = data.get("callback")
            if not callable(callback):
                continue
            button = IconTextButton(
                contributions.icon_of(data.get("icon")),
                contributions.title_of(item, "text"),
                self,
            )
            tip = str(data.get("tip") or "")
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(partial(self._run_plugin_action, callback))
            # 排在「开始导入」之后、右侧伸缩之前
            row.insertWidget(max(1, row.count() - 1), button)
            self._action_buttons.append(button)

    def _run_plugin_action(self, callback) -> None:
        """把当前导入表单交给插件按钮，插件只能「补建议」（预填标签 / 关键词）。"""
        contributions.resolve(callback, self._plugin_action_context())

    def _plugin_action_context(self) -> ImportContext:
        return ImportContext(
            paths=tuple(path for path, _subdir in self._collect_sources()),
            user_id=self.target_user_id(),
            category_id=self.category_box.currentData(),
            add_tags=self._fill_tags,
            add_keywords=self._fill_keywords,
            notify=lambda message: self.toast_info("插件", str(message)),
            scan=self._plugin_action_paths,
        )

    def _plugin_action_paths(self) -> tuple[Path, ...]:
        """给插件一份「当前待导入文件」的最新清单。"""
        return tuple(path for path, _subdir in self._collect_sources())

    def _fill_tags(self, names) -> int:
        """把插件建议的标签并进标签框，返回新增个数。"""
        merged = list(self.tag_input.keywords())
        added = [str(name).strip() for name in names if str(name).strip()]
        fresh = [name for name in added if name not in merged]
        if fresh:
            self.tag_input.set_keywords(merged + fresh)
        return len(fresh)

    def _fill_keywords(self, words) -> int:
        merged = list(self.keyword_input.keywords())
        added = [str(word).strip() for word in words if str(word).strip()]
        fresh = [word for word in added if word not in merged]
        if fresh:
            self.keyword_input.set_keywords(merged + fresh)
        return len(fresh)

    def _build_details_card(self) -> CardWidget:
        card, layout = self.add_section("待导入文件信息", "先做重复检查，已在库中的文件会标记为跳过")
        self.details_summary = CaptionLabel("尚未选择文件", card)
        layout.addWidget(self.details_summary)

        self.details_table = TableWidget(card)
        self.details_table.setColumnCount(6)
        self.details_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "修改时间", "子目录", "状态"]
        )
        prepare_table(self.details_table, movable=False)
        self.details_table.setMinimumHeight(220)
        # 选中某一行 → 「取消选中项」按钮可用
        self.details_table.itemSelectionChanged.connect(self._sync_job_buttons)
        layout.addWidget(self.details_table)

        self.details_card = card
        card.hide()
        return card

    def _build_progress_card(self) -> CardWidget:
        card, layout = self.add_section("导入进度", "逐个文件显示结果，失败的条目会给出原因")

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.progress_label = CaptionLabel("等待开始", card)
        layout.addWidget(self.progress_label)

        # 「待导入清单」的各种操作：暂停 / 继续、取消整批、取消个别还没导入的项
        self.job_label = CaptionLabel("", card)
        self.job_label.setWordWrap(True)
        layout.addWidget(self.job_label)

        controls = QHBoxLayout()
        self.pause_btn = PushButton("暂停导入", card)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.cancel_btn = PushButton("取消整批", card)
        self.cancel_btn.clicked.connect(self._cancel_batch)
        self.cancel_item_btn = PushButton("取消选中项", card)
        self.cancel_item_btn.clicked.connect(self._cancel_selected_item)
        for button in (self.pause_btn, self.cancel_btn, self.cancel_item_btn):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.result_table = TableWidget(card)
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["文件", "状态", "说明"])
        prepare_table(self.result_table, movable=True)
        # 结果表里的文件名与库内路径都很长：换行显示、不省略，行高随内容长高
        wrap_table(self.result_table, max_width=_RESULT_TABLE_MAX_WIDTH)
        self.result_table.setMinimumHeight(_RESULT_TABLE_MIN_HEIGHT)
        layout.addWidget(self.result_table)

        self.progress_card = card
        card.hide()
        return card

    def _build_recovery_card(self) -> CardWidget:
        card, layout = self.add_section(
            "上次未完成的导入",
            "程序上次批量导入时异常退出；清单还在，可以接着做完，也可以放弃",
        )
        self.recovery_label = CaptionLabel("", card)
        self.recovery_label.setWordWrap(True)
        layout.addWidget(self.recovery_label)

        row = QHBoxLayout()
        self.recovery_resume = PrimaryPushButton("继续导入", card)
        self.recovery_resume.clicked.connect(self._resume_recovery)
        self.recovery_abandon = PushButton("放弃这次导入", card)
        self.recovery_abandon.clicked.connect(self._abandon_recovery)
        row.addWidget(self.recovery_resume)
        row.addWidget(self.recovery_abandon)
        row.addStretch(1)
        layout.addLayout(row)

        self.recovery_card = card
        card.hide()
        return card

    def _sync_job_buttons(self) -> None:
        """按「有没有在跑 / 有没有清单 / 有没有选中行」刷新三个操作按钮。"""
        if not hasattr(self, "pause_btn"):
            return
        running = self._worker is not None and self._worker.isRunning()
        configured = bool(self._job_id)
        has_selection = self.details_table.currentRow() >= 0
        self.pause_btn.setText("暂停导入" if running else "继续导入")
        self.pause_btn.setEnabled(running or configured)
        self.cancel_btn.setEnabled(running or configured)
        self.cancel_item_btn.setEnabled(configured and has_selection)
        self.pause_btn.setToolTip(
            "当前文件处理完后停下，清单会保留" if running else "接着上次的清单继续导入"
        )
        self.cancel_btn.setToolTip("剩余还没开始的项都会被标成「已取消」，已导入的不受影响")
        self.cancel_item_btn.setToolTip("在「待导入文件信息」里选中一行，取消这一项的导入请求")

    # ------------------------------------------------------------------ 交互
    def _set_mode(self, mode: str) -> None:
        is_text = mode == "text"
        self.text_edit.setVisible(is_text)
        self.drop_area.setVisible(not is_text)
        self._file_buttons.setVisible(not is_text)
        self.source_hint.setText(
            "当前为文本模式：直接输入或粘贴文本内容"
            if is_text
            else "当前为批量导入：可选择多个文件，或选择一个文件夹（文件夹会作为一个新分类整体导入）"
        )
        self.selected_label.setVisible(not is_text)
        if not is_text:
            self._refresh_sources()

    def _on_files(self, files: list[str]) -> None:
        self._directory = None
        self._tree_files = []
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
        self._tree_files = []
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        if self._directory:
            self.selected_label_text = f"已选择文件夹：{elide(self._directory, 60)}"
        elif self._files:
            names = "，".join(Path(p).name for p in self._files[:3])
            more = f" 等 {len(self._files)} 个文件" if len(self._files) > 3 else ""
            self.selected_label_text = f"已选择：{elide(names, 50)}{more}"
        else:
            self.selected_label_text = "尚未选择文件"
        self.selected_label.setText(self.selected_label_text)
        self.selected_label.setToolTip(
            self._directory or "\n".join(self._files) or self.selected_label_text
        )
        is_folder = bool(self._directory)
        self.category_box.setEnabled(not is_folder)
        self.category_hint.setText(
            f"文件夹导入时会以「{Path(self._directory).name}」作为新分类，忽略上方选择"
            if is_folder
            else "文件导入到所选分类下（多个文件共用此分类）",
        )
        self.category_box.setToolTip(self.category_hint.text())
        self._refresh_details()

    def _on_name_by_time_changed(self, checked: bool) -> None:
        config.set(config.nameByTime, bool(checked))

    # ------------------------------------------------------------------- 封面
    def _on_pick_cover(self) -> None:
        """挑一张图给这一批数据当封面（不挑就按默认规则，用户 m02499 第 2 条）。"""
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择封面图片",
            Path(self._cover_source).parent.as_posix() if self._cover_source else "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)",
        )
        if path:
            self._set_cover_source(path)

    def _clear_cover(self) -> None:
        self._set_cover_source("")

    def _set_cover_source(self, path: str | Path) -> None:
        """记住 / 清掉这一批用的封面；只做状态与预览，真正的缩放与落盘在导入时做。"""
        self._cover_source = str(path or "")
        self.cover_clear_button.setEnabled(bool(self._cover_source))
        if not self._cover_source:
            self.cover_name_label.setText("使用默认封面")
            self.cover_preview.clear()
            self.cover_preview.setVisible(False)
            return
        self.cover_name_label.setText(elide(Path(self._cover_source).name, 28))
        pixmap = QPixmap(self._cover_source)
        if pixmap.isNull():
            # 图打不开也照样记住路径：真正写入时会再判一次并给出失败提示，不在这里吞掉
            self.cover_preview.setVisible(False)
            return
        self.cover_preview.setPixmap(pixmap)
        self.cover_preview.setVisible(True)

    def refresh(self) -> None:
        """按当前数据库状态重建用户/分类/标签候选项（与其它页面保持同一入口）。"""
        self._reload_users()
        self._reload_tags()
        self._check_recovery()

    def _on_user_changed(self) -> None:
        self._reload_categories()
        self._reload_tags()

    def _reload_users(self) -> None:
        service = UserService(self.session)
        actor = service.current()
        admin = service.is_admin(actor)
        current = self.user_box.currentData()
        users = service.list_users()
        # 只有默认用户（管理员）能替别人导入，其他用户的下拉框里只有自己。
        if not admin:
            users = [info for info in users if info.user.id == actor.id]
        self.user_box.blockSignals(True)
        self.user_box.clear()
        for info in users:
            label = info.name + ("（当前用户）" if info.user.id == actor.id else "")
            self.user_box.addItem(label, userData=info.user.id)
        picked = current if current is not None else actor.id
        for index in range(self.user_box.count()):
            if self.user_box.itemData(index) == picked:
                self.user_box.setCurrentIndex(index)
                break
        self.user_box.blockSignals(False)
        self.user_box.setEnabled(admin)
        self.user_hint.setText(
            "数据会复制到该用户的用户名文件夹下"
            if admin
            else "只有默认用户可以替其他用户导入数据，其他用户只能导入到自己的文件夹"
        )
        self.user_box.setToolTip(self.user_hint.text())
        self._reload_categories()

    def target_user_id(self) -> int | None:
        """导入目标用户：非默认用户始终导入到自己名下，忽略下拉框里的其它值。"""
        service = UserService(self.session)
        data = self.user_box.currentData()
        if data is None or not service.is_admin():
            return service.current_id()
        return int(data)

    def _reload_categories(self) -> None:
        """按目标用户重建分类下拉：「未分类」是真实分类，未指定分类的数据也归入其中。"""
        current = self.category_box.currentData()
        taxonomy = TaxonomyService(self.session)
        user_id = self.target_user_id()
        uncategorized = taxonomy.uncategorized_category(user_id=user_id, create=False) if user_id is not None else None
        self.category_box.clear()
        nodes = taxonomy.tree(user_id=user_id)
        for node in nodes:
            prefix = "　" * node.depth
            self.category_box.addItem(f"{prefix}{node.category.name}", userData=node.category.id)
        # 层级选择弹窗要拿到完整分类节点，ComboBox 的 item 仍用于显示与取当前值
        self.category_box.set_picker_nodes(nodes, title="选择导入分类")
        ids = {self.category_box.itemData(index) for index in range(self.category_box.count())}
        # 目标用户切换后旧分类 id 已不属于新树：回落到该用户的「未分类」，否则会默默落到第一个根分类。
        wanted = current if current in ids else (uncategorized.id if uncategorized else None)
        if wanted in ids:
            self.category_box.setCurrentIndex(
                next(index for index in range(self.category_box.count()) if self.category_box.itemData(index) == wanted)
            )

    def _reload_tags(self) -> None:
        repo = TagRepository(self.session)
        user_id = self.target_user_id()
        self.tag_input.set_known_tags(
            repo.names(user_id=user_id),
            global_tags=set(repo.global_names()),
        )

    # -------------------------------------------------------------- 文件信息
    def _collect_sources(self) -> list[tuple[Path, str]]:
        """当前待导入的 (文件, 子目录) 列表：文件夹会展开并保留相对子目录。

        插件贡献的导入过滤器（扩展点 app.ui.import.filter）在这里统一生效，
        所以预览与实际导入看到的是同一份清单。
        """
        if self._directory:
            sources = list(self._tree_files)
        else:
            sources = [(Path(path), "") for path in self._files]
        filters = path_filters()
        if not filters:
            return sources
        return [(path, subdir) for path, subdir in sources if all(accept(path) for accept in filters)]

    def _scan_tree(self) -> None:
        self._tree_files = []
        if not self._directory:
            return
        root = Path(self._directory)
        if not root.is_dir():
            return
        from ...services.import_service import SKIP_DIRS, SKIP_NAMES

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name in SKIP_NAMES or path.name.startswith("."):
                continue
            relative = path.relative_to(root)
            if any(part in SKIP_DIRS for part in relative.parts[:-1]):
                continue
            subdir = "" if relative.parent.as_posix() == "." else relative.parent.as_posix()
            self._tree_files.append((path, subdir))

    def _refresh_details(self) -> None:
        if self._directory and not self._tree_files:
            self._scan_tree()
        sources = self._collect_sources()
        self.details_table.setRowCount(0)
        if not sources:
            self.details_card.hide()
            self.details_summary.setText("尚未选择文件")
            return

        from PyQt6.QtWidgets import QTableWidgetItem

        self.details_card.show()
        dedupe = len(sources) <= _DEDUPE_SCAN_LIMIT
        items = ItemRepository(self.session)
        total_size = 0
        rows = sources[:_MAX_DETAIL_ROWS]
        self.details_table.setRowCount(len(rows))
        for row, (path, subdir) in enumerate(rows):
            try:
                stat = path.stat()
            except OSError:
                stat = None
            size = stat.st_size if stat else 0
            total_size += size
            status = "待导入"
            if dedupe:
                try:
                    status = "库内已有同类内容" if items.by_checksum(sha256_of(path)) else "待导入"
                except OSError:
                    status = "无法读取"
            cells = [
                path.name,
                type_name(guess_type(path.name)),
                format_size(size),
                format_datetime(dt.datetime.fromtimestamp(stat.st_mtime)) if stat else "—",
                subdir or "—",
                status,
            ]
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setToolTip(str(path) if column == 0 else text)
                self.details_table.setItem(row, column, cell)
        prepare_table(self.details_table, movable=False)
        fit_columns(self.details_table, max_width=240, weights={0: 0.6})
        extra = "（仅显示前 500 个）" if len(sources) > len(rows) else ""
        folder_hint = f"，将作为新分类「{Path(self._directory).name}」导入" if self._directory else ""
        self.details_summary.setText(
            f"共 {len(sources)} 个文件，合计 {format_size(total_size)}{folder_hint}{extra}"
        )

    # ------------------------------------------------------------------ 导入
    def import_now(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast_warning("正在导入", "请先暂停或等这一轮跑完，再开始新的批量导入")
            return
        user_id = self.target_user_id()
        common = {
            "user_id": user_id,
            "keywords": self.keyword_input.keywords(),
            "tags": self.tag_input.keywords(),
            "is_hidden": self.hidden_switch.isChecked(),
        }
        if self._directory or self._files:
            self._start_batch(user_id, common)
            return

        content = self.text_edit.toPlainText()
        if not content.strip():
            self.toast_warning("没有可导入的内容", "请输入文本，或切换到批量导入")
            return
        service = ImportService(self.session, library=LibraryService(self.session).ensure_default())
        busy = None
        try:
            busy = self.busy("正在导入文本", elide(self.name_edit.text().strip() or "未命名", 40))
            item = service.import_text(
                self.name_edit.text().strip(),
                content,
                category_id=self.category_box.currentData(),
                cover=self._cover_source,
                **common,
            )
            if item is None:
                busy.finish("已跳过重复内容")
                self.toast_warning("已跳过", "相同内容的数据已存在")
                return
            self.session.commit()
            busy.finish(f"已导入「{item.name}」")
            signalBus.itemsChanged.emit()
            signalBus.librariesChanged.emit()
            self._maybe_archive(f"导入文本「{item.name}」")
            self._reset_text()
            self.toast_success("导入成功", f"已导入文本「{item.name}」（{type_name(item.type)}）")
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            if busy is not None:
                busy.finish("导入失败")
            self.toast_error("导入失败", str(exc))

    def _start_batch(self, user_id: int | None, common: dict) -> None:
        sources = self._collect_sources()
        if not sources:
            self.toast_warning("没有可导入的文件", "请先选择文件或文件夹")
            return
        # 待导入清单在这里定稿：以后一律以这份快照为准（不再重扫目录），
        # 所以「预览里看到的」就是「真正会导入的」。
        items = [
            ImportPlanItem(key=f"{index:06d}", source=str(path), subdir=subdir or "")
            for index, (path, subdir) in enumerate(sources)
        ]
        if self._directory:
            options = {**common, "name": Path(self._directory).name}
            title = f"批量导入：{Path(self._directory).name}"
        else:
            options = {**common, "category_id": self.category_box.currentData()}
            title = "批量导入"
        # 这一批统一使用的自定义封面（空串 = 按默认规则）；清单会把它一起存下来，
        # 暂停后继续、崩溃恢复都还是同一张封面
        options["cover"] = self._cover_source

        self._begin_progress(len(items), f"准备导入 {len(items)} 个文件…")
        worker = ImportWorker(items, options=options, title=title)
        self._wire_worker(worker)
        worker.start()

    def _resume_journal(self, journal_id: str) -> None:
        """接着一份已有清单继续导入（暂停后继续、或崩溃恢复）。"""
        store = import_store()
        try:
            journal = store.load(journal_id)
        except Exception as exc:  # noqa: BLE001 —— 清单读不回来就别硬跑
            logger.warning("读回导入清单失败：{}", exc)
            self.toast_error("无法继续导入", str(exc))
            self._job_id = ""
            self._sync_job_buttons()
            return
        self._job_id = journal_id
        self._begin_progress(len(journal.items), f"继续导入，剩余 {len(journal.open_items())} 项…")
        worker = ImportWorker(journal_id=journal_id, adopt=True)
        self._wire_worker(worker)
        worker.start()

    def _begin_progress(self, total: int, text: str) -> None:
        self.progress_card.show()
        self.result_table.setRowCount(0)
        self._fit_result_height()
        self.progress_bar.setValue(0)
        self._job_total = total
        self._started_at = dt.datetime.now()
        self.progress_label.setText(text)
        self.job_label.setText("")

    def _wire_worker(self, worker: ImportWorker) -> None:
        worker.progressed.connect(self._on_progress)
        worker.evented.connect(self._on_event)
        worker.jobReady.connect(self._on_job_ready)
        worker.stateChanged.connect(self._on_state_changed)
        worker.finished_job.connect(self._on_finished)
        self._worker = worker
        self._sync_job_buttons()

    def _with_job(self, action):
        """按当前清单 id 开一份任务对象，读-改-写它（暂停中也能用）。"""
        store = import_store()
        job = resume_job(store.load(self._job_id), store=store)
        return action(job)

    # ---------------------------------------------------- 暂停 / 继续 / 取消
    def _toggle_pause(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.request_pause()
            self.job_label.setText("正在暂停……当前文件处理完就会停下，清单会保留")
            return
        if not self._job_id:
            self.toast_warning("没有可继续的导入", "请先选择文件或文件夹开始一次批量导入")
            return
        self._resume_journal(self._job_id)

    def _resume_recovery(self) -> None:
        if not self._recovery_journals:
            return
        self.recovery_card.hide()
        self._resume_journal(self._recovery_journals[0].id)

    def _abandon_recovery(self) -> None:
        if not self._recovery_journals:
            return
        journal = self._recovery_journals[0]
        if not self.confirm(
            "放弃这次导入",
            "剩余还没导入的项都会被标成「已取消」，这份清单随后删除。已经导入的内容不受影响。",
        ):
            return
        try:
            store = import_store()
            resume_job(journal, store=store).abandon()
        except Exception as exc:  # noqa: BLE001
            logger.warning("放弃导入清单失败：{}", exc)
            self.toast_error("放弃失败", str(exc))
            return
        self.toast_success("已放弃", "剩余的待导入项已取消，清单已删除")
        if self._job_id == journal.id:
            self._job_id = ""
        self._sync_job_buttons()
        self._check_recovery()

    def _cancel_batch(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            self.job_label.setText("正在取消：剩余还没开始的项都会被标成「已取消」…")
            return
        if not self._job_id:
            self.toast_warning("没有进行中的导入", "请先开始一次批量导入")
            return
        if not self.confirm(
            "取消整批导入",
            "剩余还没导入的项都会被标成「已取消」，这份清单随后删除。已经导入的内容不受影响。",
        ):
            return
        try:
            self._with_job(lambda job: job.abandon())
        except Exception as exc:  # noqa: BLE001
            logger.warning("取消整批导入失败：{}", exc)
            self.toast_error("取消失败", str(exc))
            return
        self._job_id = ""
        self.result_table.setRowCount(0)
        self.progress_label.setText("已取消整批导入")
        self.job_label.setText("")
        self._sync_job_buttons()
        self._check_recovery()

    def _cancel_selected_item(self) -> None:
        if not self._job_id:
            self.toast_warning("没有进行中的导入", "请先开始一次批量导入")
            return
        row = self.details_table.currentRow()
        if row < 0:
            self.toast_warning("没有选中项", "在「待导入文件信息」里选中一行再取消")
            return
        key = f"{row:06d}"
        worker = self._worker
        try:
            if worker is not None and worker.isRunning():
                # 正在跑：交给它自己的那一轮处理（它会在文件之间跳过这一项）
                worker.cancel_item(key)
                cancelled = True
            else:
                cancelled = bool(self._with_job(lambda job: job.cancel_item(key)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("取消导入项失败：{}", exc)
            self.toast_error("取消失败", str(exc))
            return
        if not cancelled:
            self.toast_warning("无法取消", "这一项已经开始或已经处理完了")
            return
        cell = self.details_table.item(row, 5)
        if cell is not None:
            cell.setText("已取消导入")
        self.job_label.setText("这一项已取消导入，其余项照常处理")
        self.toast_success("已取消", "这一项不会再被导入")

    def _check_recovery(self) -> None:
        """扫描残留的导入清单（上次异常退出留下的），有就提示用户。"""
        try:
            leftovers = [journal for journal in open_journals() if journal.id != self._job_id]
        except Exception as exc:  # noqa: BLE001 —— 扫描失败不该影响页面
            logger.warning("扫描未完成的导入清单失败：{}", exc)
            return
        self._recovery_journals = leftovers
        if not leftovers:
            self.recovery_card.hide()
            return
        journal = leftovers[0]
        counts = journal.counts()
        done = counts.get("done", 0) + counts.get("skipped", 0)
        extra = f"（另有 {len(leftovers) - 1} 份未完成清单）" if len(leftovers) > 1 else ""
        self.recovery_label.setText(
            f"「{journal.title or journal.id}」共 {len(journal.items)} 项，"
            f"已完成 {done} 项，还有 {len(journal.open_items())} 项没处理{extra}。"
        )
        self.recovery_card.show()

    def _on_job_ready(self, journal_id: str) -> None:
        self._job_id = journal_id
        self._sync_job_buttons()

    def _on_state_changed(self, state: str) -> None:
        self._sync_job_buttons()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        percent = 100 if total <= 0 else int(done / total * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{done}/{total} · 正在处理 {elide(name, 40)}")

    def _fit_result_height(self) -> None:
        """结果表是换行表格：内容变了就重算高度，长文件名 / 长路径才不会被裁掉。"""
        fit_table_height(
            self.result_table,
            max_height=_RESULT_TABLE_MAX_HEIGHT,
            min_height=_RESULT_TABLE_MIN_HEIGHT,
        )

    def _on_event(self, source: str, status: str, detail: str) -> None:
        from PyQt6.QtWidgets import QTableWidgetItem

        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        for column, text in enumerate((source, _STATUS_LABELS.get(status, status), detail)):
            cell = QTableWidgetItem(str(text))
            cell.setToolTip(str(text))
            self.result_table.setItem(row, column, cell)
        # 换行表格要重算高度：每隔几十行算一次，别让新行被裁在可视区外
        if row % _FIT_HEIGHT_EVERY == 0:
            self._fit_result_height()
        self.result_table.scrollToBottom()

    def _on_finished(self, payload: dict) -> None:
        stopped = str(payload.get("stopped") or "")
        remaining = int(payload.get("remaining") or 0)
        paused = stopped == "paused" or (remaining > 0 and not payload.get("error"))
        failed = payload.get("failed") or []
        if paused:
            self.progress_label.setText(f"已暂停 · 还有 {remaining} 项待导入")
        else:
            self.progress_bar.setValue(100)

        summary = f"成功 {payload.get('ok', 0)}，跳过 {payload.get('skipped', 0)}，失败 {len(failed)}"
        if payload.get("cancelled"):
            summary += f"，取消 {payload['cancelled']}"
        if payload.get("category"):
            summary += f"；新分类「{payload['category']}」"
        if paused:
            summary += f"；剩余 {remaining} 项"
        if self._started_at is not None:
            elapsed = (dt.datetime.now() - self._started_at).total_seconds()
            summary += f"；耗时 {elapsed:.1f} 秒"
            self._started_at = None
        self.progress_label.setText(summary)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        # 暂停就留着清单（可以继续、可以逐项取消）；跑完/取消了就清掉
        self._job_id = (str(payload.get("journal") or self._job_id) if paused else "")
        self._sync_job_buttons()

        # 不管是暂停还是跑完，库里的内容都变了（这是逐项提交的好处）
        self.session.expire_all()
        signalBus.itemsChanged.emit()
        signalBus.librariesChanged.emit()
        signalBus.categoriesChanged.emit()
        self._reload_categories()

        if payload.get("error"):
            self._fit_result_height()
            self.toast_error("导入失败", str(payload["error"]))
            return

        if paused:
            self._fit_result_height()
            self.job_label.setText("可以点「继续导入」接着做，或在下方结果表/待导入清单里取消个别项")
            self.toast_info("已暂停", f"清单已保留，还有 {remaining} 项没处理")
            return

        self._maybe_archive(
            f"批量导入：{payload.get('category') or self.selected_label_text}"[:200]
        )
        self._clear_sources()
        self._fit_result_height()
        self._check_recovery()
        if stopped == "cancelled":
            self.toast_info("已取消", summary)
        elif payload.get("ok"):
            self.toast_success("导入完成", summary)
        elif failed:
            self.toast_error("导入失败", f"{Path(failed[0][0]).name}：{failed[0][1]}")
        else:
            self.toast_warning("没有新数据", summary)
        if failed and payload.get("ok"):
            first = failed[0]
            self.toast_error("部分文件导入失败", f"{Path(first[0]).name}：{first[1]}")

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
