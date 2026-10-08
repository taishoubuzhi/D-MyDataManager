"""程序本体的导出接口实现：插件通过扩展接口 `export.open` 用 core 的导出引擎。

设计要点：

- **每次调用自建会话**：插件会在自己的工作线程里调用，不能共用界面线程的会话；读完条目
  就关。导出本身只是往磁盘写文件，不写数据库，所以不需要事务；
- **红线**：`packages()` 默认先弹确认框（能看到导出几个包、包叫什么、落到哪个目录，目录
  还能改），用户点了「开始导出」才真写盘，用户关掉返回 `None`。`confirm=False` 只有调用方
  自己已经问过用户时才该用；
- **只给快照**：插件拿到的是 `ExportRef`，改它不会影响已经写好的文件；
- 只导没被软删除的条目；请求里的 id 顺序就是导出的编号顺序（`{number}` 按它递增）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable

from ..core.config import export_dir
from ..core.export import DEFAULT_TEMPLATE, name_packages, plan_packages
from ..db.database import new_session
from ..db.models import DataItem
from ..repositories import ItemRepository
from ..sdk import export as export_api
from ..sdk.errors import SdkError
from .export_service import ExportService

__all__ = ["ExportApi", "api", "to_ref"]

#: 程序本体共用的那一个（无状态，但统一从 `api()` 取，便于以后加缓存）
_DEFAULT: "ExportApi | None" = None


def api() -> "ExportApi":
    """取程序本体共用的导出接口实例（没有就建一个）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ExportApi()
    return _DEFAULT


def _clean_ids(values: Iterable[int] | None) -> list[int]:
    """去空白、去重、保持顺序。"""
    ids: list[int] = []
    for raw in values or ():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value and value not in ids:
            ids.append(value)
    return ids


def to_ref(result: Any) -> export_api.ExportRef:
    """把服务层的结果转成插件能看的只读快照。"""
    return export_api.ExportRef(
        directory=str(result.directory),
        packages=tuple(
            export_api.PackageRef(
                label=str(getattr(row, "label", "") or ""),
                path=str(row.path),
                exported=int(getattr(row, "exported", 0) or 0),
                missing=int(getattr(row, "missing", 0) or 0),
                warnings=tuple(str(text) for text in (getattr(row, "warnings", ()) or ())),
            )
            for row in result.packages
        ),
        warnings=tuple(str(text) for text in (getattr(result, "warnings", ()) or ())),
    )


class ExportApi:
    """`export.open` 的实现。"""

    # ------------------------------------------------------------------ 设置
    def directory(self) -> str:
        """导出设置里的默认导出目录。"""
        return str(export_dir())

    def choose_directory(self, current: str = "") -> str:
        """弹「选择导出目录」（没有界面时返回空串）。"""
        from ..ui.components.export_dialog import choose_export_directory

        return choose_export_directory(None, current or self.directory())

    # ------------------------------------------------------------------ 计划
    def plan(
        self,
        ids: Iterable[int],
        *,
        mode: str = "single",
        template: str = DEFAULT_TEMPLATE,
        directory: str = "",
        now: dt.datetime | None = None,
        user: str = "",
        creator: str = "",
    ) -> export_api.PlanRef:
        """只算不写：会导出成哪些包、各装多少条。"""
        wanted = _clean_ids(ids)
        target = Path(directory) if str(directory).strip() else Path(self.directory())
        session = new_session()
        try:
            items = self._load(session, wanted)
            if not items:
                return export_api.PlanRef(mode=mode, directory=str(target))
            service = ExportService(session)
            plan = plan_packages(service.planned_items(items), mode=mode)
            named = name_packages(
                plan.packages, template, now=now, user=user, creator=creator, used=set()
            )
        finally:
            session.close()
        packages = tuple(
            export_api.PackageRef(
                label=row.package.label,
                path=str(target / row.filename),
                exported=row.package.count,
                warnings=tuple(row.warnings),
            )
            for row in named
        )
        warnings = tuple(
            f"{row.filename}：{text}" for row in named for text in row.warnings
        )
        return export_api.PlanRef(
            mode=plan.mode,
            directory=str(target),
            packages=packages,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ 导出
    def packages(
        self,
        ids: Iterable[int],
        *,
        directory: str = "",
        mode: str = "single",
        template: str = DEFAULT_TEMPLATE,
        manifest_format: str = "csv",
        confirm: bool = True,
        parent: Any = None,
        message: str = "",
    ) -> export_api.ExportRef | None:
        """导出选中条目；`confirm=True` 时先让用户确认（取消返回 `None`）。"""
        wanted = _clean_ids(ids)
        if not wanted:
            raise SdkError("没有要导出的数据项")
        target = str(directory).strip() or self.directory()
        if confirm:
            preview = self.plan(
                wanted, mode=mode, template=template, directory=target
            )
            if not preview.packages:
                raise SdkError("没有要导出的数据项")
            chosen = self._confirm(preview, parent=parent, message=message)
            if chosen is None:
                return None
            target = chosen or target
        session = new_session()
        try:
            items = self._load(session, wanted)
            if not items:
                raise SdkError("选中的数据项都不在了")
            result = ExportService(session).export_packages(
                items,
                target,
                mode=mode,
                template=template,
                manifest_format=manifest_format,
            )
        finally:
            session.close()
        return to_ref(result)

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _load(session, ids: list[int]) -> list[DataItem]:
        """按请求顺序取没被软删除的条目。"""
        found = {
            item.id: item
            for item in ItemRepository(session).by_ids(ids)
            if not getattr(item, "is_deleted", False)
        }
        return [found[value] for value in ids if value in found]

    def _confirm(
        self,
        preview: export_api.PlanRef,
        *,
        parent: Any = None,
        message: str = "",
    ) -> str | None:
        """弹确认框；用户同意返回他选的目录（空串表示用默认），取消返回 `None`。"""
        from ..ui.components.export_dialog import ExportConfirmDialog

        dialog = ExportConfirmDialog(
            parent,
            title="导出",
            summary=message or preview.summary(),
            directory=preview.directory,
            names=[Path(row.path).name for row in preview.packages],
            hint="清单会随每个压缩包一起写入。",
        )
        try:
            if not dialog.exec():
                return None
            return dialog.directory()
        finally:
            dialog.deleteLater()
