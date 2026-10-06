"""自动标注共享库：规则模型 + 匹配引擎、数据类型 ↔ 模型对齐表、批处理管线与共用控件。

库自身**不注册页面、不写数据库**：它只负责把出厂默认（`.data/rules.json`、`.data/align.json`）
与用户在 `.configs/` 里的改动合并成可用视图，并把「规则怎么匹配」「条目怎么排成批量请求」
「模型输出怎么解析」这些纯逻辑交出去。自动标签（`auto_tag*`）与自动关键词
（`auto_keyword`）两个插件共用这里的实现，规则文件与对齐表也只有一份。

**纯规则路径不依赖模型工具库**：`auto_tag.rule` 只用到规则引擎，所以这里对 `lib.model`
的引用一律走 `_model_api()`（没启用就返回 None），模块导入期不碰它。
"""

from __future__ import annotations

from app.sdk import Plugin, storage

from .align import (
    DATATYPES,
    DATATYPE_LABELS,
    PURPOSES,
    PURPOSE_KEYWORD,
    PURPOSE_LABEL,
    PURPOSE_LABELS,
    AlignBook,
    AlignRow,
    AlignTable,
    dump_tables,
    normalize_datatype,
    parse_table,
    registered_map,
)
from .pipeline import (
    DEFAULT_CAPABILITY,
    DEFAULT_TASK,
    PipelineItem,
    PipelinePlan,
    clamp,
    collect,
    default_payload,
    default_prompt,
    failed,
    merge_names,
    parse_names,
    pending_keys,
    plan as plan_requests,
    run as run_plan,
)
from .rules import (
    FIELD_LABELS,
    FIELDS,
    KIND_LABELS,
    KINDS,
    KIND_MATCH,
    KIND_PROMPT,
    OP_LABELS,
    OPS,
    OP_IN,
    Rule,
    RuleSet,
    SOURCE_FACTORY,
    SOURCE_USER,
    dump_rules,
    parse_rules,
    split_pattern,
)

PLUGIN_ID = "lib.autolabel"
#: 其它插件可以 `ctx.require("autolabel.open")` 拿到同一个实例。
AUTOLABEL_EXTENSION = "autolabel.open"

RULES_FILE = "autolabel.rules.json"
ALIGN_FILE = "autolabel.align.json"


# --------------------------------------------------------------- 文件位置
def rules_file():
    """用户规则文件（`.configs/autolabel.rules.json`）。"""
    return storage.config_file(RULES_FILE)


def align_file():
    """用户对齐表文件（`.configs/autolabel.align.json`）。"""
    return storage.config_file(ALIGN_FILE)


# --------------------------------------------------------------- 出厂默认
def _items(payload: object) -> list:
    """出厂文件是统一清单格式，取它的 `items`（顺手容忍旧的纯数组写法，方便手改时试错）。"""
    if isinstance(payload, dict):
        raw = payload.get("items")
        return list(raw) if isinstance(raw, (list, tuple)) else []
    return list(payload) if isinstance(payload, (list, tuple)) else []


def factory_rules(ctx) -> tuple[tuple[Rule, ...], tuple[str, ...]]:
    """出厂规则（`.data/rules.json`，统一清单格式）；返回 (规则, 读取出错说明)。"""
    try:
        payload = ctx.data("rules", {})
    except Exception as exc:  # 出厂文件坏了也不该让插件起不来
        return (), (f"出厂规则读取失败：{exc}",)
    return parse_rules(_items(payload), source=SOURCE_FACTORY)


def factory_align(ctx) -> tuple[tuple[AlignTable, ...], tuple[str, ...]]:
    """出厂对齐表（`.data/align.json`，按 `purpose` 分 `label` / `keyword` 两张）。"""
    try:
        payload = ctx.data("align", {})
    except Exception as exc:
        return (), (f"出厂对齐表读取失败：{exc}",)
    grouped: dict[str, list[dict]] = {}
    for item in _items(payload):
        if not isinstance(item, dict):
            continue
        purpose = str(item.get("purpose") or "")
        grouped.setdefault(purpose, []).append(
            {key: value for key, value in item.items() if key not in ("key", "purpose")}
        )
    tables: list[AlignTable] = []
    errors: list[str] = []
    for purpose in PURPOSES:
        rows, issues = parse_table(grouped.get(purpose, []), purpose=purpose)
        tables.append(AlignTable(purpose=purpose, rows=rows, errors=tuple(issues)))
        errors.extend(f"{PURPOSE_LABELS.get(purpose, purpose)}：{issue}" for issue in issues)
    return tuple(tables), tuple(errors)


# --------------------------------------------------------------- 读 / 写
def load_rules(ctx) -> RuleSet:
    """出厂规则 + 用户改动。用户文件坏了只记错，不影响出厂规则可用。"""
    factory, errors = factory_rules(ctx)
    payload = storage.read_json(rules_file(), {})
    rule_set = RuleSet.from_payload(payload, factory)
    return RuleSet(
        factory=rule_set.factory,
        user=rule_set.user,
        hidden=rule_set.hidden,
        errors=tuple(errors) + tuple(rule_set.errors),
    )


def save_rules(ctx, rule_set: RuleSet) -> bool:
    """只写「与出厂不同」的规则 + 隐藏列表。"""
    return bool(storage.write_json(rules_file(), rule_set.user_payload))


def load_align(ctx) -> AlignBook:
    factory, errors = factory_align(ctx)
    payload = storage.read_json(align_file(), {})
    book = AlignBook.from_payload(payload, factory)
    return AlignBook(factory=book.factory, user=book.user, errors=tuple(errors) + tuple(book.errors))


def save_align(ctx, book: AlignBook) -> bool:
    return bool(storage.write_json(align_file(), book.payload))


