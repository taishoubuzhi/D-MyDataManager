"""分类与库目录的双向同步：分类就是目录，「未分类」= 用户名文件夹根目录。

覆盖两件事：`TaxonomyService` 的分类增 / 改 / 移 / 删要落到目录上；`category_sync.reconcile_categories`
要把磁盘上的目录结构（含新目录、被删掉的目录、旧「未分类」目录、找不到位置的文件）反推回分类。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.services import ImportService, LibraryService, TaxonomyService, UserService
from app.services.category_sync import CATEGORY_LAYOUT_MARKER, reconcile_categories

from tests.harness import IsolatedCase


class CategoryFolderCase(IsolatedCase):
    """分类 ↔ 目录：分类树是数据文件目录结构的可视化。"""

    def setUp(self) -> None:
        super().setUp()
        self.libraries = LibraryService(self.session)
        self.library = self.libraries.ensure_default()
        self.taxonomy = TaxonomyService(self.session)
        self.user = UserService(self.session).current()
        self.user_dir = self.libraries.user_dir(self.library, self.user)

    # ---------------------------------------------------------------- 工具
    def category(self, name: str):
        return self.taxonomy.categories.by_name(name, None, self.user.id)

    def uncategorized(self):
        return self.taxonomy.uncategorized_category(self.user.id)

    def import_text(self, name: str, category_id: int | None = None):
        return ImportService(self.session).import_text(name, "正文内容", category_id=category_id)

    def import_file(self, *parts: str, category_id: int | None = None):
        source = self.root / "source"
        source.mkdir(parents=True, exist_ok=True)
        path = source.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("内容" * 20, encoding="utf-8")
        return ImportService(self.session).import_files([path], category_id=category_id)

    def rel(self, item) -> Path:
        return self.user_dir.parent / item.file_path

    # ---------------------------------------------------------------- 未分类
    def test_uncategorized_directory_is_the_user_folder(self):
        """「未分类」没有自己的目录：它就是用户名文件夹本身。"""
        category = self.uncategorized()
        self.assertEqual(self.libraries.category_chain(category.id), [])
        self.assertEqual(self.libraries.directory_for(self.library, category.id, self.user.id), self.user_dir)

    def test_uncategorized_item_lands_in_user_root(self):
        item = self.import_text("根目录笔记", self.uncategorized().id)
        self.assertEqual(Path(item.file_path).parent.as_posix(), self.libraries.dir_name_of(self.user.name))
        self.assertTrue((self.user_dir / Path(item.file_path).name).is_file())

    # ---------------------------------------------------------------- 分类 → 目录
    def test_create_category_creates_directory(self):
        category = self.taxonomy.create_category("项目", user_id=self.user.id)
        self.assertTrue((self.user_dir / "项目").is_dir())
        self.assertTrue(self.libraries.category_chain(category.id) == ["项目"])

    def test_rename_category_renames_directory_and_item_paths(self):
        category = self.taxonomy.create_category("项目", user_id=self.user.id)
        result = self.import_file("方案.txt", category_id=category.id)
        item = result.added[0]
        self.assertTrue((self.user_dir / "项目" / "方案.txt").is_file())

        self.assertTrue(self.taxonomy.rename_category(category, "归档"))
        self.assertFalse((self.user_dir / "项目").exists())
        self.assertTrue((self.user_dir / "归档" / "方案.txt").is_file())
        self.assertEqual(Path(item.file_path).parts[:2], (self.libraries.dir_name_of(self.user.name), "归档"))

    def test_move_category_moves_directory_tree(self):
        parent = self.taxonomy.create_category("甲", user_id=self.user.id)
        child = self.taxonomy.create_category("乙", parent_id=parent.id, user_id=self.user.id)
        result = self.import_file("说明.txt", category_id=child.id)
        item = result.added[0]
        self.assertTrue((self.user_dir / "甲" / "乙" / "说明.txt").is_file())

        self.assertTrue(self.taxonomy.move_category(child, None))
        self.assertFalse((self.user_dir / "甲" / "乙").exists())
        self.assertTrue((self.user_dir / "乙" / "说明.txt").is_file())
        self.assertEqual(Path(item.file_path).parts[1], "乙")

    def test_delete_category_moves_files_to_user_root(self):
        category = self.taxonomy.create_category("临时", user_id=self.user.id)
        result = self.import_file("草稿.txt", category_id=category.id)
        item = result.added[0]
        self.assertTrue((self.user_dir / "临时" / "草稿.txt").is_file())

        removed = self.taxonomy.delete_category(category)
        self.assertEqual(removed, 1)
        self.assertFalse((self.user_dir / "临时").exists())
        self.assertTrue((self.user_dir / "草稿.txt").is_file())
        self.assertEqual(item.category_id, self.uncategorized().id)

    # ---------------------------------------------------------------- 目录 → 分类
    def test_reconcile_turns_directories_into_categories(self):
        (self.user_dir / "新目录" / "子目录").mkdir(parents=True, exist_ok=True)
        reconcile_categories(self.session)
        self.assertIsNotNone(self.category("新目录"))
        child = self.taxonomy.categories.by_name("子目录", self.category("新目录").id, self.user.id)
        self.assertIsNotNone(child)

    def test_reconcile_folds_legacy_uncategorized_directory(self):
        legacy = self.user_dir / "未分类"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "旧笔记.txt").write_text("旧内容", encoding="utf-8")
        reconcile_categories(self.session)
        self.assertFalse(legacy.exists())
        self.assertTrue((self.user_dir / "旧笔记.txt").is_file())

    def test_reconcile_recategorizes_item_from_its_path(self):
        self.libraries.ensure_category_dirs(self.library, self.user.id)
        target = self.user_dir / "学习资料" / "Python"
        target.mkdir(parents=True, exist_ok=True)
        source = self.root / "外部.txt"
        source.write_text("外部内容", encoding="utf-8")
        result = ImportService(self.session).import_files([source], category_id=self.uncategorized().id)
        item = result.added[0]
        moved = target / Path(item.file_path).name
        (self.user_dir.parent / item.file_path).replace(moved)
        item.file_path = moved.relative_to(self.user_dir.parent).as_posix()
        self.session.flush()

        reconcile_categories(self.session)
        python = self.taxonomy.categories.by_name("Python", self.category("学习资料").id, self.user.id)
        self.assertIsNotNone(python)
        self.assertEqual(item.category_id, python.id)

    def test_reconcile_prunes_category_whose_directory_is_gone(self):
        category = self.taxonomy.create_category("会消失", user_id=self.user.id)
        # 先跑一次写下游标（首次运行不做清理），再删掉目录模拟用户在资源管理器里删除
        reconcile_categories(self.session)
        marker = self.libraries.meta_dir(self.library) / CATEGORY_LAYOUT_MARKER
        self.assertTrue(marker.is_file())
        (self.user_dir / "会消失").rmdir()

        stats = reconcile_categories(self.session)
        self.assertEqual(stats["pruned"], 1)
        self.assertIsNone(self.category("会消失"))
        self.assertIsNone(self.taxonomy.categories.get(category.id))

    def test_reconcile_heals_category_directory_with_items(self):
        category = self.taxonomy.create_category("有数据", user_id=self.user.id)
        self.import_file("数据.txt", category_id=category.id)
        self.session.flush()
        # 目录被外部删掉，但分类还有数据：同步时要把目录补回来
        shutil.rmtree(self.user_dir / "有数据")
        self.assertFalse((self.user_dir / "有数据").is_dir())

        reconcile_categories(self.session)
        self.assertTrue((self.user_dir / "有数据").is_dir())
