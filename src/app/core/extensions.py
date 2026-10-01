"""插件扩展接口注册表。

插件在 `register(api)` 里通过 `api.provide(接口名, 提供者)` 暴露能力；
声明了 `depends` 的插件按依赖顺序载入后，可以用 `api.require(接口名)` 取到提供者。
"""

from __future__ import annotations

from loguru import logger

# 接口名：小写字母开头，允许字母数字与 _ . -
_NAME_PATTERN = r"^[a-z][a-z0-9_.\-]{1,63}$"


class ExtensionRegistry:
    """接口名 → (提供者, 提供者插件 id)。"""

    def __init__(self) -> None:
        self._items: dict[str, tuple[object, str]] = {}

    def clear(self) -> None:
        self._items.clear()

    def provide(self, name: str, provider: object, plugin_id: str = "") -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("接口名不能为空")
        self._items[key] = (provider, plugin_id)

    def provider(self, name: str) -> object | None:
        entry = self._items.get(str(name or "").strip())
        return entry[0] if entry else None

    def provider_plugin(self, name: str) -> str:
        entry = self._items.get(str(name or "").strip())
        return entry[1] if entry else ""

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def drop_plugin(self, plugin_id: str) -> int:
        """插件被禁用 / 删除时撤掉它提供的全部接口。"""
        gone = [name for name, (_provider, owner) in self._items.items() if owner == plugin_id]
        for name in gone:
            self._items.pop(name, None)
        return len(gone)

    def __contains__(self, name: object) -> bool:
        return str(name or "").strip() in self._items

    def __len__(self) -> int:
        return len(self._items)


extension_registry = ExtensionRegistry()


def valid_extension_name(name: str) -> bool:
    """接口名是否合法（供插件协议校验用）。"""
    import re

    return bool(re.match(_NAME_PATTERN, str(name or "")))


__all__ = ["ExtensionRegistry", "extension_registry", "valid_extension_name"]
