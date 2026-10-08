"""导出命名模板：把 `{creator}-自定义文本-{time}` 这样的格式渲染成真实文件名。

模板语法（`{}` 里用逗号分隔参数，参数可以留空）：

* `{变量}` —— 取变量的值；
* `{变量,起点}` / `{变量,起点,间隔}` —— 只对**编码式**变量有意义：数字（`number`）、
  字母（`alpha` / `ALPHA`）、罗马字母（`roman` / `ROMAN`）。起点与间隔都可以不写，
  不写就是默认值：`{number}` 等价于 `{number,1,1}`、`{alpha}` 等价于 `{alpha,a,1}`、
  `{roman}` 等价于 `{roman,i,1}`。例：`{number,3,2}` → 3、5、7…；`{alpha,b,2}` → b、d、f…
* 数字编号还可以给第四段当补零宽度：`{number,1,1,3}` → 001、002…
* 时间式变量后面跟一段就是 strftime 格式，想怎么排就怎么排：`{date,%Y%m%d}`、
  `{time,%H%M%S}`、`{datetime,%Y.%m.%d %H%M}`；
* 认不出来的变量**原样保留**在结果里，同时记一条提示（界面据此提醒用户），
  不会因为写错一个变量就让整次导出失败。

这个模块只做字符串：不碰界面、不碰数据库、不碰文件系统，所以可以放心单测。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

__all__ = [
    "DEFAULT_TEMPLATE",
    "RenderContext",
    "RenderedName",
    "TemplateError",
    "Token",
    "VARIABLE_MAP",
    "VARIABLES",
    "Variable",
    "alpha_name",
    "alpha_ordinal",
    "from_roman",
    "ordinal",
    "parse_start",
    "preview",
    "render",
    "safe_filename",
    "to_roman",
    "token_names",
    "unique_name",
    "unknown_names",
]


class TemplateError(ValueError):
    """模板本身或参数没法解释（调用方一般会提示用户改模板）。"""


# ----------------------------------------------------------------- 变量表
@dataclass(frozen=True)
class Variable:
    """一个内置变量：名字、中文名、分组、示例，以及它支持哪些参数。"""

    name: str
    label: str
    group: str
    sample: str
    numbered: bool = False
    timed: bool = False
    description: str = ""


VARIABLES: tuple[Variable, ...] = (
    Variable(
        "number", "数字编号", "编码", "1", numbered=True,
        description="从起点开始按间隔递增的整数：{number,3,2} → 3、5、7…；"
                    "第四段是补零宽度：{number,1,1,3} → 001",
    ),
    Variable(
        "alpha", "小写字母编号", "编码", "a", numbered=True,
        description="小写字母编号：{alpha,b,2} → b、d、f…（a→z 之后是 aa、ab…）",
    ),
    Variable(
        "ALPHA", "大写字母编号", "编码", "A", numbered=True,
        description="大写字母编号：{ALPHA,b,2} → B、D、F…",
    ),
    Variable(
        "roman", "罗马字母编号", "编码", "i", numbered=True,
        description="小写罗马字母编号：{roman,iv,2} → iv、vi、viii…（1 到 3999）",
    ),
    Variable(
        "ROMAN", "大写罗马字母编号", "编码", "I", numbered=True,
        description="大写罗马字母编号：{ROMAN,IV,2} → IV、VI、VIII…",
    ),
    Variable("time", "导出时间", "时间", "20-03-05", timed=True, description="默认 %H-%M-%S，可自定义格式"),
    Variable("date", "导出日期", "时间", "2026-10-08", timed=True, description="默认 %Y-%m-%d，可自定义格式"),
    Variable(
        "datetime", "日期与时间", "时间", "2026-10-08_20-03-05", timed=True,
        description="默认 %Y-%m-%d_%H-%M-%S，可自定义格式",
    ),
    Variable("stamp", "紧凑时间戳", "时间", "20261008-200305", timed=True, description="默认 %Y%m%d-%H%M%S"),
    Variable("year", "年", "时间", "2026", timed=True),
    Variable("month", "月", "时间", "10", timed=True),
    Variable("day", "日", "时间", "08", timed=True),
    Variable("hour", "时", "时间", "20", timed=True),
    Variable("minute", "分", "时间", "03", timed=True),
    Variable("second", "秒", "时间", "05", timed=True),
    Variable("user", "当前用户", "用户", "默认用户", description="现在登录的这个用户"),
    Variable("creator", "创建用户", "用户", "默认用户", description="这次导出内容所属的用户"),
    Variable("category", "分类名", "分类", "影视", description="当前压缩包对应的分类"),
    Variable("top", "顶层分类名", "分类", "影视", description="当前压缩包对应的最顶层分类"),
    Variable("count", "文件数量", "分类", "12", description="当前压缩包里的文件数量"),
    Variable("name", "数据项名", "其他", "示例数据", description="单项导出时的数据项名字"),
)

VARIABLE_MAP: dict[str, Variable] = {item.name: item for item in VARIABLES}

# 顺手认几个别名（英文简写与中文），写在模板里也好记。
ALIASES: dict[str, str] = {
    "num": "number",
    "n": "number",
    "数字": "number",
    "编号": "number",
    "字母": "alpha",
    "大写字母": "ALPHA",
    "罗马": "roman",
    "罗马字母": "roman",
    "大写罗马": "ROMAN",
    "时间": "time",
    "日期": "date",
    "年": "year",
    "月": "month",
    "日": "day",
    "时": "hour",
    "分": "minute",
    "秒": "second",
    "用户": "user",
    "当前用户": "user",
    "创建用户": "creator",
    "分类": "category",
    "顶层分类": "top",
    "数量": "count",
    "文件数量": "count",
    "名称": "name",
    "名字": "name",
}

DEFAULT_TEMPLATE = "导出-{date}-{number}"

_TIME_FORMATS: dict[str, str] = {
    "time": "%H-%M-%S",
    "date": "%Y-%m-%d",
    "datetime": "%Y-%m-%d_%H-%M-%S",
    "stamp": "%Y%m%d-%H%M%S",
    "year": "%Y",
    "month": "%m",
    "day": "%d",
    "hour": "%H",
    "minute": "%M",
    "second": "%S",
}

_TOKEN_RE = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class Token:
    """模板里的一段：`raw` 是原文（含花括号），`name` 是变量名，`args` 是逗号后面的参数。"""

    raw: str
    name: str
    args: tuple[str, ...] = ()
    known: bool = False

    @property
    def variable(self) -> Variable | None:
        return resolve(self.name)


def resolve(name: str) -> Variable | None:
    """按名字找变量：先精确匹配，再试别名（别名不区分大小写）。"""
    text = (name or "").strip()
    if text in VARIABLE_MAP:
        return VARIABLE_MAP[text]
    alias = ALIASES.get(text) or ALIASES.get(text.lower())
    if alias:
        return VARIABLE_MAP[alias]
    lowered = text.lower()
    for item in VARIABLES:  # alpha / ALPHA 之外的写法按小写认，免得用户大小写写错就失效
        if item.name.islower() and item.name == lowered:
            return item
    return None


def parse_tokens(template: str) -> tuple[Token, ...]:
    """把模板切开：认得的算变量，认不得的原样留着（`known=False`）。"""
    tokens: list[Token] = []
    for match in _TOKEN_RE.finditer(template or ""):
        parts = [piece.strip() for piece in match.group(1).split(",")]
        name = parts[0] if parts else ""
        args = tuple(parts[1:])
        tokens.append(Token(raw=match.group(0), name=name, args=args, known=resolve(name) is not None))
    return tuple(tokens)


def token_names(template: str) -> tuple[str, ...]:
    """模板里出现过的变量名（认得的用规范名，认不得的原样），按出现顺序去重。"""
    seen: list[str] = []
    for token in parse_tokens(template):
        variable = resolve(token.name)
        text = variable.name if variable else token.name
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def unknown_names(template: str) -> tuple[str, ...]:
    """模板里认不出来的变量名（界面拿它提示「这几个变量没生效」）。"""
    return tuple(dict.fromkeys(token.name for token in parse_tokens(template) if not token.known and token.name))


def numbered_tokens(template: str) -> tuple[Token, ...]:
    """模板里所有编码式变量（数字 / 字母 / 罗马），界面靠它决定编号控件是否可改。"""
    return tuple(
        token
        for token in parse_tokens(template)
        if token.variable is not None and token.variable.numbered
    )


def apply_numbering(template: str, start: int, step: int) -> str:
    """把模板里每个编码式变量都改写成 `{名字,起点,间隔}`，原样的补零宽度保持不变。

    界面上的「起点 / 间隔」两个控件靠它把值写回模板原文 —— 改完用户能在模板框里
    直接看见 `{number,3,2}`，不是藏在别处看不见的状态。顺带把别名与大小写写法
    归一成规范名（`{数字,3,2}` → `{number,3,2}`），时间 / 文本式变量原样不动。
    """
    start = max(1, int(start))
    step = max(1, int(step))

    def swap(match: re.Match[str]) -> str:
        parts = [piece.strip() for piece in match.group(1).split(",")]
        variable = resolve(parts[0] if parts else "")
        if variable is None or not variable.numbered:
            return match.group(0)
        width = parts[3] if len(parts) > 3 else ""  # {名字,起点,间隔,宽度}
        text = f"{{{variable.name},{start},{step}"
        return f"{text},{width}}}" if width else f"{text}}}"

    return _TOKEN_RE.sub(swap, template or "")


# ----------------------------------------------------------------- 编号
_ROMAN_TABLE: tuple[tuple[int, str], ...] = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def to_roman(value: int) -> str:
    """整数转罗马数字：认 1–3999（再大就得加上划线，这里不做）。"""
    if value < 1:
        raise TemplateError(f"罗马字母编号最小是 i（1），这里给的是 {value}")
    if value > 3999:
        raise TemplateError(f"罗马字母编号最大是 MMMCMXCIX（3999），这里给的是 {value}")
    rest = value
    pieces: list[str] = []
    for amount, sign in _ROMAN_TABLE:
        while rest >= amount:
            pieces.append(sign)
            rest -= amount
    return "".join(pieces)


def from_roman(text: str) -> int:
    """罗马数字转整数：只能读规范写法（写 `IIII` 会报错，让用户改回 `IV`）。"""
    source = (text or "").strip().upper()
    if not source:
        raise TemplateError("罗马字母编号的起点是空的")
    total = 0
    index = 0
    for amount, sign in _ROMAN_TABLE:
        while source.startswith(sign, index):
            total += amount
            index += len(sign)
    if index != len(source) or to_roman(total) != source:
        raise TemplateError(f"这不是规范的罗马数字：{text}")
    return total


def alpha_ordinal(text: str) -> int:
    """字母转序号：a→1、z→26、aa→27、ab→28…（大小写都认）。"""
    source = (text or "").strip()
    if not source or not source.isalpha() or not source.isascii():
        raise TemplateError(f"字母编号的起点只能是字母：{text!r}")
    value = 0
    for char in source.lower():
        value = value * 26 + (ord(char) - ord("a") + 1)
    return value


def alpha_name(value: int) -> str:
    """序号转小写字母：1→a、26→z、27→aa…（不是 26 进制，是「Excel 列名」那套）。"""
    if value < 1:
        raise TemplateError(f"字母编号最小是 a（1），这里给的是 {value}")
    pieces: list[str] = []
    rest = value
    while rest > 0:
        rest, remainder = divmod(rest - 1, 26)
        pieces.append(chr(ord("a") + remainder))
    return "".join(reversed(pieces))


def parse_start(kind: str, raw: str) -> int:
    """把起点参数翻成序号；空就是默认起点（一律 1 = 数字 1 / a / i）。"""
    text = (raw or "").strip()
    if not text:
        return 1
    if kind == "number":
        try:
            return int(text)
        except ValueError as exc:
            raise TemplateError(f"数字编号的起点得是整数：{text!r}") from exc
    if kind in ("alpha", "ALPHA"):
        return alpha_ordinal(text)
    if kind in ("roman", "ROMAN"):
        return from_roman(text)
    return 1


def ordinal(kind: str, value: int, width: int = 0) -> str:
    """序号转字符串；`width` 只对数字编号有效（补零）。"""
    if kind == "number":
        text = str(value)
        return text.zfill(width) if width > len(text) else text
    if kind == "alpha":
        return alpha_name(value)
    if kind == "ALPHA":
        return alpha_name(value).upper()
    if kind == "roman":
        return to_roman(value).lower()
    if kind == "ROMAN":
        return to_roman(value)
    return str(value)


# ----------------------------------------------------------------- 渲染
@dataclass(frozen=True)
class RenderContext:
    """渲染一次（一般是一个压缩包）需要的全部取值。"""

    now: dt.datetime = field(default_factory=dt.datetime.now)
    index: int = 1
    count: int = 0
    creator: str = ""
    user: str = ""
    category: str = ""
    top: str = ""
    name: str = ""
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedName:
    """渲染结果：`text` 是原样的字符串，`clean` 是能当文件名的版本。"""

    text: str
    warnings: tuple[str, ...] = ()

    @property
    def clean(self) -> str:
        return safe_filename(self.text)

    @property
    def ok(self) -> bool:
        return not self.warnings


def _int_arg(raw: str, default: int, *, what: str, warnings: list[str]) -> int:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        warnings.append(f"{what}看不懂（{text!r}），按默认 {default} 算")
        return default


def _value_of(variable: Variable, context: RenderContext) -> str:
    name = variable.name
    if variable.timed:
        return context.now.strftime(_TIME_FORMATS.get(name, "%Y-%m-%d"))
    if name == "number":  # 占位：数字编号走参数分支，这里给个默认
        return str(context.index)
    if name == "count":
        return str(context.count)
    if name == "user":
        return context.user
    if name == "creator":
        return context.creator
    if name == "category":
        return context.category
    if name == "top":
        return context.top
    if name == "name":
        return context.name
    return str(context.extra.get(name, ""))


def _render_one(token: Token, context: RenderContext, warnings: list[str]) -> str:
    variable = resolve(token.name)
    if variable is None:
        warnings.append(f"认不出的变量：{token.raw}")
        return token.raw
    args = token.args
    if variable.numbered:
        start_raw = args[0] if len(args) > 0 else ""
        step_raw = args[1] if len(args) > 1 else ""
        width_raw = args[2] if len(args) > 2 else ""
        if len(args) > 3:
            warnings.append(f"{token.raw} 参数太多，只认「起点、间隔、补零宽度」")
        step = _int_arg(step_raw, 1, what="间隔", warnings=warnings)
        if step < 1:
            warnings.append(f"间隔不能小于 1（{token.raw}），按 1 算")
            step = 1
        width = _int_arg(width_raw, 0, what="补零宽度", warnings=warnings)
        if width < 0:
            warnings.append(f"补零宽度不能是负数（{token.raw}），按 0 算")
            width = 0
        try:
            start = parse_start(variable.name, start_raw)
            current = start + (context.index - 1) * step
            return ordinal(variable.name, current, width)
        except TemplateError as exc:
            warnings.append(str(exc))
            return ordinal(variable.name, 1) if variable.name == "number" else token.raw
    if variable.timed:
        if args and args[0]:
            fmt = args[0]
            try:
                return context.now.strftime(fmt)
            except (ValueError, TypeError) as exc:
                warnings.append(f"{token.raw} 的时间格式用不了（{exc}），换回默认格式")
        return _value_of(variable, context)
    if args and any(piece for piece in args):
        warnings.append(f"{token.raw} 不接受参数，已忽略")
    return _value_of(variable, context)


def render(template: str, context: RenderContext) -> RenderedName:
    """按模板渲染出名字；认不出的变量原样留着，问题都记在 `warnings` 里。"""
    warnings: list[str] = []
    text = template or ""

    def swap(match: re.Match[str]) -> str:
        parts = [piece.strip() for piece in match.group(1).split(",")]
        name = parts[0] if parts else ""
        token = Token(raw=match.group(0), name=name, args=tuple(parts[1:]), known=resolve(name) is not None)
        return _render_one(token, context, warnings)

    rendered = _TOKEN_RE.sub(swap, text)
    return RenderedName(text=rendered, warnings=tuple(warnings))


# ----------------------------------------------------------------- 文件名
_ILLEGAL_CHARS = '<>:"/\\|?*'
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def safe_filename(text: str, *, fallback: str = "导出", limit: int = 150) -> str:
    """把渲染结果收拾成一个能直接落盘的文件名（Windows 的忌讳都躲开）。"""
    cleaned = _CONTROL_RE.sub("", text or "")
    for char in _ILLEGAL_CHARS:
        cleaned = cleaned.replace(char, "")
    cleaned = _SPACE_RE.sub(" ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".")[0].lower() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def unique_name(text: str, used: set[str]) -> str:
    """重名就加 `_1`、`_2`…（不区分大小写），并把最终名字记进 `used`。

    只在最后一段文件名上动刀子，`dir/name.txt` 这种带目录的写法不会被拆坏。
    """
    head, sep, tail = (text or "").rpartition("/")
    stem, dot, suffix = tail.rpartition(".")
    if not dot or not stem:
        stem, suffix = tail, ""
    else:
        suffix = dot + suffix
    candidate = text
    index = 1
    while candidate.lower() in used:
        candidate = f"{head}{sep}{stem}_{index}{suffix}"
        index += 1
    used.add(candidate.lower())
    return candidate


def preview(template: str, contexts: Sequence[RenderContext]) -> tuple[RenderedName, ...]:
    """给界面做预览：一个上下文渲染一次。"""
    return tuple(render(template, context) for context in contexts)
