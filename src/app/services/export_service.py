"""导出服务：把数据项复制为普通文件（或打包成一个 zip），并生成清单。

打包本身交给 `app.core.export`：那里管 `.part` 暂存、原子改名、分包与命名模板；
这里只负责把数据库里的条目翻译成「要导出的文件」，以及把清单塞进包里。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from ..core.export import (
    DEFAULT_TEMPLATE,
    PackagePlan,
    PlannedItem,
    ZipPack,
    name_packages,
    plan_packages,
)
from ..core.runtime import jsonio
from ..db.models import DataItem, DataType
from ..repositories import ItemRepository
from .content_store import ContentStore

MANIFEST_FIELDS = [
    "id",
    "名称",
    "类型",
    "分类",
    "标签",
    "关键词",
    "大小",
    "隐藏",
    "校验和",
    "导入时间",
    "内容或备注",
]


@dataclass
class ExportResult:
    directory: Path
    exported: int = 0
    missing: int = 0
    manifest: Path | None = None

    def summary(self) -> str:
        text = f"已导出 {self.exported} 个文件到 {self.directory}"
        if self.missing:
            text += f"，{self.missing} 个源文件缺失"
        return text


@dataclass
class ZipExportResult:
    """整包导出：zip 落在哪、装进去几个文件、几个源文件缺失。"""

    path: Path
    exported: int = 0
    missing: int = 0

    def summary(self) -> str:
        text = f"已把 {self.exported} 个文件打包到 {self.path.name}"
        if self.missing:
            text += f"，{self.missing} 个源文件缺失"
        return text


@dataclass
class PackageResult:
    """分包导出里的一个压缩包：装的是哪一包、落在哪、装进去几个文件。"""

    label: str
    path: Path
    exported: int = 0
    missing: int = 0
    warnings: tuple[str, ...] = ()


@dataclass
class PackageExportResult:
    """一次分包导出的总账（`mode="single"` 时只会有 1 个包）。"""

    directory: Path
    packages: tuple[PackageResult, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def exported(self) -> int:
        return sum(row.exported for row in self.packages)

    @property
    def missing(self) -> int:
        return sum(row.missing for row in self.packages)

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(row.path for row in self.packages)

    def summary(self) -> str:
        if not self.packages:
            return "没有要导出的数据项"
        text = f"已导出 {len(self.packages)} 个压缩包（共 {self.exported} 个文件）到 {self.directory}"
        if self.missing:
            text += f"，{self.missing} 个源文件缺失"
        return text


class ExportService:
    def __init__(self, session: Session, store: ContentStore | None = None) -> None:
        self.session = session
        self.store = store or ContentStore(session)
        self.items = ItemRepository(session)

    def manifest_rows(self, items: list[DataItem]) -> list[dict]:
        rows: list[dict] = []
        for item in items:
            rows.append(
                {
                    "id": item.id,
                    "名称": item.name,
                    "类型": item.type_name,
                    "分类": item.category.name if item.category else "",
                    "标签": "|".join(item.tag_names),
                    "关键词": "|".join(str(k) for k in (item.keywords or [])),
                    "大小": item.size,
                    "隐藏": "是" if item.is_hidden else "否",
                    "校验和": item.checksum,
                    "导入时间": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
                    "内容或备注": (item.content or "")[:500],
                }
            )
        return rows

    def manifest_bytes(self, rows: list[dict], fmt: str = "csv") -> bytes:
        """清单的字节：zip 里装的是同一份内容，就不再走磁盘。"""
        if fmt == "json":
            return jsonio.dump_bytes(rows, indent=2)
        handle = io.StringIO()
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return handle.getvalue().encode("utf-8-sig")

    def write_manifest(self, rows: list[dict], path: Path, fmt: str = "csv") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            jsonio.write_json(path, rows)
        else:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        return path

    def export_items(
        self,
        items: list[DataItem],
        directory: str | Path,
        *,
        copy_files: bool = True,
        manifest_format: str = "csv",
    ) -> ExportResult:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        result = ExportResult(directory=target)
        used: set[str] = set()
        for item in items:
            if not copy_files:
                break
            if self._export_one(item, target, used):
                result.exported += 1
            else:
                result.missing += 1
        if items:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            suffix = "json" if manifest_format == "json" else "csv"
            result.manifest = self.write_manifest(
                self.manifest_rows(items), target / f"清单-{stamp}.{suffix}", manifest_format
            )
        logger.info("导出完成：{}", result.summary())
        return result

    def export_archive(
        self,
        items: list[DataItem],
        target: str | Path,
        *,
        manifest_format: str = "csv",
    ) -> ZipExportResult:
        """把选中数据项打进一个 zip 压缩包：每项一个文件，外加一份清单。

        `target` 不以 `.zip` 结尾时自动补上后缀（用户只写名字也不会导出一个没扩展名的包）。
        先写 `<名字>.zip.part`、装完再原子改名：中途失败只会留下 `.part`，不会留下一个
        「看着像成功」的半个压缩包。
        """
        if not items:
            raise ValueError("没有要导出的数据项")
        path = Path(target)
        if path.suffix.lower() != ".zip":
            path = path.with_name(path.name + ".zip")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = ZipExportResult(path=path)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = "json" if manifest_format == "json" else "csv"
        used: set[str] = set()
        with ZipPack(path) as pack:
            for item in items:
                if self._pack_one(pack, item, used):
                    result.exported += 1
                else:
                    result.missing += 1
            pack.add_bytes(
                f"清单-{stamp}.{suffix}",
                self.manifest_bytes(self.manifest_rows(items), manifest_format),
            )
        logger.info("导出压缩包完成：{}", result.summary())
        return result

    def export_packages(
        self,
        items: list[DataItem],
        directory: str | Path,
        *,
        mode: str = "single",
        template: str = DEFAULT_TEMPLATE,
        manifest_format: str = "csv",
        now: dt.datetime | None = None,
        user: str = "",
        creator: str = "",
    ) -> PackageExportResult:
        """按分包方式把选中数据项导成若干压缩包，包名走命名模板。

        `mode="single"` 只出一个包；`mode="top"` 按每个条目所在分类的最顶层分开打包。
        模板里认不出的变量不会中断导出，只记在 `warnings` 里（包名保留原文）。
        """
        if not items:
            raise ValueError("没有要导出的数据项")
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        by_key = {str(item.id): item for item in items}
        plan: PackagePlan = plan_packages(self.planned_items(items), mode=mode)
        named = name_packages(
            plan.packages, template, now=now, user=user, creator=creator, used=set()
        )
        moment = now or dt.datetime.now()
        stamp = moment.strftime("%Y%m%d-%H%M%S")
        suffix = "json" if manifest_format == "json" else "csv"
        results: list[PackageResult] = []
        warnings: list[str] = []
        for row in named:
            chosen = [by_key[item.key] for item in row.package.items if item.key in by_key]
            single = PackageResult(
                label=row.package.label,
                path=target / row.filename,
                warnings=tuple(row.warnings),
            )
            used: set[str] = set()
            with ZipPack(single.path) as pack:
                for item in chosen:
                    if self._pack_one(pack, item, used):
                        single.exported += 1
                    else:
                        single.missing += 1
                pack.add_bytes(
                    f"清单-{stamp}.{suffix}",
                    self.manifest_bytes(self.manifest_rows(chosen), manifest_format),
                )
            results.append(single)
            warnings.extend(f"{single.path.name}：{text}" for text in row.warnings)
        outcome = PackageExportResult(
            directory=target, packages=tuple(results), warnings=tuple(warnings)
        )
        logger.info("分包导出完成：{}", outcome.summary())
        return outcome

    def category_path(self, item: DataItem) -> str:
        """条目所在分类的完整路径（`影视/电影`）；没分类给空串。"""
        names: list[str] = []
        category = item.category
        guard = 0
        while category is not None and guard < 32:
            names.append(category.name)
            category = category.parent
            guard += 1
        return "/".join(reversed(names))

    def planned_items(self, items: list[DataItem]) -> list[PlannedItem]:
        """把数据库条目翻译成分包/命名用的只读计划项。"""
        return [
            PlannedItem(
                key=str(item.id),
                name=item.name,
                category=self.category_path(item),
                user=(item.user.name if item.user else ""),
                size=int(item.size or 0),
            )
            for item in items
        ]

    # ---------------------------------------------------------------- 内部
    def _export_one(self, item: DataItem, target: Path, used: set[str]) -> bool:
        filename = self._unique_filename(item, used)
        path = target / filename
        try:
            if item.type == DataType.TEXT and item.content:
                path.write_text(item.content, encoding="utf-8")
                return True
            if not item.checksum or not self.store.export_content(item.checksum, path):
                logger.warning("源文件缺失，跳过：{}", item.name)
                return False
            return True
        except Exception as exc:
            logger.error("导出失败 {}：{}", item.name, exc)
            return False

    def _pack_one(self, pack: ZipPack, item: DataItem, used: set[str]) -> bool:
        """把一项塞进压缩包；源文件缺失返回 False（不静默漏掉，由调用方计数）。"""
        filename = self._unique_filename(item, used)
        try:
            if item.type == DataType.TEXT and item.content:
                pack.add_text(filename, item.content)
                return True
            if not item.checksum or not self.store.content_available(item.checksum):
                logger.warning("源文件缺失，跳过：{}", item.name)
                return False
            pack.add_stream(filename, self.store.iter_content(item.checksum))
            return True
        except Exception as exc:
            logger.error("打包失败 {}：{}", item.name, exc)
            return False

    def _unique_filename(self, item: DataItem, used: set[str]) -> str:
        base = Path(item.name).name or f"item_{item.id}"
        if item.type == DataType.TEXT and not Path(base).suffix:
            base += ".txt"
        stem, suffix = Path(base).stem, Path(base).suffix
        candidate = base
        index = 1
        while candidate.lower() in used:
            candidate = f"{stem}_{index}{suffix}"
            index += 1
        used.add(candidate.lower())
        return candidate
