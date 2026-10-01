"""打开方式服务：按扩展名决定用哪个内置查看器插件、系统默认程序还是自定义程序打开。

解析顺序（每个格式都能在「打开方式」页里单独设置）：

1. 设成 `自定义` 且填了程序 → 用自定义程序打开；
2. 设成 `自定义` 但没填程序 → 交给系统「打开方式」选择框，让用户当场挑；
3. 设成 `继承`，或没设置且没有内置查看器 → 用系统默认程序；
4. `内置`（默认，仅当存在内置查看器）→ 在程序内查看；规则里记了 `viewer_id` 时用指定插件，
   该插件不可用时回退到同格式的其它查看器，再不行才交给系统；
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
    MODE_BUILTIN: "使用插件打开",
    MODE_INHERIT: "继承系统默认",
    MODE_CUSTOM: "自定义程序",
    MODE_ASK: "交给系统选择",
}

#: 程序本体暴露给插件的打开方式接口名（插件用它改自己格式的打开方式）
OPEN_WITH_EXTENSION = "app.open_with"

STATE_VERSION = 1


@dataclass(frozen=True)
class OpenRule:
    """某个扩展名的打开方式设置；`viewer_id` 非空时表示用指定插件的查看器。"""

    suffix: str
    mode: str = ""
    program: str = ""
    args: str = ""
    viewer_id: str = ""

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
                viewer_id=str(value.get("viewer_id") or ""),
            )
        return rules

    def rule_for(self, suffix: str | Path) -> OpenRule:
        name = normalize_suffix(suffix)
        return self.rules().get(name, OpenRule(suffix=name))

    def _write_rules(self, rules: dict[str, OpenRule]) -> None:
        payload = {
            "version": STATE_VERSION,
            "rules": {
                suffix: {"mode": rule.mode, "program": rule.program, "args": rule.args, "viewer_id": rule.viewer_id}
                for suffix, rule in sorted(rules.items())
            },
        }
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "", viewer_id: str = "") -> OpenRule:
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
        rule = OpenRule(
            suffix=name,
            mode=mode,
            program=str(program or "").strip(),
            args=str(args or "").strip(),
            viewer_id=str(viewer_id or "").strip() if mode == MODE_BUILTIN else "",
        )
        rules[name] = rule
        self._write_rules(rules)
        logger.info("打开方式：.{} → {}{}", name, rule.mode, f"（{rule.viewer_id}）" if rule.viewer_id else "")
        return rule

    def remove_rule(self, suffix: str) -> None:
        self.set_rule(suffix, "")

    # ---------------------------------------------------------------- 解析
    def viewers_for(self, suffix: str | Path) -> tuple[Viewer, ...]:
        """该格式可用的全部内置查看器（可能来自多个插件）。"""
        return self._registry.all_for_suffix(normalize_suffix(suffix))

    def viewer_for(self, path: str | Path) -> Viewer | None:
        return self._registry.for_suffix(normalize_suffix(path))

    def has_builtin(self, suffix: str | Path) -> bool:
        return bool(self.viewers_for(suffix))

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
            if rule.viewer_id:
                pinned = self._registry.by_id(rule.viewer_id)
                if pinned is not None:
                    return OpenDecision(
                        MODE_BUILTIN, suffix, viewer=pinned, reason=f"指定插件 {pinned.plugin_id}"
                    )
                if viewer is not None:
                    return OpenDecision(
                        MODE_BUILTIN, suffix, viewer=viewer, reason=f"指定插件不可用，改用 {viewer.plugin_id}"
                    )
                return OpenDecision(MODE_INHERIT, suffix, reason="指定插件不可用")
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


class OpenWithApi:
    """程序本体暴露给插件的「打开方式」接口（接口名 `app.open_with`）。

    插件用它把某个扩展名、或自己声明的全部扩展名改成用指定插件打开，不必了解规则文件格式；
    典型用法就是查看器插件的「插件选项」页里的「把本插件支持的格式都改成用本插件打开」。
    """

    def __init__(self, service: OpenWithService | None = None, registry: ViewerRegistry | None = None) -> None:
        self._service = service or open_with_service
        self._registry = registry or viewer_registry

    def suffix_of(self, path: str | Path) -> str:
        return normalize_suffix(path)

    def viewers_for(self, suffix: str | Path) -> tuple[Viewer, ...]:
        return self._service.viewers_for(suffix)

    def viewer_ids_for(self, suffix: str | Path) -> tuple[str, ...]:
        return tuple(viewer.id for viewer in self._service.viewers_for(suffix))

    def suffixes_of(self, viewer_id: str) -> tuple[str, ...]:
        """某个查看器声明的扩展名。"""
        viewer = self._registry.by_id(viewer_id)
        return tuple(viewer.extensions) if viewer is not None else ()

    def suffixes_of_plugin(self, plugin_id: str) -> tuple[str, ...]:
        """某个插件注册的全部查看器声明的扩展名（去重排序）。"""
        found = {extension for viewer in self._registry.by_plugin(plugin_id) for extension in viewer.extensions}
        return tuple(sorted(found))

    def current_viewer_id(self, suffix: str | Path) -> str:
        return self._service.rule_for(suffix).viewer_id

    def set_viewer(self, suffix: str | Path, viewer_id: str) -> OpenRule:
        """把某个扩展名改成用指定查看器打开（`viewer_id` 为空则恢复默认）。"""
        name = normalize_suffix(suffix)
        if not name:
            raise ValueError("扩展名不能为空")
        if not viewer_id:
            self._service.remove_rule(name)
            return OpenRule(suffix=name)
        if self._registry.by_id(viewer_id) is None:
            raise ValueError(f"查看器不存在：{viewer_id}")
        return self._service.set_rule(name, MODE_BUILTIN, viewer_id=viewer_id)

    def use_viewer_for_all(self, viewer_id: str, suffixes: tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
        """把该查看器声明的扩展名（或指定子集）都改成用它打开，返回改动的扩展名。"""
        targets = tuple(suffixes) if suffixes is not None else self.suffixes_of(viewer_id)
        changed: list[str] = []
        for suffix in targets:
            name = normalize_suffix(suffix)
            if not name:
                continue
            self.set_viewer(name, viewer_id)
            changed.append(name)
        return tuple(changed)

    def reset_viewer(self, viewer_id: str, suffixes: tuple[str, ...] | list[str] | None = None) -> tuple[str, ...]:
        """清掉该查看器相关扩展名上「指定用本插件打开」的规则，返回改动的扩展名。"""
        targets = tuple(suffixes) if suffixes is not None else self.suffixes_of(viewer_id)
        changed: list[str] = []
        for suffix in targets:
            name = normalize_suffix(suffix)
            if not name:
                continue
            rule = self._service.rule_for(name)
            if rule.viewer_id == viewer_id:
                self._service.remove_rule(name)
                changed.append(name)
        return tuple(changed)


#: 全局服务：界面与服务共用一份规则
open_with_service = OpenWithService()

#: 程序本体提供给插件的打开方式接口（`plugin_service.bootstrap(OPEN_WITH_EXTENSION, open_with_api)`）
open_with_api = OpenWithApi()

__all__ = [
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "OPEN_WITH_EXTENSION",
    "OpenDecision",
    "OpenRule",
    "OpenWithApi",
    "OpenWithService",
    "open_with_api",
    "open_with_service",
]
