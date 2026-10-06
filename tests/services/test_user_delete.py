"""用户删除：不能删除当前用户；被删用户的数据、标签、存档条目与创建者一并转移。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db.models import ArchiveEntry, Category, DataItem, Tag, User
from app.repositories.archives import ArchiveRepository
from app.repositories.items import ItemRepository
from app.services import LibraryService, TaxonomyService, UserService
from app.ui.pages.user_page import card_permissions

from tests.harness import IsolatedCase


class UserDeleteCase(IsolatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.users = UserService(self.session)
        self.taxonomy = TaxonomyService(self.session)
        self.libraries = LibraryService(self.session)
        self.items = ItemRepository(self.session)
        self.archives = ArchiveRepository(self.session)
        self.admin = self.users.default()
        self.member = self.users.create("删除用例成员", "")
        self.session.flush()
        assert self.admin is not None and self.member is not None
        self.admin_id = int(self.admin.id)
        self.member_id = int(self.member.id)
        self.users.set_current(self.admin)

    # ------------------------------------------------------------------ 工具
    def category_named(self, name: str, user_id: int | None) -> Category | None:
        return self.session.scalars(
            select(Category).where(Category.name == name, Category.user_id == user_id)
        ).first()

    def tag_named(self, name: str) -> Tag | None:
        return self.session.scalars(select(Tag).where(Tag.name == name)).first()

    def member_payload(self) -> dict[str, object]:
        """给成员建分类 / 数据项 / 文件目录 / 存档条目 / 两种标签。"""
        library = self.libraries.ensure_default()
        old_dir = self.libraries.dir_name_of(self.member.name)
        directory = Path(library.path) / old_dir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "a.txt").write_text("x", encoding="utf-8")
        category = self.taxonomy.categories.create("子分类", None, user_id=self.member_id)
        item = self.items.create(
            name="成员数据",
            user_id=self.member_id,
            category_id=category.id,
            file_path=f"{old_dir}/a.txt",
        )
        archive = self.archives.create_archive(
            "删除用例存档",
            "",
            [
                {
                    "user_id": self.member_id,
                    "user_name": self.member.name,
                    "name": "成员数据",
                    "category": "子分类",
                }
            ],
            0,
            0,
        )
        personal = self.taxonomy.create_tag("成员个人标签", user_id=self.member_id)
        shared = self.taxonomy.create_tag("成员全局标签", user_id=self.member_id, is_global=True)
        self.session.flush()
        assert personal is not None and shared is not None
        item.tags.extend([personal, shared])  # 只有被数据项引用的标签才会迁移
        return {
            "library": library,
            "old_dir": old_dir,
            "category": category,
            "item": item,
            "entry": self.archives.entries_of(archive)[0],
            "personal": personal,
            "shared": shared,
        }

    # ------------------------------------------------------------------ 用例
    def test_cannot_delete_current_user(self) -> None:
        self.users.set_current(self.member)
        self.assertFalse(self.users.delete(self.member))
        self.assertIsNotNone(self.session.get(User, self.member_id))
        self.assertEqual(self.users.current_id(), self.member_id)
        # 脚本显式放行时才允许删当前用户（删除后切回默认用户）。
        self.assertTrue(self.users.delete(self.member, allow_current=True))
        self.assertIsNone(self.session.get(User, self.member_id))
        self.assertEqual(self.users.current_id(), self.admin_id)

    def test_cannot_delete_default_user(self) -> None:
        self.assertFalse(self.users.delete(self.admin))
        self.assertFalse(self.users.delete(self.admin, allow_current=True))
        self.assertIsNotNone(self.session.get(User, self.admin_id))

    def test_delete_transfers_items_tags_and_entries(self) -> None:
        payload = self.member_payload()
        old_category: Category = payload["category"]  # type: ignore[assignment]
        item: DataItem = payload["item"]  # type: ignore[assignment]
        entry: ArchiveEntry = payload["entry"]  # type: ignore[assignment]
        personal: Tag = payload["personal"]  # type: ignore[assignment]
        shared: Tag = payload["shared"]  # type: ignore[assignment]
        admin_tag = self.taxonomy.create_tag("管理员自己的标签", user_id=self.admin_id)
        self.session.flush()
        self.assertTrue(self.users.delete(self.member))
        self.session.flush()

        self.assertIsNone(self.session.get(User, self.member_id))
        # 数据项：归属改为默认用户，分类映射到「成员名」一级分类下的同名分类，路径前缀重写。
        self.assertEqual(item.user_id, self.admin_id)
        self.assertIsNone(self.session.get(Category, old_category.id))
        mirrored = self.session.get(Category, item.category_id)
        assert mirrored is not None
        self.assertEqual(mirrored.name, "子分类")
        parent = self.session.get(Category, mirrored.parent_id)
        assert parent is not None
        self.assertEqual(parent.name, self.member.name)
        self.assertEqual(parent.user_id, self.admin_id)
        admin_dir = self.libraries.dir_name_of(self.admin.name)
        self.assertEqual(item.file_path, f"{admin_dir}/{payload['old_dir']}/a.txt")
        library = payload["library"]
        self.assertTrue((Path(library.path) / admin_dir / str(payload["old_dir"])).is_dir())
        # 个人标签转到默认用户；它创建的全局标签保持全局，只改创建者。
        self.assertEqual(personal.user_id, self.admin_id)
        self.assertEqual(personal.created_by, self.admin_id)
        self.assertTrue(shared.is_global)
        self.assertIsNone(shared.user_id)
        self.assertEqual(shared.created_by, self.admin_id)
        self.assertIsNotNone(self.tag_named("成员全局标签"))
        # 别人的标签不受影响。
        assert admin_tag is not None
        self.assertEqual(admin_tag.user_id, self.admin_id)
        self.assertEqual(admin_tag.created_by, self.admin_id)
        # 存档条目跟着改归属，分类名保留用户名以便溯源。
        self.assertEqual(entry.user_id, self.admin_id)
        self.assertEqual(entry.user_name, self.admin.name)
        self.assertEqual(entry.category, f"{self.member.name} / 子分类")

    def test_delete_without_data_cleans_categories(self) -> None:
        category = self.taxonomy.categories.create("空分类", None, user_id=self.member_id)
        self.session.flush()
        self.assertTrue(self.users.delete(self.member))
        self.assertIsNone(self.session.get(Category, category.id))
        self.assertIsNone(self.category_named(self.member.name, self.admin_id))

    def test_delete_drops_unused_tags(self) -> None:
        self.member_payload()
        unused = self.taxonomy.create_tag("成员废弃标签", user_id=self.member_id)
        self.session.flush()
        assert unused is not None
        unused_id = int(unused.id)
        self.assertTrue(self.users.delete(self.member))
        self.session.flush()
        self.assertIsNone(self.session.get(Tag, unused_id))
        self.assertIsNone(self.tag_named("成员废弃标签"))
        # 有数据项引用的标签照常迁移。
        self.assertIsNotNone(self.tag_named("成员个人标签"))
        self.assertIsNotNone(self.tag_named("成员全局标签"))

    def test_delete_prunes_empty_categories(self) -> None:
        library = self.libraries.ensure_default()
        old_dir = self.libraries.dir_name_of(self.member.name)
        directory = Path(library.path) / old_dir
        (directory / "编程" / "空目录").mkdir(parents=True, exist_ok=True)
        (directory / "学习资料").mkdir(parents=True, exist_ok=True)
        (directory / "编程" / "空目录" / "x.txt").write_text("x", encoding="utf-8")
        parent = self.taxonomy.categories.create("编程", None, user_id=self.member_id)
        child = self.taxonomy.categories.create("空目录", parent.id, user_id=self.member_id)
        item = self.items.create(
            name="嵌套数据",
            user_id=self.member_id,
            category_id=child.id,
            file_path=f"{old_dir}/编程/空目录/x.txt",
        )
        self.session.flush()
        # 种子写入的「学习资料」等空分类不该被镜像。
        self.assertIsNotNone(self.category_named("学习资料", self.member_id))
        self.assertTrue(self.users.delete(self.member))
        self.session.flush()
        mirror_root = self.category_named(self.member.name, self.admin_id)
        assert mirror_root is not None
        self.assertEqual(sorted(branch.name for branch in mirror_root.children), ["编程"])
        mirrored_child = self.session.get(Category, item.category_id)
        assert mirrored_child is not None
        self.assertEqual(mirrored_child.name, "空目录")
        mirrored_parent = self.session.get(Category, mirrored_child.parent_id)
        assert mirrored_parent is not None
        self.assertEqual(mirrored_parent.name, "编程")
        self.assertEqual(mirrored_parent.parent_id, mirror_root.id)
        # 空分类对应的目录在磁盘上也一并清掉。
        admin_dir = self.libraries.dir_name_of(self.admin.name)
        mirrored_dir = Path(library.path) / admin_dir / old_dir
        self.assertFalse((mirrored_dir / "学习资料").is_dir())
        self.assertTrue((mirrored_dir / "编程" / "空目录" / "x.txt").is_file())

    def test_card_permissions_delete_is_admin_only(self) -> None:
        admin_other = card_permissions(
            is_current=False, is_default=False, protected=False, is_admin=True
        )
        self.assertTrue(admin_other["delete"])
        self.assertFalse(
            card_permissions(
                is_current=True, is_default=False, protected=False, is_admin=True
            )["delete"]
        )
        self.assertFalse(
            card_permissions(
                is_current=False, is_default=True, protected=False, is_admin=True
            )["delete"]
        )
        self.assertFalse(
            card_permissions(
                is_current=False, is_default=False, protected=False, is_admin=False
            )["delete"]
        )
