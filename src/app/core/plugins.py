"""插件协议：清单字段、校验与依赖排序。

统一协议字段（plugin.json）：

    id              插件唯一标识（字母数字开头，可含 . _ -，长度 2-64）
    name            显示名
    version         插件版本
    kind            插件类型 id（必须已被某个类型插件声明，如内置的 viewer = 打开方式、
                    page = 弹窗页面，或 community 类型插件声明出来的自定义类型）
    kinds           类型插件声明的插件类型列表，每项是
                    {id, label?, description?, requires_extensions?, contributor?, order?}；
                    只有类型插件才写这个字段，载入时通过 plugin.kind 扩展接口登记全局类型表
    description     插件介绍
    author          创建者
    manager_version 适用的管理器版本（不得高于当前管理器版本）
    entry           入口文件（Python 模块，需定义 register(api)）
    depends         依赖的插件 id 列表
    provides        对外提供的扩展接口名列表
    capabilities    功能说明列表
    extensions      涉及的扩展名（查看器插件用）
    options_title   插件选项页的标题（可选，默认「<插件名> 选项」）
    options         插件选项声明：界面据此自动生成「插件选项」页，
                    插件用 `api.option("键")` 读回用户设置（见 core.plugin_options）
    builtin         是否随程序分发（内置插件为 true）

`parse_manifest()` 校验协议，`sort_by_dependency()` 做依赖排序并挑出缺依赖 / 循环依赖的插件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .extensions import valid_extension_name
from .plugin_kinds import KIND_KIND, KIND_PAGE, KIND_VIEWER, PluginKindSpec, plugin_kinds, valid_kind_name
from .plugin_options import PluginOptionError, PluginOptionSpec, defaults, parse_options, settings_text
from .version import MANAGER_VERSION, is_compatible

MANIFEST_NAME = "plugin.json"

SOURCE_BUILTIN = "builtin"
SOURCE_EXTERNAL = "external"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$")


class PluginError(Exception):
    """插件清单、导入或载入过程中可以预期的错误。"""


def _as_list(value: object) -> tuple[str, ...]:
    """把清单里的字符串或字符串列表规范成去重元组。"""
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        raw: list = value.replace(",", " ").replace(";", " ").split()
    else:
        raw = list(value)  # type: ignore[arg-type]
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _normalize_extensions(value: object) -> tuple[str, ...]:
    """扩展名统一去点、小写，便于注册表做后缀比较。"""
    result: list[str] = []
    for item in _as_list(value):
        text = item.lstrip(".").lower()
        if text and text not in result:
            result.append(text)
    return tuple(result)


@dataclass
class PluginInfo:
    """一个插件的协议信息。"""

    id: str
    name: str
    version: str = ""
    kind: str = KIND_VIEWER
    kinds: tuple[PluginKindSpec, ...] = ()
    description: str = ""
    author: str = ""
    entry: str = ""
    path: Path | None = None
    extensions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    depends: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    manager_version: str = ""
    builtin: bool = False
    enabled: bool = True
    error: str = ""
    note: str = ""
    options_title: str = ""
    options: tuple[PluginOptionSpec, ...] = ()
    settings: dict[str, object] = field(default_factory=dict)
    viewers: tuple[str, ...] = field(default=(), repr=False)

    @property
    def source(self) -> str:
        return SOURCE_BUILTIN if self.builtin else SOURCE_EXTERNAL

    @property
    def kind_label(self) -> str:
        return plugin_kinds.label(self.kind)

    @property
    def kinds_text(self) -> str:
        return "、".join(spec.text for spec in self.kinds) if self.kinds else "—"

    @property
    def source_label(self) -> str:
        return "内置" if self.builtin else "外部"

    @property
    def state_label(self) -> str:
        if self.error:
            return "异常"
        return "已启用" if self.enabled else "已禁用"

    @property
    def extensions_text(self) -> str:
        return "、".join(self.extensions) if self.extensions else "—"

    @property
    def capabilities_text(self) -> str:
        return "、".join(self.capabilities) if self.capabilities else "—"

    @property
    def depends_text(self) -> str:
        return "、".join(self.depends) if self.depends else "无"

    @property
    def provides_text(self) -> str:
        return "、".join(self.provides) if self.provides else "—"

    @property
    def manager_text(self) -> str:
        return self.manager_version or MANAGER_VERSION

    @property
    def author_text(self) -> str:
        return self.author or "未填写"

    @property
    def version_text(self) -> str:
        return self.version or "未填写"

    @property
    def options_label(self) -> str:
        return self.options_title or f"{self.name} 选项"

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    @property
    def settings_text(self) -> str:
        return settings_text(self.options, self.settings) or "默认"

    def option_spec(self, key: str) -> PluginOptionSpec | None:
        for spec in self.options:
            if spec.key == key:
                return spec
        return None

    def clone(self, **fields: object) -> "PluginInfo":
        return replace(self, **fields)  # type: ignore[arg-type]


def _require_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PluginError(f"插件清单缺少 {field_name}")
    if not _ID_PATTERN.match(text):
        raise PluginError(f"插件 id 不合法：{text}")
    return text


_LEGACY_KIND_FIELDS = ("kind_label", "kind_description", "kind_requires_extensions")


def parse_kinds(value: object, plugin_id: str) -> tuple[PluginKindSpec, ...]:
    """解析清单里的 `kinds`：类型插件用它声明自己创造的插件类型。"""
    if value is None or value == "":
        return ()
    if not isinstance(value, list):
        raise PluginError("插件清单的 kinds 必须是一个列表")
    result: list[PluginKindSpec] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise PluginError("插件清单的 kinds 每一项都必须是一个对象")
        kind_id = str(item.get("id") or "").strip()
        if not kind_id:
            raise PluginError("插件类型缺少 id")
        if not valid_kind_name(kind_id):
            raise PluginError(
                f"插件类型不合法：{kind_id}（小写字母开头，可含数字、下划线、点和连字符）"
            )
        if kind_id in seen:
            raise PluginError(f"插件类型重复声明：{kind_id}")
        seen.add(kind_id)
        contributor = str(item.get("contributor") or "").strip()
        if contributor and not contributor.isidentifier():
            raise PluginError(f"插件类型 {kind_id} 的登记方法不合法：{contributor}")
        order = item.get("order", 100)
        if isinstance(order, bool) or not isinstance(order, int):
            raise PluginError(f"插件类型 {kind_id} 的排序值不是整数：{order}")
        result.append(
            PluginKindSpec(
                kind_id,
                label=str(item.get("label") or "").strip(),
                description=str(item.get("description") or "").strip(),
                requires_extensions=bool(item.get("requires_extensions", False)),
                contributor=contributor,
                order=order,
            ).declared_by(plugin_id)
        )
    return tuple(result)


def validate_kind(info: PluginInfo) -> PluginKindSpec:
    """载入插件前校验它的类型：类型必须已被类型插件声明，且满足扩展名要求。"""
    spec = plugin_kinds.get(info.kind)
    if spec is None:
        raise PluginError(
            f"插件类型未注册：{info.kind}（请依赖声明该类型的类型插件：{plugin_kinds.ids()}）"
        )
    if spec.requires_extensions and not info.extensions:
        raise PluginError(f"{spec.name}插件至少要声明一个扩展名")
    return spec


def parse_manifest(data: dict, path: Path | None = None, builtin: bool = False) -> PluginInfo:
    """按插件协议校验清单字典，返回 PluginInfo。"""
    if not isinstance(data, dict):
        raise PluginError("插件清单必须是一个 JSON 对象")
    plugin_id = _require_id(data.get("id"), "id")
    name = str(data.get("name") or "").strip()
    if not name:
        raise PluginError("插件清单缺少 name")
    for legacy in _LEGACY_KIND_FIELDS:
        if data.get(legacy) not in (None, ""):
            raise PluginError(
                f"插件清单不再用 {legacy} 声明类型信息：请改由类型插件的 kinds 声明"
            )
    kind = str(data.get("kind") or KIND_VIEWER).strip() or KIND_VIEWER
    if not valid_kind_name(kind):
        raise PluginError(f"插件类型不合法：{kind}（小写字母开头，可含数字、下划线、点和连字符）")
    kinds = parse_kinds(data.get("kinds"), plugin_id)
    manager_version = str(data.get("manager_version") or "").strip()
    if manager_version and not is_compatible(manager_version):
        raise PluginError(f"需要管理器版本 {manager_version}，当前 {MANAGER_VERSION}")
    depends = _as_list(data.get("depends"))
    for dep in depends:
        if not _ID_PATTERN.match(dep):
            raise PluginError(f"依赖插件 id 不合法：{dep}")
        if dep == plugin_id:
            raise PluginError("插件不能依赖自己")
    provides = _as_list(data.get("provides"))
    for name_ in provides:
        if not valid_extension_name(name_):
            raise PluginError(f"扩展接口名不合法：{name_}")
    entry = str(data.get("entry") or "").strip()
    is_builtin = builtin or bool(data.get("builtin", False))
    if not entry:
        if not is_builtin:
            raise PluginError("插件清单缺少 entry（入口文件）")
    elif path is not None and not (Path(path) / entry).exists():
        raise PluginError(f"入口文件不存在：{entry}")
    extensions = _normalize_extensions(data.get("extensions"))
    try:
        options = parse_options(data.get("options"))
    except PluginOptionError as exc:
        raise PluginError(str(exc)) from exc
    return PluginInfo(
        id=plugin_id,
        name=name,
        version=str(data.get("version") or "").strip(),
        kind=kind,
        kinds=kinds,
        description=str(data.get("description") or "").strip(),
        author=str(data.get("author") or "").strip(),
        entry=entry,
        path=Path(path) if path is not None else None,
        extensions=extensions,
        capabilities=_as_list(data.get("capabilities")),
        depends=depends,
        provides=provides,
        manager_version=manager_version,
        builtin=is_builtin,
        options_title=str(data.get("options_title") or "").strip(),
        options=options,
        settings=defaults(options),
    )


def load_manifest(plugin_dir: Path, builtin: bool = False) -> PluginInfo:
    """读取插件目录下的 plugin.json 并校验。"""
    folder = Path(plugin_dir)
    manifest = folder / MANIFEST_NAME
    if not manifest.exists():
        raise PluginError(f"缺少 {MANIFEST_NAME}：{folder.name}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginError(f"插件清单不是合法的 JSON：{exc}") from exc
    return parse_manifest(data, path=folder, builtin=builtin)


def _order_key(info: PluginInfo) -> tuple[int, str]:
    """排序键：内置插件优先，其次按 id 字典序，保证载入顺序稳定。"""
    return (0 if info.builtin else 1, info.id)


def sort_by_dependency(infos: list[PluginInfo]) -> tuple[list[PluginInfo], dict[str, str]]:
    """按依赖顺序排列插件，返回 (顺序列表, 出错的插件 id → 原因)。

    缺依赖、循环依赖的插件不进入顺序列表，原因写进第二个返回值。
    """
    errors: dict[str, str] = {}
    index = {info.id: info for info in infos}
    for info in infos:
        missing = [dep for dep in info.depends if dep not in index]
        if missing:
            errors[info.id] = "缺少依赖插件：" + "、".join(missing)

    def blocked(info: PluginInfo) -> set[str]:
        return {dep for dep in info.depends if dep in index}

    remaining = {info.id: info for info in infos if info.id not in errors}
    done: set[str] = set()
    ordered: list[PluginInfo] = []
    while remaining:
        ready = sorted(
            (info for info in remaining.values() if blocked(info) <= done),
            key=_order_key,
        )
        if not ready:
            break
        for info in ready:
            ordered.append(info)
            done.add(info.id)
            remaining.pop(info.id, None)
    for info in sorted(remaining.values(), key=_order_key):
        errors[info.id] = "插件依赖存在循环：" + " → ".join([*sorted(blocked(info)), info.id])
    return ordered, errors


__all__ = [
    "KIND_KIND",
    "KIND_PAGE",
    "KIND_VIEWER",
    "MANIFEST_NAME",
    "plugin_kinds",
    "SOURCE_BUILTIN",
    "SOURCE_EXTERNAL",
    "PluginError",
    "PluginInfo",
    "load_manifest",
    "parse_kinds",
    "parse_manifest",
    "sort_by_dependency",
    "validate_kind",
    "PluginOptionSpec",
]
