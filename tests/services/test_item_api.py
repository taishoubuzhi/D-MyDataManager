"""数据接口（`app.sdk.items` + `app.services.item_api`）的用例。

插件只能通过这套接口读条目、读写标签与关键词；这里的重点是：
筛选（类型 / 后缀 / 排除标签）走的是数据库查询而不是全量拉回内存、
写操作返回的是「真的改了几条」而不是「给了几条」。
"""

from __future__ import annotations

import unittest

from app.core.plugins.extensions import extension_registry
from app.sdk import items as items_sdk
from app.sdk.errors import SdkError
from app.services import LibraryService
from app.services.item_api import ItemsApi, to_ref

from tests.harness import IsolatedCase


class ToRefCase(IsolatedCase):
    """快照转换：插件看到的是小写类型、小写后缀与排好序的标签。"""

    def setUp(self):
        super().setUp()
        self.library = self.default_library()
        self.api = ItemsApi()

    def _import_text(self, name: str = "用例", *, content: str = "正文内容", tags=(), words=()):
        service = self.importer(library=self.library)
        item = service.import_text(name, content, user_id=self.current_user().id)
        self.session.flush()
        from app.services import ItemService

        helper = ItemService(self.session)
        if tags:
            helper.add_tags([item], list(tags))
        if words:
            helper.add_keywords([item], list(words))
        self.session.commit()
        return item

    def test_ref_uses_lowercase_type_and_suffix(self):
        item = self._import_text("报告")
        ref = to_ref(item)
        self.assertEqual(ref.type, item.type.value.lower())
        self.assertEqual(ref.type, "text")
        self.assertEqual(ref.suffix, "txt")
        self.assertEqual(ref.id, item.id)
        self.assertEqual(ref.user_id, item.user_id)

    def test_ref_sorts_tags_and_keywords(self):
        item = self._import_text("排序", tags=("乙标签", "甲标签"), words=("z", "a"))
        ref = to_ref(item)
        self.assertEqual(ref.tags, tuple(sorted(ref.tags)))
        self.assertEqual(set(ref.tags), {"甲标签", "乙标签"})
        self.assertEqual(list(ref.keywords), ["z", "a"])


class ItemsApiCase(IsolatedCase):
    """宿主实现：查询、打标签、关键词与正文。"""

    def setUp(self):
        super().setUp()
        self.library = self.default_library()
        self.api = ItemsApi()
        self.user = self.current_user()

    def _import_text(self, name: str, *, content: str = "正文内容"):
        item = self.importer(library=self.library).import_text(name, content, user_id=self.user.id)
        self.session.flush()
        self.session.commit()
        return item

    def test_list_items_filters_by_type_and_ids(self):
        first = self._import_text("第一条")
        second = self._import_text("第二条")

        all_text = self.api.list_items(type="text", user_id=self.user.id)
        self.assertEqual({ref.id for ref in all_text}, {first.id, second.id})

        only_first = self.api.list_items(ids=[first.id])
        self.assertEqual([ref.id for ref in only_first], [first.id])

    def test_list_items_excludes_entries_that_already_have_a_tag(self):
        first = self._import_text("已打标签")
        second = self._import_text("没打标签")
        self.assertEqual(self.api.tag_items([first.id], ["成品"]), 1)

        pending = self.api.list_items(type="text", exclude_tags=["成品"], user_id=self.user.id)
        self.assertEqual([ref.id for ref in pending], [second.id])

    def test_tag_items_counts_only_real_changes(self):
        item = self._import_text("数标签")
        self.assertEqual(self.api.tag_items([item.id], ["甲", "乙"]), 1)
        self.assertEqual(self.api.tag_items([item.id], ["甲", "乙"]), 0, "已经有的标签不该重复计一次")

        self.session.expire_all()
        ref = self.api.get_item(item.id)
        self.assertEqual(set(ref.tags), {"甲", "乙"})

    def test_untag_items_removes_only_existing_tags(self):
        item = self._import_text("摘标签")
        self.api.tag_items([item.id], ["甲", "乙"])

        self.assertEqual(self.api.untag_items([item.id], ["乙"]), 1)
        self.assertEqual(self.api.untag_items([item.id], ["乙"]), 0)
        self.assertEqual(self.api.get_item(item.id).tags, ("甲",))

    def test_add_and_remove_keywords_report_real_changes(self):
        item = self._import_text("关键词")
        self.assertEqual(self.api.add_keywords([item.id], ["甲", "乙"]), 1)
        self.assertEqual(self.api.add_keywords([item.id], ["甲", "乙"]), 0)
        self.assertEqual(self.api.remove_keywords([item.id], ["甲"]), 1)

        ref = self.api.get_item(item.id)
        self.assertEqual(list(ref.keywords), ["乙"])

    def test_suffixes_in_use_lists_lowercase_suffixes(self):
        self._import_text("后缀")
        counts = self.api.suffixes_in_use(self.user.id)
        self.assertIn("txt", counts)
        self.assertGreaterEqual(sum(counts.values()), 1)

    def test_read_text_returns_content_and_encoding(self):
        item = self._import_text("读正文", content="这是一段正文")
        text, encoding, truncated = self.api.read_text(item.id, 4)
        self.assertEqual(text, "这是一段")
        self.assertTrue(truncated)
        self.assertTrue(encoding)

    def test_tag_names_include_created_tag(self):
        item = self._import_text("标签名")
        self.api.tag_items([item.id], ["新标签"])
        self.assertIn("新标签", self.api.tag_names(self.user.id))


