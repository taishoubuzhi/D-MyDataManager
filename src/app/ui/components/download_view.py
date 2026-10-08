"""下载任务列表（可复用控件）。

下载管理页的「下载列表」Tab 用它，插件里的下载视图也用同一份实现：工具条、任务表格、
逐任务操作、以及「有任务在跑就定时刷新」的节奏都封在这里。外部只要给它一个任务队列
（默认取程序本体共享的 `app.core.download.service.manager`）。

刷新走「自己开定时器轮询」而不是接引擎回调：引擎的回调来自工作线程，直接碰控件不安全，
而轮询的代价只是每 0.5 秒读一次内存里的任务列表。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    ProgressBar,
    SubtitleLabel,
    TableWidget,
    TextEdit,
)

from ...core.config import config
from ...core.download import (
    STATE_CANCELLED,
    STATE_ERROR,
    STATE_PAUSED,
    DownloadError,
    DownloadJob,
    DownloadManager,
)
from ...core.download import service as download_service
from ...core.download.settings import default_target
from ..framework import (
    IconTextButton,
    confirm,
    empty_state,
    format_size,
    open_path,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
)
from .data_table import fill_columns, prepare_table

#: 表格列：名称 / 状态 / 进度 / 速度 / 剩余 / 大小 / 保存位置 / 地址
COLUMNS = ("名称", "状态", "进度", "速度", "剩余", "大小", "保存位置", "地址")
COL_NAME, COL_STATE, COL_PROGRESS, COL_SPEED, COL_ETA, COL_SIZE, COL_TARGET, COL_URL = range(8)
#: 有任务在跑时的刷新间隔（毫秒）
REFRESH_MS = 500
#: 还能被「全部开始」拉起来的任务状态：暂停的用 resume，失败 / 取消的用 retry
RESUMABLE_STATES = (STATE_PAUSED,)
RETRYABLE_STATES = (STATE_ERROR, STATE_CANCELLED)
#: 列宽：内容长度可控的窄列直接写死，剩下的宽度按权重全给文本列（见 `_fit_columns`）。
FIXED_COLUMNS = {
    COL_STATE: 90,  # 状态
    COL_PROGRESS: 120,  # 进度
    COL_SPEED: 100,  # 速度
    COL_ETA: 80,  # 剩余
    COL_SIZE: 140,  # 大小
}
#: 名称 / 保存位置 / 地址：内容长、又真的要看，剩余宽度都留给它们
TEXT_COLUMNS = (COL_NAME, COL_TARGET, COL_URL)
#: 文本列的下限；窗口再窄就让它横向滚动，总比把内容切掉强
TEXT_MIN_WIDTH = 110


def format_eta(seconds: float) -> str:
    """剩余时间：`1:05` / `2:03:07`，未知返回 `—`。"""
    if not seconds or seconds <= 0:
        return "—"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def resolve_parent(parent):
    """给模态框找一个能当父窗口的控件。

    `MessageBoxBase` 建的时候会读 `parent.width()`，传 None 直接 AttributeError；所以调用方
    没给父窗口时要自己找一个——先问活动窗口，再退而求其次找任意可见的顶层窗口。实在没有
    （无界面的自检环境）返回 None，由调用方决定是报错还是不弹。
    """
    if parent is not None:
        return parent
    application = QApplication.instance()
    if application is None:
        return None
    window = application.activeWindow()
    if window is not None:
        return window
    for widget in application.topLevelWidgets():
        if widget.isVisible():
            return widget
    return None


def _progress_text(job: DownloadJob) -> str:
    if job.progress <= 0:
        return "—"
    return f"{job.progress * 100:.0f}%"


def _size_text(job: DownloadJob) -> str:
    if not job.total_bytes:
        return format_size(job.done_bytes) if job.done_bytes else "—"
    return f"{format_size(job.done_bytes)} / {format_size(job.total_bytes)}"


class AddDownloadDialog(MessageBoxBase):
    """新建下载：一行一个地址，开始前先选好保存位置。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        directory: str = "",
        title: str = "新建下载",
        urls: Iterable[str] = (),
        name: str = "",
        hint: str = "",
    ) -> None:
        parent = resolve_parent(parent)
        if parent is None:
            raise RuntimeError("没有可用的窗口，弹不出新建下载对话框")
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(
            BodyLabel("下载地址（一行一个；多个地址会按镜像规则展开，失败时自动切换），支持 http / https。", self)
        )
        if hint:
            tip = CaptionLabel(hint, self)
            tip.setWordWrap(True)
            self.viewLayout.addWidget(tip)

        self.urlEdit = TextEdit(self)
        self.urlEdit.setPlaceholderText("https://example.com/file.bin")
        self.urlEdit.setMinimumHeight(120)
        self.urlEdit.textChanged.connect(self._sync_buttons)
        self.viewLayout.addWidget(self.urlEdit)

        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText("保存文件名（留空则按地址里的文件名）")
        self.nameEdit.textChanged.connect(self._sync_buttons)
        self.viewLayout.addWidget(self.nameEdit)

        row = QHBoxLayout()
        row.addWidget(BodyLabel("保存位置：", self))
        self.dirEdit = LineEdit(self)
        self.dirEdit.setText(directory)
        self.dirEdit.setPlaceholderText("默认使用下载设置里的下载路径")
        row.addWidget(self.dirEdit, 1)
        self.browseButton = IconTextButton(FluentIcon.FOLDER, "浏览", self)
        self.browseButton.clicked.connect(self._choose_directory)
        row.addWidget(self.browseButton)
        self.viewLayout.addLayout(row)

        self.hintLabel = CaptionLabel("", self)
        self.hintLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.hintLabel)

        # 预填放在控件都建完之后：地址一填进去就会触发 `_sync_buttons()`，那时 `dirEdit`
        # 还没建出来，会当场 AttributeError。
        if urls:
            self.urlEdit.setPlainText("\n".join(str(item) for item in urls))
        if name:
            self.nameEdit.setText(name)

        self.yesButton.setText("开始下载")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(560)
        self._sync_buttons()

    # ------------------------------------------------------------------ 取值
    def urls(self) -> list[str]:
        """用户填的地址列表（去掉空行与首尾空白）。"""
        return [line.strip() for line in self.urlEdit.toPlainText().splitlines() if line.strip()]

    def target(self) -> Path | None:
        """保存目标；地址里没有文件名且没填文件名时返回 None。"""
        directory = self.dirEdit.text().strip()
        name = self.nameEdit.text().strip()
        if not name:
            name = self._guess_name()
        if not name:
            return None
        folder = Path(directory) if directory else Path(default_target(name).parent)
        return folder / Path(name).name

    def _guess_name(self) -> str:
        for url in self.urls():
            name = url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
            if name:
                return name
        return ""

    # ------------------------------------------------------------------ 交互
    def _choose_directory(self) -> None:
        start = self.dirEdit.text().strip() or str(default_target("x").parent)
        chosen = QFileDialog.getExistingDirectory(self, "选择保存位置", start)
        if chosen:
            self.dirEdit.setText(chosen)
            self._sync_buttons()

    def _sync_buttons(self) -> None:
        urls = self.urls()
        target = self.target() if urls else None
        if not urls:
            self.hintLabel.setText("还没有填写下载地址。")
        elif target is None:
            self.hintLabel.setText("地址里没有文件名，请填写「保存文件名」。")
        else:
            self.hintLabel.setText(f"将保存到：{target}")
        self.yesButton.setEnabled(bool(urls) and target is not None)


