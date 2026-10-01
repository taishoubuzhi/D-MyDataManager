import os
import sqlite3
import sys

from contextlib import closing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from app.core import paths
from app.services import LibraryService
from app.services.layout_migration import migrate_layout
from tests.harness import IsolatedCase


class LayoutMigrationCase(IsolatedCase):
    """旧布局（分类目录平铺 + resources/store）迁移到单一库 + 用户名文件夹。"""

    def setUp(self):
        super().setUp()
        self.service = LibraryService(self.session)
        self.user = self.current_user()

    def _legacy_file(self, rel_name: str, text: str, *, user_id: int | None = None):
        library = self.service.ensure_default()
        path = Path(library.path) / rel_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        item = self.importer().register_file(
            path, library=library, rel_path=rel_name, user_id=user_id,
        )
        self.session.flush()
        return item

    def test_migrates_files_store_covers_and_marks_done(self):
        library = self.service.ensure_default()
        owned = self._legacy_file("学习资料/笔记.txt", "旧内容", user_id=self.user.id)
        orphan = self._legacy_file("散落.txt", "无主内容", user_id=self.user.id)
        orphan.user_id = None  # 旧库里可能存在无主数据
        self.session.flush()

        legacy_store = paths.LEGACY_STORE_DIR
        legacy_store.mkdir(parents=True, exist_ok=True)
        (legacy_store / "ab.bin").write_bytes(b"blob")
        legacy_covers = paths.LEGACY_COVER_DIR
        legacy_covers.mkdir(parents=True, exist_ok=True)
        (legacy_covers / "ab.png").write_bytes(b"cover")

        stats = migrate_layout(self.session)
        self.session.commit()

        self.assertTrue(stats["migrated"])
        self.assertEqual(owned.file_path, f"{self.user.name}/学习资料/笔记.txt")
        self.assertEqual(orphan.file_path, f"{paths.UNASSIGNED_DIR_NAME}/散落.txt")
        self.assertTrue((Path(library.path) / owned.file_path).is_file())
        self.assertTrue((Path(library.path) / orphan.file_path).is_file())
        self.assertFalse((Path(library.path) / "学习资料").exists())
        self.assertTrue((self.service.store_dir(library) / "ab.bin").is_file())
        self.assertTrue((self.service.cover_dir(library) / "ab.png").is_file())
        self.assertFalse(legacy_store.exists())
        self.assertFalse(legacy_covers.exists())
        self.assertTrue((self.service.meta_dir(library) / paths.LAYOUT_MARKER_FILE).is_file())
        backups = list(self.service.backup_dir(library).glob("data-before-layout-v2-*.db.bak"))
        self.assertEqual(len(backups), 1)

    def test_migration_is_idempotent(self):
        self._legacy_file("学习资料/笔记.txt", "旧内容", user_id=self.user.id)
        self.assertTrue(migrate_layout(self.session)["migrated"])
        self.session.commit()
        self.assertEqual(
            migrate_layout(self.session), {"migrated": False, "reason": "已是新布局"}
        )

    def test_already_v2_layout_without_marker_is_left_alone(self):
        """库已是新布局但缺标记（例如刚跑完 seed_demo 或标记被删）时不得再套一层用户名目录。"""
        library = self.service.ensure_default()
        self.service.ensure_layout(library)
        rel = f"{self.user.name}/学习资料/笔记.txt"
        item = self._legacy_file(rel, "新布局内容", user_id=self.user.id)

        stats = migrate_layout(self.session)
        self.session.commit()

        self.assertEqual(stats["moved"], 0, stats)
        self.assertEqual(item.file_path, rel, stats)
        self.assertTrue((Path(library.path) / rel).is_file())
        self.assertFalse((Path(library.path) / self.user.name / self.user.name).exists())
    def test_database_backup_covers_wal_writes(self):
        """迁移前的备份要用 SQLite 在线备份：WAL 里未落盘的提交也必须包含在内。"""
        self._legacy_file("学习资料/笔记.txt", "旧内容", user_id=self.user.id)
        self.session.commit()
        stats = migrate_layout(self.session)
        self.session.commit()

        backup = Path(stats["backup"])
        self.assertTrue(backup.is_file())
        self.assertFalse(Path(f"{backup}-wal").exists(), "备份应是单文件，不能带 WAL 边车文件")
        with closing(sqlite3.connect(str(backup))) as connection:
            stored = [row[0] for row in connection.execute("SELECT file_path FROM items")]
        self.assertEqual(stored, ["学习资料/笔记.txt"])
