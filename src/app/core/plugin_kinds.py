"""插件类型表：类型不是枚举，而是由插件清单在载入时累积登记的一张表。

插件清单里写 `"kind": "theme"` 即可自定义类型，可选字段 `kind_label`（显示名）、
`kind_description`（说明）、`kind_requires_extensions`（是否必须声明扩展名）；
`parse_manifest()` 校验通过后就把该类型登记进模块级 `plugin_kinds`。
两个插件声明同一个类型不会报错，只会合并信息：已有的显示名 / 说明 / 注册方法优先，
扩展名要求取并集，声明过该类型的插件 id 累积在 `plugins` 里。
内置的 `viewer`（打开方式）与 `page`（弹窗页面）只是最先登记的两个类型。

`contributor` 是 `PluginApi` 上负责登记该类条目的方法名（如 `add_viewer`），
只有程序本体才能决定（清单不能设置）；没有 contributor 的类型，插件用
`api.add(kind, ...)` 记条目、用 `api.provide()` 暴露扩展接口，程序本体不需要先认识它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

KIND_VIEWER = "viewer"
KIND_PAGE = "page"

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

#: 内置类型：viewer 登记查看器控件，page 只提供扩展接口（内置弹窗页面插件提供 dialog）
plugin_kinds.register(
    PluginKindSpec(
        KIND_VIEWER,
        "打开方式",
        "按扩展名显示文件内容的查看器",
        requires_extensions=True,
        contributor="add_viewer",
        order=10,
    )
)
plugin_kinds.register(
    PluginKindSpec(KIND_PAGE, "弹窗页面", "在程序本体之外弹出窗口，供其他插件依赖", order=20)
)

__all__ = [
    "KIND_PAGE",
    "KIND_PATTERN",
    "KIND_VIEWER",
    "PluginKindRegistry",
    "PluginKindSpec",
    "plugin_kinds",
    "valid_kind_name",
]