class DownloadListView(QWidget):
    """下载任务列表：工具条 + 表格 + 逐任务操作。"""

    def __init__(self, parent: QWidget | None = None, *, provider=None, reader=None) -> None:
        """`provider` 建/取队列（操作时用），`reader` 只读队列（刷新时用，不许建）。

        默认分别取程序本体的共享队列与「已经建好的那个」：插件想挂自己的队列时两个都传，
        刷新就不会凭空把队列建起来。
        """
        super().__init__(parent)
        self._provider = provider or download_service.manager
        self._reader = reader or download_service.current_manager
        self._jobs: list[DownloadJob] = []
        self._rows: list[DownloadJob] = []
        self._fit_width = 0
        self._table_shown = False
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        # 列宽要等控件拿到真实宽度才算得准，所以用一次性定时器把重排推到布局落定之后。
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(lambda: self._fit_columns())
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ 队列
    def manager(self) -> DownloadManager:
        """当前任务队列；第一次取的时候会顺带把上次没做完的任务接回来。"""
        return self._provider()

    def _existing_manager(self) -> DownloadManager | None:
        return self._reader()

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 空态排在功能按钮**上方**：没有任务时它占住正文区，工具条与底部按钮留在下面；
        # 有任务时它被藏起来，工具条就回到列表上方。两者不会同时出现，所以由它来吃剩余高度。
        self.empty = empty_state(
            self, "还没有下载任务：点「新建下载」填一个地址就能开始。", icon=FluentIcon.DOWNLOAD
        )
        layout.addWidget(self.empty, 1)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self.addButton = IconTextButton(FluentIcon.ADD, "新建下载", self)
        self.addButton.clicked.connect(self.add_download)
        toolbar.addWidget(self.addButton)
        self.startAllButton = IconTextButton(FluentIcon.PLAY, "全部开始", self)
        self.startAllButton.setToolTip("继续所有暂停与失败的下载")
        self.startAllButton.clicked.connect(self._on_start_all)
        toolbar.addWidget(self.startAllButton)
        self.pauseAllButton = IconTextButton(FluentIcon.PAUSE, "全部暂停", self)
        self.pauseAllButton.clicked.connect(self._on_pause_all)
        toolbar.addWidget(self.pauseAllButton)
        self.cancelAllButton = IconTextButton(FluentIcon.CANCEL, "全部取消", self)
        self.cancelAllButton.clicked.connect(self._on_cancel_all)
        toolbar.addWidget(self.cancelAllButton)
        self.clearButton = IconTextButton(FluentIcon.BROOM, "清理已结束", self)
        self.clearButton.setToolTip("把已完成 / 失败 / 已取消的任务从列表里移除（不会删掉已下载的文件）")
        self.clearButton.clicked.connect(self._on_clear_finished)
        toolbar.addWidget(self.clearButton)
        toolbar.addStretch(1)
        self.summaryLabel = CaptionLabel("", self)
        toolbar.addWidget(self.summaryLabel)
        layout.addLayout(toolbar)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        prepare_table(self.table, movable=True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setMinimumHeight(180)
        self.table.currentCellChanged.connect(lambda *_: self._sync_buttons())
        self.table.doubleClicked.connect(lambda *_: self._on_open_folder())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.pauseButton = IconTextButton(FluentIcon.PAUSE, "暂停", self)
        self.pauseButton.clicked.connect(lambda: self._on_job_action("pause"))
        actions.addWidget(self.pauseButton)
        self.resumeButton = IconTextButton(FluentIcon.PLAY, "继续", self)
        self.resumeButton.clicked.connect(lambda: self._on_job_action("resume"))
        actions.addWidget(self.resumeButton)
        self.cancelButton = IconTextButton(FluentIcon.CANCEL, "取消", self)
        self.cancelButton.clicked.connect(lambda: self._on_job_action("cancel"))
        actions.addWidget(self.cancelButton)
        self.retryButton = IconTextButton(FluentIcon.SYNC, "重试", self)
        self.retryButton.clicked.connect(lambda: self._on_job_action("retry"))
        actions.addWidget(self.retryButton)
        self.removeButton = IconTextButton(FluentIcon.DELETE, "移除", self)
        self.removeButton.setToolTip("从列表里移除这条记录（只对已结束的任务有效）")
        self.removeButton.clicked.connect(lambda: self._on_job_action("forget"))
        actions.addWidget(self.removeButton)
        actions.addStretch(1)
        self.openButton = IconTextButton(FluentIcon.FOLDER, "打开所在文件夹", self)
        self.openButton.clicked.connect(self._on_open_folder)
        actions.addWidget(self.openButton)
        layout.addLayout(actions)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """窗口或分栏一变就重排一次列宽（一次性定时器会把连续 resize 事件合并掉）。"""
        super().resizeEvent(event)
        self._fit_timer.start()

    def _fit_columns(self, *, force: bool = False) -> None:
        """按表格当前的真实宽度排列宽。

        宽度是算出来的、不是 `fit_columns` 量出来的：那个工具用 `resizeColumnsToContents()`
        按内容量，量出来的总和要么比表格还宽（右侧被滚动条切掉），要么远小于表格（右边空一大条）；
        而且它在控件拿到真实宽度之前调用等于没生效。这里固定几个窄列的宽度，把剩下的全给
        名称 / 保存位置 / 地址，表格宽度一变就重排一次。
        """
        width = self.table.viewport().width()
        if not force and width == self._fit_width:
            return
        self._fit_width = width
        fill_columns(
            self.table,
            fixed=FIXED_COLUMNS,
            flexible=TEXT_COLUMNS,
            min_width=TEXT_MIN_WIDTH,
        )

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """重新读一遍队列并刷新表格（有任务在跑时由定时器反复调用）。"""
        manager = self._existing_manager()
        jobs = list(manager.jobs()) if manager is not None else []
        self._jobs = jobs
        if [job.id for job in jobs] != [job.id for job in self._rows]:
            self._rebuild(jobs)
        else:
            self._update(jobs)
        self._apply_visibility()
        self._sync_buttons()
        self._sync_timer()

    def _apply_visibility(self) -> None:
        has_jobs = bool(self._jobs)
        if has_jobs != self._table_shown:
            self._table_shown = has_jobs
            # 表格刚露面 / 刚收起来：等布局落定再按真实宽度排一次列宽
            self._fit_timer.start()
        self.table.setVisible(has_jobs)
        self.empty.setVisible(not has_jobs)
        active = sum(1 for job in self._jobs if not job.finished)
        finished = len(self._jobs) - active
        self.summaryLabel.setText(
            f"共 {len(self._jobs)} 项，进行中 {active} 项，已结束 {finished} 项" if has_jobs else ""
        )
        self.summaryLabel.setToolTip(f"下载路径：{config.downloadPath.value or '（默认）'}")

    def _rebuild(self, jobs: list[DownloadJob]) -> None:
        """任务集合变了：整表重建（行数、进度条控件都重来）。"""
        self.table.setRowCount(len(jobs))
        self._rows = list(jobs)
        for row, job in enumerate(jobs):
            bar = ProgressBar(self.table)
            bar.setValue(0)
            bar.setFixedHeight(6)
            self.table.setCellWidget(row, COL_PROGRESS, bar)
            self.table.setItem(row, COL_NAME, QTableWidgetItem(""))
            self.table.setItem(row, COL_STATE, QTableWidgetItem(""))
            self.table.setItem(row, COL_SPEED, QTableWidgetItem(""))
            self.table.setItem(row, COL_ETA, QTableWidgetItem(""))
            self.table.setItem(row, COL_SIZE, QTableWidgetItem(""))
            self.table.setItem(row, COL_TARGET, QTableWidgetItem(""))
            self.table.setItem(row, COL_URL, QTableWidgetItem(""))
        self._fit_columns(force=True)
        self._update(jobs)

    def _update(self, jobs: list[DownloadJob]) -> None:
        """任务集合没变：就地改单元格，避免每秒重建控件。"""
        for row, job in enumerate(jobs):
            if row >= self.table.rowCount():
                break
            self._set_text(row, COL_NAME, job.label or job.target.name, job.target.name)
            state = job.state_label
            if job.error and job.state == STATE_ERROR:
                state = f"{state}：{job.error}"
            self._set_text(row, COL_STATE, job.state_label, job.detail_label or state)
            bar = self.table.cellWidget(row, COL_PROGRESS)
            if isinstance(bar, ProgressBar):
                bar.setValue(int(round(job.progress * 100)))
            self._set_text(row, COL_PROGRESS, _progress_text(job), f"{job.progress * 100:.1f}%")
            self._set_text(
                row,
                COL_SPEED,
                f"{format_size(job.speed)}/s" if job.speed > 0 else "—",
                "当前速度",
            )
            self._set_text(row, COL_ETA, format_eta(job.eta), "预计剩余时间")
            self._set_text(row, COL_SIZE, _size_text(job), "已下载 / 总大小")
            self._set_text(row, COL_TARGET, str(job.target), str(job.target))
            self._set_text(row, COL_URL, job.last_url or (job.urls[0] if job.urls else ""), "\n".join(job.urls))

    def _set_text(self, row: int, column: int, text: str, tooltip: str = "") -> None:
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(row, column, item)
        if item.text() != text:
            item.setText(text)
        if tooltip:
            item.setToolTip(tooltip)

    # ------------------------------------------------------------------ 状态
    def _selected_job(self) -> DownloadJob | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _sync_timer(self) -> None:
        active = any(not job.finished for job in self._jobs)
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def _sync_buttons(self) -> None:
        job = self._selected_job()
        running = job is not None and not job.finished
        self.pauseButton.setEnabled(running and job.state != STATE_PAUSED)
        self.resumeButton.setEnabled(job is not None and (job.state == STATE_PAUSED or job.state in RETRYABLE_STATES))
        self.cancelButton.setEnabled(running)
        self.retryButton.setEnabled(job is not None and job.state in RETRYABLE_STATES)
        self.removeButton.setEnabled(job is not None and job.finished)
        self.openButton.setEnabled(job is not None)
        resumable = any(job.state in RESUMABLE_STATES + RETRYABLE_STATES for job in self._jobs)
        self.startAllButton.setEnabled(resumable)
        self.pauseAllButton.setEnabled(any(not job.finished for job in self._jobs))
        self.cancelAllButton.setEnabled(any(not job.finished for job in self._jobs))
        self.clearButton.setEnabled(any(job.finished for job in self._jobs))

    # ------------------------------------------------------------------ 操作
    def add_download(self) -> None:
        """新建下载：先把地址与保存位置问清楚，再交给引擎排队。"""
        dialog = AddDownloadDialog(self, directory=str(default_target("x").parent))
        if not dialog.exec():
            return
        urls = dialog.urls()
        target = dialog.target()
        if not urls or target is None:
            return
        try:
            job = self.manager().enqueue(urls, target, label=target.name)
        except DownloadError as exc:
            toast_error(self, "无法开始下载", str(exc))
            return
        toast_success(self, "已加入下载", f"{job.label} → {job.target}")
        self.refresh()

    def _on_job_action(self, action: str) -> None:
        job = self._selected_job()
        if job is None:
            toast_info(self, "请先选择一个任务")
            return
        manager = self.manager()
        handlers = {
            "pause": manager.pause,
            "resume": manager.resume,
            "cancel": manager.cancel,
            "retry": manager.retry,
            "forget": manager.forget,
        }
        changed = handlers[action](job.id)
        if not changed:
            toast_warning(self, "这个操作对当前任务无效", job.detail_label)
        elif action == "forget":
            toast_info(self, "已从列表移除", job.label)
        self.refresh()

    def _on_start_all(self) -> None:
        manager = self._existing_manager()
        if manager is None:
            return
        count = 0
        for job in manager.jobs():
            if job.state in RESUMABLE_STATES:
                count += bool(manager.resume(job.id))
            elif job.state in RETRYABLE_STATES:
                count += bool(manager.retry(job.id))
        if count:
            toast_info(self, f"已继续 {count} 个任务")
        self.refresh()

    def _on_pause_all(self) -> None:
        manager = self._existing_manager()
        if manager is None:
            return
        manager.pause_all()
        self.refresh()

    def _on_cancel_all(self) -> None:
        manager = self._existing_manager()
        if manager is None or not manager.jobs():
            return
        if not confirm(self, "取消全部下载", "正在下载的任务会中断，已下载的部分会保留在本机，下次可以继续。"):
            return
        count = manager.cancel_all()
        toast_info(self, f"已取消 {count} 个任务")
        self.refresh()

    def _on_clear_finished(self) -> None:
        manager = self._existing_manager()
        if manager is None:
            return
        count = manager.clear_finished()
        if count:
            toast_info(self, f"已清理 {count} 条记录")
        self.refresh()

    def _on_open_folder(self) -> None:
        job = self._selected_job()
        if job is None:
            toast_info(self, "请先选择一个任务")
            return
        folder = job.target.parent
        if not open_path(folder):
            toast_warning(self, "打开文件夹失败", str(folder))


__all__ = [
    "COLUMNS",
    "AddDownloadDialog",
    "DownloadListView",
    "format_eta",
]
