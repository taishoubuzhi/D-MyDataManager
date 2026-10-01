import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from app.db.models import DataType
from app.repositories import BlobRepository, ItemFilter, ItemRepository
from app.services import (
    ItemService,
    TaxonomyService,
    overview,
    recent,
    sha256_of_bytes,
    type_breakdown,
)
from tests.harness import IsolatedCase


class SearchCase(IsolatedCase):
    """搜索：中文长词走 FTS，短词退化成 LIKE，名称与正文都能命中。"""

    def setUp(self):
        super().setUp()
        self.repo = ItemRepository(self.session)
        self.service = self.importer()
        self.a = self.service.import_text(
            "机器学习笔记", "梯度下降与反向传播的推导过程，含公式。", keywords=["算法", "数学"], tags=["重要"],
        )
        self.b = self.service.import_text("weekly-report", "quarterly revenue growth summary for the team")
        self.c = self.service.import_text("红烧肉菜谱", "红烧肉的做法很简单，先焯水再小火慢炖。")
        self.session.commit()

    def test_search_by_chinese_content(self):
        self.assertIn(self.a.id, self.repo.search_ids("机器学习"))

    def test_search_requires_all_words(self):
        ids = self.repo.search_ids("梯度 反向")
        self.assertIn(self.a.id, ids)
        self.assertNotIn(self.b.id, ids)

    def test_short_term_falls_back_to_like(self):
        self.assertEqual(self.repo.search_ids("红烧"), {self.c.id})

    def test_search_english_content(self):
        self.assertIn(self.b.id, self.repo.search_ids("quarterly revenue"))

    def test_empty_and_unknown_query(self):
        self.assertEqual(self.repo.search_ids(""), set())
        self.assertEqual(self.repo.search_ids("   "), set())
        self.assertEqual(self.repo.search_ids("zzz-not-here-9999"), set())

    def test_search_hits_keywords(self):
        self.assertEqual(self.repo.search_ids("算法"), {self.a.id})

    def test_text_filter_matches_keywords(self):
        rows = self.repo.query(ItemFilter(text="算法"))
        self.assertEqual([item.id for item in rows], [self.a.id])

    def test_search_ids_feed_text_filter(self):
        flt = ItemFilter(text="机器学习", text_ids=self.repo.search_ids("机器学习"))
        rows = self.repo.query(flt)
        self.assertEqual([item.id for item in rows], [self.a.id])


class FilterCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.repo = ItemRepository(self.session)
        self.items_service = ItemService(self.session)
        service = self.importer()
        self.image = service.import_file(self.corpus.by_name("photo_gradient.png"), tags=["重要"])
        self.video = service.import_file(self.corpus.by_name("clip.mp4"))
        self.text = service.import_text("笔记", "机器学习", keywords=["算法"], tags=["收藏"])
        self.session.commit()

    def test_type_filter(self):
        self.assertEqual(self.repo.count(ItemFilter(types={DataType.IMAGE})), 1)
        self.assertEqual(self.repo.count(ItemFilter(types={DataType.IMAGE, DataType.VIDEO})), 2)
        self.assertEqual(self.repo.count(ItemFilter(types={DataType.AUDIO})), 0)

    def test_tag_filter_is_union(self):
        self.assertEqual(self.repo.count(ItemFilter(tags={"重要"})), 1)
        self.assertEqual(self.repo.count(ItemFilter(tags={"重要", "收藏"})), 2)

    def test_keyword_filter(self):
        rows = self.repo.query(ItemFilter(keywords={"算法"}))
        self.assertEqual([item.id for item in rows], [self.text.id])

    def test_size_filter(self):
        threshold = self.video.size
        self.assertEqual(self.repo.count(ItemFilter(max_size=threshold - 1)), 1)
        self.assertEqual(self.repo.count(ItemFilter(min_size=threshold)), 2)

    def test_hidden_filtering(self):
        self.items_service.set_hidden([self.text], True)
        self.session.commit()
        self.assertEqual(self.repo.count(ItemFilter()), 2)
        self.assertEqual(self.repo.count(ItemFilter(only_hidden=True)), 1)
        self.assertEqual(self.repo.count(ItemFilter(include_hidden=True)), 3)

    def test_deleted_filtering(self):
        self.items_service.delete([self.video])
        self.session.commit()
        self.assertEqual(self.repo.count(ItemFilter()), 2)
        self.assertEqual(self.repo.count(ItemFilter(only_deleted=True)), 1)
        self.assertEqual(self.repo.count(ItemFilter(include_deleted=True)), 3)


class PagingCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.repo = ItemRepository(self.session)
        service = self.importer()
        for index in range(7):
            service.import_text(f"条目{index}", "x" * (10 + index * 7))
        self.session.commit()

    def test_count_and_pages_do_not_overlap(self):
        flt = ItemFilter()
        self.assertEqual(self.repo.count(flt), 7)
        first = self.repo.query(flt, limit=3)
        second = self.repo.query(flt, limit=3, offset=3)
        third = self.repo.query(flt, limit=3, offset=6)
        self.assertEqual([len(first), len(second), len(third)], [3, 3, 1])
        ids = {item.id for item in [*first, *second, *third]}
        self.assertEqual(len(ids), 7)

    def test_sort_by_size(self):
        ascending = [item.size for item in self.repo.query(ItemFilter(sort_by="size", descending=False))]
        self.assertEqual(ascending, sorted(ascending))
        descending = [item.size for item in self.repo.query(ItemFilter(sort_by="size"))]
        self.assertEqual(descending, sorted(ascending, reverse=True))


class StatsCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.repo = ItemRepository(self.session)
        service = self.importer()
        service.import_file(self.corpus.by_name("photo_gradient.png"))
        service.import_file(self.corpus.by_name("clip.mp4"))
        service.import_file(self.corpus.by_name("dup_origin.png"))
        service.import_file(self.corpus.by_name("dup_copy.png"))
        self.text = service.import_text("笔记", "机器学习")
        self.session.commit()

    def test_overview_counts(self):
        stats = overview(self.session)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["duplicate_groups"], 1)
        self.assertEqual(stats["duplicate_extra"], 1)
        self.assertIn("IMAGE", stats["by_type"])
        self.assertIn("VIDEO", stats["by_type"])
        self.assertEqual(stats["categories"], 5)
        self.assertEqual(stats["tags"], 3)
        self.assertGreater(stats["total_size"], 0)

    def test_hidden_and_trashed_counts(self):
        items_service = ItemService(self.session)
        items_service.set_hidden([self.text], True)
        self.session.commit()
        self.assertEqual(overview(self.session)["hidden"], 1)
        items_service.delete([self.text])
        self.session.commit()
        self.assertEqual(overview(self.session)["trashed"], 1)

    def test_type_breakdown_and_recent(self):
        rows = type_breakdown(self.session)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["type"], "IMAGE")
        self.assertEqual(rows[0]["count"], 3)
        self.assertGreater(rows[0]["size"], 0)
        self.assertLessEqual(len(recent(self.session, limit=3)), 3)

    def test_item_repo_stats_keys(self):
        stats = self.repo.stats()
        for key in ("total", "total_size", "by_type", "hidden", "trashed", "duplicate_groups", "duplicate_extra"):
            self.assertIn(key, stats)


class UpdateCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.items_service = ItemService(self.session)
        self.service = self.importer()
        self.library = self.default_library()

    def test_rename_and_normalise_keywords(self):
        item = self.service.import_text("旧名字", "内容")
        self.items_service.update(item, name="新名字", keywords=[" 算法 ", "", "   ", "数学"])
        self.session.commit()
        self.assertEqual(item.name, "新名字")
        self.assertEqual(item.keywords, ["算法", "数学"])

    def test_replace_tags(self):
        item = self.service.import_text("笔记", "内容", tags=["重要"])
        self.items_service.update(item, tags=["收藏", "待整理"])
        self.session.commit()
        self.assertEqual(sorted(item.tag_names), ["待整理", "收藏"])

    def test_add_and_remove_tags(self):
        item = self.service.import_text("笔记", "内容")
        self.items_service.add_tags([item], ["临时"])
        self.session.commit()
        self.assertIn("临时", item.tag_names)
        self.items_service.remove_tags([item], ["临时"])
        self.session.commit()
        self.assertNotIn("临时", item.tag_names)

    def test_content_change_updates_file_hash_and_features(self):
        item = self.service.import_text("笔记", "原始内容")
        self.session.commit()
        old_checksum = item.checksum
        new_content = "更新后的内容\n第二行"
        self.items_service.update(item, content=new_content)
        self.session.commit()
        self.assertNotEqual(item.checksum, old_checksum)
        self.assertEqual(item.checksum, sha256_of_bytes(new_content.encode("utf-8")))
        self.assertEqual(item.size, len(new_content.encode("utf-8")))
        self.assertEqual(
            (Path(self.library.path) / item.file_path).read_text(encoding="utf-8"), new_content,
        )
        text_feature = next(f for f in item.features if f.kind == "text")
        self.assertEqual(text_feature.value.get("chars"), len(new_content))

    def test_set_category_moves_file_on_disk(self):
        item = self.service.import_file(self.corpus.by_name("favicon.ico"))
        self.session.commit()
        library = self.default_library()
        old_path = Path(library.path) / item.file_path
        self.assertTrue(old_path.is_file())
        category = TaxonomyService(self.session).create_category("图片素材二", user_id=self.current_user().id)
        self.items_service.set_category([item], category.id)
        self.session.commit()
        self.assertEqual(item.category_id, category.id)
        new_path = Path(library.path) / item.file_path
        self.assertNotEqual(old_path, new_path)
        self.assertTrue(new_path.is_file())
        self.assertFalse(old_path.exists())


class DeleteCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.items_service = ItemService(self.session)
        self.service = self.importer()
        self.repo = ItemRepository(self.session)

    def test_duplicate_map_groups_identical_content(self):
        self.service.import_file(self.corpus.by_name("dup_origin.png"))
        self.service.import_file(self.corpus.by_name("dup_copy.png"))
        self.session.commit()
        groups = self.items_service.duplicate_map()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(next(iter(groups.values()))), 2)

    def test_soft_delete_and_restore(self):
        item = self.service.import_text("笔记", "内容")
        self.session.commit()
        self.items_service.delete([item])
        self.session.commit()
        self.assertTrue(item.is_deleted)
        self.assertIsNotNone(item.deleted_at)
        self.items_service.restore([item])
        self.session.commit()
        self.assertFalse(item.is_deleted)
        self.assertIsNone(item.deleted_at)

    def test_purge_removes_file_and_blob(self):
        item = self.service.import_file(self.corpus.by_name("favicon.ico"))
        self.session.commit()
        library = self.default_library()
        stored = Path(library.path) / item.file_path
        checksum = item.checksum
        self.items_service.purge([item])
        self.session.commit()
        self.assertFalse(stored.exists())
        self.assertEqual(self.repo.by_checksum(checksum), [])

    def test_purge_keeps_blob_used_by_other_item(self):
        first = self.service.import_file(self.corpus.by_name("dup_origin.png"))
        second = self.service.import_file(self.corpus.by_name("dup_copy.png"))
        self.session.commit()
        checksum = first.checksum
        self.assertEqual(first.checksum, second.checksum)
        self.items_service.purge([first])
        self.session.commit()
        blob = BlobRepository(self.session).by_checksum(checksum)
        self.assertIsNotNone(blob)
        self.assertEqual(blob.ref_count, 1)

    def test_purge_trash_clears_trash(self):
        first = self.service.import_text("笔记一", "内容一")
        second = self.service.import_text("笔记二", "内容二")
        self.session.commit()
        self.items_service.delete([first, second])
        self.session.commit()
        self.items_service.purge_trash()
        self.session.commit()
        self.assertEqual(self.repo.count(ItemFilter(include_deleted=True)), 0)
