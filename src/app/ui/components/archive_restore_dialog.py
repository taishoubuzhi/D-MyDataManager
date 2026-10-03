"""回档变更弹窗：列出这次回档会改动什么，并让用户选择是否先给当前数据存档。"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QHeaderView, QTreeWidgetItem, QWidget
from qfluentwidgets import (
    BodyLabel,
    MessageBoxBase,
    PushButton,
    RadioButton,
    SubtitleLabel,
    TreeWidget,
)

MODE_RESTORE = "restore"
MODE_MIRROR = "mirror"

MODE_NAMES = {
    MODE_RESTORE: "恢复式（保留现有数据）",
    MODE_MIRROR: "覆盖式（以存档为镜像）",
}


class RestoreDialog(MessageBoxBase):
    """展示一份回档预览，并把用户的选择记在 ``choice`` 与 ``mode`` 上。

    ``choice`` 取值：``cancel``（取消，默认）/ ``restore``（确认回档）/
    ``snapshot``（先给当前数据存档，再回档）。
    ``mode`` 取值：``restore``（恢复式，默认）/ ``mirror``（覆盖式）。

    ``allow_mirror`` 为真时给出方式切换（只有管理员整档回档才允许覆盖式）；
    切换后调用 ``preview(mode)`` 重新预演，并用新报告刷新头部、摘要与变更清单。
    """

    CANCEL = "cancel"
    RESTORE = "restore"
    SNAPSHOT = "snapshot"

    def __init__(
        self,
        report,
        parent: QWidget | None = None,
        *,
        allow_mirror: bool = False,
        preview: Callable[[str], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.choice = self.CANCEL
        self.mode = getattr(report, "mode", MODE_RESTORE) or MODE_RESTORE
        self._allow_mirror = bool(allow_mirror)
        self._preview = preview

        self.titleLabel = SubtitleLabel("回档变更", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.sourceLabel = BodyLabel(self._source_text(), self)
        self.viewLayout.addWidget(self.sourceLabel)
        if self._allow_mirror:
            self.viewLayout.addLayout(self._build_mode_row())
        self.summaryLabel = BodyLabel(self._summary_text(), self)
        self.viewLayout.addWidget(self.summaryLabel)

        self.tree = TreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["文件", "分类", "归属"])
        # 文件名列撑满剩余宽度（不被截断），分类 / 归属按内容自适应
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumHeight(220)
        self._fill_tree()
        self.viewLayout.addWidget(self.tree)

        self.snapshotButton = PushButton("先存档再回档", self)
        self.snapshotButton.clicked.connect(self._choose_snapshot)
        self.buttonLayout.insertWidget(1, self.snapshotButton, 1, Qt.AlignmentFlag.AlignVCenter)

        self.yesButton.clicked.connect(self._choose_restore)
        self.yesButton.setText("确认回档")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(560)
        self._sync_buttons()

    def _build_mode_row(self) -> QHBoxLayout:
        """整档回档的方式切换：恢复式（默认）/ 覆盖式。"""
        row = QHBoxLayout()
        row.addWidget(BodyLabel("回档方式：", self))
        self.restoreRadio = RadioButton(MODE_NAMES[MODE_RESTORE], self)
        self.mirrorRadio = RadioButton(MODE_NAMES[MODE_MIRROR], self)
        self.restoreRadio.setChecked(self.mode != MODE_MIRROR)
        self.mirrorRadio.setChecked(self.mode == MODE_MIRROR)
        self.restoreRadio.toggled.connect(self._on_mode_toggled)
        self.mirrorRadio.toggled.connect(self._on_mode_toggled)
        row.addWidget(self.restoreRadio)
        row.addWidget(self.mirrorRadio)
        row.addStretch(1)
        return row

    def _on_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._set_mode(MODE_MIRROR if self.sender() is self.mirrorRadio else MODE_RESTORE)

    def _set_mode(self, mode: str) -> None:
        """切换方式：重新预演后用新报告刷新弹窗内容。"""
        if mode == self.mode:
            return
        if self._preview is None:
            self.mode = mode
            return
        report = self._preview(mode)
        if report is None:  # 预演失败，退回原方式
            self.restoreRadio.setChecked(True)
            return
        self.mode = mode
        self.report = report
        self.sourceLabel.setText(self._source_text())
        self.summaryLabel.setText(self._summary_text())
        self._fill_tree()
        self._sync_buttons()

    def _source_text(self) -> str:
        return f"来源：{self.report.source or '—'}    方式：{MODE_NAMES.get(self.mode, MODE_NAMES[MODE_RESTORE])}"

    def _sync_buttons(self) -> None:
        """没有变更时不允许确认。"""
        enabled = not self.report.is_empty
        self.yesButton.setEnabled(enabled)
        self.snapshotButton.setEnabled(enabled)

    def _summary_text(self) -> str:
        grouped = self.report.grouped()
        if not grouped:
            return "没有需要变更的数据。"
        detail = "，".join(f"{kind} {count}" for kind, count in grouped)
        text = f"共 {self.report.total} 项变更：{detail}"
        if self.report.skipped:
            text += f"（跳过 {self.report.skipped} 项）"
        if not self.report.actionable:
            text += "；其中没有可回档的变更，确认按钮已禁用。"
        return text

    def _fill_tree(self) -> None:
        """按变更种类分组列出每条变更（文件 / 分类 / 归属）。"""
        self.tree.clear()
        heads: dict[str, QTreeWidgetItem] = {}
        for change in self.report.changes:
            head = heads.get(change.kind)
            if head is None:
                head = QTreeWidgetItem([change.kind, "", ""])
                heads[change.kind] = head
                self.tree.addTopLevelItem(head)
            owner = change.user or "—"
            if change.archive_name:
                owner = f"{owner} · {change.archive_name}"
            child = QTreeWidgetItem(head, [change.name, change.category or "—", owner])
            # 列宽自适应时文字仍可能被省略，完整内容挂到悬停提示上
            for column, text in enumerate((change.name, change.category or "—", owner)):
                child.setToolTip(column, text)
        self.tree.expandAll()
        self._fit_tree_height()

    def _fit_tree_height(self) -> None:
        """按内容给清单定高（220–420）：条目再多就靠滚动，既不撑爆弹窗也不只露一角。"""
        rows = 0
        for index in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(index)
            rows += 1 + (head.childCount() if head is not None else 0)
        line = self.tree.fontMetrics().height() + 6
        height = self.tree.header().height() + rows * line + 8
        self.tree.setFixedHeight(min(max(height, 220), 420))

    def _choose_restore(self) -> None:
        self.choice = self.RESTORE

    def _choose_snapshot(self) -> None:
        self.choice = self.SNAPSHOT
        self.accept()
