"""插件类型表：类型不是枚举，而是由「插件类型插件」在载入时声明出来的一张表。

程序本体只登记一个引导类型 `kind`（插件类型本身），其余类型都由类型插件声明：
类型插件的清单里写 `kinds: [{"id": "theme", "label": "主题", ...}, ...]`，
载入时通过 `builtin.kind` 插件提供的 `plugin.kind` 扩展接口登记进模块级 `plugin_kinds`，
其他插件只要 `depends: ["builtin.kind.theme"]` 就能把 `"kind": "theme"` 用起来。
两个插件声明同一个类型不会报错，只会合并信息：已有的显示名 / 说明 / 注册方法优先，
扩展名要求取并集，声明过该类型的插件 id 累积在 `plugins` 里。

`contributor` 是 `PluginApi` 上负责登记该类条目的方法名（如 `add_viewer`），
只有程序本体才能决定（清单不能设置）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

KIND_KIND = "kind"
KIND_VIEWER = "viewer"
KIND_PAGE = "page"

#: 「插件类型插件」的插件 id 与它提供的扩展接口名。
KIND_PLUGIN_ID = "builtin.kind"
KIND_EXTENSION = "plugin.kind"

KIND_PATTERN = r"^[a-z][a-z0-9_.\-]{1,63}$"

_ID_RE = re.compile(KIND_PATTERN)


def valid_kind_name(text: str) -> bool:
    """插件类型 id 是否合法（小写字母开头，可含数字、下划线、点和连字符）。"""
    return bool(_ID_RE.match(text or ""))


@dataclass(frozen=True)
class PluginKindSpec:
    """一种插件类型：显示名、说明、是否必须声明扩展名、负责登记条目的 PluginApi 方法。"""

    id: str
    label: str = ""
    description: str = ""
    requires_extensions: bool = False
    contributor: str = ""
    order: int = 100
    plugins: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """显示名（清单没写就用 id）。"""
        return self.label or self.id

    @property
    def text(self) -> str:
        return f"{self.name}（{self.id}）"

    def declared_by(self, plugin_id: str) -> PluginKindSpec:
        """记下这是哪个插件声明的类型（重复声明只累积插件 id）。"""
        if not plugin_id or plugin_id in self.plugins:
            return self
        return replace(self, plugins=self.plugins + (plugin_id,))


def _merge(current: PluginKindSpec, incoming: PluginKindSpec) -> PluginKindSpec:
    """合并同一个类型的两次声明：已有的信息优先，扩展名要求取并集。"""
    plugins = current.plugins
    for plugin_id in incoming.plugins:
        if plugin_id and plugin_id not in plugins:
            plugins += (plugin_id,)
    return replace(
        current,
        label=current.label or incoming.label,
        description=current.description or incoming.description,
        requires_extensions=current.requires_extensions or incoming.requires_extensions,
        contributor=current.contributor or incoming.contributor,
        plugins=plugins,
    )


class PluginKindRegistry:
    """插件类型表（进程内单例，见模块级 `plugin_kinds`）。"""

    def __init__(self) -> None:
        self._specs: dict[str, PluginKindSpec] = {}

    def register(self, spec: PluginKindSpec) -> PluginKindSpec:
        """登记一种类型；已存在时合并信息并返回合并结果（重复登记不是错误）。"""
        if not valid_kind_name(spec.id):
            raise ValueError(f"插件类型 id 不合法：{spec.id}")
        current = self._specs.get(spec.id)
        merged = spec if current is None else _merge(current, spec)
        self._specs[spec.id] = merged
        return merged

    def unregister(self, kind: str) -> bool:
        return self._specs.pop(kind, None) is not None

    def remove_by_plugin(self, plugin_id: str) -> tuple[str, ...]:
        """撤掉某个插件声明的类型，返回被完全移除的类型 id。

        还有别的插件声明同一类型时只把它从 `plugins` 里去掉。
        """
        dropped: list[str] = []
        for kind, spec in list(self._specs.items()):
            if plugin_id not in spec.plugins:
                continue
            rest = tuple(item for item in spec.plugins if item != plugin_id)
            if rest:
                self._specs[kind] = replace(spec, plugins=rest)
            else:
                del self._specs[kind]
                dropped.append(kind)
        return tuple(dropped)

    def get(self, kind: str) -> PluginKindSpec | None:
        return self._specs.get(kind)

    def label(self, kind: str) -> str:
        spec = self.get(kind)
        return spec.name if spec is not None else kind

    def all(self) -> tuple[PluginKindSpec, ...]:
        return tuple(sorted(self._specs.values(), key=lambda item: (item.order, item.id)))

    def ids(self) -> tuple[str, ...]:
        return tuple(spec.id for spec in self.all())

    def labels(self) -> tuple[tuple[str, str], ...]:
        return tuple((spec.id, spec.name) for spec in self.all())

    def clear(self) -> None:
        self._specs.clear()

    def __contains__(self, kind: object) -> bool:
        return kind in self._specs

    def __len__(self) -> int:
        return len(self._specs)


plugin_kinds = PluginKindRegistry()


def register_builtin_kinds() -> None:
    """登记程序本体自带的引导类型（只有「插件类型」本身，其余由类型插件声明）。"""
    plugin_kinds.register(
        PluginKindSpec(
            KIND_KIND,
            "插件类型",
            "由类型插件声明其他插件类型（依赖 plugin.kind 扩展接口）",
            order=1,
        )
    )


register_builtin_kinds()

__all__ = [
    "KIND_EXTENSION",
    "KIND_KIND",
    "KIND_PAGE",
    "KIND_PATTERN",
    "KIND_PLUGIN_ID",
    "KIND_VIEWER",
    "PluginKindRegistry",
    "PluginKindSpec",
    "plugin_kinds",
    "register_builtin_kinds",
    "valid_kind_name",
]
