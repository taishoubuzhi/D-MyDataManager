"""应用版本常量。

改版本号只改同目录下的 `runtime.json`：

* 「关于」里的系统版本读的就是 `app_version`（`SettingsPage` 的版本卡
  `f"v{APP_VERSION} · 数据目录 …"`），改完这里「关于」跟着变；
* `MANAGER_VERSION` 读 `manager_version`，是**程序自身**的插件协议版本，只由程序内部使用
  （协议 v2 起插件清单不再有 `manager_version` 字段，只声明 `api_version`）；文档见 `HELP.md`。
"""

from __future__ import annotations

from .module_data import load_module_data

_DATA = load_module_data(__file__, "runtime")

APP_NAME = _DATA.value("app_name")
# 系统版本号：「关于」处显示的版本（勿在别处再写一份）
APP_VERSION = _DATA.value("app_version")
# 程序自身的插件协议版本（插件清单只声明 api_version）
MANAGER_VERSION = _DATA.value("manager_version")


def version_tuple(text: str) -> tuple[int, ...]:
    """把 "1.2.3" 解析为 (1, 2, 3)；非数字段截断，空值返回 ()。"""
    parts: list[int] = []
    for chunk in str(text or "").strip().split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_compatible(required: str, current: str = MANAGER_VERSION) -> bool:
    """插件要求的版本不高于当前管理器版本时返回 True。"""
    need = version_tuple(required)
    if not need:
        return True
    have = version_tuple(current)
    return have >= need


def version_text() -> str:
    return f"{APP_NAME} {APP_VERSION}"


__all__ = ["APP_NAME", "APP_VERSION", "MANAGER_VERSION", "is_compatible", "version_text", "version_tuple"]
