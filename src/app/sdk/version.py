"""SDK 版本与版本范围：自研解析，不依赖第三方包。

范围写法（空格或逗号分隔的多个条件之间是「且」，``||`` 之间是「或」）::

    >=1.0 <2          区间
    1.2               缺省部分版本（等价于 >=1.2 <1.3）
    1.2.*             前缀（等价于 >=1.2 <1.3）
    1.2.3             精确版本（等价于 =1.2.3）
    ^1.2              第一位非零段不变（>=1.2 <2.0；^0.2 等价于 >=0.2 <0.3）
    ~1.2.3            只允许末段变化（>=1.2.3 <1.3）
    >=1.0 || <0.9     或

版本号按点分段、逐段取前导数字比较（``1.2.3-beta`` 视为 ``(1, 2, 3)``）。
"""

from __future__ import annotations

import re
from functools import lru_cache

from .errors import VersionError

__all__ = [
    "SDK_VERSION",
    "compare_versions",
    "parse_range",
    "parse_version",
    "range_text",
    "satisfies",
]

SDK_VERSION = "1.1"

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_OPERATORS = (">=", "<=", "^", "~", ">", "<", "=")


@lru_cache(maxsize=256)
def parse_version(text: str) -> tuple[int, ...]:
    """把版本号解析成整数元组；非法版本抛 VersionError。"""
    raw = str(text or "").strip()
    if not raw:
        raise VersionError("版本号不能为空")
    parts = raw.split(".")
    numbers: list[int] = []
    for part in parts:
        digits = re.match(r"\d+", part.strip())
        if digits is None:
            raise VersionError(f"版本号不合法：{raw}")
        numbers.append(int(digits.group()))
    return tuple(numbers)


def _pad(version: tuple[int, ...], size: int) -> tuple[int, ...]:
    return version + (0,) * (size - len(version))


def compare_versions(left: str, right: str) -> int:
    """比较两个版本号：左小于右返回 -1，相等 0，大于 1。"""
    a = parse_version(left)
    b = parse_version(right)
    size = max(len(a), len(b))
    a, b = _pad(a, size), _pad(b, size)
    return (a > b) - (a < b)


def _bump(version: tuple[int, ...], index: int) -> tuple[int, ...]:
    """把 version 的第 index 段加一，后面的段截掉（-1 表示最后一段）。"""
    if index < 0:
        index += len(version)
    head = list(version[: index + 1])
    head[index] += 1
    return tuple(head)


def _caret_upper(version: tuple[int, ...]) -> tuple[int, ...]:
    for index, value in enumerate(version):
        if value:
            return _bump(version, index)
    return _bump(version, -1)


def _tilde_upper(version: tuple[int, ...]) -> tuple[int, ...]:
    """`~1.2.3` → 1.3，`~1.2` → 1.3，`~1` → 2。"""
    if len(version) == 1:
        return _bump(version, 0)
    return (version[0], version[1] + 1)


def _parse_condition(text: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
    raw = text.strip()
    if not raw:
        raise VersionError("版本范围里出现空条件")
    if raw.endswith(".*"):
        base = parse_version(raw[:-2])
        return ((">=", base), ("<", _bump(base, -1)))
    for operator in _OPERATORS:
        if raw.startswith(operator):
            base = parse_version(raw[len(operator) :])
            if operator == "^":
                return ((">=", base), ("<", _caret_upper(base)))
            if operator == "~":
                return ((">=", base), ("<", _tilde_upper(base)))
            return ((operator, base),)
    base = parse_version(raw)
    if raw.count(".") == 0:
        # 只写主版本号：`1` 等价于 >=1 <2
        return ((">=", base), ("<", _bump(base, 0)))
    if len(base) >= 3:
        # 三段以上且不带运算符时按精确版本处理
        return (("=", base),)
    return ((">=", base), ("<", _bump(base, -1)))


@lru_cache(maxsize=256)
def parse_range(spec: str) -> tuple[tuple[tuple[str, tuple[int, ...]], ...], ...]:
    """把范围表达式解析成「或组」：每组是一串「且」条件。"""
    raw = str(spec or "").strip()
    if not raw:
        return ()
    groups: list[tuple[tuple[str, tuple[int, ...]], ...]] = []
    for chunk in raw.split("||"):
        conditions: list[tuple[str, tuple[int, ...]]] = []
        for piece in chunk.replace(",", " ").split():
            conditions.extend(_parse_condition(piece))
        if not conditions:
            raise VersionError(f"版本范围不合法：{raw}")
        groups.append(tuple(conditions))
    return tuple(groups)


def _match(operator: str, version: tuple[int, ...], ref: tuple[int, ...]) -> bool:
    size = max(len(version), len(ref))
    a, b = _pad(version, size), _pad(ref, size)
    if operator == ">=":
        return a >= b
    if operator == "<=":
        return a <= b
    if operator == ">":
        return a > b
    if operator == "<":
        return a < b
    return a == b


def satisfies(version: str, spec: str) -> bool:
    """判断版本号是否落在范围表达式里；空范围视为「都满足」。"""
    if not str(spec or "").strip():
        return True
    groups = parse_range(spec)
    if not groups:
        return True
    current = parse_version(version)
    return any(all(_match(op, current, ref) for op, ref in group) for group in groups)


def range_text(spec: str) -> str:
    """范围表达式的展示文本（空范围显示「不限」）。"""
    return str(spec or "").strip() or "不限"
