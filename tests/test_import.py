import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path

from app.core import paths
from app.core.config import config, store_dir
from app.db.models import DataType, Version, guess_type
from app.repositories import BlobRepository, ItemFilter, ItemRepository
from app.services import ImportService, TaxonomyService, sha256_of, sha256_of_bytes
from tests.harness import IsolatedCase


class ImportByTypeCase(IsolatedCase):
    """按类型导入：类型、大小、校验和、落盘位置与版本记录。"""

    SAMPLES = (
        ("photo_gradient.png", DataType.IMAGE),
        ("clip.mp4", DataType.VIDEO),
        ("tone.wav", DataType.AUDIO),
        ("report.pdf", DataType.DOCUMENT),
        ("data.csv", DataType.SPREADSHEET),
        ("slides.pptx", DataType.PRESENTATION),
        ("bundle.zip", DataType.ARCHIVE),
        ("script.py", DataType.CODE),
        ("学习笔记.txt", DataType.TEXT),
        ("no_extension", DataType.OTHER),
    )

    def test_every_recognised_type_can_be_imported(self):
        service = self.importer()
        library = self.default_library()
        for name, expected in self.SAMPLES:
            source = self.corpus.by_name(name)
            with self.subTest(name=name):
                item = service.import_file(source)
                self.assertIsNotNone(item)
                self.assertIs(item.type, expected)
                self.assertIs(item.type, guess_type(source.name))
                self.assertEqual(item.size, source.stat().st_size)
                self.assertEqual(item.checksum, sha256_of(source))
                self.assertEqual(item.name, source.name)
                self.assertTrue(item.mime)
                self.assertEqual(Path(item.file_path).parts[0], self.current_user().name)
                self.assertEqual(Path(item.file_path).name, source.name)
                self.assertTrue((Path(library.path) / item.file_path).is_file())
                self.assertEqual(Path(library.path, item.file_path).read_bytes(), source.read_bytes())
        self.session.commit()
        self.assertEqual(len(ItemRepository(self.session).query(ItemFilter())), len(self.SAMPLES))

    def test_blob_and_version_are_registered(self):
        service = self.importer()
        source = self.corpus.by_name("report.pdf")
        item = service.import_file(source)
        self.session.commit()
        blob = BlobRepository(self.session).by_checksum(item.checksum)
        self.assertIsNotNone(blob)
        self.assertEqual(blob.ref_count, 1)
        self.assertEqual(blob.size, source.stat().st_size)
        versions = self.session.query(Version).filter(Version.item_id == item.id).all()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].label, "导入")
        self.assertTrue(blob.rel_path)
        self.assertTrue((store_dir() / blob.rel_path).is_file())

    def test_image_items_get_cover_and_features(self):
        service = self.importer()
        item = service.import_file(self.corpus.by_name("photo_gradient.png"))
        self.session.commit()
        kinds = {feature.kind for feature in item.features}
        self.assertIn("checksum", kinds)
        self.assertIn("image", kinds)
        self.assertIn("phash", kinds)
        self.assertTrue(item.cover_path)
        self.assertTrue(Path(item.cover_path).is_file())
        image_feature = next(f for f in item.features if f.kind == "image")
        self.assertEqual((image_feature.value["width"], image_feature.value["height"]), (320, 240))

    def test_text_file_items_get_text_features(self):
        service = self.importer()
        item = service.import_file(self.corpus.by_name("学习笔记.txt"))
        self.session.commit()
        kinds = {feature.kind for feature in item.features}
        self.assertIn("text", kinds)

    def test_import_into_named_category_uses_user_folder(self):
        category = TaxonomyService(self.session).create_category("书", user_id=self.current_user().id)
        service = self.importer()
        library = self.default_library()
        item = service.import_file(self.corpus.by_name("picture.webp"), category_id=category.id)
        self.session.commit()
        self.assertEqual(item.library_id, library.id)
        self.assertEqual(item.category_id, category.id)
        self.assertEqual(Path(item.file_path).parts[:2], (self.current_user().name, "书"))
        self.assertTrue((Path(library.path) / item.file_path).is_file())

    def test_import_files_reports_failures(self):
        service = self.importer()
        missing = self.root / "does-not-exist.bin"
        result = service.import_files([self.corpus.by_name("UPPER.TXT"), missing])
        self.session.commit()
        self.assertEqual(result.added_count, 1)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("does-not-exist.bin", result.failed[0][0])
        self.assertIn("成功 1", result.summary())

    def test_import_directory_is_recursive(self):
        service = self.importer()
        result = service.import_directory(self.corpus.root)
        self.session.commit()
        self.assertEqual(result.failed, [])
        self.assertEqual(result.added_count, len(self.corpus.all_files))
        self.assertIn("成功", result.summary())

    def test_import_directory_without_recursion(self):
        service = self.importer()
        result = service.import_directory(self.corpus.root, recursive=False)
        self.session.commit()
        self.assertEqual(result.added_count, len(self.corpus.files))


