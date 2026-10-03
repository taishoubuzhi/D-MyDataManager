"""数据改名用例：改显示名时库内文件一起改名，重名加序号，文件不在库里也不报错。"""

from __future__ import annotations

from pathlib import Path

from app.services import ItemService, LibraryService

from tests.harness import IsolatedCase


class ItemRenameCase(IsolatedCase):
    """改名与库内文件的同步。"""

    def setUp(self):
        super().setUp()
        self.library = self.default_library()
        self.libraries = LibraryService(self.session)
        self.items = ItemService(self.session)

    def _import_text(self, name: str, content: str | None = None):
        item = self.importer(library=self.library).import_text(
            name, content or f"{name} 的正文", user_id=self.current_user().id
        )
        self.session.flush()
        return item

    def test_rename_moves_library_file(self):
        item = self._import_text("旧名")
        before = self.libraries.abs_path(item)
        self.assertEqual(before.name, "旧名.txt")

        self.items.update(item, name="新名")
        self.session.flush()

        after = self.libraries.abs_path(item)
        self.assertEqual(after.name, "新名.txt")
        self.assertTrue(after.is_file(), after)
        self.assertFalse(before.exists())
        self.assertEqual(item.name, "新名")
        self.assertEqual(item.file_path, after.relative_to(Path(self.library.path)).as_posix())

    def test_rename_keeps_single_suffix(self):
        item = self._import_text("旧名")
        self.items.update(item, name="报告.txt")
        self.assertEqual(self.libraries.abs_path(item).name, "报告.txt")

    def test_rename_avoids_overwriting_existing_file(self):
        first = self._import_text("甲")
        second = self._import_text("乙", content="乙的正文和甲不一样")
        self.items.update(second, name="甲")
        self.assertEqual(self.libraries.abs_path(second).name, "甲_1.txt")
        self.assertTrue(self.libraries.abs_path(first).is_file())

    def test_rename_without_library_file_only_changes_name(self):
        item = self._import_text("旧名")
        self.libraries.abs_path(item).unlink()
        self.items.update(item, name="新名")
        self.assertEqual(item.name, "新名")
        self.assertFalse(self.libraries.abs_path(item).exists())