class ItemsSdkCase(unittest.TestCase):
    """门面：没注册实现时读接口给空值、写接口抛 SdkError；注册后转发给实现。"""

    def tearDown(self):
        extension_registry.drop_plugin("test-items")

    def test_missing_provider_returns_empty_and_raises_on_write(self):
        self.assertIsNone(items_sdk.provider())
        self.assertFalse(items_sdk.available())
        self.assertEqual(items_sdk.list_items(ids=[1]), ())
        self.assertEqual(items_sdk.tag_names(), ())
        self.assertEqual(items_sdk.suffixes_in_use(), {})
        self.assertEqual(items_sdk.read_text(1), ("", "", False))
        with self.assertRaises(SdkError):
            items_sdk.tag_items([1], ["甲"])
        with self.assertRaises(SdkError):
            items_sdk.ensure_tags(["甲"])

    def test_registered_provider_receives_calls(self):
        class Fake:
            def __init__(self):
                self.calls = []

            def current_user_id(self):
                return 7

            def list_items(self, **kwargs):
                self.calls.append(("list", kwargs))
                return [items_sdk.ItemRef(id=1, name="甲")]

            def ensure_tags(self, names, user_id=None):
                self.calls.append(("ensure", list(names)))
                return list(names)

            def tag_items(self, ids, names):
                self.calls.append(("tag", list(ids), list(names)))
                return 2

            def notify_changed(self):
                self.calls.append(("notify",))

            def notify_tags_changed(self):
                self.calls.append(("notify-tags",))

        fake = Fake()
        extension_registry.provide(items_sdk.ITEMS_EXTENSION, fake, "test-items")

        self.assertTrue(items_sdk.available())
        self.assertEqual(items_sdk.current_user_id(), 7)
        self.assertEqual([ref.id for ref in items_sdk.list_items(type="image")], [1])
        self.assertEqual(items_sdk.ensure_tags(["甲"]), ("甲",))
        self.assertEqual(items_sdk.tag_items([1, 2], ["乙"]), 2)
        items_sdk.notify_tags_changed()
        self.assertIn(("notify-tags",), fake.calls)
        self.assertEqual(fake.calls[0][1]["type"], "image")


class ItemChangedBridgeCase(unittest.TestCase):
    """`notify_changed()` 既要刷界面，也要让插件收到 `item.changed`。"""

    def tearDown(self):
        from app.services.plugin_service import plugin_service

        plugin_service._handlers.pop("item.changed", None)

    def test_notify_changed_publishes_plugin_event(self):
        from app.sdk.points import Events
        from app.services.plugin_service import plugin_service

        seen = []
        plugin_service.on("test-items", Events.ITEM_CHANGED, lambda **payload: seen.append(payload))
        ItemsApi().notify_changed()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], {})


class TagChangedBridgeCase(unittest.TestCase):
    """`notify_tags_changed()`：整批写完标签后广播一次，标签页才会看到新标签（用户 m07851）。"""

    def test_notify_tags_changed_emits_once(self):
        from app.core.runtime.signals import signalBus

        seen: list[int] = []
        signalBus.tagsChanged.connect(lambda: seen.append(1))
        try:
            ItemsApi().notify_tags_changed()
        finally:
            signalBus.tagsChanged.disconnect()
        self.assertEqual(len(seen), 1)

    def test_provider_without_the_hook_is_safe(self):
        class Fake:
            """老版本实现没有 `notify_tags_changed()`：门面要安静降级。"""

        extension_registry.provide(items_sdk.ITEMS_EXTENSION, Fake(), "test-items")
        self.addCleanup(extension_registry.drop_plugin, "test-items")
        items_sdk.notify_tags_changed()


class ContextCase(unittest.TestCase):
    """两个上下文对象是纯数据：插件能不能拿到东西、拿不到时是否安静降级。"""

    def test_selection_context_exposes_ids_and_refresh(self):
        calls = []
        context = items_sdk.SelectionContext(
            items=(items_sdk.ItemRef(id=3, name="丙"), items_sdk.ItemRef(id=4, name="丁")),
            user_id=1,
            refresh=lambda: calls.append("refresh"),
        )
        self.assertEqual(context.item_ids, (3, 4))
        self.assertEqual(context.count, 2)
        context.do_refresh()
        self.assertEqual(calls, ["refresh"])

    def test_selection_context_without_refresh_is_safe(self):
        items_sdk.SelectionContext().do_refresh()

    def test_import_context_fills_forms_and_reports_counts(self):
        collected = {"tags": [], "words": [], "toasts": []}
        context = items_sdk.ImportContext(
            add_tags=lambda names: collected["tags"].extend(names) or len(names),
            add_keywords=lambda words: collected["words"].extend(words) or len(words),
            notify=collected["toasts"].append,
        )
        self.assertEqual(context.apply_tags(["甲", "乙"]), 2)
        self.assertEqual(context.apply_keywords(["关键词"]), 1)
        context.toast("已预填")
        self.assertEqual(collected["tags"], ["甲", "乙"])
        self.assertEqual(collected["words"], ["关键词"])
        self.assertEqual(collected["toasts"], ["已预填"])

    def test_import_context_survives_missing_handlers(self):
        context = items_sdk.ImportContext()
        self.assertEqual(context.apply_tags(["甲"]), 0)
        self.assertEqual(context.apply_keywords(["甲"]), 0)
        context.toast("没人接")
        self.assertEqual(context.recent_paths(), ())


if __name__ == "__main__":
    unittest.main()
