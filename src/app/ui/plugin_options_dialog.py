"""插件选项对话框：按清单声明的选项自动生成控件，并写回插件设置。

两部分内容：

* **插件选项** —— 插件在 `plugin.json` 的 `options` 里声明，用户改完存在插件状态文件里，
  插件下次载入时用 `api.option("键")` 读回来。
* **打开方式** —— 该插件注册了查看器时，程序本体通过 `app.open_with` 扩展接口让用户
  把某个扩展名（或整组）指定成「用这个插件打开」，不必去「打开方式」页逐个格式设置。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
)

from ..core.extensions import extension_registry
from ..core.plugin_options import OPTION_BOOL, OPTION_CHOICE
from ..core.plugins import PluginInfo
from ..core.signals import signalBus
from ..services.open_with_service import OPEN_WITH_EXTENSION, open_with_api
from ..services.plugin_service import plugin_service
from .framework import clear_scroll_background, toast_error, toast_success


class PluginOptionsDialog(MessageBoxBase):
    """一个插件的「插件选项」页。"""

    def __init__(self, info: PluginInfo, service=plugin_service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.service = service
        # 扩展接口由主程序在启动时登记；测试 / 自检里没登记时退回模块级接口实现
        self._open_with = extension_registry.provider(OPEN_WITH_EXTENSION) or open_with_api
        self._editors: dict[str, Callable[[], object]] = {}
        self._checks: dict[str, tuple[CheckBox, str]] = {}
        self._viewers = service.viewers_of(info.id)

        self.widget.setMinimumWidth(560)
        self.titleLabel = SubtitleLabel(info.options_label, self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(f"{info.name} · {info.id} · {info.kind_label}", self))

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setMinimumHeight(220)
        holder = QWidget(area)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        has_options = self._build_options(layout)
        has_viewers = self._build_viewers(layout)
        if not has_options and not has_viewers:
            layout.addWidget(CaptionLabel("该插件还没有声明可配置的选项，也没有可指定的打开方式。", holder))
        layout.addStretch(1)
        area.setWidget(holder)
        clear_scroll_background(area)
        self.viewLayout.addWidget(area)

        self.reset_button = PushButton(FluentIcon.RETURN, "恢复默认", self)
        self.reset_button.clicked.connect(self._on_reset)
        self.reset_button.setEnabled(has_options or has_viewers)
        self.buttonLayout.insertWidget(0, self.reset_button)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")

    # -------------------------------------------------------------- 选项控件
    def _build_options(self, layout: QVBoxLayout) -> bool:
        specs = self.info.options
        if not specs:
            return False
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)
        card_layout.addWidget(BodyLabel("插件选项", card))
        for spec in specs:
            row = QHBoxLayout()
            row.addWidget(BodyLabel(f"{spec.name}", card))
            value = self.info.settings.get(spec.key, spec.default)
            if spec.kind == OPTION_BOOL:
                editor = CheckBox("启用" if value else "关闭", card)
                editor.setChecked(bool(value))
                editor.toggled.connect(
                    lambda checked, box=editor: box.setText("启用" if checked else "关闭")
                )
                self._editors[spec.key] = editor.isChecked
            elif spec.kind == OPTION_CHOICE:
                editor = ComboBox(card)
                for item_value, item_label in spec.choices:
                    editor.addItem(item_label, userData=item_value)
                index = editor.findData(str(value))
                editor.setCurrentIndex(index if index >= 0 else 0)
                self._editors[spec.key] = editor.currentData
            else:
                editor = LineEdit(card)
                editor.setText("" if value is None else str(value))
                editor.setMinimumWidth(200)
                self._editors[spec.key] = editor.text
            editor.setToolTip(spec.description or spec.text)
            row.addWidget(editor, 1)
            card_layout.addLayout(row)
            if spec.description:
                card_layout.addWidget(CaptionLabel(spec.description, card))
        layout.addWidget(card)
        return True

    # ---------------------------------------------------------- 打开方式控件
    def _build_viewers(self, layout: QVBoxLayout) -> bool:
        if not self._viewers or self._open_with is None:
            return False
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)
        card_layout.addWidget(BodyLabel("打开方式：勾选后这些格式用本插件打开", card))
        for viewer in self._viewers:
            card_layout.addWidget(CaptionLabel(f"{viewer.name}（{viewer.id}）", card))
            for extension in viewer.extensions:
                current = self._open_with.current_viewer_id(extension)
                box = CheckBox(f".{extension}", card)
                box.setChecked(current == viewer.id)
                if current and current != viewer.id:
                    box.setToolTip(f"当前指定给 {current}，勾选后会改用本插件")
                self._checks[extension] = (box, viewer.id)
                card_layout.addWidget(box)
        row = QHBoxLayout()
        select_all = PushButton(FluentIcon.ACCEPT, "全选", card)
        select_all.clicked.connect(lambda: self._set_all(True))
        row.addWidget(select_all)
        select_none = PushButton(FluentIcon.CANCEL, "全不选", card)
        select_none.clicked.connect(lambda: self._set_all(False))
        row.addWidget(select_none)
        row.addStretch(1)
        card_layout.addLayout(row)
        layout.addWidget(card)
        return True

    def _set_all(self, checked: bool) -> None:
        for box, _viewer_id in self._checks.values():
            box.setChecked(checked)

    # ------------------------------------------------------------------ 保存
    def _save_options(self) -> int:
        changed = 0
        for key, read in self._editors.items():
            value = read()
            if self.info.settings.get(key) == value:
                continue
            try:
                self.service.set_option(self.info.id, key, value)
            except ValueError as exc:
                toast_error(self, "保存失败", str(exc))
                continue
            changed += 1
        return changed

    def _save_viewers(self) -> int:
        if not self._checks or self._open_with is None:
            return 0
        changed = 0
        for extension, (box, viewer_id) in self._checks.items():
            current = self._open_with.current_viewer_id(extension)
            if box.isChecked():
                if current != viewer_id:
                    self._open_with.set_viewer(extension, viewer_id)
                    changed += 1
            elif current == viewer_id:
                self._open_with.reset_viewer(viewer_id, [extension])
                changed += 1
        return changed

    def _on_reset(self) -> None:
        self.service.reset_options(self.info.id)
        for extension, (_box, viewer_id) in self._checks.items():
            if self._open_with is not None:
                self._open_with.reset_viewer(viewer_id, [extension])
        self._apply()
        toast_success(self, "已恢复默认", self.info.name)

    def _apply(self) -> None:
        self.service.load_viewers()
        signalBus.pluginsChanged.emit()
        signalBus.openWithChanged.emit()

    def accept(self) -> None:
        changed = self._save_options() + self._save_viewers()
        self._apply()
        if changed:
            toast_success(self, "已保存插件选项", f"{self.info.name} · {changed} 项改动")
        super().accept()


__all__ = ["PluginOptionsDialog"]
