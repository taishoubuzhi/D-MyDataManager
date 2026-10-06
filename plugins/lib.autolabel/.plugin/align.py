"""数据类型 ↔ 模型对齐表：哪种数据类型归哪个模型管，外加「先对齐再判断」的辅助模型。

`data/align.json` 是出厂表，用户改动写在自己的文件里（默认 `.configs/autolabel.align.json`），
只在载荷里存**与出厂不同的行**，出厂表以后加新数据类型 / 换模型仍然生效。

两列模型的含义：

* `model_id` / `template`：真正干活的模型（`model_id` 是登记表里的 id，优先；
  `template` 是预定义方案 key，用户还没登记时用它 —— 登记过就等价于 `registered_id`）；
* `align_model` / `align_template`：可选的对齐模型，先把图片描述出来、把音频转成文字，
  再交给干活的模型（图片默认 clip、音频默认 whisper）。

本模块同样不碰界面、不碰数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

PURPOSE_LABEL = "label"
PURPOSE_KEYWORD = "keyword"
PURPOSES: tuple[str, ...] = (PURPOSE_LABEL, PURPOSE_KEYWORD)
PURPOSE_LABELS: dict[str, str] = {
    PURPOSE_LABEL: "自动标签",
    PURPOSE_KEYWORD: "自动关键词",
}

#: 与 `app.db.models.DataType` 同名（插件不 import 宿主模型层，自己列一份）。
DATATYPES: tuple[str, ...] = (
    "IMAGE",
    "VIDEO",
    "AUDIO",
    "DOCUMENT",
    "SPREADSHEET",
    "PRESENTATION",
    "ARCHIVE",
    "CODE",
    "TEXT",
    "OTHER",
)
DATATYPE_LABELS: dict[str, str] = {
    "IMAGE": "图片",
    "VIDEO": "视频",
    "AUDIO": "音频",
    "DOCUMENT": "文档",
    "SPREADSHEET": "表格",
    "PRESENTATION": "演示文稿",
    "ARCHIVE": "压缩包",
    "CODE": "代码",
    "TEXT": "纯文本",
    "OTHER": "其它",
}


def normalize_datatype(value: object) -> str:
    """条目的 `type` → `DataType` 名；认不出来的一律算 OTHER。"""
    text = str(value or "").strip().upper()
    return text if text in DATATYPES else "OTHER"


@dataclass(frozen=True)
class AlignRow:
    """一个数据类型对应的模型选择。"""

    datatype: str
    model_id: str = ""
    template: str = ""
    align_model: str = ""
    align_template: str = ""
    enabled: bool = True
    #: 这一行是否跟随出厂「系统方案」：跟随则模型与对齐模型都用 `data/align.json` 里配好的，
    #: 用户不能单独改（要先在「设置对齐」编辑框里取消「启用系统方案」）。
    use_preset: bool = False
    note: str = ""

    @property
    def datatype_label(self) -> str:
        return DATATYPE_LABELS.get(self.datatype, self.datatype)

    @property
    def target_text(self) -> str:
        return self.model_id or self.template or "未设置"

    @property
    def align_text(self) -> str:
        return self.align_model or self.align_template or "不用"

    @property
    def problems(self) -> tuple[str, ...]:
        if self.datatype not in DATATYPES:
            return (f"未知的数据类型：{self.datatype}",)
        if self.enabled and not (self.model_id or self.template):
            return ("没有选模型",)
        return ()

    def resolved(self, *, registered: Mapping[str, str] | None = None) -> str:
        """真正要调用的 model_id：显式登记 > 预定义方案已登记 > 空串。"""
        if self.model_id:
            return self.model_id
        if self.template:
            return str((registered or {}).get(self.template, ""))
        return ""

    def resolved_align(self, *, registered: Mapping[str, str] | None = None) -> str:
        if self.align_model:
            return self.align_model
        if self.align_template:
            return str((registered or {}).get(self.align_template, ""))
        return ""

    def missing_parts(self, *, registered: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """还没着落的模型：先列对齐模型，再列干活的模型（都缺就都列）。

        对齐模型（clip / whisper 这种）缺了照样跑不出结果，所以它和干活模型一样算
        「没就绪」——只判 `resolved()` 会让图片在缺 clip 时显示「就绪」。
        """
        if not self.enabled:
            return ()
        parts: list[str] = []
        if (self.align_model or self.align_template) and not self.resolved_align(registered=registered):
            parts.append("对齐模型")
        if not self.resolved(registered=registered):
            parts.append("模型")
        return tuple(parts)

    def status_text(self, *, registered: Mapping[str, str] | None = None) -> str:
        """界面上的状态列：按这一行自己的主模型与对齐模型有没有登记来说清楚缺什么。"""
        if not self.enabled:
            return "已关闭"
        problems: list[str] = []
        if (self.align_model or self.align_template) and not self.resolved_align(registered=registered):
            problems.append(f"对齐模型未登记：{self.align_model or self.align_template}")
        if not self.resolved(registered=registered):
            problems.append(f"主模型未登记：{self.model_id or self.template or '未设置'}")
        return "；".join(problems) if problems else "就绪"

    def to_dict(self) -> dict:
        return {
            "datatype": self.datatype,
            "model_id": self.model_id,
            "template": self.template,
            "align_model": self.align_model,
            "align_template": self.align_template,
            "enabled": bool(self.enabled),
            "use_preset": bool(self.use_preset),
            "note": self.note,
        }

    def with_datatype(self, datatype: str) -> "AlignRow":
        return replace(self, datatype=datatype)

    def same_as(self, other: "AlignRow") -> bool:
        return self.to_dict() == other.to_dict()

    @classmethod
    def from_dict(cls, payload: object) -> "AlignRow | None":
        if not isinstance(payload, dict):
            return None
        datatype = str(payload.get("datatype") or "").strip().upper()
        if not datatype:
            return None
        return cls(
            datatype=datatype,
            model_id=str(payload.get("model_id") or "").strip(),
            template=str(payload.get("template") or "").strip(),
            align_model=str(payload.get("align_model") or "").strip(),
            align_template=str(payload.get("align_template") or "").strip(),
            enabled=bool(payload.get("enabled", True)),
            use_preset=bool(payload.get("use_preset", False)),
            note=str(payload.get("note") or "").strip(),
        )


def registered_map(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """`models.templates()` 的行 → `{方案 key: 已登记 model_id}`（没登记的不要）。"""
    found: dict[str, str] = {}
    for row in rows or ():
        key = str(row.get("id") or "").strip()
        model_id = str(row.get("registered_id") or "").strip()
        if key and model_id:
            found[key] = model_id
    return found


@dataclass(frozen=True)
class AlignTable:
    """一个用途（标签 / 关键词）的对齐表。"""

    purpose: str = PURPOSE_LABEL
    rows: tuple[AlignRow, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def purpose_label(self) -> str:
        return PURPOSE_LABELS.get(self.purpose, self.purpose)

    def row(self, datatype: str) -> AlignRow | None:
        wanted = normalize_datatype(datatype)
        for row in self.rows:
            if row.datatype == wanted:
                return row
        return None

    def set_row(self, row: AlignRow) -> "AlignTable":
        rows = [item for item in self.rows if item.datatype != row.datatype]
        rows.append(row)
        rows.sort(key=lambda item: DATATYPES.index(item.datatype) if item.datatype in DATATYPES else len(DATATYPES))
        return replace(self, rows=tuple(rows))

    def resolved(self, datatype: str, *, registered: Mapping[str, str] | None = None) -> str:
        row = self.row(datatype)
        return row.resolved(registered=registered) if row is not None and row.enabled else ""

    def alignment(self, datatype: str, *, registered: Mapping[str, str] | None = None) -> str:
        row = self.row(datatype)
        return row.resolved_align(registered=registered) if row is not None and row.enabled else ""

    def ready_rows(self, *, registered: Mapping[str, str] | None = None) -> tuple[AlignRow, ...]:
        """就绪行：启用、且干活的模型与对齐模型（若有）都着落了。"""
        return tuple(row for row in self.rows if row.enabled and not row.missing_parts(registered=registered))

    def missing(self, *, registered: Mapping[str, str] | None = None) -> tuple[AlignRow, ...]:
        """启用、但还有模型没着落的数据类型（界面上要提示「一键使用 / 去登记」）。"""
        return tuple(
            row
            for row in self.rows
            if row.enabled and row.missing_parts(registered=registered)
        )

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "rows": [row.to_dict() for row in self.rows],
        }


def parse_table(data: object, *, purpose: str) -> tuple[tuple[AlignRow, ...], list[str]]:
    if isinstance(data, dict):
        raw = data.get("rows")
        if raw is None:
            # 也接受 `{"IMAGE": {...}}` 这种按数据类型直排的写法。
            raw = [
                dict(value, datatype=key) if isinstance(value, dict) else {"datatype": key}
                for key, value in data.items()
                if key != "purpose"
            ]
    else:
        raw = data
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        return (), ["对齐表必须是一个数组"]
    rows: list[AlignRow] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        row = AlignRow.from_dict(entry)
        if row is None:
            errors.append(f"第 {index} 行缺少 datatype，已跳过")
            continue
        if row.datatype in seen:
            errors.append(f"数据类型重复：{row.datatype}（只保留第一条）")
            continue
        seen.add(row.datatype)
        rows.append(row)
    return tuple(rows), errors


def dump_tables(tables: Sequence[AlignTable]) -> dict:
    payload: dict = {"version": 1}
    for table in tables:
        payload[table.purpose] = [row.to_dict() for row in table.rows]
    return payload


@dataclass(frozen=True)
class AlignBook:
    """出厂对齐表 + 用户改动的合并视图（与 `RuleSet` 同一套思路）。"""

    factory: tuple[AlignTable, ...] = ()
    user: Mapping[str, tuple[AlignRow, ...]] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def purposes(self) -> tuple[str, ...]:
        return tuple(table.purpose for table in self.factory) or PURPOSES

    def factory_table(self, purpose: str) -> AlignTable:
        for table in self.factory:
            if table.purpose == purpose:
                return table
        return AlignTable(purpose=purpose)

    def table(self, purpose: str = PURPOSE_LABEL) -> AlignTable:
        """出厂表按行覆盖上用户改动（用户新增的数据类型自动排到对应位置）。

        勾了「跟随系统方案」（`use_preset`）的行直接用出厂方案里的 `template` /
        `align_template`，并忽略用户自己填的 `model_id` / `align_model`——出厂方案以后
        换模型，这些行跟着换。

        清单里**没有**写到的数据类型说明用户没单独设过它，按「跟随系统方案」算
        （`use_preset=True`），表格的方案列才会显示「启用」，而不是把出厂默认误报成「自定义」。
        """
        base = self.factory_table(purpose)
        presets = {row.datatype: row for row in base.rows}
        covered = {row.datatype for row in self.user.get(purpose, ())}
        for row in self.user.get(purpose, ()):
            if row.use_preset:
                preset = presets.get(row.datatype)
                if preset is not None:
                    row = replace(
                        row,
                        model_id="",
                        template=preset.template,
                        align_model="",
                        align_template=preset.align_template,
                    )
            base = base.set_row(row)
        rows = tuple(
            row if row.datatype in covered else replace(row, use_preset=True)
            for row in base.rows
        )
        return AlignTable(purpose=purpose, rows=rows, errors=base.errors)

    def set_row(self, purpose: str, row: AlignRow) -> "AlignBook":
        base = self.table(purpose)
        merged = base.set_row(row)
        user = dict(self.user)
        user[purpose] = merged.rows
        return replace(self, user=user)

    def reset_row(self, purpose: str, datatype: str) -> "AlignBook":
        user = dict(self.user)
        rows = tuple(row for row in user.get(purpose, ()) if row.datatype != normalize_datatype(datatype))
        if rows:
            user[purpose] = rows
        else:
            user.pop(purpose, None)
        return replace(self, user=user)

    def clear_user(self) -> "AlignBook":
        return replace(self, user={})

    @property
    def payload(self) -> dict:
        """整份清单：每个用途下把所有数据类型**当前生效的设置**都写进去。

        以前只写「和出厂不一样」的行，于是用户把某一项改回与出厂一致（比如取消「启用系统方案」
        但选的还是系统方案里的同一个模型）时，那一行会被当成「没有改动」丢掉，刷新读回的是
        出厂行，表格里显示的方案列就与刚做的设置对不上。现在清单就是「表格看到的东西」本身：
        每次保存写全，刷新直接铺到表里。
        """
        payload: dict = {"version": 1}
        for purpose in self.purposes:
            payload[purpose] = [row.to_dict() for row in self.table(purpose).rows]
        return payload

    @classmethod
    def from_payload(
        cls,
        payload: object,
        factory: Sequence[AlignTable] = (),
    ) -> "AlignBook":
        factory = tuple(factory)
        if not isinstance(payload, dict):
            return cls(factory=factory)
        user: dict[str, tuple[AlignRow, ...]] = {}
        errors: list[str] = []
        for purpose in PURPOSES:
            rows, issues = parse_table(payload.get(purpose), purpose=purpose)
            errors.extend(f"{PURPOSE_LABELS.get(purpose, purpose)}：{issue}" for issue in issues)
            if rows:
                user[purpose] = rows
        return cls(factory=factory, user=user, errors=tuple(errors))
