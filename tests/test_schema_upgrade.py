"""数据库结构升级：低版本库原地补列、回填并保留数据，不重建库。"""

from __future__ import annotations

import shutil
import sqlite3
import unittest

from tests.harness import KEEP_TMP, TMP_ROOT, redirect_paths, reset_config

_V2_SCHEMA = """
CREATE TABLE app_meta (key VARCHAR(64) PRIMARY KEY, value TEXT);
INSERT INTO app_meta(key, value) VALUES ('schema_version', '2');
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    password_hash VARCHAR(256),
    created_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    description VARCHAR(256),
    color VARCHAR(16),
    created_at DATETIME
);
CREATE UNIQUE INDEX uq_tag_user_name ON tags(user_id, name);
"""


class SchemaUpgradeCase(unittest.TestCase):
    """手工造一个 v2 结构的 sqlite 库，验证 init_db 会原地升级到当前版本。"""

    root = TMP_ROOT / "schemaupgradecase"

    def setUp(self) -> None:
        from app.core import paths
        from app.db import database

        database.dispose_engine()
        shutil.rmtree(type(self).root, ignore_errors=True)
        redirect_paths(type(self).root)
        reset_config(type(self).root)
        self.path = paths.DB_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        from app.db import database

        database.dispose_engine()
        if not KEEP_TMP:
            shutil.rmtree(type(self).root, ignore_errors=True)

    def _build_v2(self, tag_rows: str) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(_V2_SCHEMA)
            connection.execute("INSERT INTO users(id, name) VALUES (1, '老用户')")
            connection.execute(
                "INSERT INTO tags(id, name, user_id) VALUES " + tag_rows
            )
            connection.commit()
        finally:
            connection.close()

    def _upgrade(self) -> None:
        from app.db import database

        database.init_db()

    def test_upgrade_adds_columns_and_backfills(self):
        self._build_v2("(1, '个人标签', 1), (2, '老全局标签', NULL)")
        self._upgrade()

        from app.db import database

        with database.get_engine().connect() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(tags)")}
            rows = connection.exec_driver_sql(
                "SELECT id, name, user_id, is_global, created_by FROM tags ORDER BY id"
            ).fetchall()
            user_columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")
            }
            user_rows = connection.exec_driver_sql(
                "SELECT id, name, is_default FROM users ORDER BY id"
            ).fetchall()
            entry_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(archive_entries)")
            }
            version = connection.exec_driver_sql(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).scalar()
            indexes = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }

        self.assertIn("is_global", columns)
        self.assertIn("created_by", columns)
        self.assertEqual(
            rows,
            [
                (1, "个人标签", 1, 0, 1),
                (2, "老全局标签", None, 1, None),
            ],
        )
        self.assertIn("is_default", user_columns)
        self.assertEqual(user_rows, [(1, "老用户", 1)])
        self.assertIn("user_id", entry_columns)
        self.assertIn("user_name", entry_columns)
        self.assertEqual(version, "5")
        self.assertIn("uq_tag_global_name", indexes)
        self.assertIn("ix_users_is_default", indexes)

    def test_upgrade_dedupes_global_names(self):
        self._build_v2("(1, '重复', NULL), (2, '重复', NULL), (3, '重复', 1)")
        self._upgrade()

        from app.db import database

        with database.get_engine().connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT name, is_global FROM tags ORDER BY id"
            ).fetchall()
            indexes = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }

        self.assertEqual(rows, [("重复", 1), ("重复（2）", 1), ("重复", 0)])
        self.assertIn("uq_tag_global_name", indexes)

    def test_upgrade_is_idempotent(self):
        self._build_v2("(1, '标签', 1)")
        self._upgrade()
        self._upgrade()

        from app.db import database

        with database.get_engine().connect() as connection:
            count = connection.exec_driver_sql("SELECT COUNT(*) FROM tags").scalar()
            version = connection.exec_driver_sql(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).scalar()
        self.assertEqual(count, 1)
        self.assertEqual(version, "5")

    def test_orm_reads_upgraded_tags(self):
        self._build_v2("(1, '个人标签', 1), (2, '老全局标签', NULL)")
        self._upgrade()

        from app.db.database import new_session
        from app.db.models import Tag

        session = new_session()
        try:
            tags = {tag.name: tag for tag in session.query(Tag).all()}
            self.assertTrue(tags["老全局标签"].is_global)
            self.assertFalse(tags["个人标签"].is_global)
            self.assertEqual(tags["个人标签"].created_by, 1)
        finally:
            session.close()
