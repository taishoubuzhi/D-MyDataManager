"""编辑器窗口外壳：文件名 / 编辑器名 / 保存 / 用系统编辑器打开 / 定位文件 / 内容区。

内部编辑器控件只要能提供 `save()`（可选 `is_dirty()` / `caption`），外壳就会自动接上保存按钮、
在关闭前询问未保存的改动。窗口由界面工具库弹出（`builtin.lib.ui` 的 open_page）。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from app.sdk.console import console_for
from qfluentwidgets import FluentIcon

from app.sdk import ui
from dm_plugin.builtin.lib.ui.plugin import (
    confirm,
    icon_button,
    status_label,
    strong_label,
    toast_success,
    toast_warning,
    toolbar,
)

_console = console_for("builtin.lib.editor")


def _call(widget, name: str):
    """安全调用控件上的可选方法（没有就返回 None）。"""
    if widget is None:
        return None
    method = getattr(widget, name, None)
    if not callable(method):
        return None
    try:
        return method()
    except Exception:
        _console.exception(f"编辑器控件调用 {name}() 失败")
        return None


class EditorWindow(QWidget):
    """通用编辑器窗口：把内容控件的保存能力接到工具条上。"""

    def __init__(self, path, build, name, on_saved=None, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.build = build
        self.editor_name = str(name or "")
        self._on_saved = on_saved
        self.content_widget = None
        self.setObjectName("editorWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar, bar_layout = toolbar(self)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        self.title_label = strong_label(bar, self.path.name)
        bar_layout.addWidget(self.title_label)
        self.meta_label = status_label(bar, self.editor_name)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        self.save_button = icon_button(bar, FluentIcon.SAVE, "保存", self._on_save)
        bar_layout.addWidget(self.save_button)
        self.external_button = icon_button(bar, FluentIcon.LINK, "用系统编辑器打开", self._on_open_external)
        bar_layout.addWidget(self.external_button)
        self.reveal_button = icon_button(bar, FluentIcon.FOLDER, "定位文件", self._on_reveal)
        bar_layout.addWidget(self.reveal_button)
        root.addWidget(bar)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        self._build_content()
        self._refresh_state()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_state)
        self._timer.start()

    # ------------------------------------------------------------ 内容
    def _build_content(self) -> None:
        if self.build is None:
            self._show_hint("没有可用的编辑器控件")
            return
        try:
            widget = self.build(self.content)
        except Exception as exc:
            _console.exception(f"内置编辑器无法打开：{self.path}")
            self._show_hint(f"内置编辑器无法打开该文件：{exc}")
            return
        self.content_widget = widget
        self.content_layout.addWidget(widget, 1)
        caption_text = str(getattr(widget, "caption", "") or "")
        if caption_text:
            self.meta_label.setText(f"{self.editor_name} · {caption_text}" if self.editor_name else caption_text)

    def _show_hint(self, text: str) -> None:
        self.content_layout.addWidget(status_label(self.content, text))

    def _refresh_state(self) -> None:
        dirty = bool(_call(self.content_widget, "is_dirty"))
        self.save_button.setEnabled(dirty)
        if not self.editor_name:
            return
        caption_text = str(getattr(self.content_widget, "caption", "") or "")
        base = f"{self.editor_name} · {caption_text}" if caption_text else self.editor_name
        self.meta_label.setText(f"{base} · 已修改" if dirty else base)

    # ------------------------------------------------------------ 工具条
    def _on_save(self) -> None:
        widget = self.content_widget
        save = getattr(widget, "save", None)
        if not callable(save):
            toast_warning(self, "无法保存", "当前编辑器没有提供保存接口")
            return
        try:
            result = save()
        except Exception as exc:
            _console.exception(f"保存失败：{self.path}")
            toast_warning(self, "保存失败", str(exc))
            return
        ok, message = True, "已保存"
        if isinstance(result, tuple) and len(result) == 2:
            ok, message = bool(result[0]), str(result[1])
        elif result is False:
            ok, message = False, "保存失败"
        if ok:
            toast_success(self, "已保存", self.path.name)
            if self._on_saved is not None:
                try:
                    self._on_saved(self.path)
                except Exception:
                    _console.exception(f"保存后同步库内条目失败：{self.path}")
        else:
            toast_warning(self, "保存失败", message)
        self._refresh_state()

    def _on_open_external(self) -> None:
        if not ui.open_default(self.path):
            self.meta_label.setText("系统无法打开该文件，可在「编辑器」页设为自定义程序")

    def _on_reveal(self) -> None:
        ui.reveal(self.path)

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, event):  # noqa: N802 - Qt 命名
        if bool(_call(self.content_widget, "is_dirty")):
            if not confirm(self, "放弃未保存的修改？", f"{self.path.name} 还有未保存的内容，确定关闭吗？"):
                event.ignore()
                return
        self._timer.stop()
        super().closeEvent(event)


__all__ = ["EditorWindow"]
