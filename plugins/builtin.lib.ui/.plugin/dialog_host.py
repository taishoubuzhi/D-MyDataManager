"""弹窗外壳：独立窗口（PopupWindow）与 dialog 扩展接口（DialogApi）。

其他插件用 ctx.require("dialog") 拿到 DialogApi，再用 open_page() 把自己的页面放进
这个外壳；窗口没有父控件，因此与程序主体相互独立。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.sdk.console import console_for
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel, TransparentToolButton

from .settings import DEFAULT_TITLE as DEFAULT_SETTINGS_TITLE
from .settings import attach_settings

_console = console_for("builtin.lib.ui")

#: 扩展接口名：查看器等插件用 ctx.require(EXTENSION_NAME) 取到 DialogApi
EXTENSION_NAME = "dialog"


class PopupWindow(QWidget):
    """独立弹窗：标题栏 + 内容区；关闭按钮或 Esc 关闭，关闭后自动销毁。"""

    def __init__(
        self,
        title: str,
        content_factory: Callable[[QWidget], QWidget],
        meta: str = "",
        buttons: Sequence[tuple[str, Callable[[], None]]] = (),
        width: int = 980,
        height: int = 700,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("popupWindow")
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(width, height)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)
        self._bar = bar
        self._bar_layout = bar_layout
        self.title_label = StrongBodyLabel(title, bar)
        bar_layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(meta, bar)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        for caption, callback in buttons:
            button = PushButton(caption, bar)
            button.clicked.connect(callback)
            bar_layout.addWidget(button)
        self.close_button = TransparentToolButton(FluentIcon.CLOSE, bar)
        self.close_button.setToolTip("关闭（Esc）")
        self.close_button.clicked.connect(self.close)
        bar_layout.addWidget(self.close_button)
        root.addWidget(bar)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        self._escape_handler: Callable[[], bool] | None = None
        try:
            widget = content_factory(self.content)
        except Exception as exc:  # 插件页面出错不应该影响主界面
            _console.exception(f"创建弹窗内容失败：{title}")
            widget = CaptionLabel(f"页面无法显示：{exc}", self.content)
        self.content_widget = widget
        if widget is not None:
            self.content_layout.addWidget(widget, 1)
        self._attach_content(widget)
        self._escape_shortcut = QShortcut(QKeySequence("Escape"), self, activated=self._on_escape)

    # ------------------------------------------------------------------ 标题栏
    def add_action(
        self,
        icon: FluentIcon,
        tooltip: str,
        callback: Callable[[], None],
    ) -> TransparentToolButton:
        """在标题栏（关闭按钮左侧）加一个动作按钮，返回按钮本体。

        内容页把"用系统程序打开""定位文件"这类动作交给外壳，避免标题栏重复一套。
        """
        button = TransparentToolButton(icon, self)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        index = max(0, self._bar_layout.count() - 1)
        self._bar_layout.insertWidget(index, button)
        return button

    def remove_action(self, button) -> bool:
        """把之前加进标题栏的动作按钮摘掉（内容页把动作搬到别处时用）。"""
        for position in range(self._bar_layout.count()):
            if self._bar_layout.itemAt(position).widget() is button:
                self._bar_layout.removeWidget(button)
                button.setParent(None)
                button.deleteLater()
                return True
        return False

    def set_meta(self, text: str) -> None:
        """更新标题栏副标题（查看器 / 编辑器把文件信息写在这里）。"""
        self.meta_label.setText(text or "")

    def set_title(self, text: str) -> None:
        self.title_label.setText(text or "")
        self.setWindowTitle(text or "")

    def set_bar_visible(self, visible: bool) -> None:
        """显示 / 隐藏整条标题栏（视频全屏时用，退出全屏再恢复）。"""
        self._bar.setVisible(bool(visible))

    def set_escape_handler(self, handler: Callable[[], bool] | None) -> None:
        """把 Esc 优先交给内容页：它返回 True 表示已处理，窗口不关闭。

        视频全屏就是在内容页里处理的——按 Esc 应该先退出全屏，而不是直接关掉窗口。
        """
        self._escape_handler = handler

    def _on_escape(self) -> None:
        handler = self._escape_handler
        if handler is not None:
            try:
                if handler():
                    return
            except Exception:  # 内容页的 Esc 处理失败不影响关窗
                _console.exception("Esc 处理失败")
        self.close()

    def _attach_content(self, widget: QWidget | None) -> None:
        """内容页若实现 attach_popup(popup)，就把外壳交给它，由它注册标题栏动作。

        之后再看内容页有没有声明设置项（`settings_items()`）：声明了就补一个齿轮，
        点开是改完即生效的设置对话框。两步都失败也只记日志，窗口照常显示。
        """
        if widget is None:
            return
        hook = getattr(widget, "attach_popup", None)
        if callable(hook):
            try:
                hook(self)
            except Exception:  # 内容页的挂载失败不影响窗口显示
                _console.exception("弹窗内容挂载失败")
        try:
            attach_settings(self, widget, title=DEFAULT_SETTINGS_TITLE)
        except Exception:  # 设置入口加不上也不该拦住窗口
            _console.exception("设置入口挂载失败")


class DialogApi:
    """dialog 扩展接口：为其他插件创建独立弹窗页面。"""

    def __init__(self) -> None:
        self._windows: list[PopupWindow] = []

    def open_page(
        self,
        title: str,
        content_factory: Callable[[QWidget], QWidget],
        meta: str = "",
        buttons: Sequence[tuple[str, Callable[[], None]]] = (),
        width: int = 980,
        height: int = 700,
    ) -> PopupWindow:
        window = PopupWindow(title, content_factory, meta, buttons, width, height)
        self._windows.append(window)
        window.destroyed.connect(lambda *_: self._forget(window))
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def windows(self) -> tuple[PopupWindow, ...]:
        return tuple(self._windows)

    def close_all(self) -> int:
        count = 0
        for window in list(self._windows):
            window.close()
            count += 1
        return count

    def _forget(self, window: PopupWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)


__all__ = ["EXTENSION_NAME", "DialogApi", "PopupWindow"]
