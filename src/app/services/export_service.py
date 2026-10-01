"""导出服务：把数据项复制为普通文件，并生成清单。"""

from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from ..core.config import store_dir
from ..db.models import DataItem, DataType
from ..repositories import ItemRepository
from .blob_store import BlobStore

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


class ExportService:
    def __init__(self, session: Session, store: BlobStore | None = None) -> None:
        self.session = session
        self.store = store or BlobStore(store_dir())
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

    def write_manifest(self, rows: list[dict], path: Path, fmt: str = "csv") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
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

    # ---------------------------------------------------------------- 内部
    def _export_one(self, item: DataItem, target: Path, used: set[str]) -> bool:
        filename = self._unique_filename(item, used)
        path = target / filename
        try:
            if item.type == DataType.TEXT and item.content:
                path.write_text(item.content, encoding="utf-8")
                return True
            source = self.store.path_of(self.store.rel_path_for(item.checksum)) if item.checksum else None
            if source is None or not source.exists():
                logger.warning("源文件缺失，跳过：{}", item.name)
                return False
            shutil.copy2(source, path)
            return True
        except Exception as exc:
            logger.error("导出失败 {}：{}", item.name, exc)
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
