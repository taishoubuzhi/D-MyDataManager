"""导出确认框与导出对话框（可复用控件）。

程序本体要走「导出前先让用户点一次头」，插件通过 `app.sdk.export` 发起的导出也走这里：
同一个对话框，用户能看到要导出几个包、包叫什么、落到哪个目录，也能改目录。

`ExportConfirmDialog` 只做「确认」这一件事（插件接口用）；程序本体的「导出为压缩包」
用 `ExportDialog` —— 那里能选分包方式、改命名模板、调编号起点与间隔，边改边看包名。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    SegmentedWidget,
    SpinBox,
    SubtitleLabel,
)

from ...core.export import (
    DEFAULT_TEMPLATE,
    PLAN_MODES,
    VARIABLES,
    PlannedItem,
    TemplateError,
    apply_numbering,
    name_packages,
    numbered_tokens,
    parse_start,
    plan_packages,
)
from ..framework import IconTextButton
from .download_view import resolve_parent

#: 对话框里最多列几个包名，多出来的折成一行「还有 N 个」
_PREVIEW_LIMIT = 8

#: 模板改动后隔多久真的重算预览（连打字时只算最后一次）
_PREVIEW_DELAY_MS = 160


def choose_export_directory(parent: QWidget | None = None, current: str = "") -> str:
    """弹「选择导出目录」；用户取消返回空串。"""
    window = resolve_parent(parent)
    chosen = QFileDialog.getExistingDirectory(window, "选择导出目录", str(current or ""))
    return str(chosen or "")


class ExportConfirmDialog(MessageBoxBase):
    """导出前确认：看一眼包的名字与落点，能改目录，点「开始导出」才真写盘。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "导出",
        summary: str = "",
        directory: str = "",
        names: Iterable[str] = (),
        hint: str = "",
    ) -> None:
        parent = resolve_parent(parent)
        if parent is None:
            raise RuntimeError("没有可用的窗口，弹不出导出对话框")
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)
        if summary:
            self.viewLayout.addWidget(BodyLabel(summary, self))

        rows = [str(name) for name in names if str(name)]
        self.previewLabel: CaptionLabel | None = None
        if rows:
            shown = rows[:_PREVIEW_LIMIT]
            text = "\n".join(f"· {name}" for name in shown)
            if len(rows) > len(shown):
                text += f"\n· ……还有 {len(rows) - len(shown)} 个"
            self.previewLabel = CaptionLabel(text, self)
            self.previewLabel.setWordWrap(True)
            self.viewLayout.addWidget(self.previewLabel)

        if hint:
            tip = CaptionLabel(hint, self)
            tip.setWordWrap(True)
            self.viewLayout.addWidget(tip)

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(BodyLabel("导出到：", holder))
        self.dirEdit = LineEdit(holder)
        self.dirEdit.setText(str(directory or ""))
        self.dirEdit.setPlaceholderText("默认导出目录")
        row.addWidget(self.dirEdit, 1)
        self.browseButton = IconTextButton(FluentIcon.FOLDER, "浏览", holder)
        self.browseButton.clicked.connect(self._choose_directory)
        row.addWidget(self.browseButton)
        self.viewLayout.addWidget(holder)

        self.yesButton.setText("开始导出")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(560)

    # ------------------------------------------------------------------ 取值
    def directory(self) -> str:
        """用户选的导出目录（留空表示用默认导出目录）。"""
        return self.dirEdit.text().strip()

    def names(self) -> list[str]:
        """对话框里列出的包名（纯展示用，方便调用方与界面核对）。"""
        if self.previewLabel is None:
            return []
        lines = [line.strip() for line in self.previewLabel.text().splitlines() if line.strip()]
        return [line.lstrip("·").strip() for line in lines if not line.startswith("· ……")]

    # ------------------------------------------------------------------ 交互
    def _choose_directory(self) -> None:
        chosen = choose_export_directory(self, self.dirEdit.text().strip())
        if chosen:
            self.dirEdit.setText(chosen)


