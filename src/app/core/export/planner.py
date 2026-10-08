"""导出分包：把选中的数据按「同级分类的最顶层」分成几个包，再按模板给每个包起名。

界面上的「分包方式」就两种：

* `single` —— 全部打成一个压缩包；
* `top`    —— 按每条数据所属分类的**最顶层**分组，一个顶层分类一个包；没有分类的
  统一进「未分类」包（永远排在最后）。

分包与命名都是纯逻辑：这里只认识 `PlannedItem` / `PlannedPackage` 这种小快照，
不碰数据库、不碰界面、不写文件，所以怎么测都行。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Sequence

from .template import RenderContext, RenderedName, render, safe_filename, unique_name

__all__ = [
    "DEFAULT_SUFFIX",
    "NamedPackage",
    "PLAN_MODES",
    "PackagePlan",
    "PlannedItem",
    "PlannedPackage",
    "UNASSIGNED_LABEL",
    "mode_label",
    "name_packages",
    "plan_packages",
]

UNASSIGNED_LABEL = "未分类"
DEFAULT_SUFFIX = ".zip"

PLAN_MODES: tuple[tuple[str, str], ...] = (
    ("single", "打成一个压缩包"),
    ("top", "按最顶层分类分别打包"),
)
_MODE_LABELS = dict(PLAN_MODES)


def mode_label(mode: str) -> str:
    """分包方式的中文名（认不出就原样返回）。"""
    return _MODE_LABELS.get(mode, mode)


@dataclass(frozen=True)
class PlannedItem:
    """一条要导出的数据；`key` 留给调用方回查原对象（一般是数据项 id）。"""

    key: str
    name: str
    category: str = ""  # 分类路径，如 "影视/电影"；没有分类就是空
    user: str = ""
    size: int = 0

    @property
    def top(self) -> str:
        """这条数据所属分类的最顶层（"影视/电影" → "影视"）。"""
        return (self.category or "").replace("\\", "/").split("/")[0].strip()


@dataclass(frozen=True)
class PlannedPackage:
    """一个待打包的组：里面装着数据，标签是它对应的分类名（未分类时是「未分类」）。"""

    key: str
    label: str
    top: str
    items: tuple[PlannedItem, ...]

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def size(self) -> int:
        return sum(max(0, int(item.size or 0)) for item in self.items)

    @property
    def users(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.user for item in self.items if item.user))


@dataclass(frozen=True)
class PackagePlan:
    """一次导出的分包结果。"""

    mode: str
    packages: tuple[PlannedPackage, ...]

    @property
    def total(self) -> int:
        return sum(package.count for package in self.packages)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(package.label for package in self.packages)

    @property
    def is_empty(self) -> bool:
        return not self.packages

    def summary(self) -> str:
        if not self.packages:
            return "没有要导出的数据"
        if self.mode == "top":
            return f"按分类分成 {len(self.packages)} 个压缩包，共 {self.total} 个文件"
        return f"打成 1 个压缩包，共 {self.total} 个文件"


def plan_packages(items: Sequence[PlannedItem], *, mode: str = "single") -> PackagePlan:
    """把数据分成包：`single` 一个包，`top` 按最顶层分类分组（未分类最后）。"""
    rows = tuple(items)
    if not rows:
        return PackagePlan(mode=mode, packages=())
    if mode != "top":
        return PackagePlan(mode="single", packages=(PlannedPackage(key="all", label="", top="", items=rows),))

    groups: dict[str, list[PlannedItem]] = {}
    for item in rows:
        groups.setdefault(item.top or UNASSIGNED_LABEL, []).append(item)
    order = [label for label in groups if label != UNASSIGNED_LABEL]
    if UNASSIGNED_LABEL in groups:
        order.append(UNASSIGNED_LABEL)
    packages = tuple(
        PlannedPackage(
            key=label,
            label=label,
            top="" if label == UNASSIGNED_LABEL else label,
            items=tuple(groups[label]),
        )
        for label in order
    )
    return PackagePlan(mode="top", packages=packages)


@dataclass(frozen=True)
class NamedPackage:
    """分好的包 + 渲染出来的文件名。"""

    package: PlannedPackage
    title: str
    filename: str
    warnings: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return self.package.count


def name_packages(
    packages: Sequence[PlannedPackage],
    template: str,
    *,
    now: dt.datetime | None = None,
    user: str = "",
    creator: str = "",
    suffix: str = DEFAULT_SUFFIX,
    used: set[str] | None = None,
) -> tuple[NamedPackage, ...]:
    """按模板给每个包起名：编号变量从 1 开始按包的顺序递增。

    名字里的非法字符会被洗掉；`suffix`（默认 `.zip`）只在模板没写后缀时补上；
    重名自动加 `_1`、`_2`。
    """
    taken = used if used is not None else set()
    moment = now or dt.datetime.now()
    named: list[NamedPackage] = []
    for index, package in enumerate(packages, start=1):
        owner = creator or (package.users[0] if package.users else user)
        context = RenderContext(
            now=moment,
            index=index,
            count=package.count,
            creator=owner,
            user=user,
            category=package.label,
            top=package.top or package.label,
        )
        rendered: RenderedName = render(template, context)
        cleaned = safe_filename(rendered.text, fallback=f"导出-{index}")
        if suffix and not cleaned.lower().endswith(suffix.lower()):
            cleaned += suffix
        named.append(
            NamedPackage(
                package=package,
                title=rendered.text,
                filename=unique_name(cleaned, taken),
                warnings=rendered.warnings,
            )
        )
    return tuple(named)
