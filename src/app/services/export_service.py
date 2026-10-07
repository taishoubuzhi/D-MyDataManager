"""导出服务：把数据项复制为普通文件（或打包成一个 zip），并生成清单。"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

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
        staging = path.with_name(path.name + ".part")
        used: set[str] = set()
        try:
            with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in items:
                    if self._archive_one(archive, item, used):
                        result.exported += 1
                    else:
                        result.missing += 1
                archive.writestr(
                    f"清单-{stamp}.{suffix}",
                    self.manifest_bytes(self.manifest_rows(items), manifest_format),
                )
            os.replace(staging, path)
        except BaseException:  # 磁盘满 / 用户中途关掉之类：别留下半个包
            staging.unlink(missing_ok=True)
            raise
        logger.info("导出压缩包完成：{}", result.summary())
        return result

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

    def _archive_one(self, archive: zipfile.ZipFile, item: DataItem, used: set[str]) -> bool:
        """把一项塞进压缩包；源文件缺失返回 False（不静默漏掉，由调用方计数）。"""
        filename = self._unique_filename(item, used)
        try:
            if item.type == DataType.TEXT and item.content:
                archive.writestr(filename, item.content.encode("utf-8"))
                return True
            if not item.checksum or not self.store.content_available(item.checksum):
                logger.warning("源文件缺失，跳过：{}", item.name)
                return False
            with archive.open(filename, "w") as handle:
                for data in self.store.iter_content(item.checksum):
                    handle.write(data)
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
