"""存档包（`app.services.archive_bundle`）的用例。

覆盖三件事：导出的包结构（清单在最前面、内容按校验和命名、缺内容要被数出来）、
导入只重建存档记录（重名加后缀、时间与用户认得回来）、以及坏包/坏内容不许静默通过。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.repositories import ItemRepository
from app.repositories.archives import ArchiveRepository
from app.db.models import DataType
from app.services.archive_bundle import (
    CONTENT_DIR,
    FORMAT,
    MANIFEST_NAME,
    VERSION,
    ArchiveBundleService,
    BundleError,
)
from app.services.archive_service import ArchiveService
from app.services.content_store import ContentStore

from tests.harness import IsolatedCase


class ArchiveBundleCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.library = self.default_library()
        self.user = self.current_user()
        self.store = ContentStore(self.session)
        self.service = ArchiveBundleService(self.session, store=self.store)
        self.archives = ArchiveRepository(self.session)
        self._temp = tempfile.TemporaryDirectory(prefix="dm_archive_bundle_")
        self.out = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()
        super().tearDown()

    # ------------------------------------------------------------------ 便捷
    def _item(self, name: str, content: str = "正文"):
        item = self.importer(library=self.library).import_text(
            name, content, user_id=self.user.id
        )
        self.session.flush()
        return item

    def _archive(self, name: str = "存档 A", note: str = "备注"):
        archive = ArchiveService(self.session).create(name, note)
        self.session.commit()
        return archive

    def _broken(self):
        """一条内容不在仓库里的条目。"""
        item = ItemRepository(self.session).create(
            name="缺源.bin",
            type=DataType.OTHER,
            checksum="0" * 64,
            size=11,
            library_id=self.library.id,
            user_id=self.user.id,
        )
        self.session.flush()
        return item

    def _entry(
        self,
        name: str = "条目",
        checksum: str = "",
        size: int = 0,
        user_name: str = "",
        content: str = "正文",
        type_: str = "TEXT",
        category: str = "",
        tags: tuple[str, ...] = (),
    ) -> dict:
        return {
            "name": name,
            "type": type_,
            "checksum": checksum,
            "size": size,
            "user_name": user_name,
            "category": category,
            "tags": list(tags),
            "content": content,
            "is_hidden": False,
        }

    def _row(
        self,
        key: str = "archive-1",
        name: str = "甲的存档",
        note: str = "",
        created_at: str = "2026-10-08 20:00:00",
        entries: list[dict] | None = None,
    ) -> dict:
        return {
            "key": key,
            "name": name,
            "note": note,
            "created_at": created_at,
            "entries": list(entries or []),
        }

    def _hand_bundle(self, name: str, rows: list[dict], contents: dict[str, bytes] | None = None) -> Path:
        """手写一个包：用来造导出流程造不出来的坏包与特殊情况。"""
        path = self.out / name
        payload = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": "2026-10-08 21:00:00",
            "archives": rows,
        }
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(payload, ensure_ascii=False))
            for checksum, data in (contents or {}).items():
                archive.writestr(f"{CONTENT_DIR}/{checksum}", data)
        return path

    # ------------------------------------------------------------------ 导出
    def test_export_writes_manifest_first_and_content(self) -> None:
        item = self._item("甲")
        archive = self._archive()

        result = self.service.export([archive], self.out / "包")

        self.assertEqual(result.path.name, "包.zip")
        self.assertEqual((result.archives, result.entries, result.files, result.missing), (1, 1, 1, 0))
        self.assertGreater(result.size, 0)
        self.assertIn("已导出 1 份存档", result.summary())
        with zipfile.ZipFile(result.path) as bundle:
            names = bundle.namelist()
            payload = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
        self.assertEqual(names[0], MANIFEST_NAME)
        self.assertIn(f"{CONTENT_DIR}/{item.checksum}", names)
        self.assertEqual(payload["format"], FORMAT)
        self.assertEqual(payload["version"], VERSION)
        self.assertEqual(len(payload["archives"]), 1)
        entry = payload["archives"][0]["entries"][0]
        self.assertEqual(entry["checksum"], item.checksum)
        self.assertEqual(entry["name"], "甲")
        self.assertEqual(payload["archives"][0]["note"], "备注")
        self.assertTrue(payload["archives"][0]["created_at"])

    def test_export_empty_selection_raises(self) -> None:
        with self.assertRaises(BundleError) as ctx:
            self.service.export([], self.out / "空.zip")
        self.assertIn("没有要导出的存档", str(ctx.exception))

    def test_export_counts_missing_content(self) -> None:
        self._broken()
        archive = self._archive()

        result = self.service.export([archive], self.out / "缺内容")

        self.assertEqual((result.files, result.missing), (0, 1))
        self.assertIn("内容已丢失", result.summary())
        with zipfile.ZipFile(result.path) as bundle:
            self.assertNotIn(CONTENT_DIR, {name.split("/")[0] for name in bundle.namelist()})
            payload = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
        self.assertEqual(len(payload["archives"][0]["entries"]), 1)

    def test_inspect_reads_manifest_only(self) -> None:
        self._item("甲", "第一份正文")
        self._item("乙", "第二份正文")
        archive = self._archive("存档 A")
        result = self.service.export([archive], self.out / "包.zip")

        info = self.service.inspect(result.path)

        self.assertEqual(info.names, ("存档 A",))
        self.assertEqual(info.entries, 2)
        self.assertEqual(info.files, 2)
        self.assertEqual(len(info.created_at), 19)
        self.assertIn("包里有 1 份存档、共 2 个条目", info.summary())
        self.assertFalse(info.is_empty)

    def test_inspect_rejects_non_zip(self) -> None:
        bad = self.out / "文本.zip"
        bad.write_text("这不是压缩包", encoding="utf-8")
        with self.assertRaises(BundleError) as ctx:
            self.service.inspect(bad)
        self.assertIn("这不是一个压缩包", str(ctx.exception))

    # ------------------------------------------------------------------ 导入
    def test_round_trip_renames_duplicates(self) -> None:
        item = self._item("甲")
        archive = self._archive("存档 A")
        result = self.service.export([archive], self.out / "包.zip")

        first = self.service.import_bundle(result.path)

        self.assertEqual(first.archives, 1)
        self.assertEqual(first.entries, 1)
        self.assertEqual(first.restored, 1)
        self.assertEqual(first.names, ("存档 A（导入）",))
        self.assertTrue(self.store.content_available(item.checksum))
        fresh = next(
            archive for archive in self.archives.latest(limit=5) if archive.name == "存档 A（导入）"
        )
        self.assertEqual(fresh.item_count, 1)
        self.assertEqual([entry.checksum for entry in fresh.entries], [item.checksum])
        self.assertEqual(len(self.archives.latest(limit=5)), 2)

        second = self.service.import_bundle(result.path)
        self.assertEqual(second.names, ("存档 A（导入 2）",))

    def test_import_writes_back_missing_content(self) -> None:
        data = b"archive-bundle-payload-0123456789"
        checksum = hashlib.sha256(data).hexdigest()
        path = self._hand_bundle(
            "手作.zip",
            [self._row(entries=[self._entry(checksum=checksum, size=len(data))])],
            {checksum: data},
        )
        self.assertFalse(self.store.content_available(checksum))

        result = self.service.import_bundle(path)

        self.assertEqual((result.archives, result.restored, result.missing), (1, 1, 0))
        self.assertTrue(self.store.content_available(checksum))
        archive = self.archives.latest(limit=1)[0]
        self.assertEqual(archive.new_blobs, 1)
        self.assertEqual(archive.total_size, len(data))
        self.assertEqual(archive.entries[0].checksum, checksum)

    def test_import_checksum_mismatch_counts_missing(self) -> None:
        declared = hashlib.sha256("预期的内容".encode("utf-8")).hexdigest()
        path = self._hand_bundle(
            "坏内容.zip",
            [self._row(entries=[self._entry(checksum=declared, size=5)])],
            {declared: "实际内容不同".encode("utf-8")},
        )

        result = self.service.import_bundle(path)

        self.assertEqual((result.restored, result.missing), (0, 1))
        self.assertFalse(self.store.content_available(declared))
        self.assertEqual(len(self.archives.latest(limit=1)), 1)

    def test_import_missing_member_counts_missing(self) -> None:
        checksum = hashlib.sha256("包外内容".encode("utf-8")).hexdigest()
        path = self._hand_bundle(
            "缺成员.zip", [self._row(entries=[self._entry(checksum=checksum, size=4)])]
        )

        result = self.service.import_bundle(path)

        self.assertEqual((result.restored, result.missing), (0, 1))
        self.assertEqual(result.archives, 1)

    def test_import_entry_without_checksum_is_kept(self) -> None:
        path = self._hand_bundle("纯文字.zip", [self._row(entries=[self._entry(checksum="")])])

        result = self.service.import_bundle(path)

        self.assertEqual((result.restored, result.missing), (0, 0))
        archive = self.archives.latest(limit=1)[0]
        self.assertEqual(archive.entries[0].content, "正文")

    def test_import_selected_keys(self) -> None:
        one = hashlib.sha256(b"one").hexdigest()
        two = hashlib.sha256(b"two").hexdigest()
        path = self._hand_bundle(
            "两份.zip",
            [
                self._row(key="archive-1", name="甲", entries=[self._entry(checksum=one, size=3)]),
                self._row(key="archive-2", name="乙", entries=[self._entry(checksum=two, size=3)]),
            ],
            {one: b"one", two: b"two"},
        )

        result = self.service.import_bundle(path, keys=["archive-2"])

        self.assertEqual(result.names, ("乙",))
        self.assertEqual(result.archives, 1)
        self.assertTrue(self.store.content_available(two))
        # 没选的包连内容都不写回
        self.assertFalse(self.store.content_available(one))

        with self.assertRaises(BundleError) as ctx:
            self.service.import_bundle(path, keys=["不存在"])
        self.assertIn("没有选中要导入的存档", str(ctx.exception))

    def test_import_restores_user_and_time(self) -> None:
        path = self._hand_bundle(
            "用户与时间.zip",
            [
                self._row(
                    created_at="2026-10-01 09:30:00",
                    entries=[
                        self._entry(name="有主", user_name=self.user.name, category="影视/电影"),
                        self._entry(name="无主", user_name="查无此人"),
                    ],
                )
            ],
        )

        self.service.import_bundle(path)

        archive = self.archives.latest(limit=1)[0]
        self.assertEqual(archive.created_at.strftime("%Y-%m-%d %H:%M:%S"), "2026-10-01 09:30:00")
        by_name = {entry.name: entry for entry in archive.entries}
        self.assertEqual(by_name["有主"].user_id, self.user.id)
        self.assertEqual(by_name["有主"].category, "影视/电影")
        self.assertIsNone(by_name["无主"].user_id)
        self.assertEqual(by_name["无主"].user_name, "查无此人")

    def test_import_bad_packages(self) -> None:
        not_zip = self.out / "甲.zip"
        not_zip.write_text("不是压缩包", encoding="utf-8")
        with self.assertRaises(BundleError) as ctx:
            self.service.import_bundle(not_zip)
        self.assertIn("这不是一个压缩包", str(ctx.exception))

        no_manifest = self.out / "乙.zip"
        with zipfile.ZipFile(no_manifest, "w") as archive:
            archive.writestr("别的.txt", "x")
        with self.assertRaises(BundleError) as ctx:
            self.service.import_bundle(no_manifest)
        self.assertIn("这不是存档包", str(ctx.exception))

        wrong_format = self.out / "丙.zip"
        with zipfile.ZipFile(wrong_format, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({"format": "别的东西"}))
        with self.assertRaises(BundleError) as ctx:
            self.service.import_bundle(wrong_format)
        self.assertIn("这不是本程序的存档包", str(ctx.exception))

        broken_json = self.out / "丁.zip"
        with zipfile.ZipFile(broken_json, "w") as archive:
            archive.writestr(MANIFEST_NAME, "{不是 JSON")
        with self.assertRaises(BundleError) as ctx:
            self.service.import_bundle(broken_json)
        self.assertIn("清单读不出来", str(ctx.exception))

    def test_import_ignores_bad_checksum_in_manifest(self) -> None:
        """清单里的校验和不成形时不认它，条目照样重建，但不当内容看。"""
        path = self._hand_bundle(
            "怪校验和.zip",
            [self._row(entries=[self._entry(checksum="../../etc/passwd")])],
            {"../../etc/passwd": b"x"},
        )

        result = self.service.import_bundle(path)

        self.assertEqual((result.restored, result.missing), (0, 0))
        archive = self.archives.latest(limit=1)[0]
        self.assertEqual(archive.entries[0].checksum, "../../etc/passwd")

    def test_own_session_commits_and_closes(self) -> None:
        data = b"own-session-payload"
        checksum = hashlib.sha256(data).hexdigest()
        path = self._hand_bundle(
            "自开会话.zip",
            [self._row(entries=[self._entry(checksum=checksum, size=len(data))])],
            {checksum: data},
        )
        other = ContentStore(self.session)
        self.assertFalse(other.content_available(checksum))

        with ArchiveBundleService() as service:
            result = service.import_bundle(path)

        self.assertEqual(result.archives, 1)
        self.assertTrue(ContentStore(self.session).content_available(checksum))


if __name__ == "__main__":
    unittest.main()
