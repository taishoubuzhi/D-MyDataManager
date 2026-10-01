"""插件类型插件：提供 `plugin.kind` 扩展接口，让其他插件声明新的插件类型。

类型插件只需要依赖本插件、在自己的 `plugin.json` 里写 `kinds`，不必写 Python 代码；
需要在程序本体里登记条目（例如查看器控件）的类型，才在 `contributor` 里指名登记方法。
"""

from __future__ import annotations

from app.core.plugin_kinds import KIND_EXTENSION, PluginKindSpec, plugin_kinds


class KindApi:
    """插件类型接口：类型插件用 `declare()` 登记（或补充）自己创造的插件类型。"""

    def declare(self, spec: PluginKindSpec, plugin_id: str = "") -> PluginKindSpec:
        """登记一种插件类型；重复声明只合并信息，不会报错。"""
        return plugin_kinds.register(spec.declared_by(plugin_id))

    def kinds(self) -> tuple[PluginKindSpec, ...]:
        """当前已声明的全部插件类型（按 order、id 排序）。"""
        return plugin_kinds.all()


def register(api) -> None:
    api.provide(KIND_EXTENSION, KindApi())
