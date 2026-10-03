"""扩展接口 editor.open 的实现：注册表段 + 打开段 + 规则段。

`EditorLibraryPlugin` 在 setup() 里 `ctx.provide("editor.open", EditorOpenApi(ctx))`，
程序侧 `app.sdk.editors` 只做转调。
"""

from __future__ import annotations

from pathlib import Path

from app.sdk.data import suffix_of

from .registry import (
    KIND_INTERNAL,
    Editor,
    EditorRegistry,
    editor_registry,
    normalize_suffix,
)
from .rules import (
    MODE_ASK,
    MODE_BUILTIN,
    MODE_INHERIT,
    EditorDecision,
    EditorRule,
    EditorRules,
)


class EditorOpenApi:
    """编辑器调度接口。"""

    def __init__(self, ctx, rules: EditorRules | None = None, registry: EditorRegistry | None = None) -> None:
        self._ctx = ctx
        self._registry = registry or editor_registry
        self._rules = rules or EditorRules(registry=self._registry)

    # --------------------------------------------------------- 注册表
    def add_editor(
        self,
        plugin_id: str,
        *,
        editor_id: str,
        name: str,
        extensions,
        kind: str = KIND_INTERNAL,
        factory=None,
        opener=None,
        host: str = "",
        description: str = "",
        capabilities=(),
    ) -> Editor:
        editor = Editor(
            id=str(editor_id),
            name=str(name),
            extensions=tuple(extensions),
            kind=str(kind or KIND_INTERNAL),
            plugin_id=str(plugin_id),
            factory=factory,
            opener=opener,
            host=str(host or ""),
            description=str(description or ""),
            capabilities=tuple(capabilities),
        )
        return self._registry.register(editor)

    def editors(self) -> tuple[Editor, ...]:
        return self._registry.all()

    def editor_by_id(self, editor_id: str) -> Editor | None:
        return self._registry.by_id(editor_id)

    def extensions(self) -> tuple[str, ...]:
        return self._registry.extensions()

    def plugin_ids(self) -> tuple[str, ...]:
        return self._registry.plugin_ids()

    def editor_for(self, path: str | Path) -> Editor | None:
        return self._registry.for_suffix(suffix_of(path))

    def editors_for(self, suffix: str) -> tuple[Editor, ...]:
        return self._registry.all_for_suffix(suffix)

    def clear(self) -> None:
        self._registry.clear()

    def unregister_plugin(self, plugin_id: str) -> int:
        return self._registry.unregister_plugin(plugin_id)

    # ----------------------------------------------------------- 打开
    def edit_path(self, path: str | Path, parent=None) -> tuple[bool, str]:
        """按规则编辑文件：内置编辑器建窗口，否则交给系统程序。"""
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        decision = self._rules.resolve(target)
        if decision.is_builtin and decision.editor is not None:
            return self.edit_with(target, decision.editor, parent)
        return self._rules.open_external(decision, target)

    def edit_with(self, path: str | Path, editor, parent=None) -> tuple[bool, str]:
        from .window import edit_editor

        return edit_editor(self._ctx, editor, Path(path), parent)

    def open_external(self, path: str | Path, *, ask: bool = False) -> tuple[bool, str]:
        target = Path(path)
        if not target.exists():
            return False, f"文件不存在：{target.name}"
        decision = EditorDecision(
            MODE_ASK if ask else MODE_INHERIT,
            normalize_suffix(target),
            reason="编辑器库交给系统程序",
        )
        return self._rules.open_external(decision, target)

    # ----------------------------------------------------------- 规则
    @property
    def config_file(self) -> Path:
        return self._rules.config_file

    def rules(self) -> dict[str, EditorRule]:
        return self._rules.rules()

    def rule_for(self, suffix: str) -> EditorRule | None:
        return self._rules.rule_for(suffix)

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "", editor_id: str = "") -> EditorRule:
        return self._rules.set_rule(suffix, mode, program=program, args=args, editor_id=editor_id)

    def remove_rule(self, suffix: str) -> bool:
        return self._rules.remove_rule(suffix)

    def resolve(self, path: str | Path) -> EditorDecision:
        return self._rules.resolve(path)

    def has_builtin(self, suffix: str | None = None) -> bool:
        return self._rules.has_builtin(suffix)

    def available_modes(self, suffix: str) -> tuple[str, ...]:
        return self._rules.available_modes(suffix)

    # ------------------------------------------------------- 兼容门面
    def suffix_of(self, value: object) -> str:
        return suffix_of(value)

    def editor_ids_for(self, plugin_id: str) -> tuple[str, ...]:
        return tuple(editor.id for editor in self._registry.by_plugin(plugin_id))

    def suffixes_of(self, editor_id: str) -> tuple[str, ...]:
        editor = self._registry.by_id(editor_id)
        return editor.extensions if editor is not None else ()

    def suffixes_of_plugin(self, plugin_id: str) -> tuple[str, ...]:
        seen: list[str] = []
        for editor in self._registry.by_plugin(plugin_id):
            for suffix in editor.extensions:
                if suffix not in seen:
                    seen.append(suffix)
        return tuple(seen)

    def current_editor_id(self, suffix: str) -> str:
        rule = self._rules.rule_for(suffix)
        if rule is not None and rule.mode == MODE_BUILTIN:
            return rule.editor_id
        return ""

    def set_editor(self, suffix: str, editor_id: str) -> bool:
        target = str(editor_id or "")
        if not target:
            self._rules.remove_rule(suffix)
            return True
        if self._registry.by_id(target) is None:
            raise ValueError(f"编辑器不存在：{target}")
        self._rules.set_rule(suffix, MODE_BUILTIN, editor_id=target)
        return True

    def use_editor_for_all(self, editor_id: str, suffixes=None) -> int:
        editor = self._registry.by_id(editor_id)
        if editor is None:
            raise ValueError(f"编辑器不存在：{editor_id}")
        wanted = [normalize_suffix(item) for item in suffixes] if suffixes else list(editor.extensions)
        count = 0
        for suffix in wanted:
            self._rules.set_rule(suffix, MODE_BUILTIN, editor_id=editor.id)
            count += 1
        return count

    def reset_editor(self, editor_id: str, suffixes=None) -> int:
        editor = self._registry.by_id(editor_id)
        if editor is None:
            return 0
        wanted = {normalize_suffix(item) for item in suffixes} if suffixes else set(editor.extensions)
        count = 0
        for suffix, rule in self._rules.rules().items():
            if rule.editor_id == editor.id and (not wanted or suffix in wanted):
                if self._rules.remove_rule(suffix):
                    count += 1
        return count


__all__ = ["EditorOpenApi"]
