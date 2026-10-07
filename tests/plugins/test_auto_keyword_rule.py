"""新增插件（`auto_keyword.rule`）：关键词库、规则挂关键词的执行逻辑与插件接线。

不需要 Qt：页面模块在接线用例里用假模块替换（真页面由自检覆盖）。
关键词库与规则文件都走 `app.sdk.storage`，所以文件用例用 `IsolatedCase` 把 `.configs/` 重定向。
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "auto_keyword.rule"
AUTOLABEL_DIR = REPO / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.auto_keyword.rule` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", AUTOLABEL_DIR),
        ("dm_plugin.auto_keyword", None),
        ("dm_plugin.auto_keyword.rule", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()

import app.sdk.items as items_sdk  # noqa: E402

from app.core.plugins.plugin_core import load_manifest  # noqa: E402
from dm_plugin.lib.autolabel.rules import (  # noqa: E402
    FIELD_NAME,
    FIELD_SUFFIX,
    FIELD_TEXT,
    KIND_MATCH,
    OP_CONTAINS,
    OP_IN,
    SOURCE_FACTORY,
    SOURCE_USER,
    Rule,
    RuleSet,
)
from dm_plugin.auto_keyword.rule import plugin as plugin_module  # noqa: E402
from dm_plugin.auto_keyword.rule import runner, store  # noqa: E402


@dataclass
class FakeItem:
    """够规则引擎取字段、也够扫描关键词的条目替身。"""

    id: object = 1
    name: str = ""
    suffix: str = ""
    type: str = ""
    file_path: str = ""
    keywords: tuple = ()


def _rule(key="pdf", *, field=FIELD_SUFFIX, op=OP_IN, pattern="pdf", keywords=("PDF",), enabled=True):
    return Rule(
        key=key,
        name=key,
        kind=KIND_MATCH,
        enabled=enabled,
        field=field,
        op=op,
        pattern=pattern,
        tags=tuple(keywords),
        source=SOURCE_FACTORY,
    )


class ManifestCase(unittest.TestCase):
    """清单：外部插件、默认不启用、依赖齐、与模型插件互斥。"""

    def test_manifest_describes_external_plugin(self) -> None:
        info = load_manifest(PLUGIN_DIR, builtin=False)
        self.assertEqual(info.id, "auto_keyword.rule")
        self.assertFalse(info.builtin)
        self.assertFalse(info.enabled)
        self.assertEqual(info.class_name, "AutoKeywordRulePlugin")
        self.assertTrue((PLUGIN_DIR / info.entry).is_file())

    def test_manifest_declares_dependencies_and_conflict(self) -> None:
        info = load_manifest(PLUGIN_DIR, builtin=False)
        self.assertEqual(sorted(info.depends_ids), ["builtin.lib.ui", "lib.autolabel"])
        self.assertIn("auto_keyword", info.conflicts)  # 与 auto_keyword 不能同时启用


class LibraryFileCase(IsolatedCase):
    """关键词库落盘：`.configs/autolabel.keywords.json`。"""

    def test_save_and_load_library(self) -> None:
        self.assertFalse(store.keywords_file().is_file())
        self.assertEqual(store.load_library(), ())

        self.assertTrue(store.save_library(["图片", "视频", "图片", "  "]))
        self.assertTrue(store.keywords_file().is_file())
        payload = json.loads(store.keywords_file().read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], store.LIBRARY_VERSION)
        self.assertEqual(payload["keywords"], ["图片", "视频"])
        self.assertEqual(store.load_library(), ("图片", "视频"))

    def test_load_library_ignores_broken_file(self) -> None:
        store.keywords_file().parent.mkdir(parents=True, exist_ok=True)
        store.keywords_file().write_text("{ 这不是 JSON", encoding="utf-8")
        self.assertEqual(store.load_library(), ())


class RulesFileCase(IsolatedCase):
    """规则落盘：`.configs/autolabel.keyword-rules.json`（与标签那份分开存）。"""

    def test_load_rules_without_file_is_factory(self) -> None:
        store.rules_file().unlink(missing_ok=True)
        rule_set = store.load_rules()
        self.assertEqual(rule_set.user, ())
        self.assertEqual(
            [rule.key for rule in rule_set.rules], [rule.key for rule in store.FACTORY_RULES]
        )

    def test_save_rules_keeps_only_user_part(self) -> None:
        rule_set = store.load_rules().with_rule(
            Rule(key="mine", name="我的", field=FIELD_SUFFIX, op=OP_IN, pattern="ppt", tags=("演示",))
        ).without_key("image")
        self.assertTrue(store.save_rules(rule_set))

        payload = json.loads(store.rules_file().read_text(encoding="utf-8"))
        self.assertEqual([rule["key"] for rule in payload["rules"]], ["mine"])
        self.assertEqual(payload["hidden"], ["image"])

        again = store.load_rules()
        self.assertIsNone(again.by_key("image"))
        self.assertEqual(again.by_key("mine").tags, ("演示",))
        self.assertEqual(again.by_key("mine").source, SOURCE_USER)

    def test_broken_file_falls_back_to_factory(self) -> None:
        store.rules_file().parent.mkdir(parents=True, exist_ok=True)
        store.rules_file().write_text("[1, 2, 3]", encoding="utf-8")
        rule_set = store.load_rules()
        self.assertEqual(rule_set.user, ())
        self.assertEqual(len(rule_set.rules), len(store.FACTORY_RULES))

    def test_keyword_rules_file_is_not_the_tag_one(self) -> None:
        self.assertEqual(store.RULES_FILE, "autolabel.keyword-rules.json")
        self.assertNotEqual(store.RULES_FILE, "autolabel.rules.json")


class KeywordHelpersCase(unittest.TestCase):
    def test_normalize_keywords_dedupes_case_insensitively(self) -> None:
        self.assertEqual(store.normalize_keywords(["PDF", " pdf ", "图片", ""]), ("PDF", "图片"))
        self.assertEqual(store.normalize_keywords(["a", "b", "c"], limit=2), ("a", "b"))

    def test_merge_keywords_keeps_first_spelling(self) -> None:
        self.assertEqual(store.merge_keywords(["图片"], ("图片", "截图")), ("图片", "截图"))


class FactoryRuleCase(unittest.TestCase):
    def test_factory_rules_are_match_rules_with_keywords(self) -> None:
        self.assertGreaterEqual(len(store.FACTORY_RULES), 8)
        keys = [rule.key for rule in store.FACTORY_RULES]
        self.assertEqual(len(set(keys)), len(keys))
        for rule in store.FACTORY_RULES:
            self.assertEqual(rule.kind, KIND_MATCH, rule.key)
            self.assertTrue(rule.tags, rule.key)
            self.assertEqual(rule.source, SOURCE_FACTORY, rule.key)

    def test_factory_suffix_rule_hangs_keyword(self) -> None:
        rule_set = RuleSet(factory=store.FACTORY_RULES)
        plan = runner.plan_keywords(
            [FakeItem(id=1, name="照片.PNG", suffix="png")], rule_set
        )
        self.assertEqual(plan.matched[0].keywords, ("图片",))
        self.assertEqual(plan.matched[0].rules, ("图片文件",))

    def test_factory_name_rule_hangs_screenshot_keyword(self) -> None:
        rule_set = RuleSet(factory=store.FACTORY_RULES)
        plan = runner.plan_keywords(
            [FakeItem(id=2, name="微信截图.png", suffix="png")], rule_set
        )
        self.assertIn("截图", plan.matched[0].keywords)


class SuffixKeyCase(unittest.TestCase):
    def test_normalizes(self) -> None:
        self.assertEqual(runner.suffix_key("PDF"), "suffix.pdf")
        self.assertEqual(runner.suffix_key(".Txt"), "suffix.txt")


class FileRefCase(unittest.TestCase):
    def test_reads_path(self) -> None:
        ref = runner.FileRef(r"C:\资料\报表 2026.PDF")
        self.assertEqual(ref.name, "报表 2026.PDF")
        self.assertEqual(ref.suffix, "pdf")
        self.assertEqual(ref.type, "")
        self.assertIsNone(ref.id)


class KeywordsForPathsCase(unittest.TestCase):
    def test_matches_paths_and_dedupes(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", pattern="pdf", keywords=("PDF", "文档")),
            _rule("doc", pattern="doc", keywords=("文档",)),
        ))
        words = runner.keywords_for_paths([r"C:\a\报表.pdf", r"C:\a\说明.doc"], rules)
        self.assertEqual(words, ("PDF", "文档"))


class PlanKeywordsCase(unittest.TestCase):
    def test_marks_rows_without_id(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf"),))
        plan = runner.plan_keywords([FakeItem(id=None, name="没入库.pdf")], rules)
        self.assertFalse(plan.matched)
        self.assertEqual(plan.skipped[0].reason, "条目没有 id")

    def test_reads_text_only_when_a_rule_needs_it(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf"),))
        calls: list[int] = []

        def reader(item_id, limit=0):
            calls.append(item_id)
            return "报销单"

        plan = runner.plan_keywords([FakeItem(id=1, suffix="pdf")], rules, reader=reader)
        self.assertEqual(calls, [])
        self.assertEqual(plan.matched[0].keywords, ("PDF",))

    def test_text_rule_uses_reader_and_tolerates_one_arg_reader(self) -> None:
        rules = RuleSet(factory=(
            _rule("报销", field=FIELD_TEXT, op=OP_CONTAINS, pattern="报销", keywords=("报销",)),
        ))
        plan = runner.plan_keywords(
            [FakeItem(id=1, name="a.pdf", suffix="pdf")],
            rules,
            reader=lambda item_id: "这是一张报销单",
        )
        self.assertEqual(plan.matched[0].keywords, ("报销",))

    def test_reader_failure_only_records_note(self) -> None:
        rules = RuleSet(factory=(
            _rule("报销", field=FIELD_TEXT, op=OP_CONTAINS, pattern="报销", keywords=("报销",)),
        ))

        def boom(item_id, limit=0):
            raise OSError("文件不见了")

        plan = runner.plan_keywords([FakeItem(id=1, name="a.pdf")], rules, reader=boom)
        self.assertFalse(plan.matched)
        self.assertEqual(len(plan.notes), 1)
        self.assertIn("读取《a.pdf》正文失败", plan.notes[0])


class _FakeItemsApi:
    """`add_keywords` 的替身：记下每一批调用。"""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[tuple[list[int], list[str]]] = []
        self.fail_first = fail_first

    def add_keywords(self, ids, words):
        if self.fail_first and not self.calls:
            self.calls.append((list(ids), list(words)))
            raise RuntimeError("盘满了")
        self.calls.append((list(ids), list(words)))
        return len(list(ids))


class RunKeywordsCase(unittest.TestCase):
    def _plan(self):
        rules = RuleSet(factory=(
            _rule("pdf", pattern="pdf", keywords=("PDF",)),
            _rule("jpg", pattern="jpg", keywords=("图片",)),
        ))
        rows = [
            FakeItem(id=1, suffix="pdf"),
            FakeItem(id=2, suffix="pdf"),
            FakeItem(id=3, suffix="jpg"),
        ]
        return runner.plan_keywords(rows, rules)

    def test_batches_same_keywords_together(self) -> None:
        plan = self._plan()
        api = _FakeItemsApi()
        progress: list[tuple[int, int]] = []
        report = runner.run_keywords(plan, api=api, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(api.calls, [([1, 2], ["PDF"]), ([3], ["图片"])])
        self.assertEqual(report.written, 3)
        self.assertTrue(report.ok)
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_cancel_stops_before_writing(self) -> None:
        plan = self._plan()
        api = _FakeItemsApi()
        report = runner.run_keywords(plan, api=api, cancel=lambda: True)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.ok)
        self.assertEqual(api.calls, [])

    def test_one_failed_batch_does_not_stop_the_rest(self) -> None:
        plan = self._plan()
        api = _FakeItemsApi(fail_first=True)
        report = runner.run_keywords(plan, api=api)
        self.assertEqual(len(report.failed), 1)
        self.assertIn("PDF", report.failed[0])
        self.assertEqual(report.written, 1)
        self.assertFalse(report.ok)


class ScanKeywordsCase(unittest.TestCase):
    def test_counts_and_sorts(self) -> None:
        rows = [
            FakeItem(id=1, keywords=("图片", "文档")),
            FakeItem(id=2, keywords=("图片",)),
            FakeItem(id=3, keywords=("图片", "  ")),
        ]
        self.assertEqual(runner.scan_keywords(rows), (("图片", 3), ("文档", 1)))
        self.assertEqual(runner.scan_keywords(rows, limit=1), (("图片", 3),))

    def test_empty_rows(self) -> None:
        self.assertEqual(runner.scan_keywords([]), ())


class SummaryCase(unittest.TestCase):
    def test_reports_written_and_cancelled(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf", keywords=("PDF",)),))
        plan = runner.plan_keywords([FakeItem(id=1, suffix="pdf")], rules)
        text = runner.summary_text(runner.KeywordReport(plan, written=1))
        self.assertIn("扫描 1 个条目", text)
        self.assertIn("写入 1 个关键词", text)
        cancelled = runner.summary_text(runner.KeywordReport(plan, cancelled=True))
        self.assertIn("已取消", cancelled)


class FakeHost:
    def __init__(self) -> None:
        self.toasts: list[tuple[str, str]] = []

    def toast(self, title: str, content: str = "") -> None:
        self.toasts.append((title, content))


class FakeCtx:
    """插件 `setup()` 用到的 ctx 面。"""

    def __init__(self) -> None:
        self.pages: list[tuple] = []
        self.contributions: list[tuple] = []
        self.log = mock.Mock()
        self.host = FakeHost()

    def add_page(self, key, title, factory, **fields) -> None:
        self.pages.append((key, title, factory, fields))

    def contribute(self, point, value, key="", description="") -> None:
        self.contributions.append((point, value, key, description))


class FakeKeywordApi:
    def __init__(self, rules=None) -> None:
        self._rules = rules or RuleSet(factory=(_rule("pdf", pattern="pdf", keywords=("PDF",)),))
        self._library = ("图片",)
        self.saved = []
        self.saved_library = []

    def rules(self, *, reload=False):
        return self._rules

    def save_rules(self, rule_set) -> bool:
        self.saved.append(rule_set)
        self._rules = rule_set
        return True

    def library(self):
        return self._library

    def save_library(self, words) -> bool:
        self.saved_library.append(tuple(words))
        self._library = tuple(words)
        return True


def _fake_page_module():
    """把 `ui.page` 换成假模块，接线用例就不用拉起 Qt。"""
    ui = types.ModuleType("dm_plugin.auto_keyword.rule.ui")
    ui.__path__ = []
    page = types.ModuleType("dm_plugin.auto_keyword.rule.ui.page")

    class AutoKeywordRulePage:
        def __init__(self, ctx, api) -> None:
            self.ctx = ctx
            self.api = api

    page.AutoKeywordRulePage = AutoKeywordRulePage
    ui.page = page
    return {
        "dm_plugin.auto_keyword.rule.ui": ui,
        "dm_plugin.auto_keyword.rule.ui.page": page,
    }, AutoKeywordRulePage


class PluginWiringCase(unittest.TestCase):
    def setUp(self) -> None:
        patches, self.page_class = _fake_page_module()
        self._patch = mock.patch.dict(sys.modules, patches)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.api = FakeKeywordApi()
        self.ctx = FakeCtx()
        self.plugin = plugin_module.AutoKeywordRulePlugin()
        self.plugin.setup(self.ctx)
        self.plugin._api = self.api

    def _points(self):
        return {key: (point, value) for point, value, key, _ in self.ctx.contributions}

    def test_registers_page(self) -> None:
        key, title, factory, fields = self.ctx.pages[0]
        self.assertEqual((key, title), ("auto_keyword_rule", "自动关键词（规则）"))
        self.assertEqual(fields["icon"], "DICTIONARY")
        self.assertEqual(fields["order"], 170)
        page = factory()
        self.assertIsInstance(page, self.page_class)
        self.assertIs(page.api, self.api)

    def test_contributes_three_endpoints(self) -> None:
        points = {point for point, _, _, _ in self.ctx.contributions}
        self.assertEqual(points, {
            "app.ui.import.action",
            "app.ui.manage.toolbar",
            "app.ui.manage.item_menu",
        })
        keys = sorted(key for _, _, key, _ in self.ctx.contributions)
        self.assertEqual(
            keys, ["auto_keyword_rule.import", "auto_keyword_rule.item", "auto_keyword_rule.manage"]
        )

    # ------------------------------------------------------------- 导入页

    def _import_ctx(self, paths, *, added=1):
        class Ctx:
            def __init__(self) -> None:
                self.applied = []
                self.messages = []

            def recent_paths(self):
                return tuple(Path(p) for p in paths)

            def apply_keywords(self, words):
                self.applied.append(tuple(words))
                return added

            def toast(self, message):
                self.messages.append(message)

        return Ctx()

    def test_import_action_prefills_keywords(self) -> None:
        callback = self._points()["auto_keyword_rule.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\报表.pdf"])
        callback(ictx)
        self.assertEqual(ictx.applied, [("PDF",)])
        self.assertIn("预填", ictx.messages[0])

    def test_import_action_without_match(self) -> None:
        callback = self._points()["auto_keyword_rule.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\笔记.xyz"])
        callback(ictx)
        self.assertEqual(ictx.applied, [])
        self.assertIn("没有命中规则", ictx.messages[0])

    def test_import_action_without_paths(self) -> None:
        callback = self._points()["auto_keyword_rule.import"][1]["callback"]
        ictx = self._import_ctx([])
        callback(ictx)
        self.assertIn("还没有待导入的文件", ictx.messages[0])

    # --------------------------------------------------------- 管理页入口

    def test_toolbar_action_without_selection(self) -> None:
        callback = self._points()["auto_keyword_rule.manage"][1]["callback"]
        callback(None)
        self.assertIn("先选条目", self.ctx.host.toasts[0][0])

    def test_toolbar_action_writes_keywords(self) -> None:
        callback = self._points()["auto_keyword_rule.manage"][1]["callback"]

        class Selection:
            def __init__(self) -> None:
                self.items = (FakeItem(id=1, name="a.pdf", suffix="pdf"),)
                self.refreshed = 0

            def do_refresh(self):
                self.refreshed += 1

        selection = Selection()
        with mock.patch.object(items_sdk, "add_keywords", return_value=1) as add_keywords, \
                mock.patch.object(items_sdk, "notify_changed") as notify:
            callback(selection)
        self.assertEqual(selection.refreshed, 1)
        self.assertEqual(add_keywords.call_args[0][0], [1])
        self.assertEqual(add_keywords.call_args[0][1], ["PDF"])
        self.assertIn("写入 1 个关键词", self.ctx.host.toasts[0][1])
        notify.assert_called_once()

    def test_item_menu_action_uses_item_id(self) -> None:
        callback = self._points()["auto_keyword_rule.item"][1]["callback"]
        with mock.patch.object(
            items_sdk, "get_item", return_value=FakeItem(id=9, suffix="pdf")
        ) as get_item, mock.patch.object(
            items_sdk, "add_keywords", return_value=1
        ), mock.patch.object(items_sdk, "notify_changed") as notify:
            callback(types.SimpleNamespace(id=9))
        get_item.assert_called_once_with(9)
        notify.assert_called_once()

    def test_item_menu_action_without_id(self) -> None:
        callback = self._points()["auto_keyword_rule.item"][1]["callback"]
        with mock.patch.object(items_sdk, "add_keywords") as add_keywords:
            callback(types.SimpleNamespace())
        add_keywords.assert_not_called()

    def test_item_menu_action_when_item_is_gone(self) -> None:
        callback = self._points()["auto_keyword_rule.item"][1]["callback"]
        with mock.patch.object(items_sdk, "get_item", return_value=None):
            callback(types.SimpleNamespace(id=9))
        self.assertIn("找不到这个条目", self.ctx.host.toasts[0][0])


if __name__ == "__main__":
    unittest.main()
