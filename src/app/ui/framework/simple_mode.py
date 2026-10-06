"""「简化显示」的挡位与逐控件判定。

挡位存在配置项 `Layout/Simple-Display`：

- `none` 不简化：图标 + 文字的控件都显示文字；
- `default` 默认：只简化**不会混淆**的图标 —— 同一个容器里多个控件共用一枚图标时保留文字
  （插件页的「重命名 / 编辑说明 / 编辑备注」都是 `FluentIcon.EDIT`，只留图标就分不清是哪个功能）；
- `full` 完全简化：一律缩成图标。

判定依赖控件所在的容器，所以控件加入容器后要调用 `refresh_peers()`，让同容器的兄弟重新算一遍。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from ...core.config import SIMPLE_DEFAULT, SIMPLE_FULL, SIMPLE_NONE, SIMPLE_MODES, config


def simple_mode() -> str:
    """当前的简化挡位（只认三挡位；旧版布尔值在启动时已被换算掉）。"""
    value = config.simpleDisplay.value
    return str(value) if value in SIMPLE_MODES else SIMPLE_DEFAULT


def simple_display() -> bool:
    """是否处于某种简化挡位（默认挡位也算开着，具体控件用 `should_simplify()` 判定）。"""
    return simple_mode() != SIMPLE_NONE


def icon_key(icon) -> str | None:
    """图标身份：枚举图标（`FluentIcon.EDIT`）取名字，路径 / 字号字符串原样返回，其余当没有图标。"""
    name = getattr(icon, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(icon, str) and icon:
        return icon
    return None


def _peers(widget: QWidget) -> list[QWidget]:
    """同容器的兄弟控件：挡位判定只在容器内部比较，跨容器的同款图标不算混淆。"""
    parent = widget.parentWidget()
    if parent is None:
        return []
    children = parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
    return [child for child in children if child is not widget]


def shares_icon_with_peers(widget: QWidget, key: str | None) -> bool:
    """同容器里还有别的图标 + 文字控件用这枚图标。"""
    if key is None:
        return False
    return any(getattr(peer, "_icon_key", None) == key for peer in _peers(widget))


def should_simplify(widget: QWidget, key: str | None, *, keep_text: bool = False) -> bool:
    """这个控件在当前挡位下要不要简化成只显示图标。"""
    if key is None or keep_text:
        return False
    mode = simple_mode()
    if mode == SIMPLE_NONE:
        return False
    if mode == SIMPLE_FULL:
        return True
    return not shares_icon_with_peers(widget, key)


def refresh_peers(widget: QWidget) -> None:
    """控件的图标变了 / 新控件加入容器后，让同容器的兄弟重新判定一次。"""
    for peer in _peers(widget):
        if getattr(peer, "_icon_key", None) is None:
            continue
        apply = getattr(peer, "_apply_display", None)
        if callable(apply):
            apply()


__all__ = [
    "icon_key",
    "refresh_peers",
    "should_simplify",
    "simple_display",
    "simple_mode",
]
