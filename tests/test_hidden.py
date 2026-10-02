"""隐藏数据（`.hiddens/`）的用例：搬动、回搬、清理、扫描与过滤。

隐藏位对应磁盘上的 `<分类目录>/.hiddens/`：勾选隐藏时文件搬进去，取消时搬回来，
目录空了就删掉；扫描时 `.hiddens` 里的文件登记为隐藏项，且分类链在 `.hiddens` 处截断。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.core import paths
from app.repositories.items import ItemFilter, ItemRepository
from app.services import LibraryService

from tests.harness import IsolatedCase


class HiddenDataCase(IsolatedCase):
    """隐藏位的物理流转。"""

    def setUp(self):
        super().setUp()
        self.library = self.default_library()
        self.service = LibraryService(self.session)
        self.items = ItemRepository(self.session)

    def _import_text(self, name: str = "隐藏用例", *, content: str | None = None):
        item = self.importer(library=self.library).import_text(
            name, content or f"{name} 的正文", user_id=self.current_user().id
        )
        self.session.flush()
        return item

    def test_hidden_dir_is_dot_hiddens_under_category(self):
        item = self._import_text()
        hidden = self.service.hidden_dir(self.library, item.category_id, item.user_id)
        self.assertEqual(hidden.name, paths.HIDDEN_DIR_NAME)
        self.assertEqual(
            hidden, self.service.directory_for(self.library, item.category_id, item.user_id) / ".hiddens"
        )

    def test_hiding_moves_file_into_hiddens(self):
        item = self._import_text()
        original = self.service.abs_path(item)
        self.assertTrue(original.is_file())

        self.service.set_item_hidden(item, True)

        self.assertTrue(item.is_hidden)
        self.assertIn(f"/{paths.HIDDEN_DIR_NAME}/", item.file_path)
        moved = self.service.abs_path(item)
        self.assertTrue(moved.is_file(), moved)
        self.assertEqual(moved.parent.name, paths.HIDDEN_DIR_NAME)
        self.assertFalse(original.exists())

    def test_unhiding_moves_file_back_and_prunes_empty_dir(self):
        item = self._import_text()
        self.service.set_item_hidden(item, True)
        hidden = self.service.abs_path(item).parent

        self.service.set_item_hidden(item, False)

        self.assertFalse(item.is_hidden)
        self.assertNotIn(paths.HIDDEN_DIR_NAME, item.file_path)
        restored = self.service.abs_path(item)
        self.assertTrue(restored.is_file(), restored)
        self.assertEqual(restored.parent.name, self.service.category_chain(item.category_id)[-1])
        self.assertFalse(hidden.exists(), "空的隐藏目录应当被清理")

    def test_hiding_same_name_twice_keeps_both_files(self):
        first = self._import_text("同名文件", content="第一份")
        second = self._import_text("同名文件", content="第二份")
        self.assertNotEqual(first.file_path, second.file_path)

        self.service.set_item_hidden(first, True)
        self.service.set_item_hidden(second, True)

        paths_on_disk = {self.service.abs_path(item) for item in (first, second)}
        self.assertEqual(len(paths_on_disk), 2)
        for path in paths_on_disk:
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.parent.name, paths.HIDDEN_DIR_NAME)

    def test_unhiding_one_of_two_keeps_hidden_dir(self):
        first = self._import_text("甲")
        second = self._import_text("乙")
        self.service.set_item_hidden(first, True)
        self.service.set_item_hidden(second, True)
        hidden = self.service.abs_path(second).parent

        self.service.set_item_hidden(first, False)

        self.assertTrue(hidden.is_dir(), "还有隐藏项时不应删除隐藏目录")
        self.assertTrue(self.service.abs_path(second).is_file())

    def test_prune_ignores_regular_directories(self):
        item = self._import_text()
        directory = self.service.directory_for(self.library, item.category_id, item.user_id)
        paths.make_dir(directory)

        self.service._prune_empty_hidden(directory)

        self.assertTrue(directory.is_dir())

    def test_filter_excludes_hidden_items_by_default(self):
        hidden_item = self._import_text("隐藏项")
        visible_item = self._import_text("可见项")
        self.service.set_item_hidden(hidden_item, True)
        self.session.flush()
        library_ids = {self.library.id}

        default = self.items.query(ItemFilter(library_ids=library_ids))
        self.assertEqual([item.id for item in default], [visible_item.id])

        with_hidden = self.items.query(ItemFilter(library_ids=library_ids, include_hidden=True))
        self.assertEqual({item.id for item in with_hidden}, {hidden_item.id, visible_item.id})

        only_hidden = self.items.query(ItemFilter(library_ids=library_ids, only_hidden=True))
        self.assertEqual([item.id for item in only_hidden], [hidden_item.id])

    def test_scan_registers_hidden_files_and_stops_at_hiddens(self):
        reference = self._import_text("分类参照")
        directory = self.service.directory_for(self.library, reference.category_id, reference.user_id)
        hidden = self.service.hidden_dir(self.library, reference.category_id, reference.user_id)
        paths.make_dir(hidden)
        manual = hidden / "手工放进来的.txt"
        manual.write_text("手工内容", encoding="utf-8")

        result = self.service.scan(self.library)

        self.assertEqual(len(result.failed), 0, result.failed)
        rel = manual.relative_to(Path(self.library.path)).as_posix()
        self.assertIn(rel, {item.file_path for item in result.added})
        registered = self.items.by_library_path(self.library.id, rel)
        self.assertIsNotNone(registered)
        self.assertTrue(registered.is_hidden)
        self.assertEqual(registered.category_id, reference.category_id)
        self.assertEqual(registered.user_id, reference.user_id)

    def test_scan_skips_dot_directories_other_than_hiddens(self):
        reference = self._import_text("参照")
        directory = self.service.directory_for(self.library, reference.category_id, reference.user_id)
        other = directory / ".cache"
        paths.make_dir(other)
        (other / "忽略我.txt").write_text("不该登记", encoding="utf-8")

        result = self.service.scan(self.library)

        self.assertNotIn("忽略我.txt", " ".join(item.file_path for item in result.added))


if __name__ == "__main__":
    unittest.main()
