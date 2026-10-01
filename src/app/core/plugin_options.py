"""插件选项协议：清单里声明的可配置项，以及值的规范化。

插件在 `plugin.json` 里用 `options` 声明自己有哪些选项，程序本体据此在
「插件选项」页里自动生成控件；用户在界面上改完的值会被存进插件状态文件，
插件在 `register(api)` 里用 `api.option("键")` 读回来，从而实现同一个插件在不同
配置下的差异化功能。

清单里两种写法都支持：

    "options": [
        {"key": "auto_fit", "label": "打开时自适应", "kind": "bool", "default": true},
        {"key": "zoom_step", "label": "缩放步长", "kind": "choice",
         "choices": [["1.1", "小"], ["1.25", "中"], ["1.5", "大"]], "default": "1.25"}
    ]

    "options": {"auto_fit": {"label": "打开时自适应", "kind": "bool", "default": true}}

可选字段：`label`（显示名，缺省用 key）、`description`（说明）、`kind`（bool / text / choice）、
`default`、`choices`（choice 必填，元素为 `[值, 显示名]` 或纯字符串）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OPTION_BOOL = "bool"
OPTION_TEXT = "text"
OPTION_CHOICE = "choice"

OPTION_KINDS = (OPTION_BOOL, OPTION_TEXT, OPTION_CHOICE)

OPTION_KIND_LABELS = {
    OPTION_BOOL: "开关",
    OPTION_TEXT: "文本",
    OPTION_CHOICE: "单选",
}

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")

_TRUE_TEXTS = ("1", "true", "yes", "on", "是", "开")
_FALSE_TEXTS = ("0", "false", "no", "off", "否", "关")


class PluginOptionError(Exception):
    """插件选项声明不合法。"""


@dataclass(frozen=True)
class PluginOptionSpec:
    """一个插件选项的声明。"""

    key: str
    label: str = ""
    kind: str = OPTION_TEXT
    default: object = ""
    choices: tuple[tuple[str, str], ...] = ()
    description: str = ""

    @property
    def name(self) -> str:
        return self.label or self.key

    @property
    def kind_label(self) -> str:
        return OPTION_KIND_LABELS.get(self.kind, self.kind)

    @property
    def text(self) -> str:
        return f"{self.name}（{self.key}）"

    def label_of(self, value: object) -> str:
        """把选项值翻译成显示名（choice 用声明的显示名，bool 用「开 / 关」）。"""
        text = str(value)
        for item_value, item_label in self.choices:
            if item_value == text:
                return item_label
        if self.kind == OPTION_BOOL:
            return "开" if coerce_option(self, value) else "关"
        return text


def valid_option_key(text: object) -> bool:
    return bool(_KEY_PATTERN.match(str(text or "")))


def _choice_pairs(value: object, key: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        items: list = [[str(item_key), str(item_value)] for item_key, item_value in value.items()]
    elif isinstance(value, str):
        items = [[item, item] for item in value.replace(",", " ").replace(";", " ").split()]
    else:
        items = list(value)  # type: ignore[arg-type]
    pairs: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, (list, tuple)):
            raw = list(item)
            item_value = str(raw[0]).strip() if raw else ""
            item_label = str(raw[1]).strip() if len(raw) > 1 else ""
        else:
            item_value = item_label = str(item).strip()
        if not item_value:
            continue
        if any(existing == item_value for existing, _ in pairs):
            continue
        pairs.append((item_value, item_label or item_value))
    if not pairs:
        raise PluginOptionError(f"插件选项 {key} 是 choice 类型，至少要声明一个选项值：choices")
    return tuple(pairs)


def _spec_from(key: str, data: object) -> PluginOptionSpec:
    if not valid_option_key(key):
        raise PluginOptionError(f"插件选项 key 不合法：{key}（小写字母开头，可含数字、下划线、点和连字符）")
    if isinstance(data, dict):
        label = str(data.get("label") or "").strip()
        kind = str(data.get("kind") or OPTION_TEXT).strip().lower() or OPTION_TEXT
        description = str(data.get("description") or "").strip()
        raw_choices = data.get("choices")
        has_default = "default" in data
        raw_default = data.get("default")
    else:
        label = str(data or "").strip()
        kind = OPTION_TEXT
        description = ""
        raw_choices = None
        has_default = False
        raw_default = None
    if kind not in OPTION_KINDS:
        raise PluginOptionError(f"插件选项 kind 不合法：{kind}（可用：bool、text、choice）")
    choices = _choice_pairs(raw_choices, key) if kind == OPTION_CHOICE else ()
    if kind == OPTION_CHOICE and not choices:
        raise PluginOptionError(f"插件选项 {key} 是 choice 类型，至少要声明一个选项值：choices")
    if kind == OPTION_BOOL:
        default: object = bool(raw_default) if has_default else False
    else:
        default = str(raw_default).strip() if has_default else (choices[0][0] if choices else "")
    spec = PluginOptionSpec(
        key=key,
        label=label,
        kind=kind,
        default=default,
        choices=choices,
        description=description,
    )
    if spec.kind == OPTION_CHOICE and str(spec.default) not in {item_value for item_value, _ in spec.choices}:
        raise PluginOptionError(f"插件选项 {key} 的默认值不在选项里：{spec.default}")
    return spec


def parse_options(value: object) -> tuple[PluginOptionSpec, ...]:
    """解析清单里的 `options` 字段（列表或字典），返回选项声明。"""
    if value is None or value == "":
        return ()
    specs: list[PluginOptionSpec] = []
    if isinstance(value, dict):
        for key, data in value.items():
            specs.append(_spec_from(str(key).strip(), data))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, dict):
                raise PluginOptionError(f"插件选项条目必须是一个对象：{item}")
            key = str(item.get("key") or "").strip()
            if not key:
                raise PluginOptionError("插件选项缺少 key")
            payload = {name: item[name] for name in item if name != "key"}
            specs.append(_spec_from(key, payload))
    else:
        raise PluginOptionError("插件选项必须是一个列表或对象")
    seen: list[str] = []
    for spec in specs:
        if spec.key in seen:
            raise PluginOptionError(f"插件选项 key 重复：{spec.key}")
        seen.append(spec.key)
    return tuple(specs)


def coerce_option(spec: PluginOptionSpec, value: object) -> object:
    """把任意来源的值规范成该选项声明的类型；无法识别时回退默认值。"""
    if spec.kind == OPTION_BOOL:
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE_TEXTS:
                return True
            if text in _FALSE_TEXTS:
                return False
            return bool(spec.default)
        return bool(value)
    if spec.kind == OPTION_CHOICE:
        text = str(value).strip()
        for item_value, _ in spec.choices:
            if item_value == text:
                return item_value
        return str(spec.default)
    if value is None:
        return ""
    return str(value)


def defaults(specs: tuple[PluginOptionSpec, ...]) -> dict[str, object]:
    """选项的默认值字典。"""
    return {spec.key: spec.default for spec in specs}


def settings_text(specs: tuple[PluginOptionSpec, ...], values: dict) -> str:
    """把已经设置过的选项拼成一行摘要（未设置的选项不出现）。"""
    parts: list[str] = []
    for spec in specs:
        if spec.key in values:
            parts.append(f"{spec.name}={spec.label_of(values[spec.key])}")
    return "、".join(parts)


__all__ = [
    "OPTION_BOOL",
    "OPTION_CHOICE",
    "OPTION_KIND_LABELS",
    "OPTION_KINDS",
    "OPTION_TEXT",
    "PluginOptionError",
    "PluginOptionSpec",
    "coerce_option",
    "defaults",
    "parse_options",
    "settings_text",
    "valid_option_key",
]
