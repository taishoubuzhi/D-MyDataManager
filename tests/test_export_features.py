import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

from app.core import paths
from app.core.config import cover_dir, store_dir
from app.repositories import BlobRepository
from app.services import ExportService, make_cover, sha256_of
from app.services.feature_service import perceptual_hash, text_stats
from tests.harness import IsolatedCase


class ExportCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.service = ExportService(self.session)
        self.importer_service = self.importer()
        self.text = self.importer_service.import_text(
            "笔记", "导出内容：机器学习", keywords=["算法"], tags=["重要"],
        )
        self.image = self.importer_service.import_file(self.corpus.by_name("photo_gradient.png"))
        self.session.commit()
        self.target = self.root / "exports"

    def test_export_copies_files_and_writes_manifest(self):
        result = self.service.export_items([self.text, self.image], self.target)
        self.assertEqual(result.exported, 2)
        self.assertEqual(result.missing, 0)
        self.assertIsNotNone(result.manifest)
        self.assertTrue(Path(result.manifest).is_file())
        self.assertTrue(Path(result.manifest).name.startswith("清单-"))
        exported = sorted(path.name for path in self.target.iterdir() if path.is_file())
        self.assertEqual(len(exported), 3)
        self.assertEqual((self.target / "笔记.txt").read_text(encoding="utf-8"), self.text.content)
        self.assertEqual(
            (self.target / "photo_gradient.png").read_bytes(),
            self.corpus.by_name("photo_gradient.png").read_bytes(),
        )

    def test_manifest_rows_have_chinese_columns(self):
        rows = self.service.manifest_rows([self.text, self.image])
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            set(rows[0]),
            {"id", "名称", "类型", "分类", "标签", "关键词", "大小", "隐藏", "校验和", "导入时间", "内容或备注"},
        )
        self.assertEqual(rows[0]["名称"], "笔记")
        self.assertEqual(rows[0]["标签"], "重要")
        self.assertEqual(rows[0]["关键词"], "算法")
        self.assertEqual(rows[0]["隐藏"], "否")

    def test_manifest_can_be_json(self):
        path = self.service.write_manifest(
            self.service.manifest_rows([self.text]), self.target / "manifest.json", "json",
        )
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["名称"], "笔记")

    def test_export_without_copy_only_writes_manifest(self):
        result = self.service.export_items([self.text], self.target, copy_files=False)
        self.assertEqual(result.exported, 0)
        self.assertEqual(result.missing, 0)
        self.assertTrue(Path(result.manifest).is_file())
        self.assertEqual([path for path in self.target.iterdir() if path.suffix == ".txt"], [])

    def test_missing_content_is_counted(self):
        blob = BlobRepository(self.session).by_checksum(self.image.checksum)
        (store_dir() / blob.rel_path).unlink()
        (Path(self.default_library().path) / self.image.file_path).unlink()
        result = self.service.export_items([self.image], self.target)
        self.assertEqual(result.exported, 0)
        self.assertEqual(result.missing, 1)

    def test_duplicate_names_stay_unique(self):
        other = self.importer_service.import_text("笔记", "第二份内容")
        self.session.commit()
        result = self.service.export_items([self.text, other], self.target)
        self.assertEqual(result.exported, 2)
        self.assertEqual(len(list(self.target.glob("*.txt"))), 2)


class FeatureCase(IsolatedCase):
    def test_text_and_image_features(self):
        service = self.importer()
        text_item = service.import_text("笔记", "特征测试内容")
        image_item = service.import_file(self.corpus.by_name("photo_gradient.png"))
        self.session.commit()
        self.assertEqual({feature.kind for feature in text_item.features}, {"checksum", "text"})
        text_feature = next(feature for feature in text_item.features if feature.kind == "text")
        self.assertEqual(text_feature.value.get("chars"), len("特征测试内容"))
        self.assertEqual({feature.kind for feature in image_item.features}, {"checksum", "image", "phash"})

    def test_checksum_feature_matches_item(self):
        item = self.importer().import_text("笔记", "内容")
        self.session.commit()
        feature = next(f for f in item.features if f.kind == "checksum")
        self.assertTrue(feature.value)
        self.assertIn(item.checksum, json.dumps(feature.value, ensure_ascii=False))

    def test_perceptual_hash_is_stable(self):
        path = self.corpus.by_name("photo_gradient.png")
        self.assertEqual(perceptual_hash(path), perceptual_hash(path))
        self.assertNotEqual(perceptual_hash(path), perceptual_hash(self.corpus.by_name("screenshot.png")))

    def test_make_cover_writes_into_cover_dir(self):
        source = self.corpus.by_name("photo_gradient.png")
        checksum = sha256_of(source)
        cover = Path(make_cover(source, checksum))
        self.assertTrue(cover.is_file())
        self.assertEqual(cover.parent, cover_dir())
        self.assertIn(checksum, cover.name)

    def test_text_stats_counts_characters_and_lines(self):
        stats = text_stats("第一行\n第二行\n第三行")
        self.assertEqual(stats.get("chars"), 11)
        self.assertEqual(stats.get("lines"), 3)
