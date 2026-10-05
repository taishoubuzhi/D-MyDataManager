"""自动标注规则：规则模型、字段匹配引擎与「出厂 + 用户」合并视图。

这里只有纯逻辑，不碰界面、不碰数据库，方便单测：

    rule = Rule(key="word", name="Word 文档", field=FIELD_SUFFIX, op=OP_IN, pattern="doc,docx",
                tags=("Word文档",))
    rule.match(item_ref)   # True / False

用户改动写在自己的文件里（由 plugin.py 决定路径，默认 `.configs/autolabel.rules.json`），
载荷形如 `{"version": 1, "rules": [...], "hidden": ["pdf"]}`：**只存与出厂不同的规则**，
出厂规则以后升级、加新格式仍然生效，用户也不会因为程序升级被覆盖掉自己的改动。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

# --------------------------------------------------------------- 规则种类
KIND_MATCH = "match"
KIND_PROMPT = "prompt"
KINDS: tuple[str, ...] = (KIND_MATCH, KIND_PROMPT)
KIND_LABELS: dict[str, str] = {
    KIND_MATCH: "按字段匹配",
    KIND_PROMPT: "交模型判断",
}

# --------------------------------------------------------------- 匹配字段
FIELD_SUFFIX = "suffix"
FIELD_NAME = "name"
FIELD_PATH = "path"
FIELD_TEXT = "text"
FIELD_TYPE = "type"
FIELDS: tuple[str, ...] = (FIELD_SUFFIX, FIELD_NAME, FIELD_PATH, FIELD_TEXT, FIELD_TYPE)
FIELD_LABELS: dict[str, str] = {
    FIELD_SUFFIX: "文件后缀",
    FIELD_NAME: "文件名称",
    FIELD_PATH: "文件路径",
    FIELD_TEXT: "文件内容",
    FIELD_TYPE: "数据类型",
}

# --------------------------------------------------------------- 算子
OP_IS = "is"
OP_IN = "in"
OP_CONTAINS = "contains"
OP_STARTSWITH = "startswith"
OP_ENDSWITH = "endswith"
OP_REGEX = "regex"
OP_GLOB = "glob"
OPS: tuple[str, ...] = (OP_IS, OP_IN, OP_CONTAINS, OP_STARTSWITH, OP_ENDSWITH, OP_REGEX, OP_GLOB)
OP_LABELS: dict[str, str] = {
    OP_IS: "等于",
    OP_IN: "在列表里",
    OP_CONTAINS: "包含",
    OP_STARTSWITH: "开头是",
    OP_ENDSWITH: "结尾是",
    OP_REGEX: "正则匹配",
    OP_GLOB: "通配符匹配",
}

#: 多值分隔符：英文/中文逗号、顿号、分号、空白。
SPLIT_PATTERN = re.compile(r"[,，、;；\s]+")

SOURCE_FACTORY = "factory"
SOURCE_USER = "user"


def split_pattern(text: str) -> tuple[str, ...]:
    """把 `"doc,docx"` 拆成 `("doc", "docx")`（去空、保序、去重）。"""
    seen: list[str] = []
    for part in SPLIT_PATTERN.split(str(text or "")):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return tuple(seen)


@dataclass(frozen=True)
class Rule:
    """一条规则：`match` 用字段匹配出标签，`prompt` 则交给模型判断。"""

    key: str
    name: str = ""
    kind: str = KIND_MATCH
    enabled: bool = True
    field: str = FIELD_SUFFIX
    op: str = OP_IN
    pattern: str = ""
    tags: tuple[str, ...] = ()
    prompt: str = ""
    source: str = SOURCE_FACTORY

    # ---------------------------------------------------------- 展示
    @property
    def title(self) -> str:
        return self.name or self.key

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def field_label(self) -> str:
        return FIELD_LABELS.get(self.field, self.field)

    @property
    def op_label(self) -> str:
        return OP_LABELS.get(self.op, self.op)

    @property
    def field_text(self) -> str:
        """一行人话：`文件后缀 在列表里 doc,docx`。"""
        return f"{self.field_label} {self.op_label} {self.pattern}".strip()

    @property
    def tag_text(self) -> str:
        return "、".join(self.tags)

    @property
    def model_ready(self) -> bool:
        return self.kind == KIND_PROMPT and bool(self.prompt.strip())

    @property
    def problems(self) -> tuple[str, ...]:
        """载入时要标出来的问题（只提示，不炸）。"""
        issues: list[str] = []
        if self.kind not in KINDS:
            issues.append(f"未知的规则类型：{self.kind}")
        if self.field not in FIELDS:
            issues.append(f"未知的匹配字段：{self.field}")
        if self.op not in OPS:
            issues.append(f"未知的匹配方式：{self.op}")
        if not self.tags and not self.model_ready:
            issues.append("没有要挂的标签")
        if self.kind == KIND_MATCH and not self.pattern.strip():
            issues.append("没有填匹配内容")
        if self.kind == KIND_MATCH and self.op == OP_REGEX and self.pattern.strip():
            try:
                re.compile(self.pattern)
            except re.error as exc:
                issues.append(f"正则表达式有问题：{exc}")
        return tuple(issues)

    @property
    def usable(self) -> bool:
        return self.enabled and not self.problems

    # ---------------------------------------------------------- 匹配
    def match(self, item, *, text: str = "") -> bool:
        """`item` 是 `app.sdk.items.ItemRef`；字段「文件内容」用 `text` 传入。"""
        if not self.usable or self.kind != KIND_MATCH:
            return False
        return self.matches_fields(item, text=text)

    def matches_fields(self, item, *, text: str = "") -> bool:
        """只看字段是否命中，不管规则类型（`prompt` 规则用它做初筛）。"""
        value = self._value_of(item, text=text)
        if not value:
            return False
        return self._compare(value)

    def _value_of(self, item, *, text: str = "") -> str:
        if self.field == FIELD_TEXT:
            return str(text or "")
        if self.field == FIELD_NAME:
            return str(getattr(item, "name", "") or "")
        if self.field == FIELD_PATH:
            return str(getattr(item, "file_path", "") or "")
        if self.field == FIELD_TYPE:
            return str(getattr(item, "type", "") or "")
        return str(getattr(item, "suffix", "") or "")

    def _compare(self, value: str) -> bool:
        raw = self.pattern.strip()
        low = value.strip().lower()
        if self.op == OP_REGEX:
            try:
                return re.search(raw, value, re.IGNORECASE) is not None
            except re.error:
                return False
        if self.op == OP_GLOB:
            return fnmatch.fnmatch(low, raw.lower())
        parts = [part.lower() for part in split_pattern(raw)]
        if self.op == OP_IS:
            return low == raw.lower()
        if self.op == OP_IN:
            return low in parts
        if self.op == OP_CONTAINS:
            return any(part in low for part in parts)
        if self.op == OP_STARTSWITH:
            return any(low.startswith(part) for part in parts)
        if self.op == OP_ENDSWITH:
            return any(low.endswith(part) for part in parts)
        return False

    # ---------------------------------------------------------- 存取
    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "enabled": bool(self.enabled),
            "field": self.field,
            "op": self.op,
            "pattern": self.pattern,
            "tags": list(self.tags),
            "prompt": self.prompt,
        }

    def with_source(self, source: str) -> "Rule":
        return replace(self, source=source)

    def same_as(self, other: "Rule") -> bool:
        return self.to_dict() == other.to_dict()

    @classmethod
    def from_dict(cls, payload: object, *, source: str = SOURCE_FACTORY) -> "Rule | None":
        """字典 → 规则；键缺失/类型不对就返回 None（由调用方报错）。"""
        if not isinstance(payload, dict):
            return None
        key = str(payload.get("key") or "").strip()
        if not key:
            return None
        raw_tags = payload.get("tags")
        if isinstance(raw_tags, str):
            tags = split_pattern(raw_tags)
        elif isinstance(raw_tags, (list, tuple)):
            tags = tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
        else:
            tags = ()
        kind = str(payload.get("kind") or KIND_MATCH).strip().lower() or KIND_MATCH
        field = str(payload.get("field") or FIELD_SUFFIX).strip().lower() or FIELD_SUFFIX
        op = str(payload.get("op") or OP_IN).strip().lower() or OP_IN
        return cls(
            key=key,
            name=str(payload.get("name") or "").strip(),
            kind=kind,
            enabled=bool(payload.get("enabled", True)),
            field=field,
            op=op,
            pattern=str(payload.get("pattern") or ""),
            tags=tags,
            prompt=str(payload.get("prompt") or ""),
            source=source,
        )


def parse_rules(data: object, *, source: str = SOURCE_FACTORY) -> tuple[tuple[Rule, ...], list[str]]:
    """解析 `{version, rules}` / 纯列表；返回 (规则, 出错说明)。"""
    if isinstance(data, dict):
        raw = data.get("rules")
    else:
        raw = data
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        return (), ["规则列表必须是一个数组"]
    rules: list[Rule] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        rule = Rule.from_dict(entry, source=source)
        if rule is None:
            errors.append(f"第 {index} 条规则缺少 key，已跳过")
            continue
        if rule.key in seen:
            errors.append(f"规则 key 重复：{rule.key}（只保留第一条）")
            continue
        seen.add(rule.key)
        rules.append(rule)
    return tuple(rules), errors


def dump_rules(rules: Iterable[Rule]) -> dict:
    return {"version": 1, "rules": [rule.to_dict() for rule in rules]}


@dataclass(frozen=True)
class RuleSet:
    """出厂规则 + 用户改动的合并视图。

    * 同名 key 的用户规则**覆盖**出厂规则（保留出厂位置，顺序稳定）；
    * `hidden` 里的出厂 key 从视图里去掉（用户「删除」出厂规则）；
    * 用户新增的 key 排在出厂规则之后。
    """

    factory: tuple[Rule, ...] = ()
    user: tuple[Rule, ...] = ()
    hidden: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    # ---------------------------------------------------------- 视图
    @property
    def rules(self) -> tuple[Rule, ...]:
        changed = {rule.key: rule for rule in self.user}
        hidden = set(self.hidden)
        merged: list[Rule] = []
        for rule in self.factory:
            if rule.key in hidden:
                continue
            merged.append(changed.pop(rule.key, rule))
        for rule in self.user:
            if rule.key in changed:
                merged.append(changed.pop(rule.key))
        return tuple(merged)

    @property
    def enabled(self) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.usable)

    def by_key(self, key: str) -> Rule | None:
        for rule in self.rules:
            if rule.key == key:
                return rule
        return None

    def factory_changed(self, key: str) -> bool:
        """该 key 是否是用户改过的（用于界面上标「已改」）。"""
        return any(rule.key == key for rule in self.user) or key in set(self.hidden)

    # ---------------------------------------------------------- 匹配
    def match_tags(self, item, *, text: str = "") -> tuple[str, ...]:
        """所有启用的 `match` 规则命中的标签（去重、保序）。"""
        names: list[str] = []
        for rule in self.enabled:
            if rule.kind != KIND_MATCH or not rule.match(item, text=text):
                continue
            for tag in rule.tags:
                if tag not in names:
                    names.append(tag)
        return tuple(names)

    def match_rules(self, item, *, text: str = "") -> tuple[Rule, ...]:
        return tuple(
            rule
            for rule in self.enabled
            if rule.kind == KIND_MATCH and rule.match(item, text=text)
        )

    def prompt_rules(self, item=None, *, text: str = "") -> tuple[Rule, ...]:
        """启用的 `prompt` 规则；给了条目就先用 `match` 部分做初筛。"""
        picked: list[Rule] = []
        for rule in self.enabled:
            if rule.kind != KIND_PROMPT or not rule.model_ready:
                continue
            if item is not None and rule.pattern.strip() and not rule.matches_fields(item, text=text):
                continue
            picked.append(rule)
        return tuple(picked)

    # ---------------------------------------------------------- 改动
    def with_rule(self, rule: Rule) -> "RuleSet":
        user = [item for item in self.user if item.key != rule.key]
        user.append(rule.with_source(SOURCE_USER))
        hidden = tuple(key for key in self.hidden if key != rule.key)
        return replace(self, user=tuple(user), hidden=hidden)

    def without_key(self, key: str) -> "RuleSet":
        """删规则：出厂规则改记进 `hidden`，用户规则直接去掉。"""
        user = tuple(rule for rule in self.user if rule.key != key)
        if any(rule.key == key for rule in self.factory) and key not in self.hidden:
            return replace(self, user=user, hidden=self.hidden + (key,))
        return replace(self, user=user)

    def restore_key(self, key: str) -> "RuleSet":
        """恢复某条出厂规则的原样。"""
        user = tuple(rule for rule in self.user if rule.key != key)
        return replace(self, user=user, hidden=tuple(item for item in self.hidden if item != key))

    def clear_user(self) -> "RuleSet":
        return replace(self, user=(), hidden=())

    # ---------------------------------------------------------- 存取
    @property
    def user_payload(self) -> dict:
        """只写「和出厂不一样」的部分，出厂规则升级仍能生效。"""
        base = {rule.key: rule for rule in self.factory}
        changed = [
            rule.to_dict()
            for rule in self.user
            if rule.key not in base or not rule.same_as(base[rule.key])
        ]
        hidden = [key for key in self.hidden if key in base]
        return {"version": 1, "rules": changed, "hidden": hidden}

    @classmethod
    def from_payload(
        cls,
        payload: object,
        factory: Sequence[Rule] = (),
    ) -> "RuleSet":
        factory = tuple(factory)
        if not isinstance(payload, dict):
            return cls(factory=factory)
        user, errors = parse_rules({"rules": payload.get("rules")}, source=SOURCE_USER)
        known = {rule.key for rule in factory}
        raw_hidden = payload.get("hidden")
        hidden = tuple(
            str(key).strip() for key in raw_hidden if str(key).strip() in known
        ) if isinstance(raw_hidden, (list, tuple)) else ()
        # 用户规则里与出厂完全一致的，不占覆盖位（否则出厂更新会被旧值挡住）。
        base = {rule.key: rule for rule in factory}
        effective = tuple(
            rule for rule in user if rule.key not in base or not rule.same_as(base[rule.key])
        )
        return cls(factory=factory, user=effective, hidden=hidden, errors=tuple(errors))