class TextImportCase(IsolatedCase):
    def test_import_text_writes_content_and_file(self):
        service = self.importer()
        library = self.default_library()
        content = "第一行\n第二行：机器学习笔记\n"
        item = service.import_text("学习笔记", content, keywords=["算法"], tags=["重要"])
        self.session.commit()
        self.assertIs(item.type, DataType.TEXT)
        self.assertEqual(item.content, content)
        self.assertEqual(item.size, len(content.encode("utf-8")))
        self.assertEqual(item.checksum, sha256_of_bytes(content.encode("utf-8")))
        self.assertEqual(item.keywords, ["算法"])
        self.assertEqual(item.tag_names, ["重要"])
        self.assertEqual(item.file_path, f"{self.current_user().name}/未分类/学习笔记.txt")
        self.assertEqual((Path(library.path) / item.file_path).read_text(encoding="utf-8"), content)

    def test_import_text_uses_timestamp_name_when_blank(self):
        item = self.importer().import_text("", "内容")
        self.session.commit()
        self.assertRegex(item.name, r"^\d{8}-\d{6}(\.txt)?$")
        self.assertEqual(item.file_path, f"{self.current_user().name}/未分类/{item.name}.txt")

    def test_import_text_hidden_flag(self):
        item = self.importer().import_text("私密笔记", "只给自己看", is_hidden=True)
        self.session.commit()
        self.assertTrue(item.is_hidden)


class DuplicatePolicyCase(IsolatedCase):
    def test_rename_policy_keeps_both(self):
        service = self.importer()
        source = self.corpus.by_name("favicon.ico")
        first = service.import_file(source)
        second = service.import_file(source)
        self.session.commit()
        self.assertEqual(first.name, "favicon.ico")
        self.assertEqual(second.name, "favicon_1.ico")
        self.assertNotEqual(first.id, second.id)

    def test_skip_policy_skips_second(self):
        config.set(config.duplicatePolicy, "skip")
        service = self.importer()
        first = service.import_file(self.corpus.by_name("favicon.ico"))
        second = service.import_file(self.corpus.by_name("favicon.ico"))
        self.session.commit()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_overwrite_policy_reuses_item(self):
        config.set(config.duplicatePolicy, "overwrite")
        service = self.importer()
        first = service.import_file(self.corpus.by_name("favicon.ico"))
        second = service.import_file(self.corpus.by_name("favicon.ico"))
        self.session.commit()
        self.assertEqual(first.id, second.id)
        items = ItemRepository(self.session).by_checksum(first.checksum)
        self.assertEqual(len(items), 1)

    def test_name_by_time_configuration(self):
        config.set(config.nameByTime, True)
        item = self.importer().import_file(self.corpus.by_name("UPPER.TXT"))
        self.session.commit()
        self.assertRegex(item.name, r"^\d{8}-\d{6}\.txt$")


class RegisterFileCase(IsolatedCase):
    def test_register_existing_library_file_does_not_copy(self):
        library = self.default_library()
        source = self.corpus.by_name("UPPER.TXT")
        service = self.importer()
        item = service.register_file(
            source, library=library, rel_path="学习资料/UPPER.TXT", tags=["收藏"],
        )
        self.session.commit()
        self.assertTrue(source.is_file())
        self.assertEqual(item.file_path, "学习资料/UPPER.TXT")
        self.assertEqual(item.name, "UPPER.TXT")
        self.assertEqual(item.content, source.read_text(encoding="utf-8"))
        self.assertEqual(item.tag_names, ["收藏"])
        versions = self.session.query(Version).filter(Version.item_id == item.id).all()
        self.assertEqual(versions[0].label, "扫描登记")

    def test_register_truncates_large_text_content(self):
        service = self.importer()
        library = self.default_library()
        item = service.register_file(
            self.corpus.by_name("big_text.txt"), library=library, rel_path="big_text.txt",
        )
        self.session.commit()
        self.assertEqual(item.content, "")
        self.assertGreater(item.size, 512 * 1024)
