"""服务层自检脚本：走一遍导入 / 导出 / 存档 / 隐私 / 统计。

用法：.venv\\Scripts\\python.exe scripts\\dev_check_services.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_check_guard import run_guarded  # noqa: E402

import datetime as dt  # noqa: E402

from app.core.config import config  # noqa: E402
from app.db import database  # noqa: E402
from app.db.models import Feature  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.repositories import CategoryRepository, ItemFilter, ItemRepository  # noqa: E402
from app.services import (  # noqa: E402
    ArchiveService,
    ExportService,
    ImportService,
    ItemService,
    TaxonomyService,
    UserService,
    overview,
    storage_usage,
    type_breakdown,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_IMAGE = ROOT / "src" / "app" / "resource" / "images" / "logo.png"
EXPORT_DIR = ROOT / "_tmp_export"


def main() -> int:
    database.init_db(force=True)
    with database.session_scope() as session:
        print("seeded:", seed(session))

    with database.session_scope() as session:
        categories = CategoryRepository(session)
        items = ItemRepository(session)
        taxonomy = TaxonomyService(session)

        root = categories.by_name("学习资料")
        child = taxonomy.create_category("Python", parent_id=root.id if root else None)
        taxonomy.create_tag("示例标签", description="自检用")
        nodes = [node for node in taxonomy.tree() if node.category.name == "Python"]
        print("tree node:", [(n.category.name, n.depth) for n in nodes])
        print("duplicate category rejected:", taxonomy.create_category("Python", parent_id=root.id if root else None) is None)

        importer = ImportService(session)
        text_item = importer.import_text(
            "测试笔记", "第一行\n第二行", category_id=child.id, keywords=["笔记"], tags=["示例标签"]
        )
        print("text item:", text_item.id, text_item.name, text_item.size, text_item.checksum[:8])
        print(
            "text features:",
            [(f.kind, f.value) for f in session.query(Feature).filter(Feature.item_id == text_item.id)],
        )

        file_item = None
        if SAMPLE_IMAGE.exists():
            result = importer.import_files([SAMPLE_IMAGE])
            print("file import:", result.summary(), "failed:", result.failed)
            file_item = result.added[0] if result.added else None
            if file_item is not None:
                print(
                    "file item:",
                    file_item.name,
                    file_item.type_name,
                    file_item.size,
                    "cover:",
                    bool(file_item.cover_path),
                )
                print(
                    "file features:",
                    [(f.kind, f.value) for f in session.query(Feature).filter(Feature.item_id == file_item.id)],
                )
        print("item count:", items.count(ItemFilter()))

        service = ItemService(session)
        if text_item is not None:
            service.update(text_item, name="改名后的笔记", tags=["示例标签", "重要"])
            print("after update:", text_item.name, text_item.tag_names)
            service.delete([text_item])
            print("trashed:", items.count(ItemFilter(only_deleted=True)))
            service.restore([text_item])
            print("restored:", items.count(ItemFilter(only_deleted=True)) == 0)
        print("duplicates:", len(service.duplicate_map()))

        all_items = items.query(ItemFilter())
        exporter = ExportService(session)
        report = exporter.export_items(all_items, EXPORT_DIR)
        print("export:", report.summary(), "manifest:", report.manifest.name if report.manifest else None)
        print("exported:", sorted(p.name for p in EXPORT_DIR.iterdir()))

        archives = ArchiveService(session)
        archive = archives.create(note="自检快照")
        print("archive:", archive.name, archive.item_count, archive.new_blobs, archive.total_size)
        print("diff empty:", archives.compare(archive).is_empty)
        print("orphans:", len(archives.orphans()))
        print("cleanup:", archives.cleanup_orphans())

        # 存档清理策略：按数量 / 容量 / 时间
        policy: list[str] = []
        config.set(config.pruneMode, "count")
        config.set(config.keepVersions, 10)
        archives.create(note="策略自检 A")
        archives.create(note="策略自检 B")
        backdated = archives.history()[-1]
        backdated.created_at = dt.datetime.now() - dt.timedelta(days=30)
        session.flush()
        before = len(archives.history())
        removed = archives.prune_by_age(7)
        after = len(archives.history())
        policy.append(f"by-age removed={removed} {before}->{after} ok={removed == 1 and after == before - 1}")

        before = len(archives.history())
        removed = archives.prune_by_size(0)
        after = len(archives.history())
        policy.append(f"by-size(0) removed={removed} {before}->{after} ok={after == 1}")

        archives.create(note="策略自检 C")
        archives.create(note="策略自检 D")
        stale = archives.history()[-1]
        stale.created_at = dt.datetime.now() - dt.timedelta(days=5)
        session.flush()
        config.set(config.pruneMode, "size")
        config.set(config.keepSize, 64)
        policy.append(f"mode=size {archives.auto_prune()} {archives.policy_summary()}")
        config.set(config.pruneMode, "age")
        config.set(config.keepDays, 1)
        policy.append(f"mode=age {archives.auto_prune()} {archives.policy_summary()}")
        config.set(config.pruneMode, "none")
        policy.append(f"mode=none {archives.auto_prune()} {archives.policy_summary()}")
        config.set(config.pruneMode, "count")
        policy.append(f"mode=count {archives.policy_summary()}")
        print("prune policy:", policy)

        users = UserService(session)
        owner = users.current()
        users.set_password(owner, "secret")
        session.commit()
        print(
            "user password:",
            users.verify(owner, "secret"),
            users.verify(owner, "wrong"),
            bool(users.list_users()[0].protected),
        )
        users.set_password(owner, "")
        session.commit()

        print("type breakdown:", type_breakdown(session))
        stats = overview(session)
        print(
            "overview:",
            {key: stats[key] for key in ("total", "total_size", "categories", "tags", "today", "duplicate_groups")},
        )
        print("storage:", storage_usage(session))

    print("export dir:", EXPORT_DIR, EXPORT_DIR.exists())
    shutil.rmtree(EXPORT_DIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