# --------------------------------------------------------------- 模型清单

def _model_api():
    """模型工具库的门面模块；没启用（或没装）时返回 None。"""
    try:
        from dm_plugin.lib.model import api
    except ImportError:
        return None
    return api


def registered_models() -> dict[str, str]:
    """`{预定义方案 key: 已登记 model_id}`；没启用模型插件时为空。"""
    model_api = _model_api()
    if model_api is None:
        return {}
    try:
        return registered_map(model_api.templates())
    except Exception:
        return {}


def known_model_ids() -> set[str] | None:
    """所有已登记模型的 id；模型插件不可用时返回 `None`（调用方据此跳过清理）。

    对齐表里存的是 `model_id`：模型页把模型删掉之后那份 id 就成了死引用，状态列会一直
    显示未就绪、补全逻辑也以为「已经登记过」。有这份 id 集合就能识别并清掉它们。
    """
    model_api = _model_api()
    if model_api is None:
        return None
    try:
        records = model_api.list_models()
    except Exception:
        return None
    return {str(getattr(record, "id", "") or "") for record in records} - {""}


def model_choices(*, capability: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """已登记模型的下拉选项：返回 `(显示文字, 传给 combo_box data 的值)`。"""
    labels: list[str] = []
    values: list[str] = []
    model_api = _model_api()
    try:
        records = model_api.list_models(capability=capability) if model_api is not None else ()
    except Exception:
        records = ()
    for record in records:
        model_id = str(getattr(record, "id", "") or "")
        if not model_id:
            continue
        labels.append(str(getattr(record, "name", "") or model_id))
        values.append(model_id)
    return tuple(labels), tuple(values)


def template_choices(*, capability: str = "", lightweight_only: bool = False):
    """预定义方案的下拉选项：`(显示文字, 方案 key)`；轻量档排前面。"""
    labels: list[str] = []
    values: list[str] = []
    model_api = _model_api()
    try:
        rows = (
            model_api.templates(capability=capability, lightweight_only=lightweight_only)
            if model_api is not None
            else ()
        )
    except Exception:
        rows = ()
    for row in rows:
        key = str(row.get("id") or "")
        if not key:
            continue
        name = str(row.get("name") or key)
        size = row.get("size_bytes")
        hint = f"{name}（{round(int(size) / 1e9, 1)} GB）" if size else name
        if row.get("lightweight"):
            hint += "·轻量"
        labels.append(hint)
        values.append(key)
    return tuple(labels), tuple(values)


# --------------------------------------------------------------- 扩展接口
class AutoLabelApi:
    """共享实例：其它插件通过 `ctx.require("autolabel.open")` 拿到它。

    规则与对齐表都是**惰性读一次、写时回写**；`reload()` 用于用户手改了文件以后刷新。
    """

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._rules: RuleSet | None = None
        self._align: AlignBook | None = None

    # ---------------------------------------------------------- 规则
    def rules(self, *, reload: bool = False) -> RuleSet:
        if self._rules is None or reload:
            self._rules = load_rules(self._ctx)
        return self._rules

    def save_rules(self, rule_set: RuleSet) -> bool:
        ok = save_rules(self._ctx, rule_set)
        if ok:
            self._rules = rule_set
        return ok

    # ---------------------------------------------------------- 对齐表
    def align(self, *, reload: bool = False) -> AlignBook:
        if self._align is None or reload:
            self._align = load_align(self._ctx)
        return self._align

    def save_align(self, book: AlignBook) -> bool:
        ok = save_align(self._ctx, book)
        if ok:
            self._align = book
        return ok

    # ---------------------------------------------------------- 其它
    def registered(self) -> dict[str, str]:
        return registered_models()

    def reload(self) -> None:
        self.rules(reload=True)
        self.align(reload=True)

    @property
    def path_text(self) -> str:
        return f"规则：{rules_file().name}　对齐表：{align_file().name}"


class AutoLabelLibraryPlugin(Plugin):
    """共享库入口：把 `autolabel.open` 交给其它插件，出厂文件由清单的 `data` 声明。"""

    def setup(self, ctx) -> None:
        ctx.provide(AUTOLABEL_EXTENSION, AutoLabelApi(ctx))


__all__ = [
    "AutoLabelApi",
    "AutoLabelLibraryPlugin",
    "AUTOLABEL_EXTENSION",
    "ALIGN_FILE",
    "AlignBook",
    "AlignRow",
    "AlignTable",
    "DATATYPES",
    "DATATYPE_LABELS",
    "DEFAULT_CAPABILITY",
    "DEFAULT_TASK",
    "FIELD_LABELS",
    "FIELDS",
    "KIND_LABELS",
    "KIND_MATCH",
    "KIND_PROMPT",
    "KINDS",
    "OP_LABELS",
    "OPS",
    "PLUGIN_ID",
    "PURPOSES",
    "PURPOSE_KEYWORD",
    "PURPOSE_LABEL",
    "PURPOSE_LABELS",
    "PipelineItem",
    "PipelinePlan",
    "RULES_FILE",
    "Rule",
    "RuleSet",
    "SOURCE_FACTORY",
    "SOURCE_USER",
    "align_file",
    "clamp",
    "collect",
    "default_payload",
    "default_prompt",
    "dump_rules",
    "dump_tables",
    "factory_align",
    "factory_rules",
    "failed",
    "load_align",
    "load_rules",
    "merge_names",
    "model_choices",
    "normalize_datatype",
    "parse_names",
    "parse_rules",
    "pending_keys",
    "plan_requests",
    "registered_map",
    "registered_models",
    "rules_file",
    "run_plan",
    "save_align",
    "save_rules",
    "split_pattern",
    "template_choices",
]
