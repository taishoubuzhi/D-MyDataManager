"""数据层检查：库结构、FTS5 同步、内容仓库布局与配置落盘。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import inspect, text

from .fixtures import SAMPLE_IMAGE
from .harness import Case, check

CORE_TABLES = {
    "app_meta",
    "archive_entries",
    "archives",
    "blobs",
    "categories",
    "features",
    "item_tags",
    "items",
    "libraries",
    "tags",
    "users",
    "versions",
}
FTS_TABLES = {
    "items_fts",
    "items_fts_config",
    "items_fts_content",
    "items_fts_data",
    "items_fts_docsize",
    "items_fts_idx",
}
FTS_TRIGGERS = {"items_fts_ai", "items_fts_au", "items_fts_ad"}


@check("schema_tables", "data")
def schema_tables(case: Case) -> None:
    """12 张业务表、FTS5 虚表与 3 个同步触发器都在。"""
    tables = set(inspect(case.session.get_bind()).get_table_names())
    missing = CORE_TABLES - tables
    assert not missing, f"缺少业务表：{sorted(missing)}"
    assert FTS_TABLES <= tables, f"缺少 FTS 表：{sorted(FTS_TABLES - tables)}"
    triggers = {
        row[0] for row in case.session.execute(text("select name from sqlite_master where type='trigger'"))
    }
    assert FTS_TRIGGERS <= triggers, f"缺少 FTS 触发器：{sorted(FTS_TRIGGERS - triggers)}"


@check("fts_sync", "data")
def fts_sync(case: Case) -> None:
    """FTS 索引随 增 / 改 / 删 自动同步（触发器 + trigram 分词）。"""
    from app.services import ImportService, ItemService

    session = case.session
    item = ImportService(session).import_text("自检文档甲", "自检内容甲", keywords=["自检关键词"])
    session.commit()
    assert item is not None, "导入文本项失败"

    def hits(term: str) -> int:
        return session.execute(
            text("select count(*) from items_fts where items_fts match :q"), {"q": term}
        ).scalar_one()

    assert hits("自检文档甲") >= 1, "新增后 FTS 里查不到"
    ItemService(session).update(item, name="重命名乙文档")
    session.commit()
    assert hits("重命名乙文档") >= 1, "改名后 FTS 里查不到新名字"
    assert hits("自检文档甲") == 0, "改名后 FTS 里仍有旧名字"
    service = ItemService(session)
    service.delete([item])
    service.purge([item])
    session.commit()
    assert hits("重命名乙文档") == 0, "删除后 FTS 里仍有残留"


@check("library_layout", "data")
def library_layout(case: Case) -> None:
    """文件项落到内容仓库：库内相对路径可解析，同一校验和只有一条 blob 记录。"""
    from app.core import paths
    from app.services import ImportService, LibraryService

    session = case.session
    assert SAMPLE_IMAGE.exists(), f"缺少示例图片：{SAMPLE_IMAGE}"
    result = ImportService(session).import_files([SAMPLE_IMAGE])
    session.commit()
    assert not result.failed and result.added, f"导入失败：{result.failed}"
    item = result.added[0]
    path = LibraryService(session).abs_path(item)
    assert path is not None and Path(path).exists(), f"库内文件不存在：{path}"
    assert str(path).startswith(str(paths.DEFAULT_LIBRARY_DIR)), f"文件不在内容仓库里：{path}"
    rows = session.execute(
        text("select rel_path, ref_count from blobs where checksum = :c"), {"c": item.checksum}
    ).all()
    assert len(rows) == 1, f"同一校验和应有且只有一条 blob 记录，实际 {len(rows)}"
    rel_path, ref_count = rows[0]
    assert rel_path and ref_count >= 1, f"blob 记录异常：rel_path={rel_path!r} ref_count={ref_count}"


CONFIG_GROUPS = {"Archive", "Database", "Import", "Log", "MainWindow", "QFluentWidgets", "Storage", "User"}


def _flatten(payload: dict, prefix: str = "") -> dict[str, object]:
    """把分组配置摊平成 `组.键` → 值。"""
    flat: dict[str, object] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


@check("config_roundtrip", "data")
def config_roundtrip(case: Case) -> None:
    """配置分组键齐全、写盘后能读回，且运行期目录都在临时根目录下（没有碰真实配置）。"""
    from qfluentwidgets import qconfig

    from app.core import paths
    from app.core.config import config

    config.set(config.pruneMode, "count")
    config.set(config.keepVersions, 7)
    qconfig.save()
    target = paths.CONFIG_FILE
    assert target.exists(), f"配置没有落盘：{target}"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(payload, dict) and payload, "配置内容为空"
    groups = set(payload)
    missing = CONFIG_GROUPS - groups
    assert not missing, f"配置缺少分组：{sorted(missing)}"
    flat = _flatten(payload)
    assert flat.get("Archive.Prune-Mode") == "count", f"清理模式没写进配置：{flat.get('Archive.Prune-Mode')!r}"
    assert flat.get("Archive.Keep-Versions") == 7, f"保留份数没写进配置：{flat.get('Archive.Keep-Versions')!r}"
    assert str(paths.CONFIG_DIR).startswith(str(case.root)), "配置目录没有重定向到临时目录"
    assert str(paths.DATA_DIR).startswith(str(case.root)), "数据目录没有重定向到临时目录"
