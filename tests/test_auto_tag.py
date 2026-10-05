"""任务 3（`auto_tag`）：规则 + 模型两套方案、合并写库与插件接线。

不需要 Qt：页面模块在接线用例里用假模块替换（真页面由自检与探针覆盖）。
"""

from __future__ import annotations

import sys
import types
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
        self.assertEqual(runner.option_values(self.Ctx()), (1, 5, False))

    def test_clamps_minimum(self) -> None:
        ctx = self.Ctx({"min_tags": 0, "max_tags": 5})
        self.assertEqual(runner.option_values(ctx)[0], 1)

    def test_maximum_smaller_than_minimum(self) -> None:
        ctx = self.Ctx({"min_tags": 3, "max_tags": 2})
        self.assertEqual(runner.option_values(ctx)[:2], (3, 3))

    def test_bad_values_fall_back(self) -> None:
        ctx = self.Ctx({"min_tags": "x", "max_tags": None})
        self.assertEqual(runner.option_values(ctx)[:2], (1, 0))

    def test_merge_flag(self) -> None:
        ctx = self.Ctx({"merge_rule_tags": True})
        self.assertTrue(runner.option_values(ctx)[2])


class EffectiveMaxCase(unittest.TestCase):
    def test_limited_by_existing_tags(self) -> None:
        self.assertEqual(runner.effective_max(5, ("a", "b")), 2)

    def test_no_tags_keeps_maximum(self) -> None:
        self.assertEqual(runner.effective_max(5, ()), 5)

    def test_zero_means_unlimited(self) -> None:
        self.assertEqual(runner.effective_max(0, ("a",)), 0)

    def test_smaller_maximum_kept(self) -> None:
        self.assertEqual(runner.effective_max(3, DATATYPES), 3)


class TextRulesCase(unittest.TestCase):
    def test_any_kind_with_text_field(self) -> None:
        rules = RuleSet(factory=(
            _rule("text-match", field=FIELD_TEXT, op=OP_CONTAINS, pattern="合同", tags=("合同",)),
            _rule(
                "text-prompt",
                kind=KIND_PROMPT,
                field=FIELD_TEXT,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同",),
                prompt="只挑合同相关的标签",
            ),
            _rule("suffix", pattern="pdf", tags=("PDF",)),
            _rule(
                "off",
                field=FIELD_TEXT,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同",),
                enabled=False,
            ),
        ))
        self.assertEqual(
            tuple(rule.key for rule in runner.text_rules(rules)),
            ("text-match", "text-prompt"),
        )

    def test_prompt_rules(self) -> None:
        rules = RuleSet(factory=(
            _rule(
                "p",
                kind=KIND_PROMPT,
                field=FIELD_NAME,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同",),
                prompt="只挑合同相关的标签",
            ),
            _rule("m", pattern="pdf", tags=("PDF",)),
        ))
        self.assertEqual(tuple(rule.key for rule in runner.prompt_rules(rules)), ("p",))


class ReadTextsCase(unittest.TestCase):
    def test_without_text_rules_does_not_read(self) -> None:
        reader = mock.Mock(return_value=("正文", "utf-8", False))
        rules = RuleSet(factory=(_rule("suffix", tags=("PDF",)),))
        self.assertEqual(runner.read_texts([FakeItem()], rules, reader=reader), {})
        reader.assert_not_called()

    def test_without_reader(self) -> None:
        rules = RuleSet(factory=(_rule("t", field=FIELD_TEXT, op=OP_CONTAINS, pattern="合同", tags=("合同",)),))
        self.assertEqual(runner.read_texts([FakeItem()], rules), {})

    def test_reads_tuple_payload(self) -> None:
        rules = RuleSet(factory=(_rule("t", field=FIELD_TEXT, op=OP_CONTAINS, pattern="合同", tags=("合同",)),))
        reader = mock.Mock(return_value=("合同正文", "utf-8", False))
        self.assertEqual(runner.read_texts([FakeItem(id=3)], rules, reader=reader), {"3": "合同正文"})
        reader.assert_called_once_with("3", runner.TEXT_LIMIT)

    def test_read_failure_is_ignored(self) -> None:
        rules = RuleSet(factory=(_rule("t", field=FIELD_TEXT, op=OP_CONTAINS, pattern="合同", tags=("合同",)),))

        def reader(key, limit):
            raise OSError("读不了")

        self.assertEqual(runner.read_texts([FakeItem(id=3)], rules, reader=reader), {})

    def test_entries_without_id_are_skipped(self) -> None:
        rules = RuleSet(factory=(_rule("t", field=FIELD_TEXT, op=OP_CONTAINS, pattern="合同", tags=("合同",)),))
        reader = mock.Mock(return_value="正文")
        self.assertEqual(runner.read_texts([FakeItem(id=None)], rules, reader=reader), {})
        reader.assert_not_called()


