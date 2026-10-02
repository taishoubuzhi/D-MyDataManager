"""扩展点贡献的界面侧取用：把插件放进坑里的东西变成控件、菜单项或筛选器。

各个界面扩展点的 value 形状见 `app.sdk.points.ExtensionPoint` 的说明。这里负责
取值、兜底与异常隔离——插件给的数据形状不对或回调抛异常时只记日志，不影响界面。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger
from qfluentwidgets import FluentIcon

from ...sdk import Contribution, ExtensionPoint


def items(point: str) -> tuple[Contribution, ...]:
    """某个扩展点上按 order 排好的贡献；没有载入插件时为空。"""
    from ...services.plugin_service import plugin_service

    return plugin_service.point_items(point)


def value_of(item: Contribution) -> Mapping[str, Any]:
    """贡献的值必须是对象；形状不对时当成空对象。"""
    return item.value if isinstance(item.value, Mapping) else {}


def title_of(item: Contribution, key: str = "title") -> str:
    """贡献的显示标题：先看值里的 title/text，都没有就用贡献的键。"""
    return str(value_of(item).get(key) or item.name)


def icon_of(name: Any, fallback: FluentIcon = FluentIcon.APPLICATION) -> FluentIcon:
    """按名字取 FluentIcon；插件给的名字不存在时退回 fallback。"""
    icon = getattr(FluentIcon, str(name or "").upper(), None)
    return icon if isinstance(icon, FluentIcon) else fallback


def resolve(callback: Any, *args: Any) -> Any:
    """调用插件给的回调；不可调用或抛异常时返回 None。"""
    if not callable(callback):
        return None
    try:
        return callback(*args)
    except Exception:
        logger.exception("插件贡献的回调执行失败")
        return None


def text_of(value: Any, *args: Any) -> str:
    """取值并转成文本：可调用就调用，拿不到就是空串。"""
    result = resolve(value, *args) if callable(value) else value
    return "" if result is None else str(result)


def path_filters() -> tuple[Callable[[Path], bool], ...]:
    """导入过滤器（`app.ui.import.filter`）：全部通过才会导入；回调异常按通过处理。"""
    filters: list[Callable[[Path], bool]] = []
    for item in items(ExtensionPoint.IMPORT_FILTER):
        data = value_of(item)
        accept = data.get("accept")
        if callable(accept):
            filters.append(_guarded(accept, str(data.get("name") or item.name)))
    return tuple(filters)


def _guarded(accept: Callable[[Path], bool], name: str) -> Callable[[Path], bool]:
    def check(path: Path) -> bool:
        try:
            return bool(accept(path))
        except Exception:
            logger.exception("导入过滤器 {} 执行失败，已按通过处理", name)
            return True

    return check
