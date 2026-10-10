"""`viewer.open` 扩展接口：程序本体与别的插件用它打开文件、读写查看器规则。

程序本体只剩调度门面（`app.services.viewer_service`）；注册表、规则与界面都在本插件里，
「用哪个查看器、还是交给系统程序」这件事全部由本接口回答。
"""

from __future__ import annotations

from pathlib import Path

from .registry import Viewer, ViewerRegistry, normalize_suffix, viewer_registry

from .rules import (
    MODE_ASK,
    MODE_BUILTIN,
    MODE_INHERIT,
    ViewerDecision,
    ViewerRule,
    ViewerRules,
)
from .window import open_viewer


class ViewerOpenApi:
    """查看器调度接口（扩展接口名 `viewer.open`）。"""

    def __init__(
        self,
        ctx,
        rules: ViewerRules | None = None,
        registry: ViewerRegistry | None = None,
    ) -> None:
        self._ctx = ctx
        self._registry = registry or viewer_registry
        self._rules = rules or ViewerRules(registry=self._registry)

    # -------------------------------------------------------------- 注册表
    def add_viewer(
        self,
        plugin_id: str,
        *,
        viewer_id: str,
        name: str,
        extensions: tuple[str, ...],
        kind: str = "text",
        factory=None,
        opener=None,
        host: str = "",
        description: str = "",
        capabilities: tuple[str, ...] = (),
    ) -> Viewer:
        """登记一个查看器（由插件服务转交 `ctx.add_viewer(...)` 的请求）。"""
        viewer = Viewer(
            id=viewer_id,
            name=name,
            extensions=tuple(extensions),
            kind=kind,
            plugin_id=plugin_id,
            factory=factory,
            opener=opener,
            host=host,
            description=description,
            capabilities=tuple(capabilities),
        )
        return self._registry.register(viewer)

    def viewers(self) -> list[Viewer]:
        """当前登记的全部查看器（按 id 排序）。"""
        return self._registry.all()

    def viewer_by_id(self, viewer_id: str) -> Viewer | None:
        return self._registry.by_id(str(viewer_id or ""))

    def extensions(self) -> list[str]:
        """全部已登记的扩展名（供界面列出可配置格式）。"""
        return self._registry.extensions()

    def plugin_ids(self) -> list[str]:
        return self._registry.plugin_ids()

    def viewer_for(self, path: str | Path) -> Viewer | None:
        return self._registry.for_suffix(normalize_suffix(path))

    def clear(self) -> None:
        """清空注册表（插件服务在重新载入全部插件前调用）。"""
        self._registry.clear()

    def unregister_plugin(self, plugin_id: str) -> int:
        """注销某个插件的全部查看器，返回注销数量。"""
        return self._registry.unregister_plugin(plugin_id)

    # ---------------------------------------------------------------- 打开
    def open_path(self, path, parent=None, *, sources=()) -> tuple[bool, str]:
        """按当前规则打开文件：内置查看器 / 自定义程序 / 系统默认程序。

        `sources` 是宿主「当前列表里的文件顺序」，交给内容页做上一张 / 下一张。
        """
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        decision = self._rules.resolve(target)
        if decision.is_builtin and decision.viewer is not None:
            return open_viewer(self._ctx, decision.viewer, target, parent, sources=sources)
        return self._rules.open_external(decision, target)

    def open_viewer(self, path, viewer, parent=None, *, sources=()) -> tuple[bool, str]:
        """点名用某个查看器打开（右键菜单的「用…查看」走这里，`sources` 同上）。"""
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        return open_viewer(self._ctx, viewer, target, parent, sources=sources)

    def open_external(self, path, *, ask: bool = False) -> tuple[bool, str]:
        """跳过内置查看器：`ask=True` 弹出系统选择框，否则用系统默认程序。"""
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        mode = MODE_ASK if ask else MODE_INHERIT
        reason = "用户要求交给系统选择" if ask else "用户要求用系统默认程序"
        return self._rules.open_external(ViewerDecision(mode, normalize_suffix(target), reason=reason), target)

    # ---------------------------------------------------------------- 规则
    @property
    def config_file(self) -> Path:
        return self._rules.config_file

    def rules(self) -> dict[str, ViewerRule]:
        return self._rules.rules()

    def rule_for(self, suffix: str | Path) -> ViewerRule | None:
        return self._rules.rule_for(suffix)

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "", viewer_id: str = "") -> bool:
        return self._rules.set_rule(suffix, mode, program, args, viewer_id)

    def remove_rule(self, suffix: str | Path) -> bool:
        return self._rules.remove_rule(suffix)

    def resolve(self, path: str | Path) -> ViewerDecision:
        return self._rules.resolve(path)

    def viewers_for(self, suffix: str | Path):
        return self._rules.viewers_for(suffix)

    def rule_viewer_for(self, path: str | Path):
        """规则挑出来的查看器（与注册表无关，受规则影响）。"""
        return self._rules.viewer_for(path)

    def has_builtin(self, suffix: str | Path) -> bool:
        return self._rules.has_builtin(suffix)

    def available_modes(self, suffix: str | Path) -> tuple[str, ...]:
        return self._rules.available_modes(suffix)

    # -------------------------------------------------- 兼容面（程序本体用）
    def suffix_of(self, value: str | Path) -> str:
        return normalize_suffix(value)

    def viewer_ids_for(self, plugin_id: str) -> list[str]:
        return [viewer.id for viewer in self._registry.by_plugin(plugin_id)]

    def suffixes_of(self, viewer_id: str) -> tuple[str, ...]:
        viewer = self._registry.by_id(str(viewer_id or ""))
        return tuple(viewer.extensions) if viewer is not None else ()

    def suffixes_of_plugin(self, plugin_id: str) -> tuple[str, ...]:
        found: list[str] = []
        for viewer in self._registry.by_plugin(plugin_id):
            for suffix in viewer.extensions:
                if suffix not in found:
                    found.append(suffix)
        return tuple(found)

    def current_viewer_id(self, suffix: str | Path) -> str:
        rule = self._rules.rule_for(suffix)
        if rule is None or rule.mode != MODE_BUILTIN:
            return ""
        return rule.viewer_id

    def set_viewer(self, suffix: str | Path, viewer_id: str) -> bool:
        """把某个格式固定到某个查看器；`viewer_id` 为空时恢复默认策略。"""
        name = normalize_suffix(suffix)
        if not name:
            return False
        target = str(viewer_id or "")
        if not target:
            return self.remove_rule(name)
        if self._registry.by_id(target) is None:
            raise ValueError(f"查看器不存在：{target}")
        return self.set_rule(name, MODE_BUILTIN, viewer_id=target)

    def use_viewer_for_all(self, viewer_id: str, suffixes=None) -> int:
        """把该查看器支持的格式（或指定的一批格式）全部固定到它，返回写入条数。"""
        viewer = self._registry.by_id(str(viewer_id or ""))
        if viewer is None:
            raise ValueError(f"查看器不存在：{viewer_id}")
        names = [normalize_suffix(item) for item in (suffixes if suffixes is not None else viewer.extensions)]
        count = 0
        for name in names:
            if name and self.set_rule(name, MODE_BUILTIN, viewer_id=viewer.id):
                count += 1
        return count

    def reset_viewer(self, viewer_id: str, suffixes=None) -> int:
        """撤销该查看器的固定设置，返回恢复默认的格式数。"""
        target = str(viewer_id or "")
        wanted = {normalize_suffix(item) for item in suffixes} if suffixes is not None else None
        rules = self._rules.rules()
        count = 0
        for name, rule in list(rules.items()):
            if rule.viewer_id != target:
                continue
            if wanted is not None and name not in wanted:
                continue
            if self._rules.remove_rule(name):
                count += 1
        return count


__all__ = ["ViewerOpenApi"]
