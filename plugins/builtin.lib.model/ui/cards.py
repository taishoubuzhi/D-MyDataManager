"""模型卡片：一张卡片就是一条登记项，按钮按状态给出下一步动作。"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from dm_plugin.builtin.lib.ui.plugin import ClickCard, status_label, strong_label, tool_button

from ..constants import ADAPTER_LABELS, STATE_DRAFT, STATE_DOWNLOADING, STATE_ERROR, STATE_LOADING, STATE_READY

__all__ = ["ModelCard"]


class ModelCard(ClickCard):
    """模型卡片：`actionRequested(model_id, action)` 把操作交回页面。"""

    actionRequested = pyqtSignal(str, str)

    def __init__(self, record=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record = record
        self.setMinimumWidth(280)
        self.setMinimumHeight(168)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.name_label = strong_label(self)
        self.state_label = status_label(self)
        head.addWidget(self.name_label, 1)
        head.addWidget(self.state_label, 0)
        layout.addLayout(head)

        self.meta_label = status_label(self)
        layout.addWidget(self.meta_label)

        self.capability_label = status_label(self)
        layout.addWidget(self.capability_label)

        self.detail_label = status_label(self)
        layout.addWidget(self.detail_label)

        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self._buttons: dict = {}
        for key, icon, tooltip in (
            ("download", FluentIcon.DOWNLOAD, "下载 / 更换权重"),
            ("load", FluentIcon.PLAY, "加载到内存（用时才加载，也可以在这里手动加载）"),
            ("unload", FluentIcon.CANCEL, "卸载并释放显存"),
            ("test", FluentIcon.LINK, "测试能不能调通"),
            ("log", FluentIcon.DOCUMENT, "看最新的运行日志"),
            ("open_dir", FluentIcon.FOLDER, "打开权重目录"),
            ("edit", FluentIcon.EDIT, "编辑信息"),
            ("delete", FluentIcon.DELETE, "删除这条模型记录"),
        ):
            button = tool_button(self, icon, tooltip, lambda name=key: self._emit(name))
            actions.addWidget(button)
            self._buttons[key] = button
        actions.addStretch(1)
        layout.addLayout(actions)

        if record is not None:
            self.set_record(record)

    # ------------------------------------------------------------ 刷新
    def set_record(self, record, *, running: bool = False, has_log: bool = False) -> None:
        """原地刷新卡片（复用控件，避免整页重建）。"""
        self.record = record
        self.name_label.setText(record.name)
        self.name_label.setToolTip(record.description or record.name)
        self.state_label.setText(record.state_label)
        self._paint_state(record)
        self.meta_label.setText(f"{record.kind_label} · {record.size_text} · {self._adapter_text(record)}")
        self.capability_label.setText(record.capability_text)
        self.detail_label.setText(self._detail_text(record, running))
        self._apply_actions(record, running, has_log)

    def _paint_state(self, record) -> None:
        color = {
            STATE_READY: "#107c10",
            STATE_DRAFT: "#8a8a8a",
            STATE_DOWNLOADING: "#0f6cbd",
            STATE_LOADING: "#c07f00",
            STATE_ERROR: "#c42b1c",
        }.get(record.state, "#8a8a8a")
        self.state_label.setStyleSheet(f"color: {color};")

    def _adapter_text(self, record) -> str:
        adapter = record.adapter
        return ADAPTER_LABELS.get(adapter, adapter) if adapter else "—"

    def _detail_text(self, record, running: bool) -> str:
        if record.is_local:
            file_name = record.primary_file()
            where = file_name.name if file_name is not None else "（权重还没有就位）"
            return f"{'运行中 · ' if running else ''}文件：{where}"
        base = str(record.api.get("base_url") or "（未填接口地址）")
        return f"{'运行中 · ' if running else ''}{base}"

    def _apply_actions(self, record, running: bool, has_log: bool = False) -> None:
        local = record.is_local
        ready = record.state == STATE_READY
        # 权重在不在只看文件和磁盘，不看状态：上次加载失败（state=error）时权重还在，
        # 之前按 ready 判断会让「下载/加载/测试」整排按钮一起消失，只能刷新页面才回来。
        has_weights = bool(record.files) or (local and record.primary_file() is not None)
        download = self._buttons["download"]
        download.setVisible(local)
        download.setEnabled(not running and record.state != STATE_DOWNLOADING)
        if has_weights:
            download.setIcon(FluentIcon.SYNC)
            download.setToolTip("更换权重：换磁盘上已有的文件，或按仓库信息重新下载")
        else:
            download.setIcon(FluentIcon.DOWNLOAD)
            download.setToolTip("下载权重")
        # 「加载」和「测试」都由「权重在不在」决定：错误状态也能立刻重试，不用先刷新页面。
        self._buttons["load"].setVisible(local and has_weights and not running and record.state != STATE_LOADING)
        self._buttons["unload"].setVisible(local and running)
        self._buttons["test"].setVisible(not local or has_weights)
        load = self._buttons["load"]
        if not ready:
            load.setToolTip("加载这个模型（上次没跑起来的话，改好设置再点一次）")
        else:
            load.setToolTip("加载这个模型")
        self._buttons["log"].setVisible(bool(has_log))
        self._buttons["open_dir"].setVisible(local)
        self._buttons["edit"].setVisible(True)
        self._buttons["delete"].setVisible(True)

    def action_button(self, key: str) -> QWidget:
        """给自检 / 页面用：直接拿某个按钮。"""
        return self._buttons[key]

    def _emit(self, action: str) -> None:
        if self.record is not None:
            self.actionRequested.emit(self.record.id, action)
