"""编辑器规则与决策：按后缀决定用内置编辑器还是交给系统程序。

规则落盘在 `.configs/editors.json`，结构 `{"version": 1, "rules": {"<后缀>": {...}}}`，
与查看器库的 `viewers.json` 同构。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.sdk import storage

from .registry import Editor, EditorRegistry, editor_registry, normalize_suffix

MODE_BUILTIN = "builtin"
MODE_INHERIT = "inherit"
MODE_CUSTOM = "custom"
MODE_ASK = "ask"
MODES = (MODE_BUILTIN, MODE_INHERIT, MODE_CUSTOM)
MODE_LABELS = {
    MODE_BUILTIN: "内置编辑器",
    MODE_INHERIT: "系统默认程序",
    MODE_CUSTOM: "自定义程序",
    MODE_ASK: "每次询问",
}

STATE_VERSION = 1
RULES_FILE_NAME = "editors.json"


@dataclass(frozen=True)
class EditorRule:
    """某个后缀的编辑规则。"""

    suffix: str
    mode: str = ""
    program: str = ""
    args: str = ""
    editor_id: str = ""

    @property
    def defined(self) -> bool:
        return bool(self.mode)


@dataclass(frozen=True)
class EditorDecision:
    """一次编辑请求的决策结果。"""

    mode: str
    suffix: str
    editor: Editor | None = None
    program: str = ""
    args: str = ""
    reason: str = ""

    @property
    def is_builtin(self) -> bool:
        return self.mode == MODE_BUILTIN and self.editor is not None

    @property
    def needs_ask(self) -> bool:
        return self.mode == MODE_ASK

    @property
    def label(self) -> str:
        if self.is_builtin and self.editor is not None:
            return self.editor.name
        return MODE_LABELS.get(self.mode, self.mode or "系统默认程序")


class EditorRules:
    """规则的读写与决策；注册表用于校验后缀、编辑器是否可用。"""

    def __init__(self, config_file: str | Path | None = None, registry: EditorRegistry | None = None) -> None:
        self._config_file = Path(config_file) if config_file is not None else storage.config_file(RULES_FILE_NAME)
        self._registry = registry or editor_registry

    @property
    def config_file(self) -> Path:
        return self._config_file

    def rules(self) -> dict[str, EditorRule]:
        payload = storage.read_json(self._config_file, {}) or {}
        raw = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return {}
        result: dict[str, EditorRule] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            result[normalize_suffix(key)] = EditorRule(
                suffix=normalize_suffix(key),
                mode=str(value.get("mode") or ""),
                program=str(value.get("program") or ""),
                args=str(value.get("args") or ""),
                editor_id=str(value.get("editor_id") or ""),
            )
        return result

    def rule_for(self, suffix: str) -> EditorRule | None:
        return self.rules().get(normalize_suffix(suffix))

    def _write(self, rules: dict[str, EditorRule]) -> None:
        payload = {
            "version": STATE_VERSION,
            "rules": {
                key: {
                    "mode": rule.mode,
                    "program": rule.program,
                    "args": rule.args,
                    "editor_id": rule.editor_id,
                }
                for key, rule in sorted(rules.items())
            },
        }
        if not storage.write_json(self._config_file, payload):
            logger.warning("写入编辑器规则失败：{}", self._config_file)

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "", editor_id: str = "") -> EditorRule:
        rule = EditorRule(
            suffix=normalize_suffix(suffix),
            mode=str(mode or ""),
            program=str(program or ""),
            args=str(args or ""),
            editor_id=str(editor_id or ""),
        )
        rules = self.rules()
        rules[rule.suffix] = rule
        self._write(rules)
        return rule

    def remove_rule(self, suffix: str) -> bool:
        rules = self.rules()
        key = normalize_suffix(suffix)
        if key not in rules:
            return False
        rules.pop(key, None)
        self._write(rules)
        return True

    # ------------------------------------------------------------- 查询
    def editors_for(self, suffix: str) -> tuple[Editor, ...]:
        return self._registry.all_for_suffix(suffix)

    def editor_for(self, suffix: str) -> Editor | None:
        return self._registry.for_suffix(suffix)

    def has_builtin(self, suffix: str | None = None) -> bool:
        if suffix is None:
            return bool(self._registry.extensions())
        return bool(self._registry.all_for_suffix(suffix))

    def available_modes(self, suffix: str) -> tuple[str, ...]:
        return MODES

    # ------------------------------------------------------------- 决策
    def resolve(self, path: str | Path) -> EditorDecision:
        suffix = normalize_suffix(path)
        rule = self.rule_for(suffix)
        if rule is None or not rule.defined:
            editor = self._registry.for_suffix(suffix)
            if editor is not None:
                return EditorDecision(MODE_BUILTIN, suffix, editor=editor, reason="默认使用已注册编辑器")
            return EditorDecision(MODE_INHERIT, suffix, reason="没有注册编辑器，交给系统")
        if rule.mode == MODE_BUILTIN:
            editor = self._registry.by_id(rule.editor_id) if rule.editor_id else self._registry.for_suffix(suffix)
            if editor is None:
                return EditorDecision(MODE_INHERIT, suffix, reason="指定的编辑器不存在，交给系统")
            return EditorDecision(MODE_BUILTIN, suffix, editor=editor, reason="按规则使用编辑器")
        if rule.mode == MODE_CUSTOM:
            if rule.program:
                return EditorDecision(MODE_CUSTOM, suffix, program=rule.program, args=rule.args, reason="按规则用自定义程序")
            return EditorDecision(MODE_ASK, suffix, reason="自定义程序为空，弹出选择框")
        return EditorDecision(MODE_INHERIT, suffix, reason="按规则交给系统")

    def open_external(self, decision: EditorDecision, path: str | Path) -> tuple[bool, str]:
        from app.sdk import ui

        target = Path(path)
        if decision.mode == MODE_CUSTOM and decision.program:
            if ui.open_with_program(decision.program, decision.args, target):
                return True, f"已用 {Path(decision.program).name} 打开"
            return False, "自定义程序无法打开该文件"
        if decision.needs_ask:
            if ui.ask_open_with(target):
                return True, "请在弹出的对话框里选择程序"
            return False, "已取消打开"
        if ui.open_default(target):
            return True, "已交给系统默认程序打开"
        return False, "系统无法打开该文件"


__all__ = [
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "STATE_VERSION",
    "EditorDecision",
    "EditorRule",
    "EditorRules",
]
