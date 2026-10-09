"""内容页的「设置」入口：把页面声明的设置项收进一个改完即生效的对话框。

内容页（查看器 / 编辑器的内容区）只要实现一个 `settings_items()`，返回一串设置项：

    {
        "key": "loop",                    # 选项键，回写时用
        "label": "循环播放",               # 显示名
        "kind": "bool",                   # bool / int / choice
        "value": False,                   # 当前值
        "description": "播完从头再来",      # 可省，显示在控件下面
        "minimum": 200, "maximum": 5000,  # kind="int" 时用
        "step": 100, "suffix": "毫秒",
        "choices": {"fit": "适应窗口"},    # kind="choice" 时用
        "on_change": 回调,                 # 值一变就调用；页面负责立即生效并回写选项
    }

弹窗外壳挂载内容页时调用 `attach_settings()`：内容页没声明设置项就不加按钮，
声明了就在标题栏加一个齿轮，点开是即时生效的设置对话框（改一下存一下，没有「取消」）。
"""

from __future__ import annotations

from collections.abc import Mapping

from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk.console import console_for

from .ui_tools import FormDialog, check_box, combo_box, form_row, spin_box, status_label

_console = console_for("builtin.lib.ui")

#: 支持的设置项类型
KIND_BOOL = "bool"
KIND_INT = "int"
KIND_CHOICE = "choice"
_KINDS = (KIND_BOOL, KIND_INT, KIND_CHOICE)
#: 设置对话框的默认标题
DEFAULT_TITLE = "设置"

__all__ = [
    "DEFAULT_TITLE",
    "KIND_BOOL",
    "KIND_CHOICE",
    "KIND_INT",
    "attach_settings",
    "open_settings",
    "settings_items",
]


def settings_items(widget: QWidget | None) -> list[dict]:
    """读出内容页声明的设置项；没实现、抛错、格式不对都当作「没有设置」。"""
    hook = getattr(widget, "settings_items", None)
    if not callable(hook):
        return []
    try:
        items = hook()
    except Exception:  # noqa: BLE001 - 内容页出错不该拖累窗口
        _console.exception("读取设置项失败")
        return []
    result: list[dict] = []
    for item in items or ():
        if isinstance(item, Mapping) and str(item.get("kind") or "") in _KINDS:
            result.append(dict(item))
    return result


def attach_settings(popup, widget: QWidget | None, *, title: str = DEFAULT_TITLE) -> bool:
    """内容页声明了设置项，就在弹窗标题栏加一个齿轮；返回是否加了。"""
    if not settings_items(widget):
        return False
    popup.add_action(
        FluentIcon.SETTING,
        title,
        lambda: open_settings(popup, widget, title=title),
    )
    return True


def open_settings(parent: QWidget | None, widget: QWidget | None, *, title: str = DEFAULT_TITLE) -> bool:
    """弹出设置对话框（改一下立即生效）；没有设置项时不弹，返回是否真的弹了。"""
    items = settings_items(widget)
    if not items:
        return False
    dialog = FormDialog(
        parent,
        title=title,
        width=520,
        minimum_height=220,
        maximum_height=520,
    )
    for item in items:
        dialog.add_widget(_row(dialog, item))
    dialog.add_hint("改完立即生效，设置会记在这个插件自己的选项里。")
    dialog.set_buttons(yes="完成", cancel="关闭")
    dialog.exec()
    return True


def _row(parent: QWidget, item: dict) -> QWidget:
    """一项设置：勾选类把标签写在勾选框上，其它类型走「标签 + 控件」一行。"""
    host = QWidget(parent)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)

    kind = str(item["kind"])
    label = str(item.get("label") or item.get("key") or "")
    if kind == KIND_BOOL:
        column.addWidget(
            check_box(host, text=label, checked=bool(item.get("value")), on_change=_callback(item))
        )
    else:
        control = _choice(host, item) if kind == KIND_CHOICE else _number(host, item)
        column.addWidget(form_row(host, label, control))

    description = str(item.get("description") or "")
    if description:
        column.addWidget(status_label(host, description))
    return host


def _choice(parent: QWidget, item: dict):
    """下拉设置项：choices 是 {值: 文案}，回传的是里面的值。"""
    choices = dict(item.get("choices") or {})
    return combo_box(
        parent,
        items=[str(text) for text in choices.values()],
        data=list(choices.keys()),
        value=item.get("value"),
        on_change=_callback(item),
    )


def _number(parent: QWidget, item: dict):
    """数字设置项：minimum / maximum / step / suffix 都可省。"""
    return spin_box(
        parent,
        value=int(_number_value(item.get("value"))),
        minimum=int(_number_value(item.get("minimum"))),
        maximum=int(_number_value(item.get("maximum"), 100)),
        step=int(_number_value(item.get("step"), 1)),
        suffix=str(item.get("suffix") or ""),
        on_change=_callback(item),
    )


def _callback(item: dict):
    """设置项的值变化回调；没给回调就返回 None（控件自己就不断开）。"""
    callback = item.get("on_change")
    return callback if callable(callback) else None


def _number_value(value, default: int = 0) -> int:
    """把设置项里的数字尽量转成整数，转不动就用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
