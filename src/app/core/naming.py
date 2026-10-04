"""批量改名的纯文本规则：替换 / 覆盖 / 添加 / 删改，以及重名后的 -1、-2 后缀。

只处理字符串，不碰数据库与磁盘，方便单独验证「原名会变成什么」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

#: 改名方式：标识 → 说明（界面直接用这份顺序做下拉）。
RENAME_MODES: tuple[tuple[str, str], ...] = (
    ("replace", "替换：把原名里的某段文字换成新文字"),
    ("overwrite", "覆盖：用统一的新名称整体替换（可自动编号）"),
    ("insert", "添加：在指定位置插入统一文字"),
    ("delete", "删改：删除指定位置的一段文字"),
)
RENAME_MODE_KEYS = tuple(key for key, _ in RENAME_MODES)

#: 覆盖模式的自动编号样式。
NUMBER_STYLES: tuple[tuple[str, str], ...] = (
    ("number", "数字 1、2、3"),
    ("lower", "小写字母 a、b、c"),
    ("upper", "大写字母 A、B、C"),
)
NUMBER_STYLE_KEYS = tuple(key for key, _ in NUMBER_STYLES)

#: 重名后的后缀序号从这里开始。
DUPLICATE_FROM = 1


def split_suffix(name: str) -> tuple[str, str]:
    """拆出主干与扩展名：`abc.md` → (`abc`, `.md`)；没有扩展名时后缀为空串。"""
    suffix = PurePosixPath(name).suffix
    if not suffix or suffix == name:
        return name, ""
    return name[: -len(suffix)], suffix


def alpha_index(index: int, upper: bool = False) -> str:
    """多级字母编号：1 → a …… 27 → aa，用于覆盖模式的字母编号。"""
    value = max(1, int(index))
    letters: list[str] = []
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr((ord("A") if upper else ord("a")) + remainder))
    return "".join(reversed(letters))


def number_text(index: int, style: str = "number", width: int = 0) -> str:
    """按样式生成第 index 个编号；width 是数字编号的补零位数。"""
    if style == "lower":
        return alpha_index(index)
    if style == "upper":
        return alpha_index(index, upper=True)
    return str(max(1, int(index))).zfill(max(0, int(width)))


@dataclass
class RenameRule:
    """一条批量改名规则；应用时逐行传入行号，覆盖模式的编号按行号递增。"""

    mode: str = "replace"
    find: str = ""
    replace: str = ""
    base: str = ""
    numbered: bool = True
    start: int = 1
    step: int = 1
    width: int = 0
    style: str = "number"
    text: str = ""
    position: int = 1
    length: int = 1

    def apply(self, name: str, index: int = 0) -> str:
        """把规则作用在一个名称上（扩展名保持不动）。空结果回落到原名。"""
        stem, suffix = split_suffix(name)
        if self.mode == "overwrite":
            base = self.base.strip()
            if not base:
                return name
            tail = number_text(self.start + index * self.step, self.style, self.width) if self.numbered else ""
            new_stem = f"{base}{tail}"
        elif self.mode == "insert":
            if not self.text:
                return name
            at = max(1, int(self.position)) - 1
            at = min(at, len(stem))
            new_stem = f"{stem[:at]}{self.text}{stem[at:]}"
        elif self.mode == "delete":
            at = int(self.position) - 1
            if at < 0 or at >= len(stem) or int(self.length) <= 0:
                return name
            new_stem = stem[:at] + stem[at + int(self.length) :]
        else:  # replace
            if not self.find:
                return name
            new_stem = stem.replace(self.find, self.replace)
        if new_stem == stem:
            return name
        if not new_stem.strip():
            return name
        return f"{new_stem}{suffix}"


def build_plan(names: list[str], rule: RenameRule, selected: list[bool], reserved: set[str]) -> list[str]:
    """逐行算出最终名称：选中的行按规则改名，未选中的行原样保留。

    新名称与「保留的名称 + 前面已改好的名称」重名时，依次追加 -1、-2
    （扩展名保持在末尾），保证一批里不会出现两个同名文件。
    """
    used = set(reserved)
    plan: list[str] = []
    changed = 0
    for name, keep in zip(names, selected):
        candidate = name
        if keep:
            candidate = rule.apply(name, changed)
            if candidate != name:
                changed += 1
        if candidate != name:
            stem, suffix = split_suffix(candidate)
            if candidate in used:
                serial = DUPLICATE_FROM
                while f"{stem}-{serial}{suffix}" in used:
                    serial += 1
                candidate = f"{stem}-{serial}{suffix}"
        used.add(candidate)
        plan.append(candidate)
    return plan
