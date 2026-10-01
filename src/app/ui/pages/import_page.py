"""数据导入页：文本 / 批量（文件、文件夹）三种来源，并显示导入进度与逐文件结果。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
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
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TextEdit,
    TitleLabel,
)

from loguru import logger

from ...core.config import config
from ...core.signals import signalBus
from ...db import database
from ...db.models import guess_type
from ...repositories import ItemRepository, TagRepository
from ...services import ArchiveService, ImportService, LibraryService, TaxonomyService, UserService
from ...services.blob_store import sha256_of
from ..common import (
    BusyTip,
    elide,
    format_datetime,
    format_size,
    toast_error,
    toast_success,
    toast_warning,
    type_name,
)
from ..widgets.data_table import fit_columns, prepare_table
from ..widgets.drop_area import DropArea
from ..widgets.keyword_input import KeywordInput
from ..widgets.tag_picker import TagPicker

_STATUS_LABELS = {"added": "已导入", "skipped": "已跳过", "failed": "失败"}
_MAX_DETAIL_ROWS = 500
_DEDUPE_SCAN_LIMIT = 200


class _ImportWorker(QThread):
    """后台执行批量导入：把服务层的进度回调转成 Qt 信号，避免界面卡死。"""

    progressed = pyqtSignal(int, int, str)
    evented = pyqtSignal(str, str, str)
    finished_job = pyqtSignal(dict)

    def __init__(self, kind: str, sources: list[str], options: dict) -> None:
        super().__init__()
        self._kind = kind
        self._sources = sources
        self._options = options

    def run(self) -> None:  # noqa: D102
        session = database.new_session()
        payload = {"ok": 0, "skipped": 0, "failed": [], "category": "", "total": 0, "error": ""}
        try:
            service = ImportService(session, library=LibraryService(session).ensure_default())

            def hook(event) -> None:
                self.evented.emit(event.source, event.status, event.detail)
                self.progressed.emit(event.index, event.total, event.source)

            if self._kind == "folder":
                result = service.import_folder(self._sources[0], on_event=hook, **self._options)
            else:
                result = service.import_files(self._sources, on_event=hook, **self._options)
            session.commit()
            payload.update(
                ok=result.added_count,
                skipped=len(result.skipped),
                failed=result.failed,
                category=result.category_name,
                total=result.total,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("批量导入失败")
            payload["error"] = str(exc)
        finally:
            session.close()
        self.finished_job.emit(payload)


class ImportPage(ScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("importPage")
        self.session = database.new_session()
        self._files: list[str] = []
        self._directory: str | None = None
        self._tree_files: list[tuple[Path, str]] = []
        self._worker: _ImportWorker | None = None

        host = QWidget(self)
        host.setObjectName("importHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(16)

        layout.addWidget(TitleLabel("导入数据", host))
        layout.addWidget(
            CaptionLabel(
                "文件会按内容去重后保存到本机仓库，可单个文件导入，也可整个文件夹批量导入",
                host,
            )
        )

        layout.addWidget(self._build_mode_card(host))
        layout.addWidget(self._build_target_card(host))
        layout.addWidget(self._build_details_card(host))
        layout.addWidget(self._build_progress_card(host))
        layout.addStretch(1)
        self.setWidget(host)
        self.setWidgetResizable(True)

        self._reload_users()
        self._reload_categories()
        self._reload_tags()
        signalBus.categoriesChanged.connect(self._reload_categories)
        signalBus.tagsChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_users)
        signalBus.userChanged.connect(self._reload_tags)
        signalBus.userChanged.connect(self._reload_categories)

    # ------------------------------------------------------------------ 界面
    def _build_mode_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(StrongBodyLabel("数据来源", card))

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

        self._file_buttons = QWidget(card)
        buttons = QHBoxLayout(self._file_buttons)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        pick_files = PushButton(FluentIcon.FOLDER_ADD, "选择多个文件", self._file_buttons)
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

    def _build_target_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(12)
        outer.addWidget(StrongBodyLabel("导入目标与数据信息", card))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.user_box = ComboBox(card)
        self.user_box.setMinimumWidth(180)
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        self.user_hint = CaptionLabel("数据会复制到该用户的用户名文件夹下", card)

        self.name_edit = LineEdit(card)
        self.name_edit.setPlaceholderText("留空则使用文件名或当前时间")
        self.category_box = ComboBox(card)
        self.category_hint = CaptionLabel("文件导入到所选分类下", card)
        self.tag_input = TagPicker([], "输入标签后回车，或点右侧按钮选择已有标签", card)
        self.keyword_input = KeywordInput("输入关键词后回车", card)

        self.hidden_switch = SwitchButton(card)
        self.name_by_time_switch = SwitchButton(card)
        self.name_by_time_switch.setChecked(bool(config.nameByTime.value))
        self.name_by_time_switch.checkedChanged.connect(self._on_name_by_time_changed)

        grid.addWidget(BodyLabel("导入用户", card), 0, 0)
        grid.addWidget(self.user_box, 0, 1, 1, 3)
        grid.addWidget(self.user_hint, 1, 1, 1, 3)
        grid.addWidget(BodyLabel("名称", card), 2, 0)
        grid.addWidget(self.name_edit, 2, 1, 1, 3)
        grid.addWidget(BodyLabel("分类", card), 3, 0)
        grid.addWidget(self.category_box, 3, 1, 1, 3)
        grid.addWidget(self.category_hint, 4, 1, 1, 3)
        grid.addWidget(BodyLabel("标签", card), 5, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.tag_input, 5, 1, 1, 3)
        grid.addWidget(BodyLabel("关键词", card), 6, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.keyword_input, 6, 1, 1, 3)
        grid.addWidget(BodyLabel("隐藏项", card), 7, 0)
        grid.addWidget(self.hidden_switch, 7, 1)
        grid.addWidget(BodyLabel("按时间命名", card), 7, 2)
        grid.addWidget(self.name_by_time_switch, 7, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        import_button = PrimaryPushButton(FluentIcon.CLOUD, "开始导入", card)
        import_button.clicked.connect(self.import_now)
        actions.addWidget(import_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        return card

    def _build_details_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel("待导入文件信息", card))
        self.details_summary = CaptionLabel("尚未选择文件", card)
        layout.addWidget(self.details_summary)

        self.details_table = TableWidget(card)
        self.details_table.setColumnCount(6)
        self.details_table.setHorizontalHeaderLabels(
            ["文件名", "类型", "大小", "修改时间", "子目录", "状态"]
        )
        prepare_table(self.details_table, movable=False)
        self.details_table.setMinimumHeight(220)
        layout.addWidget(self.details_table)

        self.details_card = card
        card.hide()
        return card

    def _build_progress_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel("导入进度", card))

        self.progress_bar = ProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.progress_label = CaptionLabel("等待开始", card)
        layout.addWidget(self.progress_label)

        self.result_table = TableWidget(card)
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["文件", "状态", "说明"])
        prepare_table(self.result_table, movable=True)
        self.result_table.setMinimumHeight(200)
        layout.addWidget(self.result_table)

        self.progress_card = card
        card.hide()
        return card

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
            self.selected_label_text = f"已选择文件夹：{self._directory}"
        elif self._files:
            names = "，".join(Path(p).name for p in self._files[:3])
            more = f" 等 {len(self._files)} 个文件" if len(self._files) > 3 else ""
            self.selected_label_text = f"已选择：{elide(names, 50)}{more}"
        else:
            self.selected_label_text = "尚未选择文件"
        is_folder = bool(self._directory)
        self.category_box.setEnabled(not is_folder)
        self.category_hint.setText(
            f"文件夹导入时会以「{Path(self._directory).name}」作为新分类，忽略上方选择"
            if is_folder
            else "文件导入到所选分类下（多个文件共用此分类）",
        )
        self._refresh_details()

    def _on_name_by_time_changed(self, checked: bool) -> None:
        config.set(config.nameByTime, bool(checked))

    def refresh(self) -> None:
        """按当前数据库状态重建用户/分类/标签候选项（与其它页面保持同一入口）。"""
        self._reload_users()
        self._reload_tags()

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
        for node in taxonomy.tree(user_id=user_id):
            prefix = "　" * node.depth
            self.category_box.addItem(f"{prefix}{node.category.name}", userData=node.category.id)
        wanted = current if current is not None else (uncategorized.id if uncategorized else None)
        for index in range(self.category_box.count()):
            if self.category_box.itemData(index) == wanted:
                self.category_box.setCurrentIndex(index)
                break

    def _reload_tags(self) -> None:
        repo = TagRepository(self.session)
        user_id = self.target_user_id()
        self.tag_input.set_known_tags(
            repo.names(user_id=user_id),
            global_tags=set(repo.global_names()),
        )

    # -------------------------------------------------------------- 文件信息
    def _collect_sources(self) -> list[tuple[Path, str]]:
        """当前待导入的 (文件, 子目录) 列表：文件夹会展开并保留相对子目录。"""
        if self._directory:
            return self._tree_files
        return [(Path(path), "") for path in self._files]

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
            toast_warning(self, "正在导入", "请等待当前批量导入结束")
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
            toast_warning(self, "没有可导入的内容", "请输入文本，或切换到批量导入")
            return
        service = ImportService(self.session, library=LibraryService(self.session).ensure_default())
        busy = None
        try:
            busy = BusyTip(self, "正在导入文本", elide(self.name_edit.text().strip() or "未命名", 40))
            item = service.import_text(
                self.name_edit.text().strip(),
                content,
                category_id=self.category_box.currentData(),
                **common,
            )
            if item is None:
                busy.finish("已跳过重复内容")
                toast_warning(self, "已跳过", "相同内容的数据已存在")
                return
            self.session.commit()
            busy.finish(f"已导入「{item.name}」")
            signalBus.itemsChanged.emit()
            signalBus.librariesChanged.emit()
            self._maybe_archive(f"导入文本「{item.name}」")
            self._reset_text()
            toast_success(self, "导入成功", f"已导入文本「{item.name}」（{type_name(item.type)}）")
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            if busy is not None:
                busy.finish("导入失败")
            toast_error(self, "导入失败", str(exc))

    def _start_batch(self, user_id: int | None, common: dict) -> None:
        if self._directory:
            kind = "folder"
            sources = [self._directory]
            options = {**common, "name": Path(self._directory).name}
        else:
            kind = "files"
            sources = list(self._files)
            options = {**common, "category_id": self.category_box.currentData()}

        self.progress_card.show()
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"准备导入 {len(sources) if kind == 'files' else len(self._tree_files)} 个文件…")

        worker = _ImportWorker(kind, sources, options)
        worker.progressed.connect(self._on_progress)
        worker.evented.connect(self._on_event)
        worker.finished_job.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        percent = 100 if total <= 0 else int(done / total * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{done}/{total} · 正在处理 {elide(name, 40)}")

    def _on_event(self, source: str, status: str, detail: str) -> None:
        from PyQt6.QtWidgets import QTableWidgetItem

        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        for column, text in enumerate((source, _STATUS_LABELS.get(status, status), detail)):
            cell = QTableWidgetItem(str(text))
            cell.setToolTip(str(text))
            self.result_table.setItem(row, column, cell)
        self.result_table.scrollToBottom()

    def _on_finished(self, payload: dict) -> None:
        self.progress_bar.setValue(100)
        failed = payload.get("failed") or []
        summary = f"成功 {payload.get('ok', 0)}，跳过 {payload.get('skipped', 0)}，失败 {len(failed)}"
        if payload.get("category"):
            summary += f"；新分类「{payload['category']}」"
        self.progress_label.setText(summary)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

        if payload.get("error"):
            toast_error(self, "导入失败", str(payload["error"]))
            return

        self.session.expire_all()
        signalBus.itemsChanged.emit()
        signalBus.librariesChanged.emit()
        signalBus.categoriesChanged.emit()
        self._reload_categories()
        self._maybe_archive(
            f"批量导入：{payload.get('category') or self.selected_label_text}"[:200]
        )
        self._clear_sources()
        fit_columns(self.result_table, max_width=320, weights={0: 0.4, 2: 0.3})
        if payload.get("ok"):
            toast_success(self, "导入完成", summary)
        elif failed:
            toast_error(self, "导入失败", f"{Path(failed[0][0]).name}：{failed[0][1]}")
        else:
            toast_warning(self, "没有新数据", summary)
        if failed and payload.get("ok"):
            first = failed[0]
            toast_error(self, "部分文件导入失败", f"{Path(first[0]).name}：{first[1]}")

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