class ExportDialog(MessageBoxBase):
    """导出对话框：选分包方式、改命名模板、调编号起点与间隔，边改边看包名。

    它只负责「问清楚参数」，真写盘还是 `ExportService.export_packages()` —— 对话框
    里的预览与实际包名走同一套 `app.core.export`，不会出现「预览是一个名字、导出是另一个」。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        items: Sequence[PlannedItem] = (),
        title: str = "导出为压缩包",
        summary: str = "",
        directory: str = "",
        template: str = DEFAULT_TEMPLATE,
        mode: str = "single",
        user: str = "",
        creator: str = "",
        now: dt.datetime | None = None,
    ) -> None:
        parent = resolve_parent(parent)
        if parent is None:
            raise RuntimeError("没有可用的窗口，弹不出导出对话框")
        super().__init__(parent)
        self._items = tuple(items)
        self._now = now
        self._user = str(user or "")
        self._creator = str(creator or "")
        self._syncing = False

        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)
        if summary:
            self.viewLayout.addWidget(BodyLabel(summary, self))

        # ---------------------------------------------------------- 分包方式
        self.viewLayout.addWidget(BodyLabel("分包方式", self))
        self.modeTabs = SegmentedWidget(self)
        modes = {key: label for key, label in PLAN_MODES}
        for key, label in PLAN_MODES:
            self.modeTabs.addItem(key, label)
        self.modeTabs.setCurrentItem(mode if mode in modes else "single")
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.modeTabs)
        mode_row.addStretch(1)
        self.viewLayout.addLayout(mode_row)

        # ---------------------------------------------------------- 命名模板
        self.viewLayout.addWidget(BodyLabel("命名模板", self))
        template_row = QHBoxLayout()
        self.templateEdit = LineEdit(self)
        self.templateEdit.setText(str(template or DEFAULT_TEMPLATE))
        self.templateEdit.setPlaceholderText("{creator}-{time}-{number}")
        self.templateEdit.setClearButtonEnabled(True)
        template_row.addWidget(self.templateEdit, 1)
        self._variable_names = [item.name for item in VARIABLES]
        self.variableBox = ComboBox(self)
        for item in VARIABLES:
            self.variableBox.addItem(f"{item.name} · {item.label}", userData=item.name)
        template_row.addWidget(self.variableBox)
        self.insertButton = IconTextButton(FluentIcon.ADD, "插入", self)
        self.insertButton.clicked.connect(self._insert_variable)
        template_row.addWidget(self.insertButton)
        self.viewLayout.addLayout(template_row)

        # ---------------------------------------------------------- 编号起点与间隔
        number_row = QHBoxLayout()
        number_row.addWidget(BodyLabel("起点", self))
        self.startBox = SpinBox(self)
        self.startBox.setRange(1, 9999)
        self.startBox.setValue(1)
        number_row.addWidget(self.startBox)
        number_row.addSpacing(12)
        number_row.addWidget(BodyLabel("间隔", self))
        self.stepBox = SpinBox(self)
        self.stepBox.setRange(1, 9999)
        self.stepBox.setValue(1)
        number_row.addWidget(self.stepBox)
        number_row.addStretch(1)
        self.viewLayout.addLayout(number_row)
        self.numberHint = CaptionLabel("", self)
        self.numberHint.setWordWrap(True)
        self.viewLayout.addWidget(self.numberHint)

        # ---------------------------------------------------------- 包名预览
        self.viewLayout.addWidget(BodyLabel("包名预览", self))
        self.previewLabel = CaptionLabel("", self)
        self.previewLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.previewLabel)
        self.warnLabel = CaptionLabel("", self)
        self.warnLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.warnLabel)

        # ---------------------------------------------------------- 导出目录
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(BodyLabel("导出到：", holder))
        self.dirEdit = LineEdit(holder)
        self.dirEdit.setText(str(directory or ""))
        self.dirEdit.setPlaceholderText("默认导出目录")
        row.addWidget(self.dirEdit, 1)
        self.browseButton = IconTextButton(FluentIcon.FOLDER, "浏览", holder)
        self.browseButton.clicked.connect(self._choose_directory)
        row.addWidget(self.browseButton)
        self.viewLayout.addWidget(holder)

        self.yesButton.setText("开始导出")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(620)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._refresh_preview)
        self.templateEdit.textChanged.connect(self._on_template_edited)
        self.startBox.valueChanged.connect(self._on_numbering_changed)
        self.stepBox.valueChanged.connect(self._on_numbering_changed)
        self.modeTabs.currentItemChanged.connect(self._on_mode_changed)
        self._sync_from_template()
        self._refresh_preview()

    # ------------------------------------------------------------------ 取值
    def mode(self) -> str:
        """分包方式：`single`（一个包）或 `top`（按最顶层分类分开打包）。"""
        chosen = self.modeTabs.currentRouteKey()
        return chosen if chosen in {key for key, _ in PLAN_MODES} else "single"

    def template(self) -> str:
        """当前的命名模板原文（起点 / 间隔已经写回这里）。"""
        return self.templateEdit.text().strip()

    def directory(self) -> str:
        """用户选的导出目录（留空表示用默认导出目录）。"""
        return self.dirEdit.text().strip()

    def start(self) -> int:
        return int(self.startBox.value())

    def step(self) -> int:
        return int(self.stepBox.value())

    def names(self) -> list[str]:
        """预览里的包名（调用方拿它与界面核对，不必再解一遍预览文字）。"""
        return list(self._names)

    # ------------------------------------------------------------------ 预览
    def _queue_preview(self) -> None:
        self._timer.start()

    def _refresh_preview(self) -> None:
        self._names: list[str] = []
        if not self._items:
            self.previewLabel.setText("没有要导出的数据项")
            self.warnLabel.setText("")
            return
        plan = plan_packages(self._items, mode=self.mode())
        try:
            named = name_packages(
                plan.packages,
                self.template(),
                now=self._now,
                user=self._user,
                creator=self._creator,
                used=set(),
            )
        except TemplateError as exc:
            self.previewLabel.setText(f"模板读不出来：{exc}")
            self.warnLabel.setText("")
            return
        self._names = [row.filename for row in named]
        shown = self._names[:_PREVIEW_LIMIT]
        text = "\n".join(f"· {name}" for name in shown)
        if len(self._names) > len(shown):
            text += f"\n· ……还有 {len(self._names) - len(shown)} 个"
        self.previewLabel.setText(text or "没有要导出的数据项")
        problems: list[str] = []
        for row in named:
            problems.extend(row.warnings)
        unique = list(dict.fromkeys(problems))
        self.warnLabel.setText("；".join(unique) if unique else "")

    # ------------------------------------------------------------------ 编号
    def _on_mode_changed(self, _key: str = "") -> None:
        self._queue_preview()

    def _on_template_edited(self) -> None:
        if self._syncing:
            return
        self._sync_from_template()
        self._queue_preview()

    def _on_numbering_changed(self) -> None:
        if self._syncing:
            return
        text = self.template()
        if not numbered_tokens(text):
            return
        self._syncing = True
        try:
            self.templateEdit.setText(apply_numbering(text, self.start(), self.step()))
        finally:
            self._syncing = False
        self._queue_preview()

    def _sync_from_template(self) -> None:
        """模板里的编号写法决定起点 / 间隔控件的可用状态与当前值。"""
        tokens = numbered_tokens(self.template())
        usable = bool(tokens)
        self.startBox.setEnabled(usable)
        self.stepBox.setEnabled(usable)
        if not usable:
            self.numberHint.setText("模板里没有编号变量（number / alpha / roman…），起点与间隔用不上")
            return
        self.numberHint.setText("改这两个数会把模板里的编号写成「{变量,起点,间隔}」")
        token = tokens[0]
        variable = token.variable
        args = token.args
        start = parse_start(variable.name, args[0]) if args else 1
        step = 1
        if len(args) > 1:
            try:
                step = max(1, int(args[1]))
            except ValueError:
                step = 1
        self._syncing = True
        try:
            self.startBox.setValue(max(1, start))
            self.stepBox.setValue(step)
        finally:
            self._syncing = False

    def _insert_variable(self) -> None:
        name = str(self.variableBox.currentData() or "")
        if not name:
            return
        self.templateEdit.insert(f"{{{name}}}")
        self._sync_from_template()
        self._queue_preview()

    # ------------------------------------------------------------------ 交互
    def _choose_directory(self) -> None:
        chosen = choose_export_directory(self, self.dirEdit.text().strip())
        if chosen:
            self.dirEdit.setText(chosen)

