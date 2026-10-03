"""查看器规则：每个文件格式用哪个查看器打开，还是交给系统程序。

规则文件是 `.configs/viewers.json`（旧版是 `.configs/open_with.json`，首次读取时自动迁移）：

    {"version": 1, "rules": {"md": {"mode": "builtin", "viewer_id": "builtin.markdown"}}}

解析顺序（程序本体不再参与，全部由本库决定）：

1. 设为 `custom` 且填了程序 → 用自定义程序打开；
2. 设为 `custom` 但没填程序 → 弹出系统的「打开方式」对话框让用户当场挑；
3. 设为 `inherit` → 用系统默认程序；
4. 设为 `builtin`（或没有规则但有查看器）→ 用查看器；规则里记了 `viewer_id` 时用指定的那个，
   该插件不可用时回退到同格式的其它查看器，再不行才交给系统；
5. 该格式没有任何查看器 → 回退系统默认程序。

本模块不导入 Qt，界面与窗口都由 `provider` / `window` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.sdk import storage
from app.sdk import ui as sdk_ui
from .registry import Viewer, ViewerRegistry, normalize_suffix, viewer_registry

#: 写入规则文件的三种模式
MODE_BUILTIN = "builtin"
MODE_INHERIT = "inherit"
MODE_CUSTOM = "custom"

#: 解析结果里的第四种模式：不是规则，而是「当场问系统」的结论
MODE_ASK = "ask"

MODES: tuple[str, ...] = (MODE_BUILTIN, MODE_INHERIT, MODE_CUSTOM)
MODE_LABELS: dict[str, str] = {
    MODE_BUILTIN: "使用查看器",
    MODE_INHERIT: "继承系统默认",
    MODE_CUSTOM: "自定义程序",
    MODE_ASK: "交给系统选择",
}

STATE_VERSION = 1
RULES_FILE_NAME = "viewers.json"
LEGACY_RULES_FILE_NAME = "open_with.json"


@dataclass(frozen=True)
class ViewerRule:
    """某个扩展名的查看器设置；`viewer_id` 非空时表示用指定插件的查看器。"""

    suffix: str
    mode: str = ""
    program: str = ""
    args: str = ""
    viewer_id: str = ""

    @property
    def defined(self) -> bool:
        return self.mode in MODES


@dataclass(frozen=True)
class ViewerDecision:
    """一次打开请求的结论：用哪个查看器、还是交给哪个系统程序。"""

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


class ViewerRules:
    """规则读写 + 解析；规则文件损坏时按默认策略继续工作。"""

    def __init__(self, config_file: Path | None = None, registry: ViewerRegistry | None = None) -> None:
        self._config_file = Path(config_file) if config_file is not None else None
        self._registry = registry or viewer_registry

    @property
    def config_file(self) -> Path:
        """规则文件路径；旧版 `open_with.json` 存在时会先迁移过来。"""
        if self._config_file is not None:
            return self._config_file
        target = storage.config_file(RULES_FILE_NAME)
        legacy = storage.config_file(LEGACY_RULES_FILE_NAME)
        if target.exists() or not legacy.exists():
            return target
        payload = storage.read_json(legacy, None)
        if isinstance(payload, dict) and not storage.write_json(target, payload):
            return legacy  # 写不进去就继续用旧文件，至少规则还能生效
        try:
            legacy.unlink()
        except OSError:
            pass
        return target

    # ------------------------------------------------------------------ 规则
    def rules(self) -> dict[str, ViewerRule]:
        payload = storage.read_json(self.config_file, None)
        raw = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return {}
        rules: dict[str, ViewerRule] = {}
        for key, value in raw.items():
            suffix = normalize_suffix(key)
            if not suffix or not isinstance(value, dict):
                continue
            mode = str(value.get("mode") or "")
            if mode not in MODES:
                continue
            rules[suffix] = ViewerRule(
                suffix=suffix,
                mode=mode,
                program=str(value.get("program") or ""),
                args=str(value.get("args") or ""),
                viewer_id=str(value.get("viewer_id") or ""),
            )
        return rules

    def rule_for(self, suffix: str | Path) -> ViewerRule | None:
        name = normalize_suffix(suffix)
        return self.rules().get(name) if name else None

    def _write(self, rules: dict[str, ViewerRule]) -> bool:
        payload = {
            "version": STATE_VERSION,
            "rules": {
                suffix: {
                    "mode": rule.mode,
                    "program": rule.program,
                    "args": rule.args,
                    "viewer_id": rule.viewer_id,
                }
                for suffix, rule in sorted(rules.items())
            },
        }
        return storage.write_json(self.config_file, payload)

    def set_rule(self, suffix: str, mode: str, program: str = "", args: str = "", viewer_id: str = "") -> bool:
        """写入一条规则；`mode` 不在三种模式里时相当于删掉这条规则。"""
        name = normalize_suffix(suffix)
        if not name:
            return False
        rules = self.rules()
        if mode not in MODES:
            rules.pop(name, None)
        else:
            rules[name] = ViewerRule(
                suffix=name,
                mode=mode,
                program=str(program or ""),
                args=str(args or ""),
                viewer_id=str(viewer_id or "") if mode == MODE_BUILTIN else "",
            )
        return self._write(rules)

    def remove_rule(self, suffix: str | Path) -> bool:
        name = normalize_suffix(suffix)
        if not name:
            return False
        rules = self.rules()
        if rules.pop(name, None) is None:
            return False
        return self._write(rules)

    # --------------------------------------------------------------- 查看器
    def viewers_for(self, suffix: str | Path) -> tuple[Viewer, ...]:
        return self._registry.all_for_suffix(suffix)

    def viewer_for(self, path: str | Path) -> Viewer | None:
        return self._registry.for_suffix(path)

    def has_builtin(self, suffix: str | Path) -> bool:
        return bool(self.viewers_for(suffix))

    def available_modes(self, suffix: str | Path) -> tuple[str, ...]:
        """该格式能选的模式；没有查看器时不再提供「使用查看器」。"""
        if self.has_builtin(suffix):
            return MODES
        return (MODE_INHERIT, MODE_CUSTOM)

    def resolve(self, path: str | Path) -> ViewerDecision:
        suffix = normalize_suffix(path)
        rule = self.rule_for(suffix) if suffix else None
        viewers = self.viewers_for(suffix) if suffix else ()
        if rule is not None and rule.mode == MODE_CUSTOM:
            if rule.program.strip():
                return ViewerDecision(MODE_CUSTOM, suffix, program=rule.program, args=rule.args, reason="自定义程序")
            return ViewerDecision(MODE_ASK, suffix, reason="自定义程序没填路径，交给系统选择")
        if rule is not None and rule.mode == MODE_INHERIT:
            return ViewerDecision(MODE_INHERIT, suffix, reason="规则设为继承系统默认")
        if rule is not None and rule.mode == MODE_BUILTIN:
            viewer = self._registry.by_id(rule.viewer_id) if rule.viewer_id else None
            if viewer is not None and suffix in viewer.extensions:
                return ViewerDecision(MODE_BUILTIN, suffix, viewer=viewer, reason="指定查看器")
            if viewers:
                return ViewerDecision(MODE_BUILTIN, suffix, viewer=viewers[0], reason="指定查看器不可用，改用同格式查看器")
            return ViewerDecision(MODE_INHERIT, suffix, reason="该格式没有可用查看器")
        if viewers:
            return ViewerDecision(MODE_BUILTIN, suffix, viewer=viewers[0], reason="默认使用内置查看器")
        return ViewerDecision(MODE_INHERIT, suffix, reason="该格式没有可用查看器")

    # ------------------------------------------------------------ 系统程序
    def open_external(self, decision: ViewerDecision, path: str | Path) -> tuple[bool, str]:
        """按结论调用系统程序：自定义程序 / 系统选择框 / 系统默认程序。"""
        target = Path(path)
        if decision.mode == MODE_CUSTOM and decision.program.strip():
            if sdk_ui.open_with_program(decision.program, decision.args, target):
                return True, f"已交给自定义程序打开：{Path(decision.program).name}"
            return False, f"无法启动自定义程序：{decision.program}"
        if decision.needs_ask:
            if sdk_ui.ask_open_with(target):
                return True, "请在弹出的对话框里选择程序"
            return False, "系统无法打开该文件"
        if sdk_ui.open_default(target):
            return True, "已交给系统默认程序打开"
        return False, "系统无法打开该文件"


__all__ = [
    "LEGACY_RULES_FILE_NAME",
    "MODE_ASK",
    "MODE_BUILTIN",
    "MODE_CUSTOM",
    "MODE_INHERIT",
    "MODE_LABELS",
    "MODES",
    "RULES_FILE_NAME",
    "STATE_VERSION",
    "ViewerDecision",
    "ViewerRule",
    "ViewerRules",
]
