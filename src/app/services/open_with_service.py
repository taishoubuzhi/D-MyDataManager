"""打开方式服务：按扩展名决定用内置查看器、系统默认程序还是自定义程序打开。

解析顺序（每个格式都能在「打开方式」页里单独设置）：

1. 设成 `自定义` 且填了程序 → 用自定义程序打开；
2. 设成 `自定义` 但没填程序 → 交给系统「打开方式」选择框，让用户当场挑；
3. 设成 `继承`，或没设置且没有内置查看器 → 用系统默认程序；
4. `内置`（默认，仅当存在内置查看器）→ 在程序内查看；
5. 设成 `内置` 但查看器被禁用 / 不存在 → 回退系统默认程序。

本模块不导入 Qt：`resolve()` 只给出决定，界面按决定调用查看器或系统程序。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..core import paths, shell
from ..core.viewers import Viewer, ViewerRegistry, normalize_suffix, viewer_registry

MODE_BUILTIN = "builtin"
MODE_INHERIT = "inherit"
MODE_CUSTOM = "custom"
MODE_ASK = "ask"

#: 三种可选模式（写入规则文件时只存这三种）
MODES: tuple[str, ...] = (MODE_BUILTIN, MODE_INHERIT, MODE_CUSTOM)
MODE_LABELS: dict[str, str] = {
    MODE_BUILTIN: "内置查看",
    MODE_INHERIT: "继承系统默认",
    MODE_CUSTOM: "自定义程序",
    MODE_ASK: "交给系统选择",
}

STATE_VERSION = 1


@dataclass(frozen=True)
class OpenRule:
    """某个扩展名的打开方式设置。"""

    suffix: str
    mode: str = ""
    program: str = ""
    args: str = ""

    @property
    def defined(self) -> bool:
        return self.mode in MODES


@dataclass(frozen=True)
class OpenDecision:
    """一次打开请求的结论。"""

    mode: str
    suffix: str
    viewer: Viewer | None = None
    program: str = ""
    args: str = ""
    reason: str = ""

    @property
    def is_builtin(self) -> bool:
        return self.mode == MODE_BUILTIN

    @property
    def needs_ask(self) -> bool:
        return self.mode == MODE_ASK

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    def describe(self) -> str:
        return f"{self.label}（{self.reason}）" if self.reason else self.label


class OpenWithService:
    """打开方式规则读写 + 解析；规则文件损坏时按默认策略继续工作。"""

    def __init__(self, config_file: Path | None = None, registry: ViewerRegistry | None = None) -> None:
        self._config_file = Path(config_file) if config_file is not None else None
        self._registry = registry or viewer_registry

    @property
    def config_file(self) -> Path:
        return self._config_file or paths.OPEN_WITH_FILE

    # ------------------------------------------------------------------ 规则
    def rules(self) -> dict[str, OpenRule]:
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("打开方式规则文件损坏，已忽略：{}（{}）", self.config_file, exc)
            return {}
        raw = data.get("rules") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return {}
        rules: dict[str, OpenRule] = {}
        for key, value in raw.items():
            suffix = normalize_suffix(key)
            if not suffix or not isinstance(value, dict):
                continue
            mode = str(value.get("mode") or "")
            if mode not in MODES:
                continue
            rules[suffix] = OpenRule(
                suffix=suffix,
                mode=mode,
                program=str(value.get("program") or ""),
                args=str(value.get("args") or ""),
            )
        return rules

    def rule_for(self, suffix: str | Path) -> OpenRule:
        name = normalize_suffix(suffix)
        return self.rules().get(name, OpenRule(suffix=name))

    def _write_rules(self, rules: dict[str, OpenRule]) -> None:
        payload = {
            "version": STATE_VERSION,
            "rules": {
                suffix: {"mode": rule.mode, "program": rule.program, "args": rule.args}
                for suffix, rule in sorted(rules.items())
            },
        }
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "") -> OpenRule:
        """写入（或清除）某个扩展名的打开方式；`mode` 为空表示恢复默认。"""
        name = normalize_suffix(suffix)
        if not name:
            raise ValueError("扩展名不能为空")
        rules = self.rules()
        if not mode:
            rules.pop(name, None)
            self._write_rules(rules)
            logger.info("已恢复 {} 的默认打开方式", name)
            return OpenRule(suffix=name)
        if mode not in MODES:
            raise ValueError(f"不支持的打开方式：{mode}")
        rule = OpenRule(suffix=name, mode=mode, program=str(program or "").strip(), args=str(args or "").strip())
        rules[name] = rule
        self._write_rules(rules)
        logger.info("打开方式：.{} → {}", name, rule.mode)
        return rule

    def remove_rule(self, suffix: str) -> None:
        self.set_rule(suffix, "")

    # ---------------------------------------------------------------- 解析
    def viewer_for(self, path: str | Path) -> Viewer | None:
        return self._registry.for_suffix(normalize_suffix(path))

    def has_builtin(self, suffix: str | Path) -> bool:
        return self._registry.for_suffix(normalize_suffix(suffix)) is not None

    def available_modes(self, suffix: str | Path) -> tuple[str, ...]:
        """没有内置查看器的格式只能「继承」或「自定义」。"""
        if self.has_builtin(suffix):
            return MODES
        return (MODE_INHERIT, MODE_CUSTOM)

    def resolve(self, path: str | Path) -> OpenDecision:
        target = Path(path)
        suffix = normalize_suffix(target)
        viewer = self.viewer_for(target)
        rule = self.rules().get(suffix, OpenRule(suffix=suffix))
        if rule.mode == MODE_CUSTOM:
            if rule.program:
                return OpenDecision(MODE_CUSTOM, suffix, program=rule.program, args=rule.args, reason="自定义程序")
            return OpenDecision(MODE_ASK, suffix, reason="未指定自定义程序")
        if rule.mode == MODE_BUILTIN:
            if viewer is not None:
                return OpenDecision(MODE_BUILTIN, suffix, viewer=viewer, reason=f"内置插件 {viewer.plugin_id}")
            return OpenDecision(MODE_INHERIT, suffix, reason="内置查看器不可用")
        if rule.mode == MODE_INHERIT:
            return OpenDecision(MODE_INHERIT, suffix, reason="按设置继承系统默认程序")
        if viewer is not None:
            return OpenDecision(MODE_BUILTIN, suffix, viewer=viewer, reason="默认使用内置查看器")
        return OpenDecision(MODE_INHERIT, suffix, reason="没有内置查看器")

    # ---------------------------------------------------------------- 打开
    def open_external(self, decision: OpenDecision, path: str | Path) -> tuple[bool, str]:
        """按决定用系统程序打开（内置查看由界面负责）。"""
        target = Path(path)
        if decision.mode == MODE_CUSTOM:
            ok = shell.open_with_program(decision.program, decision.args, target)
            return ok, f"自定义程序：{decision.program}"
        if decision.mode == MODE_ASK:
            ok = shell.ask_open_with(target)
            return ok, "已交给系统「打开方式」选择"
        ok = shell.open_default(target)
        return ok, "系统默认程序"


#: 全局服务：界面与服务共用一份规则
open_with_service = OpenWithService()

__all__ = [
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "OpenDecision",
    "OpenRule",
    "OpenWithService",
    "open_with_service",
]
