"""pip 安装任务列表（可复用控件）。

下载器那套视图（`download_view.py`）的姊妹件：pip 安装器没有自己的页面，但插件发起安装后
总要让用户看见进度、能暂停 / 取消。这个控件就把 `app.services.pip_api` 的任务表列出来，
插件直接 `ui.pip_task_list()` 就能用。

刷新同样走「自己开定时器轮询」：安装任务的状态由工作线程改，直接碰控件不安全，而轮询的
代价只是每 0.7 秒读一次内存里的任务表。
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, FluentIcon, TableWidget

from ..framework import (
    IconTextButton,
    empty_state,
    open_path,
    toast_error,
    toast_info,
    toast_success,
    toast_warning,
)
from .data_table import fill_columns, prepare_table

#: 表格列：名称 / 状态 / 解释器 / 最近输出
COLUMNS = ("名称", "状态", "解释器", "最近输出")
COL_NAME, COL_STATE, COL_PYTHON, COL_TAIL = range(4)
#: 有任务在跑时的刷新间隔（毫秒）
REFRESH_MS = 700
#: 列宽：内容长度可控的窄列直接写死，剩下的宽度全给名称 / 最近输出（见 `_fit_columns`）
FIXED_COLUMNS = {
    COL_STATE: 100,  # 状态
    COL_PYTHON: 180,  # 解释器
}
TEXT_COLUMNS = (COL_NAME, COL_TAIL)
#: 文本列的下限；窗口再窄就让它横向滚动，总比把内容切掉强
TEXT_MIN_WIDTH = 140


def _default_api():
    """程序本体共享的 pip 接口（延迟导入，免得界面模块在导入期就拉起服务层）。"""
    from ...services.pip_api import api

    return api()


def _tail_line(task) -> str:
    """最近一行输出（表格里只放一行，完整日志在日志文件里）。"""
    text = str(getattr(task, "tail", "") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        return lines[-1][:200]
    error = str(getattr(task, "error", "") or "")
    if error:
        return error[:200]
    return "—"


def _python_text(task) -> str:
    text = str(getattr(task, "python", "") or "")
    return text or "—"


class PipTaskView(QWidget):
    """pip 安装任务列表：工具条 + 表格 + 逐任务操作。"""

    def __init__(self, parent: QWidget | None = None, *, provider=None, reader=None) -> None:
        """`provider` 取任务表（操作时用），`reader` 只读任务表（刷新时用）。

        默认都取程序本体共享的那一个；插件想挂自己的安装器时两个都传，刷新就不会凭空建表。
        """
        super().__init__(parent)
        self._provider = provider or _default_api
        self._reader = reader or _default_api
        self._tasks: list = []
        self._rows: list = []
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        #: 上一次排好的表格宽度、表格当前是否露着，以及把列宽重排推迟到布局稳定之后的定时器
        self._fit_width = 0
        self._table_shown = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(lambda: self._fit_columns())
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ 接口
    def api(self):
        """当前的 pip 接口（取的时候就顺带把任务表建起来了）。"""
        return self._provider()

    def _existing_api(self):
        return self._reader()

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 空态排在工具条上面：没有任务时它是主角，工具条落在下面当控制条，别让提示跑到按钮底下。
        self.empty = empty_state(self, "还没有安装任务：插件发起安装后会出现在这里。", icon=FluentIcon.DOWNLOAD)
        layout.addWidget(self.empty, 1)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self.cancelAllButton = IconTextButton(FluentIcon.CANCEL, "全部取消", self)
        self.cancelAllButton.clicked.connect(self._on_cancel_all)
        toolbar.addWidget(self.cancelAllButton)
        self.clearButton = IconTextButton(FluentIcon.BROOM, "清理已结束", self)
        self.clearButton.setToolTip("把已完成 / 失败 / 已取消的任务从列表里移除")
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
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.pauseButton = IconTextButton(FluentIcon.PAUSE, "暂停", self)
        self.pauseButton.clicked.connect(lambda: self._on_action("pause"))
        actions.addWidget(self.pauseButton)
        self.resumeButton = IconTextButton(FluentIcon.PLAY, "继续", self)
        self.resumeButton.clicked.connect(lambda: self._on_action("resume"))
        actions.addWidget(self.resumeButton)
        self.cancelButton = IconTextButton(FluentIcon.CANCEL, "取消", self)
        self.cancelButton.clicked.connect(lambda: self._on_action("cancel"))
        actions.addWidget(self.cancelButton)
        self.removeButton = IconTextButton(FluentIcon.DELETE, "移除", self)
        self.removeButton.setToolTip("从列表里移除这条记录（只对已结束的任务有效）")
        self.removeButton.clicked.connect(lambda: self._on_action("forget"))
        actions.addWidget(self.removeButton)
        actions.addStretch(1)
        self.logButton = IconTextButton(FluentIcon.DOCUMENT, "查看日志", self)
        self.logButton.clicked.connect(self._on_open_log)
        actions.addWidget(self.logButton)
        layout.addLayout(actions)

    # ------------------------------------------------------------------ 列宽
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 的命名
        """窗口一变宽就重排一次列宽。"""
        super().resizeEvent(event)
        self._fit_timer.start()

    def _fit_columns(self, *, force: bool = False) -> None:
        """按表格当前的真实宽度排列宽。

        宽度是算出来的、不是 `fit_columns` 量出来的：那个工具用 `resizeColumnsToContents()`
        按内容量，量出来的总和要么比表格还宽（右侧被滚动条切掉），要么远小于表格（右边空一大条）；
        而且它在控件拿到真实宽度之前调用等于没生效。这里固定两个窄列的宽度，把剩下的全给
        名称 / 最近输出，表格宽度一变就重排一次。
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
        """重新读一遍任务表并刷新（有任务在跑时由定时器反复调用）。"""
        api = self._existing_api()
        tasks = list(api.jobs()) if api is not None else []
        self._tasks = tasks
        if [task.id for task in tasks] != [task.id for task in self._rows]:
            self._rebuild(tasks)
        else:
            self._update(tasks)
        self._apply_visibility()
        self._sync_buttons()

    def _apply_visibility(self) -> None:
        has_rows = bool(self._tasks)
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        if has_rows != self._table_shown:
            # 表格刚从藏到显（等于换了一次真实宽度），等布局稳定后重排一次列宽。
            self._table_shown = has_rows
            self._fit_timer.start()
        active = [task for task in self._tasks if not getattr(task, "finished", False)]
        if active:
            done = len(self._tasks) - len(active)
            self.summaryLabel.setText(f"{len(active)} 项进行中" + (f"，{done} 项已结束" if done else ""))
        else:
            self.summaryLabel.setText(f"{len(self._tasks)} 项任务" if self._tasks else "")

    def _rebuild(self, tasks: list) -> None:
        self.table.setRowCount(len(tasks))
        self._rows = list(tasks)
        for row, task in enumerate(tasks):
            self.table.setItem(row, COL_NAME, QTableWidgetItem(str(task.title or task.id)))
            self.table.setItem(row, COL_STATE, QTableWidgetItem(str(task.state_label)))
            self.table.setItem(row, COL_PYTHON, QTableWidgetItem(_python_text(task)))
            self.table.setItem(row, COL_TAIL, QTableWidgetItem(_tail_line(task)))
        self._fit_columns(force=True)

    def _update(self, tasks: list) -> None:
        self._rows = list(tasks)
        for row, task in enumerate(tasks):
            self._set_text(row, COL_STATE, str(task.state_label))
            self._set_text(row, COL_TAIL, _tail_line(task))

    def _set_text(self, row: int, column: int, text: str) -> None:
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            self.table.setItem(row, column, item)
        elif item.text() != text:
            item.setText(text)

    def _selected_task(self):
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _sync_timer(self) -> None:
        active = [task for task in self._tasks if not getattr(task, "finished", False)]
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def _sync_buttons(self) -> None:
        self._sync_timer()
        task = self._selected_task()
        running = bool(task is not None and str(task.state) == "running")
        paused = bool(task is not None and str(task.state) == "paused")
        finished = bool(task is not None and getattr(task, "finished", False))
        self.pauseButton.setEnabled(running)
        self.resumeButton.setEnabled(paused)
        self.cancelButton.setEnabled(task is not None and not finished)
        self.removeButton.setEnabled(finished)
        self.logButton.setEnabled(bool(task is not None and str(task.log or "")))
        self.clearButton.setEnabled(any(getattr(task, "finished", False) for task in self._tasks))
        self.cancelAllButton.setEnabled(any(not getattr(task, "finished", False) for task in self._tasks))

    # ------------------------------------------------------------------ 动作
    def _on_action(self, action: str) -> None:
        task = self._selected_task()
        if task is None:
            return
        api = self.api()
        verb = {
            "pause": "暂停",
            "resume": "继续",
            "cancel": "取消",
            "forget": "移除",
        }.get(action, action)
        ok = False
        if action == "forget":
            ok = bool(api.forget(task.id))
        else:
            handler = getattr(api, action, None)
            ok = bool(handler(task.id)) if callable(handler) else False
        if not ok:
            toast_warning(self, f"没法{verb}", "任务状态变了，刷新后再试一次。")
        self.refresh()

    def _on_cancel_all(self) -> None:
        api = self.api()
        count = 0
        for task in list(self._tasks):
            if not getattr(task, "finished", False) and api.cancel(task.id):
                count += 1
        if count:
            toast_info(self, "已取消", f"取消了 {count} 个安装任务。")
        self.refresh()

    def _on_clear_finished(self) -> None:
        api = self.api()
        removed = api.clear_finished()
        if removed:
            toast_success(self, "已清理", f"清理了 {removed} 条记录。")
        self.refresh()

    def _on_open_log(self) -> None:
        task = self._selected_task()
        path = str(task.log or "") if task is not None else ""
        if not path:
            toast_error(self, "没有日志", "这个任务还没有日志文件。")
            return
        if not open_path(path):
            toast_warning(self, "打开失败", f"没能打开：{path}")


__all__ = [
    "COLUMNS",
    "COL_NAME",
    "COL_PYTHON",
    "COL_STATE",
    "COL_TAIL",
    "REFRESH_MS",
    "PipTaskView",
]
