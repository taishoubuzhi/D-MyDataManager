"""应用版本常量：插件协议用它判断「适用管理器版本」。

改版本号只改这个文件：

* 「关于」里的系统版本读的就是下面的 `APP_VERSION`（`SettingsPage` 的版本卡
  `f"v{APP_VERSION} · 数据目录 …"`），改完这里「关于」跟着变；
* 插件协议用的 `MANAGER_VERSION` 也等于它，插件清单里的 `manager_version`
  不得高于它；文档见 `HELP.md` 的「关于与版本」一节。
"""

from __future__ import annotations

APP_NAME = "D-MyDataManager"
# 系统版本号：改这里就能改「关于」处显示的版本（勿在别处再写一份）
APP_VERSION = "0.1.3-rc"
# 插件协议版本：插件清单里的 manager_version 不得高于它
MANAGER_VERSION = APP_VERSION


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
