"""「编辑器」配置页：按文件后缀选择用哪个编辑器打开 / 编辑。

页面本体是插件页面（`ctx.add_page`），用界面工具库的 SplitPage 与控件工厂搭出来，
规则读写全部走 `editor.open` 接口，页面自己不碰主程序。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from dm_plugin.builtin.lib.ui.plugin import (
    SplitPage,
    caption,
    combo_box,
    icon_button,
    line_edit,
    primary_button,
    toast_success,
    toast_warning,
)
from .rules import MODE_ASK, MODE_BUILTIN, MODE_CUSTOM, MODE_INHERIT, MODE_LABELS, MODES

LIST_WIDTH = 340


class EditorConfigPage(SplitPage):
    """「编辑器」配置页：左列后缀，右列编辑方式与内置编辑器。"""

    def __init__(self, ctx, api, parent=None) -> None:
        super().__init__(
            "编辑器",
            "按文件后缀选择编辑方式：内置编辑器、系统默认程序或自定义程序。",
            parent,
            list_title="文件格式",
            list_placeholder="搜索格式 / 编辑器名",
            list_width=LIST_WIDTH,
            list_count_text="",
            detail_title="未选择格式",
            detail_subtitle="先在左侧选择一个文件格式",
            on_select=self._on_select,
            on_search=lambda _text: self._fill_list(),
        )
        self._ctx = ctx
        self._api = api
        self._suffixes: list[str] = []
        self._counts: dict[str, int] = {}

        self.header.add_action(icon_button(self.header, FluentIcon.SYNC, "刷新", self.reload))
        self.header.add_hint("规则保存在 .configs/editors.json")

        self.search = self.list_panel.search
        self.suffix_list = self.list_panel.list
        self.count_label = self.list_panel.count_label

        detail = self.detail_panel
        self.mode_box = combo_box(
            detail.card,
            items=tuple(MODE_LABELS[mode] for mode in MODES),
            data=MODES,
            width=200,
            on_change=self._on_mode_changed,
        )
        detail.add_row("编辑方式", self.mode_box, label_width=88)

        self.editor_box = combo_box(detail.card, items=(), minimum_width=240, on_change=self._on_editor_changed)
        detail.add_row("内置编辑器", self.editor_box, label_width=88)

        program_row = QWidget(detail.card)
        program_layout = QHBoxLayout(program_row)
        program_layout.setContentsMargins(0, 0, 0, 0)
        program_layout.setSpacing(6)
        self.program_edit = line_edit(program_row, placeholder="系统程序路径")
        self.browse_button = icon_button(program_row, FluentIcon.FOLDER, "浏览", self._on_browse)
        program_layout.addWidget(self.program_edit, 1)
        program_layout.addWidget(self.browse_button)
        detail.add_row("自定义程序", program_row, label_width=88)

        self.args_edit = line_edit(detail.card, placeholder="程序参数，可用 {path} 占位")
        detail.add_row("参数", self.args_edit, label_width=88)

        self.status_label = caption(detail.card, "")
        detail.add_widget(self.status_label)
        detail.add_actions(
            [
                primary_button(detail.card, FluentIcon.SAVE, "保存", self._on_save),
                icon_button(detail.card, FluentIcon.SYNC, "恢复默认", self._on_reset),
                icon_button(detail.card, FluentIcon.VIEW, "测试编辑", self._on_test),
            ]
        )

    # ------------------------------------------------------------ 数据
    def showEvent(self, event):  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._suffixes:
            self.reload()

    def reload(self) -> None:
        """重新读取库内格式、已登记编辑器与规则。"""
        raw = {}
        try:
            raw = dict(self._ctx.host.file_formats() or {})
        except Exception:
            self._ctx.log.warning("读取库内文件格式失败")
        self._counts = {str(key): int(value) for key, value in raw.items()}
        names = set(self._counts) | set(self._api.rules())
        names |= {suffix for editor in self._editors() for suffix in editor.extensions}
        self._suffixes = sorted(name for name in names if name)
        self._fill_list()

    def _editors(self) -> tuple:
        try:
            return tuple(self._api.editors())
        except Exception:
            return ()

    def _editors_for(self, suffix: str) -> tuple:
        return tuple(editor for editor in self._editors() if suffix in editor.extensions)

    def _editor_name(self, editor_id: str) -> str:
        for editor in self._editors():
            if editor.id == editor_id:
                return f"{editor.name}（{editor.plugin_id}）"
        return str(editor_id or "")

    def _mode_of(self, suffix: str) -> str:
        rule = self._api.rule_for(suffix)
        if rule is not None and rule.mode:
            return rule.mode
        return MODE_BUILTIN if self._api.has_builtin(suffix) else MODE_INHERIT

    def _state_text(self, suffix: str) -> str:
        """左侧列表的格式标注：这个后缀当前会怎么打开。"""
        rule = self._api.rule_for(suffix)
        mode = self._mode_of(suffix)
        if mode == MODE_BUILTIN:
            if rule is not None and rule.editor_id:
                return f"使用内置编辑器（{self._editor_name(rule.editor_id)}）"
            editors = self._editors_for(suffix)
            if editors:
                return f"使用内置编辑器（自动：{editors[0].name}）"
            return "使用内置编辑器（没有可用编辑器，将交给系统）"
        if mode == MODE_CUSTOM:
            program = (rule.program if rule is not None else "") or ""
            return f"自定义程序（{Path(program).name}）" if program else "自定义程序（未指定程序）"
        if mode == MODE_ASK:
            return "每次询问"
        if mode == MODE_INHERIT:
            return "继承系统默认"
        return MODE_LABELS.get(mode, mode)

    def _fill_list(self) -> None:
        keyword = self.search.text().strip().lower().lstrip(".")
        entries: list[tuple] = []
        for suffix in self._suffixes:
            names = " ".join(
                f"{editor.name} {editor.plugin_id}" for editor in self._editors_for(suffix)
            )
            if keyword and keyword not in f"{suffix} {names}".lower():
                continue
            count = self._counts.get(suffix, 0)
            extra = f" · 库中 {count} 项" if count else ""
            entries.append(
                (
                    suffix,
                    f".{suffix} — {self._state_text(suffix)}{extra}",
                    f"可用编辑器：{names or '无'}",
                )
            )
        self.list_panel.set_items(entries)
        self.count_label.setText(f"{len(entries)} / {len(self._suffixes)} 个格式")

    def _current(self) -> str:
        data = self.list_panel.current_data()
        return str(data or "")

    def _on_select(self, data, _text: str) -> None:
        suffix = str(data or "")
        if not suffix:
            return
        rule = self._api.rule_for(suffix)
        if rule is not None and rule.mode:
            mode = rule.mode
        elif self._api.has_builtin(suffix):
            mode = MODE_BUILTIN
        else:
            mode = MODE_INHERIT
        if mode not in MODES:
            mode = MODE_INHERIT
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(mode)))
        self.program_edit.setText(rule.program if rule is not None else "")
        self.args_edit.setText(rule.args if rule is not None else "")
        self._fill_editors(suffix, rule.editor_id if rule is not None else "")
        self._refresh_status(suffix, mode, rule)

    def _fill_editors(self, suffix: str, selected: str = "") -> None:
        editors = tuple(self._api.editors_for(suffix))
        self.editor_box.blockSignals(True)
        self.editor_box.clear()
        for editor in editors:
            self.editor_box.addItem(editor.name, userData=editor.id)
        if selected:
            position = self.editor_box.findData(selected)
            if position < 0:
                self.editor_box.addItem(selected, userData=selected)
                position = self.editor_box.findData(selected)
            self.editor_box.setCurrentIndex(max(0, position))
        self.editor_box.blockSignals(False)
        self._sync_fields()

    def _sync_fields(self) -> None:
        mode = self.mode_box.currentData()
        builtin = mode == MODE_BUILTIN
        custom = mode == MODE_CUSTOM
        self.editor_box.setEnabled(builtin and self.editor_box.count() > 0)
        self.program_edit.setEnabled(custom)
        self.browse_button.setEnabled(custom)
        self.args_edit.setEnabled(custom)

    def _refresh_status(self, suffix: str, mode: str, rule) -> None:
        detail = MODE_LABELS.get(mode, mode)
        text = f"当前：{detail}"
        if mode == MODE_INHERIT:
            text += "（交给操作系统默认编辑器）"
        self.status_label.setText(text)
        self.detail_panel.set_title(f".{suffix}")
        self.detail_panel.set_meta(self._state_text(suffix))

    def _on_mode_changed(self, _mode) -> None:
        self._sync_fields()
        suffix = self._current()
        if suffix:
            self.detail_panel.set_meta(MODE_LABELS.get(str(self.mode_box.currentData() or ""), ""))

    def _on_editor_changed(self, _data) -> None:
        return None

    # ------------------------------------------------------------ 操作
    def _on_browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择程序", "", "可执行文件 (*)")
        if path:
            self.program_edit.setText(path)

    def _on_save(self) -> None:
        suffix = self._current()
        if not suffix:
            toast_warning(self, "没有选择格式", "先在左侧选择一个文件格式")
            return
        mode = str(self.mode_box.currentData() or MODE_INHERIT)
        if mode == MODE_BUILTIN:
            editor_id = str(self.editor_box.currentData() or "")
            if not editor_id:
                toast_warning(self, "没有可用编辑器", f".{suffix} 还没有注册内置编辑器")
                return
            self._api.set_rule(suffix, MODE_BUILTIN, editor_id=editor_id)
        elif mode == MODE_CUSTOM:
            program = self.program_edit.text().strip()
            if not program:
                toast_warning(self, "缺少程序路径", "自定义程序需要填写可执行文件路径")
                return
            self._api.set_rule(suffix, MODE_CUSTOM, program=program, args=self.args_edit.text().strip())
        else:
            self._api.set_rule(suffix, MODE_INHERIT)
        toast_success(self, "规则已保存", f".{suffix} → {MODE_LABELS.get(mode, mode)}")
        self._refresh_status(suffix, mode, self._api.rule_for(suffix))

    def _on_reset(self) -> None:
        suffix = self._current()
        if not suffix:
            return
        if self._api.remove_rule(suffix):
            toast_success(self, "已恢复默认", f".{suffix} 的规则已删除")
        else:
            toast_warning(self, "没有可删除的规则", f".{suffix} 本来就是默认行为")
        self._on_select(suffix, suffix)

    def _on_test(self) -> None:
        suffix = self._current()
        if not suffix:
            return
        sample = self._ctx.host.sample_path(suffix)
        if not sample:
            toast_warning(self, "没有样本文件", f"库里还没有 .{suffix} 文件")
            return
        ok, message = self._api.edit_path(Path(sample), self.window())
        if ok:
            toast_success(self, "已打开", message)
        else:
            toast_warning(self, "打开失败", message)


__all__ = ["EditorConfigPage"]
