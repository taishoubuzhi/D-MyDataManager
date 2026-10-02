"""查看器调度：把手里的 `Viewer` 交给插件自己在弹窗里打开。

查看器控件与窗口外壳都住在插件里（查看器工具库 builtin.lib.viewer + 各查看器插件），
程序侧只做两件事：

1. 插件提供了 `opener`（查看器插件都提供）→ 直接调它，由插件自己造窗口并找弹窗工具库弹出；
2. 老式 `factory` 查看器（单元测试、自检脚本里的假查看器）→ 退回到「用宿主扩展接口包一层」。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from ...core.extensions import extension_registry
from ...core.viewers import Viewer
from ...sdk.ui import COMPACT_MARGINS

#: 默认的弹窗扩展接口名（内置弹窗工具库 builtin.lib.dialog 提供）
DEFAULT_HOST = "dialog"


def host_api(viewer: Viewer) -> object | None:
    """取负责显示该查看器的扩展接口提供者（内置查看器即弹窗工具库）。"""
    if viewer is None:
        return None
    return extension_registry.provider(viewer.host or DEFAULT_HOST)


def _factory_container(viewer: Viewer, path: Path, parent: QWidget) -> QWidget:
    """给只有 factory 的查看器包一层容器（插件查看器不走这条路）。"""
    holder = QWidget(parent)
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(*COMPACT_MARGINS)
    try:
        layout.addWidget(viewer.factory(path, holder), 1)  # type: ignore[misc]
    except Exception as exc:
        logger.exception("创建查看器控件失败：{}", viewer.id)
        layout.addWidget(CaptionLabel(f"内置查看器无法显示该文件：{exc}", holder))
    return holder


def open_viewer(viewer: Viewer, path: Path, parent: QWidget | None = None) -> tuple[bool, str]:
    """在独立弹窗里打开文件，返回 (是否成功, 说明)。"""
    target = Path(path)
    if not target.exists():
        return False, f"文件不存在：{target.name}"
    if viewer is None or (viewer.factory is None and viewer.opener is None):
        return False, "没有可用的内置查看器"
    if viewer.opener is not None:
        try:
            return viewer.opener(target)
        except Exception as exc:
            logger.exception("打开查看器失败：{}", target)
            return False, f"打开查看器失败：{exc}"
    host_name = viewer.host or DEFAULT_HOST
    host = extension_registry.provider(host_name)
    if host is None:
        return False, f"缺少弹窗工具库（{host_name}），请到「插件」页启用后重试"
    try:
        host.open_page(  # type: ignore[attr-defined]
            title=target.name,
            content_factory=lambda container: _factory_container(viewer, target, container),
            meta=viewer.name,
        )
    except Exception as exc:
        logger.exception("打开查看器失败：{}", target)
        return False, f"打开查看器失败：{exc}"
    return True, viewer.name


__all__ = ["DEFAULT_HOST", "host_api", "open_viewer"]
