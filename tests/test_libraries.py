import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from app.core import paths
from app.core.config import config
from app.db.models import Library
from app.repositories import ItemFilter, ItemRepository
from app.services import LibraryService, TaxonomyService, sanitize_dir_name
from tests.harness import IsolatedCase


class LibraryCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.service = LibraryService(self.session)
        self.user = self.current_user()

    def test_default_library_is_stable(self):
        library = self.service.ensure_default()
        self.assertTrue(library.is_default)
        self.assertEqual(self.service.default().id, library.id)
        self.assertEqual(self.service.ensure_default().id, library.id)

    def test_ensure_layout_creates_global_and_user_dirs(self):
        library = self.service.ensure_default()
        self.assertTrue(self.service.global_dir(library).is_dir())
        self.assertTrue(self.service.store_dir(library).is_dir())
        self.assertTrue(self.service.cover_dir(library).is_dir())
        self.assertTrue(self.service.backup_dir(library).is_dir())
        self.assertTrue(self.service.meta_dir(library).is_dir())
        self.assertTrue(self.service.user_dir(library, self.user).is_dir())

    def test_extra_libraries_are_absorbed_into_default(self):
        library = self.service.ensure_default()
        item = self.importer().import_text("笔记", "内容")
        self.session.commit()
        extra = Library(name="素材库", path=str(self.root / "lib-a"))
        self.session.add(extra)
        self.session.flush()
        item.library_id = extra.id
        self.session.flush()

        absorbed = self.service.ensure_default()
        self.session.commit()
        self.assertEqual([row.id for row in self.service.list_all()], [absorbed.id])
        self.assertEqual(ItemRepository(self.session).query(ItemFilter())[0].library_id, absorbed.id)

    def test_set_path_moves_library_and_rejects_non_empty(self):
        library = self.service.ensure_default()
        self.importer().import_text("笔记", "内容")
        self.session.commit()
        old_root = Path(library.path)
        target = self.root / "moved-library"

        self.service.set_path(target)
        self.session.commit()
        self.assertEqual(Path(library.path), target)
        self.assertTrue(target.is_dir())
        self.assertFalse(old_root.exists())

        blocked = self.root / "blocked-library"
        blocked.mkdir(parents=True, exist_ok=True)
        (blocked / "占位.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.set_path(blocked)
        config.set(config.libraryPath, str(paths.DEFAULT_LIBRARY_DIR))

    def test_unique_rel_path_avoids_overwrite(self):
        library = self.service.ensure_default()
        target = self.service.user_dir(library, self.user) / "note.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("已存在", encoding="utf-8")
        rel = self.service.unique_rel_path(library, None, "note.txt", user_id=self.user.id)
        self.assertNotEqual(rel, "note.txt")
        self.assertEqual(Path(rel).parts[0], self.user.name)
        self.assertFalse((Path(library.path) / rel).exists())

    def test_category_chain_and_directory(self):
        taxonomy = TaxonomyService(self.session)
        parent = taxonomy.create_category("学习资料二", user_id=self.user.id)
        child = taxonomy.create_category("线性代数", parent_id=parent.id, user_id=self.user.id)
        library = self.service.ensure_default()
        self.assertEqual(self.service.category_chain(child.id), ["学习资料二", "线性代数"])
        self.assertEqual(
            self.service.directory_for(library, child.id, user_id=self.user.id),
            Path(library.path) / self.user.name / "学习资料二" / "线性代数",
        )
        self.assertEqual(
            self.service.directory_for(library, child.id),
            Path(library.path) / paths.UNASSIGNED_DIR_NAME / "学习资料二" / "线性代数",
        )

    def test_abs_path_points_into_library(self):
        item = self.importer().import_text("笔记", "内容")
        self.session.commit()
        library = self.service.ensure_default()
        self.assertEqual(Path(self.service.abs_path(item)), Path(library.path) / item.file_path)

    def test_item_count(self):
        library = self.service.ensure_default()
        self.importer().import_text("笔记一", "内容一")
        self.importer().import_text("笔记二", "内容二")
        self.session.commit()
        self.assertEqual(self.service.item_count(library), 2)

    def test_scan_registers_files_under_user_folder(self):
        library = self.service.ensure_default()
        user_root = self.service.user_dir(library, self.user)
        (user_root / "图片素材").mkdir(parents=True, exist_ok=True)
        (user_root / "学习资料" / "深").mkdir(parents=True, exist_ok=True)
        (user_root / "root.txt").write_text("根文件", encoding="utf-8")
        (user_root / "图片素材" / "pic.png").write_bytes(
            self.corpus.by_name("photo_gradient.png").read_bytes()
        )
        (user_root / "学习资料" / "深" / "笔记.md").write_text("# 笔记", encoding="utf-8")
        (self.service.meta_dir(library) / "meta.json").write_text("{}", encoding="utf-8")

        result = self.service.scan(library)
        self.session.commit()
        self.assertEqual(result.failed, [])
        self.assertEqual(result.added_count, 3)
        self.assertEqual(self.service.item_count(library), 3)
        rows = ItemRepository(self.session).query(ItemFilter())
        self.assertEqual({row.user_id for row in rows}, {self.user.id})

        second = self.service.scan(library)
        self.session.commit()
        self.assertEqual(second.added_count, 0)
        registered = ItemRepository(self.session).library_paths(library.id)
        self.assertNotIn(f"{paths.GLOBAL_DIR_NAME}/.datamanager/meta.json", registered)

    def test_scan_matches_category_by_folder_name(self):
        library = self.service.ensure_default()
        root = self.service.user_dir(library, self.user)
        (root / "图片素材").mkdir(parents=True, exist_ok=True)
        (root / "图片素材" / "pic.png").write_bytes(
            self.corpus.by_name("photo_gradient.png").read_bytes()
        )
        self.service.scan(library)
        self.session.commit()
        rows = ItemRepository(self.session).query(ItemFilter(library_ids={library.id}))
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0].category)
        self.assertEqual(rows[0].category.name, "图片素材")

    def test_scan_skips_global_and_unowned_files(self):
        library = self.service.ensure_default()
        globals_dir = self.service.global_dir(library)
        (globals_dir / "全局说明.txt").write_text("x", encoding="utf-8")
        stranger = Path(library.path) / "陌生人"
        stranger.mkdir(parents=True, exist_ok=True)
        (stranger / "神秘.txt").write_text("x", encoding="utf-8")

        result = self.service.scan(library)
        self.session.commit()
        self.assertEqual(result.added_count, 0)
        # 全局文件夹下的文件被静默跳过，只有用户名无法识别的文件会记入 skipped。
        self.assertEqual(result.skipped, ["陌生人/神秘.txt"])
        registered = ItemRepository(self.session).library_paths(library.id)
        self.assertEqual(registered, set())

    def test_sanitize_dir_name_strips_illegal_characters(self):
        cleaned = sanitize_dir_name('a<b>c:d"e/f\\g|h?i*j')
        self.assertTrue(cleaned)
        for char in '<>:"/\\|?*':
            self.assertNotIn(char, cleaned)
