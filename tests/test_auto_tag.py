"""任务 3（`auto_tag`）：规则 + 模型两套方案、合并写库与插件接线。

不需要 Qt：页面模块在接线用例里用假模块替换（真页面由自检与探针覆盖）。
"""

from __future__ import annotations

import sys
import types
import ast
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugins" / "auto_tag"
AUTOLABEL_DIR = REPO / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.auto_tag` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", AUTOLABEL_DIR),
        ("dm_plugin.auto_tag", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder)]
        sys.modules[name] = module


_register_plugin_namespace()

from app.core.plugin_core import load_manifest  # noqa: E402
from app.sdk.models import BatchResult  # noqa: E402
from dm_plugin.auto_tag import plugin as plugin_module  # noqa: E402
from dm_plugin.auto_tag import runner  # noqa: E402
from dm_plugin.lib.autolabel.align import (  # noqa: E402
    DATATYPES,
    PURPOSE_LABEL,
    AlignRow,
    AlignTable,
)
from dm_plugin.lib.autolabel.rules import (  # noqa: E402
    FIELD_NAME,
    FIELD_SUFFIX,
    FIELD_TEXT,
    KIND_MATCH,
    KIND_PROMPT,
    OP_CONTAINS,
    OP_IS,
    SOURCE_FACTORY,
    Rule,
    RuleSet,
)


@dataclass
class FakeItem:
    """够规则引擎与管线取字段的条目替身。"""

    id: int | None = 1
    name: str = "a.pdf"
    type: str = "DOCUMENT"
    suffix: str = "pdf"
    file_path: str = r"C:\a\a.pdf"


def _rule(
    key: str,
    *,
    kind: str = KIND_MATCH,
    field: str = FIELD_SUFFIX,
    op: str = OP_IS,
    pattern: str = "",
    tags=(),
    prompt: str = "",
    enabled: bool = True,
) -> Rule:
    return Rule(
        key=key,
        kind=kind,
        enabled=enabled,
        field=field,
        op=op,
        pattern=pattern,
        tags=tuple(tags),
        prompt=prompt,
        source=SOURCE_FACTORY,
    )


def _table(*rows: AlignRow) -> AlignTable:
    return AlignTable(purpose=PURPOSE_LABEL, rows=tuple(rows))


def _result(key: str, value, *, ok: bool = True) -> BatchResult:
    return BatchResult(key=key, ok=ok, value=value, model_id="local/qwen")


class OptionsCase(unittest.TestCase):
    class Ctx:
        def __init__(self, values=None) -> None:
            self.values = values or {}

        def option(self, key, default=None):
            return self.values.get(key, default)

    def test_defaults(self) -> None:
        self.assertEqual(runner.option_values(self.Ctx()), (1, 5, True))

    def test_clamps_minimum(self) -> None:
        ctx = self.Ctx({"min_tags": 0, "max_tags": 5})
        self.assertEqual(runner.option_values(ctx)[0], 1)

    def test_maximum_smaller_than_minimum(self) -> None:
        ctx = self.Ctx({"min_tags": 3, "max_tags": 2})
        self.assertEqual(runner.option_values(ctx)[:2], (3, 3))

    def test_bad_values_fall_back(self) -> None:
        ctx = self.Ctx({"min_tags": "x", "max_tags": None})
        self.assertEqual(runner.option_values(ctx)[:2], (1, 0))


class EffectiveMaxCase(unittest.TestCase):
    def test_limited_by_existing_tags(self) -> None:
        self.assertEqual(runner.effective_max(5, ("a", "b")), 2)

    def test_no_tags_keeps_maximum(self) -> None:
        self.assertEqual(runner.effective_max(5, ()), 5)

    def test_zero_means_unlimited(self) -> None:
        self.assertEqual(runner.effective_max(0, ("a",)), 0)

    def test_smaller_maximum_kept(self) -> None:
        self.assertEqual(runner.effective_max(3, DATATYPES), 3)


class PlanLabelsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.table = _table(
            AlignRow(datatype="DOCUMENT", template="qwen2.5-1.5b-instruct-gguf"),
            AlignRow(datatype="IMAGE", model_id="local/clip-vit-base-patch32"),
        )
        self.registered = {"qwen2.5-1.5b-instruct-gguf": "local/qwen2.5-1.5b-instruct-gguf"}

    def test_one_request_per_item(self) -> None:
        plan = runner.plan_labels(
            [FakeItem(id=1), FakeItem(id=2, name="b.png", type="IMAGE", suffix="png")],
            table=self.table,
            registered=self.registered,
            minimum=1,
            maximum=3,
        )
        self.assertEqual([request.key for request in plan.requests], ["1", "2"])
        self.assertEqual(
            [request.model_id for request in plan.requests],
            ["local/qwen2.5-1.5b-instruct-gguf", "local/clip-vit-base-patch32"],
        )
        self.assertEqual(plan.total, 2)
        self.assertEqual(plan.requests[0].task, "chat")
        self.assertIn("messages", plan.requests[0].payload)


    def test_skips_items_without_model(self) -> None:
        plan = runner.plan_labels(
            [FakeItem(id=1, type="AUDIO", name="a.mp3", suffix="mp3"), FakeItem(id=None)],
            table=self.table,
            registered=self.registered,
        )
        self.assertEqual(plan.requests, ())
        self.assertEqual(plan.total, 2)
        reasons = [reason for _, reason in plan.skipped]
        self.assertIn("音频还没有可用模型", reasons)
        self.assertIn("条目没有 id", reasons)

    def test_explicit_model_wins(self) -> None:
        plan = runner.plan_labels(
            [FakeItem(id=1), FakeItem(id=2, type="IMAGE", name="b.png", suffix="png")],
            table=self.table,
            registered=self.registered,
            explicit_model="local/other",
        )
        self.assertEqual({request.model_id for request in plan.requests}, {"local/other"})

    def test_without_table_everything_is_skipped(self) -> None:
        plan = runner.plan_labels([FakeItem(id=1)], table=AlignTable(purpose=PURPOSE_LABEL))
        self.assertEqual(plan.requests, ())
        self.assertEqual(len(plan.skipped), 1)


class CollectLabelsCase(unittest.TestCase):
    def _plan(self, *, minimum: int = 1, maximum: int = 0) -> runner.LabelPlan:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        return runner.plan_labels(
            [FakeItem(id=1)],
            table=table,
            minimum=minimum,
            maximum=maximum,
        )

    def test_model_results_are_collected(self) -> None:
        plan = self._plan()
        collected = runner.collect_labels(plan, [_result("1", '["甲","乙"]')])
        self.assertEqual(collected, {"1": ("甲", "乙")})

    def test_minimum_drops_short_results(self) -> None:
        plan = self._plan(minimum=3)
        self.assertEqual(runner.collect_labels(plan, [_result("1", '["甲"]')]), {})

    def test_known_filters_model_output_only(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels([FakeItem(id=1)], table=table)
        collected = runner.collect_labels(
            plan, [_result("1", '["新词","PDF"]')], known=("PDF",)
        )
        self.assertEqual(collected, {"1": ("PDF",)})

    def test_failed_results_are_ignored(self) -> None:
        plan = self._plan()
        self.assertEqual(runner.collect_labels(plan, [_result("1", None, ok=False)]), {})


class WriteTagsCase(unittest.TestCase):
    def test_groups_by_tag_and_counts(self) -> None:
        calls = []

        class Api:
            @staticmethod
            def tag_items(keys, names):
                calls.append((tuple(keys), tuple(names)))
                return 1

        written, failed = runner.write_tags({"1": ("甲", "乙"), "2": ("甲",)}, api=Api())
        self.assertEqual(written, 2)
        self.assertEqual(failed, ())
        self.assertEqual(sorted(calls), [(("1",), ("乙",)), (("1", "2"), ("甲",))])

    def test_failures_are_collected(self) -> None:
        class Api:
            @staticmethod
            def tag_items(keys, names):
                raise RuntimeError("写不了")

        written, failed = runner.write_tags({"1": ("甲",)}, api=Api())
        self.assertEqual(written, 0)
        self.assertEqual(failed, (("甲", "写不了"),))

    def test_cancel_stops_writing(self) -> None:
        calls = []

        class Api:
            @staticmethod
            def tag_items(keys, names):
                calls.append(names)
                return 0

        runner.write_tags({"1": ("甲", "乙")}, api=Api(), cancel=lambda: True)
        self.assertEqual(calls, [])


class RunLabelsCase(unittest.TestCase):
    def _plan(self) -> runner.LabelPlan:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        return runner.plan_labels([FakeItem(id=1)], table=table)

    def test_requests_go_through_run_batch(self) -> None:
        plan = self._plan()
        seen = {}

        def fake_run_batch(requests, **kwargs):
            seen["requests"] = tuple(requests)
            seen.update(kwargs)
            return ()

        with mock.patch.object(runner.pipeline, "run_batch", fake_run_batch):
            runner.run_labels(plan, progress=lambda done, total: None, max_workers=2)
        self.assertEqual([request.key for request in seen["requests"]], ["1"])
        self.assertEqual(seen["max_workers"], 2)
        self.assertIsNotNone(seen["on_progress"])

    def test_empty_plan_does_not_call_models(self) -> None:
        plan = runner.LabelPlan(pipeline=runner.pipeline.PipelinePlan())
        with mock.patch.object(runner.pipeline, "run_batch") as run_batch:
            report = runner.run_labels(plan)
        run_batch.assert_not_called()
        self.assertEqual(report.written, 0)

    def test_writes_collected_tags(self) -> None:
        plan = self._plan()
        api = mock.Mock()
        api.tag_items.return_value = 1
        with mock.patch.object(runner.pipeline, "run", return_value=(_result("1", '["甲"]'),)):
            report = runner.run_labels(plan, api=api)
        api.tag_items.assert_called_once_with(["1"], ["甲"])
        self.assertEqual(report.written, 1)
        self.assertEqual(report.matched, ("1",))
        self.assertTrue(report.ok)

    def test_new_tags_are_added_to_the_library(self) -> None:
        """模型给出库里还没有的标签时要先建进标签库（用户 m42577）。"""
        plan = self._plan()
        api = mock.Mock()
        api.tag_items.return_value = 1
        with mock.patch.object(runner.pipeline, "run", return_value=(_result("1", '["新词","甲"]'),)):
            runner.run_labels(plan, api=api)
        api.ensure_tags.assert_called_once_with(["新词", "甲"])

    def test_ensure_tags_survives_a_broken_api(self) -> None:
        """建标签失败不能挡住挂标签。"""
        api = mock.Mock()
        api.ensure_tags.side_effect = RuntimeError("库写不进去")
        self.assertEqual(runner.ensure_tags({"1": ("甲",)}, api=api), 1)

    def test_console_reports_what_happened(self) -> None:
        """控制台要能看出「命中多少、写没写进去」：只报成功不报事件与否就是修复前的坑。"""
        plan = self._plan()
        api = mock.Mock()
        api.tag_items.return_value = 1
        with mock.patch.object(runner.pipeline, "run", return_value=(_result("1", '["甲"]'),)):
            with mock.patch.object(runner, "_console") as console:
                runner.run_labels(plan, api=api)
                written_line = str(console.info.call_args[0][0])
                console.reset_mock()
                api.tag_items.return_value = 0
                runner.run_labels(plan, api=api)
                unchanged_line = str(console.info.call_args[0][0])
        self.assertIn("写入 1 个标签", written_line)
        self.assertIn("a.pdf", written_line)
        self.assertIn("没有新增", unchanged_line)

    def test_console_reports_nothing_to_write(self) -> None:
        plan = runner.LabelPlan(pipeline=runner.pipeline.PipelinePlan())
        with mock.patch.object(runner.pipeline, "run", return_value=()):
            with mock.patch.object(runner, "_console") as console:
                runner.run_labels(plan)
        self.assertIn("没有标签可写", str(console.info.call_args[0][0]))

    def test_cancelled_only_when_cancel_says_so(self) -> None:
        plan = self._plan()
        with mock.patch.object(runner.pipeline, "run", return_value=()):
            report = runner.run_labels(plan, cancel=lambda: False)
        self.assertFalse(report.cancelled)
        with mock.patch.object(runner.pipeline, "run", return_value=()):
            report = runner.run_labels(plan, cancel=lambda: True)
        self.assertTrue(report.cancelled)


class SummaryCase(unittest.TestCase):
    def _report(self) -> runner.LabelReport:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels(
            [FakeItem(id=1, name="a.pdf"), FakeItem(id=None, name="b.pdf")], table=table
        )
        return runner.LabelReport(plan=plan, collected={"1": ("甲",)}, written=1)

    def test_summary_text(self) -> None:
        text = runner.summary_text(self._report())
        self.assertIn("扫描 2 个条目", text)
        self.assertIn("命中 1 个", text)
        self.assertIn("写入 1 个标签", text)
        self.assertIn("跳过 1 个", text)

    def test_preview_rows(self) -> None:
        rows = runner.preview_rows(self._report())
        self.assertEqual(rows[0], "a.pdf  ←  甲")
        self.assertIn("跳过 b.pdf：条目没有 id", rows)

    def test_preview_rows_when_nothing_matched(self) -> None:
        report = runner.LabelReport(plan=runner.LabelPlan(pipeline=runner.pipeline.PipelinePlan()))
        self.assertEqual(runner.preview_rows(report), ("没有条目命中。",))


class PageWiringCase(unittest.TestCase):
    """页面里调用的 `self._xxx()` 必须在类里有定义。

    真事故（用户 m42728）：删掉规则视图时把 `_table()` 一起删了，页面一打开就
    `AttributeError: 'AutoTagPage' object has no attribute '_table'`。
    """

    def test_every_self_call_has_a_definition(self) -> None:
        tree = ast.parse((PLUGIN_DIR / "ui" / "page.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            defined = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            called = {
                sub.func.attr
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "self"
                and sub.func.attr.startswith("_")
            }
            self.assertEqual(
                sorted(called - defined),
                [],
                f"{node.name} 调用了没有定义的方法",
            )


class MergeCase(unittest.TestCase):
    """规则标签 ∪ 模型标签（用户 m42753）：可以同时按规则和模型挂，合并成一个集合。"""

    def _rule_set(self, *, tags=("规则甲",), prompt=()):
        rule_set = mock.Mock()
        rule_set.enabled = ()
        rule_set.match_tags.return_value = tuple(tags)
        rule_set.prompt_rules.return_value = tuple(prompt)
        return rule_set

    def _plan(self, rule_set, *, merge: bool = True, minimum: int = 1, maximum: int = 0):
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        return runner.plan_labels(
            [FakeItem(id=1)],
            table=table,
            rule_set=rule_set,
            merge=merge,
            minimum=minimum,
            maximum=maximum,
        )

    def test_merge_off_uses_model_only(self) -> None:
        plan = self._plan(self._rule_set(), merge=False)
        collected = runner.collect_labels(plan, [_result("1", '["模型乙"]')])
        self.assertEqual(collected, {"1": ("模型乙",)})

    def test_merge_on_unions_rule_and_model(self) -> None:
        plan = self._plan(self._rule_set())
        collected = runner.collect_labels(plan, [_result("1", '["模型乙"]')])
        self.assertEqual(collected, {"1": ("规则甲", "模型乙")})

    def test_merge_counts_rule_tags_towards_minimum(self) -> None:
        """规则已经给足时，模型给得少也不该整条作废。"""
        plan = self._plan(self._rule_set(tags=("规则甲", "规则乙")), minimum=3)
        collected = runner.collect_labels(plan, [_result("1", '["丙"]')])
        self.assertEqual(collected, {"1": ("规则甲", "规则乙", "丙")})

    def test_prompt_rules_lift_tags_and_extend_the_prompt(self) -> None:
        rule = mock.Mock(tags=("合同",), prompt="只挑合同相关的标签")
        plan = self._plan(self._rule_set(tags=(), prompt=(rule,)))
        self.assertEqual(plan.prompt_tags, {"1": ("合同",)})
        content = plan.requests[0].payload["messages"][-1]["content"]
        self.assertIn("补充要求：只挑合同相关的标签", content)

    def test_rule_tags_are_added_to_the_library_too(self) -> None:
        """规则给出的新标签也要先进标签库（用户 m42753）。"""
        plan = self._plan(self._rule_set())
        api = mock.Mock()
        api.tag_items.return_value = 1
        with mock.patch.object(runner.pipeline, "run", return_value=(_result("1", '["模型乙"]'),)):
            runner.run_labels(plan, api=api)
        api.ensure_tags.assert_called_once_with(["模型乙", "规则甲"])


class ManifestCase(unittest.TestCase):
    def test_manifest(self) -> None:
        info = load_manifest(PLUGIN_DIR, builtin=False)
        self.assertEqual(info.id, "auto_tag")
        self.assertEqual(info.class_name, "AutoTagPlugin")
        self.assertFalse(info.builtin)
        self.assertFalse(info.enabled)
        self.assertTrue((PLUGIN_DIR / info.entry).exists())
        self.assertEqual(
            info.depends_ids, ("lib.autolabel", "lib.model", "builtin.lib.ui")
        )
        self.assertIn("auto_tag.rule", info.conflicts)  # 与规则插件不能同时启用（用户 m42668）
        self.assertEqual(
            sorted(spec.key for spec in info.options),
            ["max_tags", "merge_rule_tags", "min_tags"],
        )


# ------------------------------------------------------------------ 接线


class FakeHost:
    def __init__(self) -> None:
        self.toasts: list[tuple[str, str]] = []

    def toast(self, title: str, content: str = "") -> None:
        self.toasts.append((title, content))


class FakeCtx:
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
        self._rules = rules or RuleSet(factory=(_rule("pdf", pattern="pdf", tags=("PDF",)),))

    def rules(self, *, reload=False):
        return self._rules


def _fake_page_module():
    ui = types.ModuleType("dm_plugin.auto_tag.ui")
    ui.__path__ = []
    page = types.ModuleType("dm_plugin.auto_tag.ui.page")

    class AutoTagPage:
        def __init__(self, ctx, api) -> None:
            self.ctx = ctx
            self.api = api

    page.AutoTagPage = AutoTagPage
    ui.page = page
    return {
        "dm_plugin.auto_tag.ui": ui,
        "dm_plugin.auto_tag.ui.page": page,
    }, AutoTagPage


class PluginWiringCase(unittest.TestCase):
    def setUp(self) -> None:
        patches, self.page_class = _fake_page_module()
        self._patch = mock.patch.dict(sys.modules, patches)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.api = FakeAutolabelApi()
        self.ctx = FakeCtx(self.api)
        self.plugin = plugin_module.AutoTagPlugin()
        self.plugin.setup(self.ctx)

    def _points(self):
        return {key: (point, value) for point, value, key, _ in self.ctx.contributions}

    def test_requires_autolabel_library(self) -> None:
        self.assertEqual(self.ctx.required, "autolabel.open")

    def test_registers_page(self) -> None:
        key, title, factory, fields = self.ctx.pages[0]
        self.assertEqual((key, title), ("auto_tag", "自动标签"))
        self.assertEqual(fields["icon"], "TAG")
        self.assertEqual(fields["order"], 161)
        page = factory()
        self.assertIsInstance(page, self.page_class)
        self.assertIs(page.api, self.api)

    def test_contributes_two_endpoints(self) -> None:
        """规则方案的导入页入口属于 auto_tag.rule；这里只管模型方案的两个入口。"""
        points = {point for point, _, _, _ in self.ctx.contributions}
        self.assertEqual(
            points,
            {"app.ui.manage.toolbar", "app.ui.manage.item_menu"},
        )
        keys = sorted(key for _, _, key, _ in self.ctx.contributions)
        self.assertEqual(keys, ["auto_tag.item", "auto_tag.manage"])

    # --------------------------------------------------------- 管理页入口

    def test_toolbar_action_starts_background_job(self) -> None:
        """工具栏入口改成后台生成：只负责起任务与提示，写库在线程里做。"""
        callback = self._points()["auto_tag.manage"][1]["callback"]

        class Selection:
            items = (FakeItem(id=1, suffix="pdf"),)

        with mock.patch.object(plugin_module.threading, "Thread") as thread:
            callback(Selection())
        thread.assert_called_once()
        self.assertEqual(self.ctx.host.toasts[0][0], "已开始挂标签")

    def test_toolbar_action_without_selection_covers_all(self) -> None:
        callback = self._points()["auto_tag.manage"][1]["callback"]
        with mock.patch.object(plugin_module.threading, "Thread") as thread:
            callback(None)
        thread.assert_called_once()
        self.assertIn("全部条目", self.ctx.host.toasts[0][1])

    def test_running_guard_blocks_a_second_job(self) -> None:
        callback = self._points()["auto_tag.manage"][1]["callback"]
        self.plugin._running = True
        with mock.patch.object(plugin_module.threading, "Thread") as thread:
            callback(None)
        thread.assert_not_called()
        self.assertIn("还在挂标签", self.ctx.host.toasts[0][0])

    def test_item_menu_action_starts_background_job(self) -> None:
        callback = self._points()["auto_tag.item"][1]["callback"]
        with mock.patch.object(plugin_module.threading, "Thread") as thread:
            callback(types.SimpleNamespace(id=9, name="a.pdf"))
        thread.assert_called_once()
        self.assertEqual(self.ctx.host.toasts[0][0], "已开始挂标签")

    def test_item_menu_action_without_id_covers_all(self) -> None:
        callback = self._points()["auto_tag.item"][1]["callback"]
        with mock.patch.object(plugin_module.threading, "Thread") as thread:
            callback(types.SimpleNamespace())
        thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