class RuleTagsCase(unittest.TestCase):
    def test_hits_per_item(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", pattern="pdf", tags=("PDF", "文档")),
            _rule("doc", pattern="doc", tags=("Word",)),
        ))
        items = [FakeItem(id=1, suffix="pdf"), FakeItem(id=2, suffix="doc"), FakeItem(id=3, suffix="png")]
        self.assertEqual(runner.rule_tags(items, rules), {"1": ("PDF", "文档"), "2": ("Word",)})

    def test_prompt_rules_are_not_match_tags(self) -> None:
        rules = RuleSet(factory=(
            _rule("p", kind=KIND_PROMPT, field=FIELD_SUFFIX, op=OP_IS, pattern="pdf", tags=("合同",)),
        ))
        self.assertEqual(runner.rule_tags([FakeItem(id=1)], rules), {})


class ImportFileCase(unittest.TestCase):
    def test_path_fields(self) -> None:
        ref = runner.ImportFile(file_path=r"C:\a\报表.PDF")
        self.assertIsNone(ref.id)
        self.assertEqual(ref.name, "报表.PDF")
        self.assertEqual(ref.suffix, "pdf")
        self.assertEqual(ref.type, "")

    def test_tags_for_paths_dedupes(self) -> None:
        rules = RuleSet(factory=(
            _rule("pdf", pattern="pdf", tags=("PDF", "文档")),
            _rule("img", pattern="png", tags=("PDF", "图片")),
        ))
        names = runner.tags_for_paths([r"C:\a\a.pdf", r"C:\a\b.png"], rules)
        self.assertEqual(names, ("PDF", "文档", "图片"))


class PromptTagsCase(unittest.TestCase):
    def test_tags_from_matching_prompt_rule(self) -> None:
        rules = RuleSet(factory=(
            _rule(
                "p",
                kind=KIND_PROMPT,
                field=FIELD_TEXT,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同", "法务"),
                prompt="只挑合同相关的标签",
            ),
        ))
        item = FakeItem(id=1)
        self.assertEqual(runner.prompt_tags_of(item, rules, text="这是一份合同"), ("合同", "法务"))
        self.assertEqual(runner.prompt_tags_of(item, rules, text="报销单"), ())
        self.assertEqual(runner.prompt_tags([item], rules, texts={"1": "合同"}), {"1": ("合同", "法务")})

    def test_extras_only_when_matched(self) -> None:
        rules = RuleSet(factory=(
            _rule(
                "p",
                kind=KIND_PROMPT,
                field=FIELD_TEXT,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同",),
                prompt="只挑合同相关的标签",
            ),
        ))
        self.assertEqual(runner.prompt_extras(FakeItem(), rules, text="合同正文"), ("只挑合同相关的标签",))
        self.assertEqual(runner.prompt_extras(FakeItem(), rules, text="别的"), ())

    def test_prompt_rule_without_pattern_does_not_match(self) -> None:
        rules = RuleSet(factory=(
            _rule("p", kind=KIND_PROMPT, field=FIELD_SUFFIX, op=OP_IS, pattern="", tags=("合同",)),
        ))
        self.assertEqual(runner.prompt_tags_of(FakeItem(), rules), ())


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

    def test_prompt_rule_extends_prompt_and_lifts_tags(self) -> None:
        rules = RuleSet(factory=(
            _rule(
                "p",
                kind=KIND_PROMPT,
                field=FIELD_TEXT,
                op=OP_CONTAINS,
                pattern="合同",
                tags=("合同",),
                prompt="只挑合同相关的标签",
            ),
        ))
        plan = runner.plan_labels(
            [FakeItem(id=1)],
            table=self.table,
            registered=self.registered,
            rule_set=rules,
            reader=lambda key, limit: ("这是一份合同", "utf-8", False),
        )
        request = plan.requests[0]
        self.assertIn("补充要求", request.payload["messages"][1]["content"])
        self.assertIn("只挑合同相关的标签", request.payload["messages"][1]["content"])
        self.assertEqual(plan.item_of("1").tags, ("合同",))

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


