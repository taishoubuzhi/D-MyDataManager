"""查看器内容页：只做「页面本身」，窗口标题栏由弹窗工具库（builtin.lib.ui）负责。

文件名 / 查看器名与「用系统程序打开」「定位文件」两个动作，通过 `attach_popup(popup)`
交给外壳标题栏，避免像以前那样外壳与页面各画一条一模一样的标题栏（外层套一圈）。
"""

from __future__ import annotations

from pathlib import Path

from app.sdk.console import console_for
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk import ui
from dm_plugin.builtin.lib.ui.plugin import status_label

_console = console_for("builtin.lib.viewer")

#: 默认的弹窗扩展接口名（弹窗工具库 builtin.lib.ui 提供）
DEFAULT_HOST = "dialog"


class ViewerWindow(QWidget):
    """查看器内容页：内容区 + 交给外壳的动作按钮；窗口装饰由弹窗插件负责。"""

    def __init__(
        self,
        path,
        build,
        name: str,
        parent: QWidget | None = None,
        *,
        sources: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.build = build
        self.viewer_name = str(name or "")
        self.content_widget: QWidget | None = None
        self.popup = None
        self._caption = ""
        self._meta_text = self.viewer_name
        # 调用方「当前列表里的文件顺序」，交给内容页做上一张 / 下一张
        self._sources = tuple(Path(item) for item in sources)
        self.setObjectName("viewerWindow")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        self._build_content()

    # ------------------------------------------------------------------ 外壳对接
    def attach_popup(self, popup) -> None:
        """弹窗外壳创建后调用：把动作按钮与文件信息放到外壳标题栏上。

        内容页（真正画画面的那个控件）若自己也实现 `attach_popup(popup)`，一并转交；
        设置项由内容页的 `settings_items()` 声明，外壳据此在标题栏加齿轮。
        """
        self.popup = popup
        popup.add_action(FluentIcon.LINK, "用系统程序打开", self._on_open_external)
        popup.add_action(FluentIcon.FOLDER, "定位文件", self._on_reveal)
        self._sync_meta()
        hook = getattr(self.content_widget, "attach_popup", None)
        if callable(hook):
            try:
                hook(popup)
            except Exception:  # 内容页的挂载失败不影响查看器显示
                _console.exception("查看器内容挂载失败")

    def settings_items(self) -> list[dict]:
        """设置项统一由内容页声明，这里只做转发（内容页没有就返回空列表）。"""
        hook = getattr(self.content_widget, "settings_items", None)
        if not callable(hook):
            return []
        try:
            return list(hook() or ())
        except Exception:  # noqa: BLE001 - 内容页取设置项失败按「没有设置」处理
            _console.exception("读取查看器设置项失败")
            return []

    def _sync_meta(self) -> None:
        """把「查看器名 · 文件信息」写进外壳副标题（外壳还没挂上时先记在本地）。"""
        text = f"{self.viewer_name} · {self._caption}" if self._caption else self.viewer_name
        self._meta_text = text
        if self.popup is not None:
            self.popup.set_meta(text)

    # ------------------------------------------------------------------ 内容
    def _build_content(self) -> None:
        if self.build is None:
            self._show_hint("该查看器没有提供界面，请改用系统程序打开。")
            return
        try:
            widget = self.build(self.content)
        except Exception as exc:  # 查看器异常不应影响主界面
            _console.exception(f"创建查看器控件失败：{self.viewer_name}")
            self._show_hint(f"内置查看器无法显示该文件：{exc}")
            return
        self.content_widget = widget
        self.content_layout.addWidget(widget, 1)
        self._caption = str(getattr(widget, "caption", "") or "")
        self._watch_caption(widget)
        self._watch_path(widget)
        self._apply_sources(widget)
        self._sync_meta()

    def _watch_caption(self, widget) -> None:
        """内容页若提供 `captionChanged` 信号，就在它改说明文字时同步外壳副标题。"""
        signal = getattr(widget, "captionChanged", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            connect(self._on_caption_changed)

    def _watch_path(self, widget) -> None:
        """内容页若提供 `pathChanged` 信号，就跟着换目标文件。

        上一张 / 下一张切完图，外壳标题栏的标题、以及「用系统程序打开」「定位文件」
        两个动作都必须跟着指向当前这张，否则它们还停在一开始打开的那个文件上。
        """
        signal = getattr(widget, "pathChanged", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            connect(self._on_path_changed)

    def _apply_sources(self, widget) -> None:
        """把「调用方当前列表里的文件顺序」交给内容页（可选钩子 `set_sources`）。

        内容页没有这个钩子就什么都不做：上一张 / 下一张仍由内容页自己决定。
        """
        if not self._sources:
            return
        hook = getattr(widget, "set_sources", None)
        if not callable(hook):
            return
        try:
            hook(self._sources)
        except Exception:  # 内容页用不了浏览列表时保持它自己的顺序
            _console.exception("查看器设置浏览列表失败")

    def _on_caption_changed(self, text) -> None:
        self._caption = str(text or "")
        self._sync_meta()

    def _on_path_changed(self, text) -> None:
        target = Path(str(text or ""))
        if not str(target):
            return
        self.path = target
        if self.popup is not None:
            self.popup.set_title(target.name)

    def _show_hint(self, text: str) -> None:
        """查看器缺界面或构建失败时，退化成一行提示（不留空白区）。"""
        self.content_layout.addWidget(status_label(self.content, text))

    # ------------------------------------------------------------------ 动作
    def _on_open_external(self) -> None:
        if not ui.open_default(self.path):
            self._meta_text = "系统无法打开该文件，可在「查看器」页设为自定义程序"
            if self.popup is not None:
                self.popup.set_meta(self._meta_text)

    def _on_reveal(self) -> None:
        ui.reveal(self.path)


__all__ = ["DEFAULT_HOST", "ViewerWindow"]
