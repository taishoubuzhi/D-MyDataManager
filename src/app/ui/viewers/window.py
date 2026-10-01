"""查看器页面：标题栏 + 内容区，实际显示交给「弹窗页面」插件。

界面层拿到 `OpenDecision` 后调用 `open_viewer()`：按 `viewer.host`（内置查看器都是
"dialog"）向插件服务要弹窗扩展接口，再把 `ViewerWindow` 作为内容放进那个独立弹窗 ——
弹窗没有父控件，因此不会与程序主界面重叠。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton, StrongBodyLabel

from ...core import shell
from ...core.extensions import extension_registry
from ...core.viewers import Viewer
from ...services.privacy_service import privacy

#: 默认的弹窗扩展接口名（内置弹窗页面插件 builtin.dialog 提供）
DEFAULT_HOST = "dialog"


class ViewerWindow(QWidget):
    """查看器内容页：工具栏 + 查看器控件；窗口装饰由弹窗插件负责。"""

    def __init__(self, viewer: Viewer, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewer = viewer
        self.path = Path(path)
        self.content_widget: QWidget | None = None
        self.setObjectName("viewerWindow")
        # 查看器会持续读取文件（媒体播放、翻页），打开期间保持放行，关闭后重新锁定
        self._hold_released = False
        privacy.hold()
        self.destroyed.connect(lambda *_args: self._release_hold())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(self)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 10, 14, 10)
        bar_layout.setSpacing(8)
        self.title_label = StrongBodyLabel(self.path.name, bar)
        bar_layout.addWidget(self.title_label)
        self.meta_label = CaptionLabel(viewer.name, bar)
        bar_layout.addWidget(self.meta_label)
        bar_layout.addStretch(1)
        self.external_button = PushButton(FluentIcon.LINK, "用系统程序打开", bar)
        self.external_button.clicked.connect(self._on_open_external)
        bar_layout.addWidget(self.external_button)
        self.reveal_button = PushButton(FluentIcon.FOLDER, "定位文件", bar)
        self.reveal_button.clicked.connect(self._on_reveal)
        bar_layout.addWidget(self.reveal_button)
        root.addWidget(bar)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(self.content, 1)

        self._build_content()

    def _build_content(self) -> None:
        if self.viewer.factory is None:
            self.content_layout.addWidget(CaptionLabel("该查看器没有提供界面，请改用系统程序打开。", self.content))
            return
        try:
            widget = self.viewer.factory(self.path, self.content)
        except Exception as exc:  # 查看器异常不应影响主界面
            logger.exception("创建查看器控件失败：{}", self.viewer.id)
            self.content_layout.addWidget(CaptionLabel(f"内置查看器无法显示该文件：{exc}", self.content))
            return
        self.content_widget = widget
        self.content_layout.addWidget(widget, 1)
        caption = str(getattr(widget, "caption", "") or "")
        if caption:
            self.meta_label.setText(f"{self.viewer.name} · {caption}")

    def _release_hold(self) -> None:
        if self._hold_released:
            return
        self._hold_released = True
        privacy.release()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._release_hold()
        super().closeEvent(event)

    def _on_open_external(self) -> None:
        if not shell.open_default(self.path):
            self.meta_label.setText("系统无法打开该文件，可在「打开方式」页设为自定义程序")

    def _on_reveal(self) -> None:
        shell.reveal(self.path)


def host_api(viewer: Viewer) -> object | None:
    """取负责显示该查看器的扩展接口提供者（内置查看器即弹窗页面插件）。"""
    if viewer is None:
        return None
    return extension_registry.provider(viewer.host or DEFAULT_HOST)


def open_viewer(viewer: Viewer, path: Path, parent: QWidget | None = None) -> tuple[bool, str]:
    """在独立弹窗里打开文件，返回 (是否成功, 说明)。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    if viewer is None or viewer.factory is None:
        return False, "没有可用的内置查看器"
    host_name = viewer.host or DEFAULT_HOST
    host = extension_registry.provider(host_name)
    if host is None:
        return False, f"缺少弹窗页面插件（{host_name}），请到「插件」页启用后重试"
    try:
        host.open_page(  # type: ignore[attr-defined]
            title=target.name,
            content_factory=lambda container: ViewerWindow(viewer, target, container),
            meta=viewer.name,
        )
    except Exception as exc:
        logger.exception("打开查看器失败：{}", target)
        return False, f"打开查看器失败：{exc}"
    return True, viewer.name


__all__ = ["ViewerWindow", "host_api", "open_viewer"]
