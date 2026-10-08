"""整库包（`app.services.database_bundle`）的用例。

覆盖四件事：包结构（清单在最前、带数据库快照与库内文件）、新增式导入
（不覆盖 / 不重复分类标签 / 内容只在缺的时候写回 / 内容缺了要数出来）、
覆盖式导入（整份替换、旧库改名备份、结构版本太新要拒绝），以及坏包一律拒绝。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core import config
from app.db import database
from app.db.models import DataType
from app.repositories import ItemRepository
from app.repositories.archives import ArchiveRepository
from app.services.archive_service import ArchiveService
from app.services.content_store import ContentStore
from app.services.database_bundle import (
    COUNT_KEYS,
    DB_MEMBER,
    FORMAT,
    LIBRARY_PREFIX,
    MANIFEST_NAME,
    MODE_MERGE,
    MODE_REPLACE,
    VERSION,
    BundleError,
    DatabaseBundleService,
    import_replace,
    inspect_package,
)
from app.services.maintenance import reset_runtime_data

from tests.harness import IsolatedCase


class DatabaseBundleCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.library = self.default_library()
        self.user = self.current_user()
        self.store = ContentStore(self.session)
        self.service = DatabaseBundleService(self.session, store=self.store)
        self.items = ItemRepository(self.session)
        self.archives = ArchiveRepository(self.session)
        self._temp = tempfile.TemporaryDirectory(prefix="dm_database_bundle_")
        self.out = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()
        super().tearDown()

    # ------------------------------------------------------------------ 便捷
    def _source_file(self, name: str = "甲.bin", data: bytes = b"payload-0123456789") -> Path:
        path = self.out / name
        path.write_bytes(data)
        return path

    def _item(self, name: str = "笔记.txt", content: str = "正文一"):
        item = self.importer(library=self.library).import_text(
            name, content, user_id=self.user.id
        )
        self.session.commit()
        return item

    def _file_item(self, name: str = "甲.bin"):
        item = self.importer(library=self.library).import_file(
            self._source_file(name), library=self.library, user_id=self.user.id
        )
        self.session.commit()
        return item

    def _archive(self, name: str = "存档 A"):
        archive = ArchiveService(self.session).create(name, "备注")
        self.session.commit()
        return archive

    def _export(self, name: str = "整库包.zip"):
        return self.service.export(self.out / name)

    def _reconnect(self) -> None:
        """换一份新会话（覆盖式导入 / 重置之后必须换）。"""
        self.session = database.new_session()
        self.store = ContentStore(self.session)
        self.service = DatabaseBundleService(self.session, store=self.store)
        self.items = ItemRepository(self.session)
        self.archives = ArchiveRepository(self.session)
        self.library = self.default_library()
        self.user = self.current_user()

    def _payload(self, *, schema: int | None = None, **counts: int) -> dict:
        data = {key: 0 for key in COUNT_KEYS}
        data.update(counts)
        return {
            "format": FORMAT,
            "version": VERSION,
            "app_version": "1.0",
            "schema_version": database.SCHEMA_VERSION if schema is None else schema,
            "created_at": "2026-10-01 09:30:00",
            "counts": data,
        }

    def _snapshot(self, *, schema: int | None = None) -> Path:
        path = self.out / "snap.db"
        if path.exists():
            path.unlink()
        database.backup_database_file(path)
        if schema is not None:
            conn = sqlite3.connect(path)
            conn.execute("UPDATE app_meta SET value=? WHERE key='schema_version'", (str(schema),))
            conn.commit()
            conn.close()
        return path

    def _package(
        self,
        name: str = "手写包.zip",
        *,
        payload: dict | None = None,
        db_bytes: bytes | None = None,
        members=(),
    ) -> Path:
        path = self.out / name
        with zipfile.ZipFile(path, "w") as bundle:
            if payload is not None:
                bundle.writestr(MANIFEST_NAME, json.dumps(payload, ensure_ascii=False))
            if db_bytes is not None:
                bundle.writestr(DB_MEMBER, db_bytes)
            for member, data in members:
                bundle.writestr(member, data)
        return path

    # ------------------------------------------------------------------ 导出
    def test_export_writes_manifest_db_and_library(self) -> None:
        self._item()
        self._archive("存档 A")
        result = self._export()
        self.assertTrue(result.path.is_file())
        self.assertEqual(result.counts["items"], 1)
        self.assertEqual(result.counts["archives"], 1)
        self.assertGreaterEqual(result.files, 1)
        self.assertFalse((result.path.parent / (result.path.name + ".part")).exists())
        with zipfile.ZipFile(result.path) as bundle:
            names = bundle.namelist()
            self.assertEqual(names[0], MANIFEST_NAME)
            self.assertIn(DB_MEMBER, names)
            self.assertTrue(any(name.startswith(LIBRARY_PREFIX) for name in names))
            self.assertIsNone(bundle.testzip())
            payload = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
            self.assertEqual(payload["format"], FORMAT)
            self.assertEqual(payload["schema_version"], database.SCHEMA_VERSION)
        info = inspect_package(result.path)
        self.assertEqual(info.items, 1)
        self.assertEqual(info.users, 1)
        self.assertEqual(info.schema_version, database.SCHEMA_VERSION)
        self.assertIn("数据项", info.summary())
        self.assertIn("数据项", result.summary())

    def test_inspect_rejects_bad_packages(self) -> None:
        with self.assertRaises(BundleError):
            inspect_package(self.out / "没有这个.zip")
        notzip = self.out / "文本.zip"
        notzip.write_text("这不是压缩包", encoding="utf-8")
        with self.assertRaises(BundleError):
            inspect_package(notzip)
        empty = self.out / "空包.zip"
        with zipfile.ZipFile(empty, "w") as bundle:
            bundle.writestr("x.txt", "x")
        with self.assertRaises(BundleError):
            inspect_package(empty)
        wrong_format = self._package(
            "格式不对.zip", payload={"format": "dm-other"}, db_bytes=b"x"
        )
        with self.assertRaises(BundleError):
            inspect_package(wrong_format)
        broken = self._package("清单坏.zip", db_bytes=b"x")
        with zipfile.ZipFile(broken, "a") as bundle:
            bundle.writestr(MANIFEST_NAME, "{ 不是 json")
        with self.assertRaises(BundleError):
            inspect_package(broken)
        no_db = self._package("缺库.zip", payload=self._payload(users=1))
        with self.assertRaises(BundleError):
            inspect_package(no_db)

    # ---------------------------------------------------------------- 新增式
    def test_merge_copies_everything_into_an_empty_database(self) -> None:
        self._file_item("甲.bin")
        self._archive("存档 A")
        result = self._export()
        self.session.close()
        database.dispose_engine()
        reset_runtime_data()
        self._reconnect()
        self.assertEqual(len(self.items.all()), 0)
        report = self.service.import_merge(result.path)
        self.assertTrue(report.merge)
        self.assertEqual(report.mode, MODE_MERGE)
        self.assertEqual(report.items, 1)
        self.assertEqual(report.archives, 1)
        self.assertEqual(report.categories, 0)  # 「未分类」本机已有
        self.assertEqual(report.missing, 0)
        self.assertGreaterEqual(report.contents, 1)
        names = [row.name for row in self.items.all()]
        self.assertEqual(names, ["甲.bin"])
        restored = self.items.by_names(["甲.bin"])[0]
        self.assertTrue(self.store.content_available(restored.checksum))
        self.assertTrue((config.library_root() / restored.file_path).is_file())
        self.assertEqual([row.name for row in self.archives.latest(limit=5)], ["存档 A"])
        self.assertIn("新增式导入完成", report.summary())

    def test_merge_never_overwrites_and_renames_conflicts(self) -> None:
        self._file_item("甲.bin")
        self._archive("存档 A")
        result = self._export()
        report = self.service.import_merge(result.path)
        self.assertEqual(report.users, 0)  # 同名用户认出，不重复
        self.assertEqual(report.categories, 0)
        self.assertEqual(report.contents, 0)  # 内容已在库里，只补引用
        self.assertEqual(report.missing, 0)
        self.assertEqual(report.items, 1)
        self.assertEqual(report.archives, 1)
        rows = self.items.by_names(["甲.bin"])
        self.assertEqual(len(rows), 2)
        paths = sorted(row.file_path for row in rows)
        self.assertEqual(len(set(paths)), 2)  # 同名数据文件加了 _1
        self.assertTrue(all((config.library_root() / path).is_file() for path in paths))
        names = [row.name for row in self.archives.latest(limit=5)]
        self.assertIn("存档 A", names)
        self.assertIn("存档 A（导入）", names)

    def test_merge_counts_missing_contents(self) -> None:
        self._file_item("甲.bin")
        result = self._export()
        stripped = self.out / "缺内容.zip"
        for old in (result.path,):
            with zipfile.ZipFile(old) as source, zipfile.ZipFile(stripped, "w") as target:
                for info in source.infolist():
                    if f"{LIBRARY_PREFIX}" in info.filename and "/store/" in info.filename:
                        continue  # 把内容仓库整个丢掉
                    target.writestr(info, source.read(info.filename))
        self.session.close()
        database.dispose_engine()
        reset_runtime_data()
        self._reconnect()
        report = self.service.import_merge(stripped)
        self.assertEqual(report.missing, 1)
        self.assertEqual(report.contents, 0)
        self.assertIn("缺失", report.summary())
        # 内容没了，数据项记录照样重建（只是暂时取不到正文）
        self.assertEqual([row.name for row in self.items.all()], ["甲.bin"])

    def test_merge_rejects_newer_schema(self) -> None:
        snapshot = self._snapshot(schema=database.SCHEMA_VERSION + 1)
        package = self._package(
            "太新.zip",
            payload=self._payload(users=1, items=3),
            db_bytes=snapshot.read_bytes(),
        )
        with self.assertRaises(BundleError):
            self.service.import_merge(package)

    # ---------------------------------------------------------------- 覆盖式
    def test_replace_swaps_the_whole_database(self) -> None:
        self._file_item("甲.bin")
        result = self._export()
        self._file_item("乙.bin")
        self.assertEqual(len(self.items.all()), 2)
        self.session.close()
        report = import_replace(result.path)
        self.assertEqual(report.mode, MODE_REPLACE)
        self.assertTrue(report.backup)
        self.assertTrue(Path(report.backup).is_file())
        self.assertTrue(Path(report.backup).name.startswith("data.db.bak-"))
        self.assertGreaterEqual(len(list(config.library_root().parent.glob("library.bak-*"))), 1)
        self._reconnect()
        self.assertEqual([row.name for row in self.items.all()], ["甲.bin"])
        self.assertTrue((config.library_root() / self.items.all()[0].file_path).is_file())
        self.assertIn("覆盖式导入完成", report.summary())

    def test_replace_rejects_newer_schema_and_keeps_data(self) -> None:
        self._item("甲.txt", "甲的正文")
        snapshot = self._snapshot(schema=database.SCHEMA_VERSION + 1)
        package = self._package(
            "太新.zip",
            payload=self._payload(schema=database.SCHEMA_VERSION + 1, users=1, items=1),
            db_bytes=snapshot.read_bytes(),
        )
        with self.assertRaises(BundleError):
            import_replace(package)
        self.assertEqual([row.name for row in self.items.all()], ["甲.txt"])

    def test_replace_rejects_package_without_database(self) -> None:
        self._item("甲.txt", "甲的正文")
        package = self._package("缺库.zip", payload=self._payload(users=1, items=1))
        with self.assertRaises(BundleError):
            import_replace(package)
        self.assertEqual([row.name for row in self.items.all()], ["甲.txt"])

    def test_replace_rejects_package_without_users(self) -> None:
        snapshot = self._snapshot()
        package = self._package(
            "没有用户.zip", payload=self._payload(users=0), db_bytes=snapshot.read_bytes()
        )
        with self.assertRaises(BundleError):
            import_replace(package)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
