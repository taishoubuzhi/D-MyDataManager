"""导出接口（`export.open`）：插件把选中的数据交给程序本体导成压缩包。

程序本体提供实现（见 `app.services.export_api`），插件这样用：

    from app.sdk import export

    ref = export.packages([item_id, ...], mode="top", template="{category}-{number}")
    if ref is None:
        ...  # 用户没同意

四条约定：

* **插件不许替用户决定写盘**。`packages()` 默认先弹确认框（能看到要导出几个包、包叫什么、
  落到哪个目录，目录还能改），用户点了「开始导出」才真写文件，用户关掉就返回 `None`。
  要跳过确认必须显式 `confirm=False`，那意味着调用方自己已经问过用户了；
* **只按数据项 id 办事**：插件先用 `app.sdk.items` 查到条目、把 `id` 交给这里，导出内容的
  解释（正文、仓库里的原始文件）由程序本体负责；
* **命名模板是共用的**：`render` / `preview` / `VARIABLES` 就是程序本体导出用的那一套，
  插件自己画界面时直接拿它做实时预览，保证与最终文件名一致；
* 拿到的是 `ExportRef` 快照；导出是同步做完的，返回时文件已经在盘上了。

只依赖 `app.sdk`，Qt 相关实现在函数内部延迟导入。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable

from ..core.export import (
    DEFAULT_TEMPLATE,
    PLAN_MODES,
    UNASSIGNED_LABEL,
    VARIABLES,
    VARIABLE_MAP,
    PackagePlan,
    PlannedItem,
    RenderContext,
    RenderedName,
    Variable,
    name_packages,
    plan_packages,
    preview,
    render,
    safe_filename,
    token_names,
    unknown_names,
)
from .errors import SdkError

__all__ = [
    "DEFAULT_TEMPLATE",
    "EXPORT_EXTENSION",
    "PLAN_MODES",
    "UNASSIGNED_LABEL",
    "VARIABLES",
    "VARIABLE_MAP",
    "ExportRef",
    "PackageRef",
    "PlanRef",
    "RenderContext",
    "RenderedName",
    "Variable",
    "available",
    "choose_directory",
    "directory",
    "name_packages",
    "packages",
    "plan",
    "plan_packages",
    "preview",
    "provider",
    "render",
    "safe_filename",
    "token_names",
    "unknown_names",
    "variables",
]

#: 扩展接口名：程序本体用它把导出引擎提供给插件
EXPORT_EXTENSION = "export.open"


@dataclass(frozen=True)
class PackageRef:
    """一个导出包：叫什么叫、落在哪、装进去几个文件、几个源文件缺失。"""

    label: str = ""
    path: str = ""
    exported: int = 0
    missing: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """显示名：有分类标签用标签，否则用文件名。"""
        if self.label:
            return self.label
        text = str(self.path or "").replace("\\", "/")
        return text.rsplit("/", 1)[-1] if text else ""

    @property
    def finished(self) -> bool:
        """导出是同步做完的：拿到快照时这个包已经写完了。"""
        return True


@dataclass(frozen=True)
class ExportRef:
    """一次导出的结果快照。"""

    directory: str = ""
    packages: tuple[PackageRef, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(row.path for row in self.packages)

    @property
    def exported(self) -> int:
        return sum(row.exported for row in self.packages)

    @property
    def missing(self) -> int:
        return sum(row.missing for row in self.packages)

    @property
    def finished(self) -> bool:
        return True

    def summary(self) -> str:
        if not self.packages:
            return "没有要导出的数据项"
        text = f"已导出 {len(self.packages)} 个压缩包（共 {self.exported} 个文件）到 {self.directory}"
        if self.missing:
            text += f"，{self.missing} 个源文件缺失"
        return text


@dataclass(frozen=True)
class PlanRef:
    """只算不写的导出计划：包名与每包条数，给插件做预览。"""

    mode: str = "single"
    directory: str = ""
    packages: tuple[PackageRef, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(row.path.rsplit("/", 1)[-1] for row in self.packages)

    @property
    def total(self) -> int:
        return sum(row.exported for row in self.packages)

    def summary(self) -> str:
        if not self.packages:
            return "没有要导出的数据项"
        return f"将要导出 {len(self.packages)} 个压缩包（共 {self.total} 个文件）"


def provider() -> Any:
    """程序提供的导出接口（`export.open`）；没有（脚本、测试）时为 None。"""
    from ..core.plugins.extensions import extension_registry

    return extension_registry.provider(EXPORT_EXTENSION)


def available() -> bool:
    """程序本体有没有提供导出引擎（插件要降级运行时先问这个）。"""
    return provider() is not None


def _api() -> Any:
    api = provider()
    if api is None:
        raise SdkError("程序没有提供导出接口 export.open")
    return api


def directory() -> str:
    """导出设置里的默认导出目录。"""
    return str(_api().directory())


def choose_directory(current: str = "") -> str:
    """弹「选择导出目录」，返回用户选的目录；取消返回空串。"""
    return str(_api().choose_directory(current))


def packages(
    ids: Iterable[int],
    *,
    directory: str = "",
    mode: str = "single",
    template: str = DEFAULT_TEMPLATE,
    manifest_format: str = "csv",
    confirm: bool = True,
    parent: Any = None,
    message: str = "",
) -> ExportRef | None:
    """把选中的数据项导成压缩包。

    `confirm=True`（默认）先弹确认框，用户同意返回结果快照，取消返回 `None`。
    `mode="single"` 打成一个包；`mode="top"` 按每条数据所属分类的最顶层分别打包。
    `template` 是包名模板（见 `VARIABLES` / `render`）。
    """
    return _api().packages(
        list(ids),
        directory=directory,
        mode=mode,
        template=template,
        manifest_format=manifest_format,
        confirm=confirm,
        parent=parent,
        message=message,
    )


def plan(
    ids: Iterable[int],
    *,
    mode: str = "single",
    template: str = DEFAULT_TEMPLATE,
    directory: str = "",
    now: dt.datetime | None = None,
    user: str = "",
    creator: str = "",
) -> PlanRef:
    """算一遍「会导出成哪些包、各装多少」，不写任何文件。"""
    return _api().plan(
        list(ids),
        mode=mode,
        template=template,
        directory=directory,
        now=now,
        user=user,
        creator=creator,
    )


def variables() -> tuple[Variable, ...]:
    """命名模板支持的全部变量（给插件的模板编辑器列出来）。"""
    return VARIABLES