class PlanRuleOnlyCase(unittest.TestCase):
    def test_no_requests_and_rule_tags(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf", tags=("PDF",)),))
        plan = runner.plan_rule_only([FakeItem(id=1), FakeItem(id=2, suffix="png")], rules)
        self.assertEqual(plan.requests, ())
        self.assertTrue(plan.merge)
        self.assertEqual(plan.total, 2)
        self.assertEqual(plan.rule_tags, {"1": ("PDF",)})


class CollectLabelsCase(unittest.TestCase):
    def _plan(self, *, merge: bool, minimum: int = 1, maximum: int = 0) -> runner.LabelPlan:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels(
            [FakeItem(id=1)],
            table=table,
            merge=merge,
            minimum=minimum,
            maximum=maximum,
        )
        return plan

    def test_model_results_only_when_not_merging(self) -> None:
        plan = self._plan(merge=False)
        collected = runner.collect_labels(plan, [_result("1", '["甲","乙"]')])
        self.assertEqual(collected, {"1": ("甲", "乙")})

    def test_merging_unions_rule_tags(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf", tags=("PDF",)),))
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels([FakeItem(id=1)], table=table, rule_set=rules, merge=True)
        collected = runner.collect_labels(plan, [_result("1", '["PDF","乙"]')])
        self.assertEqual(collected, {"1": ("PDF", "乙")})

    def test_maximum_clamps_merged_names(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf", tags=("PDF", "文档")),))
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels([FakeItem(id=1)], table=table, rule_set=rules, merge=True, maximum=2)
        collected = runner.collect_labels(plan, [_result("1", '["甲","乙"]')])
        self.assertEqual(collected, {"1": ("PDF", "文档")})

    def test_minimum_drops_short_results(self) -> None:
        plan = self._plan(merge=False, minimum=3)
        self.assertEqual(runner.collect_labels(plan, [_result("1", '["甲"]')]), {})

    def test_known_filters_model_output_only(self) -> None:
        rules = RuleSet(factory=(_rule("pdf", pattern="pdf", tags=("PDF",)),))
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_labels([FakeItem(id=1)], table=table, rule_set=rules, merge=True)
        collected = runner.collect_labels(
            plan, [_result("1", '["新词","PDF"]')], known=("PDF",)
        )
        self.assertEqual(collected, {"1": ("PDF",)})

    def test_failed_results_are_ignored(self) -> None:
        plan = self._plan(merge=False)
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
    def _plan(self, *, merge: bool = False) -> runner.LabelPlan:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        return runner.plan_labels([FakeItem(id=1)], table=table, merge=merge)

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
        self.assertIn("auto_tag.rule", info.conflicts)
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

    def test_contributes_three_endpoints(self) -> None:
        points = {point for point, _, _, _ in self.ctx.contributions}
        self.assertEqual(
            points,
            {
                "app.ui.import.action",
                "app.ui.manage.toolbar",
                "app.ui.manage.item_menu",
            },
        )
        keys = sorted(key for _, _, key, _ in self.ctx.contributions)
        self.assertEqual(keys, ["auto_tag.import", "auto_tag.item", "auto_tag.manage"])

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
        callback = self._points()["auto_tag.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\报表.pdf"])
        callback(ictx)
        self.assertEqual(ictx.applied, [("PDF",)])
        self.assertIn("预填", ictx.messages[0])

    def test_import_action_without_match(self) -> None:
        callback = self._points()["auto_tag.import"][1]["callback"]
        ictx = self._import_ctx([r"C:\a\笔记.xyz"])
        callback(ictx)
        self.assertEqual(ictx.applied, [])
        self.assertIn("没有命中规则", ictx.messages[0])

    def test_import_action_without_paths(self) -> None:
        callback = self._points()["auto_tag.import"][1]["callback"]
        ictx = self._import_ctx([])
        callback(ictx)
        self.assertIn("还没有待导入的文件", ictx.messages[0])

    # --------------------------------------------------------- 管理页入口

    def test_toolbar_action_without_selection(self) -> None:
        callback = self._points()["auto_tag.manage"][1]["callback"]
        callback(None)
        self.assertIn("先选条目", self.ctx.host.toasts[0][0])

    def test_toolbar_action_writes_tags_and_points_to_page(self) -> None:
        callback = self._points()["auto_tag.manage"][1]["callback"]

        class Selection:
            def __init__(self) -> None:
                self.items = (FakeItem(id=1, suffix="pdf"),)
                self.refreshed = 0

            def do_refresh(self):
                self.refreshed += 1

        selection = Selection()
        with mock.patch.object(plugin_module.items_sdk, "tag_items", return_value=1) as tag_items:
            callback(selection)
        self.assertEqual(selection.refreshed, 1)
        self.assertEqual(tag_items.call_args[0][0], ["1"])
        self.assertEqual(tag_items.call_args[0][1], ["PDF"])
        self.assertIn("写入 1 个标签", self.ctx.host.toasts[0][1])
        self.assertIn("自动标签", self.ctx.host.toasts[0][1])

    def test_toolbar_action_without_match(self) -> None:
        callback = self._points()["auto_tag.manage"][1]["callback"]

        class Selection:
            items = (FakeItem(id=1, suffix="xyz"),)

        with mock.patch.object(plugin_module.items_sdk, "tag_items") as tag_items:
            callback(Selection())
        tag_items.assert_not_called()
        self.assertIn("没有条目命中规则", self.ctx.host.toasts[0][1])

    def test_item_menu_action_uses_item_id(self) -> None:
        callback = self._points()["auto_tag.item"][1]["callback"]
        with mock.patch.object(
            plugin_module.items_sdk, "get_item", return_value=FakeItem(id=9, suffix="pdf")
        ) as get_item, mock.patch.object(
            plugin_module.items_sdk, "tag_items", return_value=1
        ), mock.patch.object(plugin_module.items_sdk, "notify_changed") as notify:
            callback(types.SimpleNamespace(id=9))
        get_item.assert_called_once_with(9)
        notify.assert_called_once()

    def test_item_menu_action_without_id(self) -> None:
        callback = self._points()["auto_tag.item"][1]["callback"]
        with mock.patch.object(plugin_module.items_sdk, "tag_items") as tag_items:
            callback(types.SimpleNamespace())
        tag_items.assert_not_called()

    def test_item_menu_action_when_item_is_gone(self) -> None:
        callback = self._points()["auto_tag.item"][1]["callback"]
        with mock.patch.object(plugin_module.items_sdk, "get_item", return_value=None):
            callback(types.SimpleNamespace(id=9))
        self.assertIn("找不到这个条目", self.ctx.host.toasts[0][0])


if __name__ == "__main__":
    unittest.main()
