"""任务 2（`auto_tag.rule`）：规则挂标签的执行逻辑、格式默认标签与插件接线。

不需要 Qt：页面模块在接线用例里用假模块替换（真页面由自检覆盖）。
"""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugins" / "auto_tag.rule"
AUTOLABEL_DIR = REPO / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.auto_tag.rule` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", AUTOLABEL_DIR),
        ("dm_plugin.auto_tag", None),
        ("dm_plugin.auto_tag.rule", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder)]
        sys.modules[name] = module


_register_plugin_namespace()

from app.core.plugin_core import load_manifest  # noqa: E402
from dm_plugin.lib.autolabel.rules import (  # noqa: E402
    FIELD_SUFFIX,
    FIELD_TEXT,
    KIND_MATCH,
    OP_CONTAINS,
    OP_IS,
    SOURCE_FACTORY,
    SOURCE_USER,
    Rule,
    RuleSet,
)
from dm_plugin.auto_tag.rule import plugin as plugin_module  # noqa: E402
from dm_plugin.auto_tag.rule import runner  # noqa: E402


@dataclass
class FakeItem:
    """够规则引擎取字段的条目替身。"""

    id: object = 1
    name: str = ""
    suffix: str = ""
    type: str = ""
    file_path: str = ""


def _rule(key="pdf", *, field=FIELD_SUFFIX, op=OP_IS, pattern="pdf", tags=("PDF",), enabled=True):
    return Rule(
        key=key,
        name=key,
        kind=KIND_MATCH,
        enabled=enabled,
        field=field,
        op=op,
        pattern=pattern,
        tags=tuple(tags),
        source=SOURCE_FACTORY,
    )


class ManifestCase(unittest.TestCase):
    """清单：外部插件、入口存在、依赖与互斥。"""

    def test_manifest_describes_external_plugin(self) -> None:
        info = load_manifest(PLUGIN_DIR, builtin=False)
        self.assertEqual(info.id, "auto_tag.rule")
        self.assertFalse(info.builtin)
        self.assertEqual(info.class_name, "AutoTagRulePlugin")
        self.assertTrue((PLUGIN_DIR / info.entry).is_file())

    def test_manifest_declares_dependencies_and_conflict(self) -> None:
        info = load_manifest(PLUGIN_DIR, builtin=False)
        self.assertEqual(sorted(info.depends_ids), ["builtin.lib.ui", "lib.autolabel"])
        self.assertIn("auto_tag", info.conflicts)


class SuffixKeyCase(unittest.TestCase):
    def test_normalizes(self) -> None:
        self.assertEqual(runner.suffix_key("PDF"), "suffix.pdf")
        self.assertEqual(runner.suffix_key(".Txt"), "suffix.txt")
        self.assertEqual(runner.suffix_key("  md "), "suffix.md")


class FileRefCase(unittest.TestCase):
    def test_reads_path(self) -> None:
        ref = runner.FileRef(r"C:\资料\报表 2026.PDF")
        self.assertEqual(ref.name, "报表 2026.PDF")
        self.assertEqual(ref.suffix, "pdf")
        self.assertEqual(ref.type, "")
        self.assertIsNone(ref.id)


class TagsForPathsCase(unittest.TestCase):
    def test_matches_paths_and_dedupes(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", tags=("PDF", "文档")),
            _rule("doc", pattern="doc", tags=("文档",)),
            _rule("正文", field=FIELD_TEXT, op=OP_CONTAINS, pattern="报销", tags=("报销",)),
        ))
        names = runner.tags_for_paths([Path("a.pdf"), Path("b.PDF"), Path("c.doc")], rules)
        self.assertEqual(names, ("PDF", "文档"))

    def test_disabled_rule_is_ignored(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", tags=("PDF",), enabled=False),))
        self.assertEqual(runner.tags_for_paths([Path("a.pdf")], rules), ())


class PlanTagsCase(unittest.TestCase):
    def test_matches_suffix_and_keeps_rule_keys(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", tags=("PDF",)),
            _rule("doc", field=FIELD_SUFFIX, pattern="doc", tags=("文档",)),
        ))
        plan = runner.plan_tags([FakeItem(id=7, name="a.pdf", suffix="pdf")], rules)
        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.items[0].tags, ("PDF",))
        self.assertEqual(plan.items[0].rules, ("pdf",))
        self.assertEqual(plan.groups, {"PDF": (7,)})

    def test_item_without_id_is_skipped(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", tags=("PDF",)),))
        plan = runner.plan_tags([FakeItem(id=None, name="a.pdf", suffix="pdf")], rules)
        self.assertEqual(plan.skipped[0].reason, "条目没有 id")
        self.assertEqual(plan.matched, ())

    def test_unmatched_item_reports_reason(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", tags=("PDF",)),))
        plan = runner.plan_tags([FakeItem(id=3, name="a.txt", suffix="txt")], rules)
        self.assertEqual(plan.skipped[0].reason, "没有命中规则")

    def test_reader_only_used_for_text_rules(self) -> None:
        plain = RuleSet(factory=(_rule("pdf", tags=("PDF",)),))
        reader = mock.Mock()
        runner.plan_tags([FakeItem(id=1, suffix="pdf")], plain, reader=reader)
        reader.assert_not_called()

        with_text = RuleSet(factory=(
            _rule("明文", field=FIELD_TEXT, op=OP_CONTAINS, pattern="报销", tags=("报销",)),
        ))
        reader = mock.Mock(return_value=("这是一份报销单", "utf-8", False))
        plan = runner.plan_tags([FakeItem(id=2, name="a.txt")], with_text, reader=reader)
        reader.assert_called_once_with(2, runner.TEXT_LIMIT)
        self.assertEqual(plan.items[0].tags, ("报销",))

    def test_reader_failure_only_notes(self) -> None:
        rules = RuleSet(factory=(
            _rule("明文", field=FIELD_TEXT, op=OP_CONTAINS, pattern="报销", tags=("报销",)),
            _rule("pdf", tags=("PDF",)),
        ))

        def boom(item_id, limit):
            raise OSError("读不了")

        plan = runner.plan_tags([FakeItem(id=5, suffix="pdf")], rules, reader=boom)
        self.assertEqual(plan.items[0].tags, ("PDF",))
        self.assertIn("读取《》正文失败", plan.notes[0])

    def test_tags_from_many_rules_are_deduped_in_order(self) -> None:
        rules = RuleSet(factory=(
            _rule("a", tags=("甲", "乙")),
            _rule("b", pattern="pdf", tags=("乙", "丙")),
        ))
        plan = runner.plan_tags([FakeItem(id=1, suffix="pdf")], rules)
        self.assertEqual(plan.tag_names, ("甲", "乙", "丙"))
        self.assertEqual(plan.items[0].tags, ("甲", "乙", "丙"))


class FakeApi:
    """`app.sdk.items` 的替身：记录批量写库调用。"""

    def __init__(self, *, result=1, boom=()) -> None:
        self.calls: list[tuple[list, list]] = []
        self.result = result
        self.boom = set(boom)

    def tag_items(self, ids, names) -> int:
        self.calls.append((list(ids), list(names)))
        tag = names[0]
        if tag in self.boom:
            raise RuntimeError(f"{tag} 写不进去")
        return self.result


class RunRulesCase(unittest.TestCase):
    def _plan(self):
        rules = RuleSet(factory=(
            _rule("pdf", tags=("PDF",)),
            _rule("doc", field=FIELD_SUFFIX, pattern="doc", tags=("文档",)),
        ))
        return runner.plan_tags(
            [FakeItem(id=1, name="logo9.png", suffix="pdf"), FakeItem(id=2, name="b.doc", suffix="doc")],
            rules,
        )

    def test_writes_per_tag_and_counts_real_changes(self) -> None:
        api = FakeApi(result=1)
        report = runner.run_rules(self._plan(), api=api)
        self.assertEqual([call[1] for call in api.calls], [["PDF"], ["文档"]])
        self.assertEqual(report.written, 2)
        self.assertTrue(report.ok)

    def test_zero_change_is_not_error(self) -> None:
        api = FakeApi(result=0)
        report = runner.run_rules(self._plan(), api=api)
        self.assertEqual(report.written, 0)
        self.assertTrue(report.ok)

    def test_failure_is_collected(self) -> None:
        api = FakeApi(boom=("文档",))
        report = runner.run_rules(self._plan(), api=api)
        self.assertEqual(len(report.failed), 1)
        self.assertIn("挂标签「文档」失败", report.failed[0])
        self.assertEqual(report.written, 1)
        self.assertFalse(report.ok)

    def test_cancel_stops_remaining_groups(self) -> None:
        api = FakeApi()
        report = runner.run_rules(self._plan(), api=api, cancel=lambda: True)
        self.assertEqual(api.calls, [])
        self.assertTrue(report.cancelled)

    def test_console_reports_what_happened(self) -> None:
        """控制台要能看出「跑了什么、写没写进去」：只报成功不报事件与否就是修复前的坑。"""
        plan = self._plan()
        with mock.patch.object(runner, "_console") as console:
            runner.run_rules(plan, api=FakeApi(result=1))
            written_line = str(console.info.call_args[0][0])
            console.reset_mock()
            runner.run_rules(plan, api=FakeApi(result=0))
            unchanged_line = str(console.info.call_args[0][0])
            console.reset_mock()
            runner.run_rules(plan, api=FakeApi(boom=("文档",)))
            failed_line = str(console.warning.call_args[0][0])
            console.reset_mock()
            runner.run_rules(runner.TagPlan(), api=FakeApi())
            empty_line = str(console.info.call_args[0][0])
        self.assertIn("写入 2 个标签", written_line)
        self.assertIn("logo9.png", written_line)
        self.assertIn("没有新增", unchanged_line)
        self.assertIn("失败", failed_line)
        self.assertIn("没有条目命中规则", empty_line)

    def test_progress_counts_matched_items_once(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", tags=("PDF", "文档")),
            _rule("txt", field=FIELD_SUFFIX, pattern="txt", tags=("文档",)),
        ))
        plan = runner.plan_tags(
            [FakeItem(id=1, suffix="pdf"), FakeItem(id=2, suffix="txt")], rules
        )
        seen: list[tuple[int, int]] = []
        runner.run_rules(plan, api=FakeApi(), progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(seen, [(1, 2), (2, 2)])


class FormatCase(unittest.TestCase):
    def test_configure_and_clear(self) -> None:
        rules = RuleSet()
        rules = runner.configure_suffix(rules, ".PDF", [" PDF ", "文档", "PDF"])
        self.assertEqual(runner.configured_suffix(rules, "pdf"), ("PDF", "文档"))
        rule = rules.by_key("suffix.pdf")
        self.assertEqual((rule.field, rule.op, rule.pattern, rule.source), (
            FIELD_SUFFIX, OP_IS, "pdf", SOURCE_USER,
        ))
        cleared = runner.configure_suffix(rules, "pdf", [])
        self.assertEqual(runner.configured_suffix(cleared, "pdf"), ())
        self.assertEqual(cleared.hidden, ())

    def test_clearing_factory_suffix_hides_it(self) -> None:
        rules = RuleSet(factory=(_rule("suffix.pdf", pattern="pdf", tags=("PDF",)),))
        cleared = runner.configure_suffix(rules, "pdf", [])
        self.assertEqual(runner.configured_suffix(cleared, "pdf"), ())
        self.assertIn("suffix.pdf", cleared.hidden)
        self.assertEqual([rule.key for rule in cleared.rules], [])

    def test_merge_suffixes_unions_library_and_configured(self) -> None:
        rules = runner.configure_suffix(RuleSet(), "docx", ["Word"])
        merged = runner.merge_suffixes({"PDF": 3, ".Zip": 1}, rules)
        self.assertEqual(merged, ("docx", "pdf", "zip"))


class SummaryCase(unittest.TestCase):
    def test_reports_written_and_cancelled(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", tags=("PDF",)),))
        plan = runner.plan_tags([FakeItem(id=1, suffix="pdf")], rules)
        text = runner.summary_text(runner.RunReport(plan, written=1))
        self.assertIn("扫描 1 个条目", text)
        self.assertIn("写入 1 个标签", text)
        cancelled = runner.summary_text(runner.RunReport(plan, cancelled=True))
        self.assertIn("已取消", cancelled)


class FakeHost:
    def __init__(self) -> None:
        self.toasts: list[tuple[str, str]] = []

    def toast(self, title: str, content: str = "") -> None:
        self.toasts.append((title, content))


class FakeCtx:
    """插件 `setup()` 用到的 ctx 面。"""

    def __init__(self, api) -> None:
        self.api = api
        self.pages: list[tuple] = []
        self.contributions: list[tuple] = []
        self.log = mock.Mock()
        self.host = FakeHost()

    def require(self, name):
        self.required = name
        return self.api

    def add_page(self, key, title, factory, **fields) -> None:
        self.pages.append((key, title, factory, fields))

    def contribute(self, point, value, key="", description="") -> None:
        self.contributions.append((point, value, key, description))


class FakeAutolabelApi:
    def __init__(self, rules=None) -> None:
        self._rules = rules or RuleSet(factory=(_rule("pdf", tags=("PDF",)),))
        self.saved = []

    def rules(self, *, reload=False):
        return self._rules

    def save_rules(self, rule_set) -> bool:
        self.saved.append(rule_set)
        self._rules = rule_set
        return True


def _fake_page_module():
    """把 `ui.page` 换成假模块，接线用例就不用拉起 Qt。"""
    ui = types.ModuleType("dm_plugin.auto_tag.rule.ui")
    ui.__path__ = []
    page = types.ModuleType("dm_plugin.auto_tag.rule.ui.page")

    class AutoTagRulePage:
        def __init__(self, ctx, api) -> None:
            self.ctx = ctx
            self.api = api

    page.AutoTagRulePage = AutoTagRulePage
    ui.page = page
    return {
        "dm_plugin.auto_tag.rule.ui": ui,
        "dm_plugin.auto_tag.rule.ui.page": page,
    }, AutoTagRulePage


class PluginWiringCase(unittest.TestCase):
    def setUp(self) -> None:
        patches, self.page_class = _fake_page_module()
        self._patch = mock.patch.dict(sys.modules, patches)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.api = FakeAutolabelApi()
        self.ctx = FakeCtx(self.api)
        self.plugin = plugin_module.AutoTagRulePlugin()
        self.plugin.setup(self.ctx)

    def _points(self):
        return {key: (point, value) for point, value, key, _ in self.ctx.contributions}

    def test_requires_autolabel_library(self) -> None:
        self.assertEqual(self.ctx.required, "autolabel.open")

    def test_registers_page(self) -> None:
        key, title, factory, fields = self.ctx.pages[0]
        self.assertEqual((key, title), ("auto_tag_rule", "自动标签"))
        self.assertEqual(fields["icon"], "TAG")
        self.assertEqual(fields["order"], 160)
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
        self.assertEqual(keys, ["auto_tag_rule.import", "auto_tag_rule.item", "auto_tag_rule.manage"])

    # ------------------------------------------------------------- 导入页

    def _import_ctx(self, paths, *, added=1):
        class Ctx:
            def __init__(self) -> None:
                self.applied = []
                self.messages = []

            def recent_paths(self):
                return tuple(Path(p) for p in paths)

            def apply_tags(self, names):
                self.applied.append(tuple(names))
                return added

            def toast(self, message):
                self.messages.append(message)

        return Ctx()

    def test_import_action_prefills_tags(self) -> None:
        callback = self._points()["auto_tag_rule.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\报表.pdf"])
        callback(ictx)
        self.assertEqual(ictx.applied, [("PDF",)])
        self.assertIn("预填", ictx.messages[0])

    def test_import_action_without_match(self) -> None:
        callback = self._points()["auto_tag_rule.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\笔记.xyz"])
        callback(ictx)
        self.assertEqual(ictx.applied, [])
        self.assertIn("没有命中规则", ictx.messages[0])

    def test_import_action_without_paths(self) -> None:
        callback = self._points()["auto_tag_rule.import"][1]["callback"]
        ictx = self._import_ctx([])
        callback(ictx)
        self.assertIn("还没有待导入的文件", ictx.messages[0])

    # --------------------------------------------------------- 管理页入口

    def test_toolbar_action_without_selection(self) -> None:
        callback = self._points()["auto_tag_rule.manage"][1]["callback"]
        callback(None)
        self.assertIn("先选条目", self.ctx.host.toasts[0][0])

    def test_toolbar_action_writes_tags(self) -> None:
        callback = self._points()["auto_tag_rule.manage"][1]["callback"]

        class Selection:
            def __init__(self) -> None:
                self.items = (FakeItem(id=1, name="a.pdf", suffix="pdf"),)
                self.refreshed = 0

            def do_refresh(self):
                self.refreshed += 1

        selection = Selection()
        with mock.patch.object(plugin_module.items_sdk, "tag_items", return_value=1) as tag_items:
            callback(selection)
        self.assertEqual(selection.refreshed, 1)
        self.assertEqual(tag_items.call_args[0][0], [1])
        self.assertEqual(tag_items.call_args[0][1], ["PDF"])
        self.assertIn("写入 1 个标签", self.ctx.host.toasts[0][1])

    def test_item_menu_action_uses_item_id(self) -> None:
        callback = self._points()["auto_tag_rule.item"][1]["callback"]
        with mock.patch.object(
            plugin_module.items_sdk, "get_item", return_value=FakeItem(id=9, suffix="pdf")
        ) as get_item, mock.patch.object(
            plugin_module.items_sdk, "tag_items", return_value=1
        ), mock.patch.object(plugin_module.items_sdk, "notify_changed") as notify:
            callback(types.SimpleNamespace(id=9))
        get_item.assert_called_once_with(9)
        notify.assert_called_once()

    def test_item_menu_action_without_id(self) -> None:
        callback = self._points()["auto_tag_rule.item"][1]["callback"]
        with mock.patch.object(plugin_module.items_sdk, "tag_items") as tag_items:
            callback(types.SimpleNamespace())
        tag_items.assert_not_called()

    def test_item_menu_action_when_item_is_gone(self) -> None:
        callback = self._points()["auto_tag_rule.item"][1]["callback"]
        with mock.patch.object(plugin_module.items_sdk, "get_item", return_value=None):
            callback(types.SimpleNamespace(id=9))
        self.assertIn("找不到这个条目", self.ctx.host.toasts[0][0])


if __name__ == "__main__":
    unittest.main()
