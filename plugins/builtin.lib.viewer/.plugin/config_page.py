"""查看器配置界面：列出库里出现过的格式，逐个指定用哪个查看器打开。

页面结构全部交给 UI 工具库（builtin.lib.ui）的 SplitPage 与控件工厂，本插件只负责：
左栏列出格式、右栏接上模式 / 查看器 / 自定义程序这三组数据与保存行为。
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

from .rules import MODE_BUILTIN, MODE_CUSTOM, MODE_INHERIT, MODE_LABELS

#: 左栏（格式列表）固定宽度
LIST_WIDTH = 340


class ViewerConfigPage(SplitPage):
    """查看器配置页：每个文件格式选一个查看器，或交给系统程序。"""

    def __init__(self, ctx, api, parent: QWidget | None = None) -> None:
        super().__init__(
            "查看器",
            "左侧列出库里出现过的所有文件格式（含插件声明支持的格式），选中后即可为它指定用哪个查看器打开。",
            parent,
            list_title="文件格式",
            list_placeholder="搜索格式 / 查看器名",
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
        self._current = ""

        self.header.add_action(icon_button(self.header, FluentIcon.SYNC, "刷新", self.reload))
        self.header.add_hint("没有单独设置的格式默认使用内置查看器；自定义程序留空时改为弹出系统的「打开方式」对话框。")

        # 左栏（文件名与自检沿用旧名字，行为一致）
        self.search = self.list_panel.search
        self.suffix_list = self.list_panel.list
        self.count_label = self.list_panel.count_label

        # 右栏
        self.detail_title = self.detail_panel.title_label
        self.detail_meta = self.detail_panel.meta_label
        self.detail_viewers = caption(self.detail_panel.card, "")
        self.detail_panel.add_widget(self.detail_viewers)

        self.mode_box = combo_box(
            self.detail_panel.card,
            width=200,
            on_change=self._on_mode_changed,
        )
        self.detail_panel.add_row("打开方式", self.mode_box, label_width=76)
        self.viewer_box = combo_box(self.detail_panel.card, minimum_width=240, on_change=self._on_mode_changed)
        self.detail_panel.add_row("查看器", self.viewer_box, label_width=76)

        self.program_edit = line_edit(self.detail_panel.card, placeholder="自定义程序，例如 C:\\Program Files\\App\\app.exe")
        self.browse_button = icon_button(self.detail_panel.card, FluentIcon.FOLDER, "浏览", self._on_browse)
        program_row = QWidget(self.detail_panel.card)
        program_layout = QHBoxLayout(program_row)
        program_layout.setContentsMargins(0, 0, 0, 0)
        program_layout.setSpacing(8)
        program_layout.addWidget(self.program_edit, 1)
        program_layout.addWidget(self.browse_button)
        self.detail_panel.add_row("自定义程序", program_row, label_width=76)

        self.args_edit = line_edit(self.detail_panel.card, placeholder="自定义参数，可留空；用 {path} 表示文件位置")
        self.detail_panel.add_row("参数", self.args_edit, label_width=76)

        self.hint_label = caption(self.detail_panel.card, "")
        self.detail_panel.add_widget(self.hint_label)

        self.save_button = primary_button(self.detail_panel.card, FluentIcon.SAVE, "保存", self._on_save)
        self.reset_button = icon_button(self.detail_panel.card, FluentIcon.SYNC, "恢复默认", self._on_reset)
        self.test_button = icon_button(self.detail_panel.card, FluentIcon.VIEW, "测试打开", self._on_test)
        self.detail_panel.add_actions((self.save_button, self.reset_button, self.test_button))

    # ---------------------------------------------------------------- 数据
    def reload(self) -> None:
        """重新读取库内格式与已登记的查看器。"""
        counts = {}
        try:
            counts = dict(self._ctx.host.file_formats() or {})
        except Exception:
            self._ctx.log.warning("读取库内文件格式失败")
        self._counts = {str(key): int(value) for key, value in counts.items()}
        names = set(self._counts) | set(self._api.rules())
        names |= {suffix for viewer in self._viewers() for suffix in viewer.extensions}
        self._suffixes = sorted(name for name in names if name)
        self._fill_list()

    def _viewers(self):
        try:
            return tuple(self._ctx.host.viewers())
        except Exception:
            return ()

    def _viewers_for(self, suffix: str):
        return tuple(viewer for viewer in self._viewers() if suffix in viewer.extensions)

    def _viewer_name(self, viewer_id: str) -> str:
        for viewer in self._viewers():
            if viewer.id == viewer_id:
                return f"{viewer.name}（{viewer.plugin_id}）"
        return str(viewer_id or "")

    def _mode_of(self, suffix: str) -> str:
        rule = self._api.rule_for(suffix)
        if rule is not None and rule.mode:
            return rule.mode
        return MODE_BUILTIN if self._api.has_builtin(suffix) else MODE_INHERIT

    def _state_text(self, suffix: str) -> str:
        rule = self._api.rule_for(suffix)
        mode = self._mode_of(suffix)
        if mode == MODE_BUILTIN:
            if rule is not None and rule.viewer_id:
                return f"使用查看器（{self._viewer_name(rule.viewer_id)}）"
            viewers = self._viewers_for(suffix)
            if viewers:
                return f"使用查看器（自动：{viewers[0].name}）"
            return "使用查看器"
        if mode == MODE_CUSTOM:
            program = (rule.program if rule else "") or "未指定程序"
            return f"自定义程序（{Path(program).name}）"
        if mode == MODE_INHERIT:
            return "继承系统默认"
        return MODE_LABELS.get(mode, mode)

    # ---------------------------------------------------------------- 列表
    def _fill_list(self) -> None:
        keyword = self.search.text().strip().lower().lstrip(".")
        entries: list[tuple] = []
        for suffix in self._suffixes:
            names = " ".join(f"{viewer.name} {viewer.plugin_id}" for viewer in self._viewers_for(suffix))
            if keyword and keyword not in f"{suffix} {names}".lower():
                continue
            count = self._counts.get(suffix, 0)
            extra = f" · 库中 {count} 项" if count else ""
            entries.append((suffix, f".{suffix} — {self._state_text(suffix)}{extra}", f"可用查看器：{names or '无'}"))
        self.list_panel.set_items(entries)
        self.count_label.setText(f"{len(entries)} / {len(self._suffixes)} 个格式")
        if not self._suffixes:
            self._current = ""
            self.detail_title.setText("没有可配置的格式")
            self.detail_meta.setText("导入数据后这里会列出出现过的文件格式")
            self.detail_viewers.setText("")
            return
        if not self.list_panel.select(self._current):
            self.suffix_list.setCurrentRow(0)

    # ---------------------------------------------------------------- 交互
    def _on_select(self, data, _text: str = "") -> None:
        if not data:
            return
        self._current = str(data)
        rule = self._api.rule_for(self._current)
        viewers = self._viewers_for(self._current)
        self.detail_title.setText(f".{self._current}")

        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for mode in self._api.available_modes(self._current):
            self.mode_box.addItem(MODE_LABELS.get(mode, mode), userData=mode)
        index = self.mode_box.findData(rule.mode if rule and rule.mode else self._mode_of(self._current))
        self.mode_box.setCurrentIndex(max(0, index))
        self.mode_box.blockSignals(False)

        self.viewer_box.blockSignals(True)
        self.viewer_box.clear()
        self.viewer_box.addItem("自动（按插件注册顺序）", userData="")
        for viewer in viewers:
            self.viewer_box.addItem(f"{viewer.name} · {viewer.plugin_id}", userData=viewer.id)
        pinned = rule.viewer_id if rule is not None else ""
        viewer_index = self.viewer_box.findData(pinned)
        self.viewer_box.setCurrentIndex(viewer_index if viewer_index >= 0 else 0)
        self.viewer_box.blockSignals(False)

        self.program_edit.setText(rule.program if rule else "")
        self.args_edit.setText(rule.args if rule else "")
        self.detail_viewers.setText(
            "可用查看器：" + ("、".join(f"{viewer.name}（{viewer.plugin_id}）" for viewer in viewers) or "无")
        )
        self._on_mode_changed()

    def _on_mode_changed(self, *_args) -> None:
        mode = self.mode_box.currentData() or MODE_INHERIT
        viewers = self._viewers_for(self._current)
        custom = mode == MODE_CUSTOM
        self.program_edit.setEnabled(custom)
        self.browse_button.setEnabled(custom)
        self.args_edit.setEnabled(custom)
        self.viewer_box.setEnabled(mode == MODE_BUILTIN and len(viewers) > 1)
        if mode == MODE_BUILTIN:
            chosen = self.viewer_box.currentData() or (viewers[0].id if viewers else "")
            if chosen:
                hint = f"使用查看器「{self._viewer_name(chosen)}」在程序内显示。"
            else:
                hint = "该格式还没有插件声明支持，保存后仍按系统默认程序打开。"
        elif mode == MODE_CUSTOM:
            hint = "保存后由指定程序打开；程序留空时改为弹出系统的「打开方式」对话框。"
        else:
            hint = "交给系统默认程序打开（与在资源管理器中双击一致）。"
        count = self._counts.get(self._current, 0)
        if count:
            hint += f" 库中现有 {count} 项该格式的数据。"
        self.hint_label.setText(hint)
        self.detail_meta.setText(self._state_text(self._current) if self._current else "")

    def _on_browse(self) -> None:
        current = self.program_edit.text().strip()
        start = str(Path(current).parent) if current else ""
        path, _filter = QFileDialog.getOpenFileName(self, "选择程序", start, "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self.program_edit.setText(path)

    def _on_save(self) -> None:
        if not self._current:
            toast_warning(self, "未选择格式", "请先在左侧选择一个文件格式")
            return
        mode = self.mode_box.currentData() or MODE_INHERIT
        program = self.program_edit.text().strip() if mode == MODE_CUSTOM else ""
        args = self.args_edit.text().strip() if mode == MODE_CUSTOM else ""
        viewer_id = (self.viewer_box.currentData() or "") if mode == MODE_BUILTIN else ""
        if not self._api.set_rule(self._current, mode, program, args, viewer_id):
            toast_warning(self, "保存失败", "规则文件写不进去，请检查配置目录权限")
            return
        state = self._state_text(self._current)
        self._fill_list()
        toast_success(self, "已保存查看器设置", f".{self._current} → {state}")

    def _on_reset(self) -> None:
        if not self._current:
            return
        if not self._api.remove_rule(self._current):
            toast_warning(self, "没有可恢复的设置", f".{self._current} 本来就没有单独规则")
            return
        self._fill_list()
        toast_success(self, "已恢复默认", f".{self._current} 不再有单独规则")

    def _on_test(self) -> None:
        if not self._current:
            return
        sample = ""
        try:
            sample = str(self._ctx.host.sample_path(self._current) or "")
        except Exception:
            sample = ""
        if not sample:
            toast_warning(self, "没有可测试的文件", f"库里还没有 .{self._current} 格式的数据")
            return
        ok, message = self._api.open_path(sample, self.window())
        if ok:
            toast_success(self, "已打开", f"{Path(sample).name} · {message}")
        else:
            toast_warning(self, "打开失败", message)

    # ---------------------------------------------------------------- 刷新
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._suffixes:
            self.reload()


__all__ = ["ViewerConfigPage"]
