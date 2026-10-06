"""任务 4（`auto_keyword`）：按数据类型生成关键词、写库与插件接线。

不需要 Qt：页面模块在接线用例里用假模块替换（真页面由自检与探针覆盖）。
"""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "auto_keyword"
AUTOLABEL_DIR = REPO / "plugins" / "lib.autolabel"


def _model_api_stub(side_effect=None):
    """替身模型门面（门面已迁到 lib.model）：`BatchRequest` 用真的，`run_batch` 打桩。"""
    stub = mock.Mock()
    stub.BatchRequest = BatchRequest
    if side_effect is not None:
        stub.run_batch.side_effect = side_effect
    return stub

def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.auto_keyword` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.model", REPO / "plugins" / "lib.model"),
        ("dm_plugin.lib.autolabel", AUTOLABEL_DIR),
        ("dm_plugin.auto_keyword", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()

from app.core.plugins.plugin_core import load_manifest  # noqa: E402
from dm_plugin.lib.model.api import BatchRequest, BatchResult  # noqa: E402
from dm_plugin.auto_keyword import plugin as plugin_module  # noqa: E402
from dm_plugin.auto_keyword import runner  # noqa: E402
from dm_plugin.lib.autolabel import pipeline as pipeline_module  # noqa: E402
from dm_plugin.lib.autolabel.align import (  # noqa: E402
    PURPOSE_KEYWORD,
    AlignRow,
    AlignTable,
)


@dataclass
class FakeItem:
    """够管线取字段的条目替身。"""

    id: int | None = 1
    name: str = "a.pdf"
    type: str = "DOCUMENT"


def _table(*rows: AlignRow) -> AlignTable:
    return AlignTable(purpose=PURPOSE_KEYWORD, rows=tuple(rows))


def _result(key: str, value, *, ok: bool = True) -> BatchResult:
    return BatchResult(key=key, ok=ok, value=value, model_id="local/qwen")


def _words(value):
    return tuple(value)


class OptionsCase(unittest.TestCase):
    class Ctx:
        def __init__(self, values=None) -> None:
            self.values = values or {}

        def option(self, key, default=None):
            return self.values.get(key, default)

    def test_defaults(self) -> None:
        self.assertEqual(runner.option_values(self.Ctx()), (3, 8))

    def test_clamps_minimum(self) -> None:
        ctx = self.Ctx({"min_keywords": 0, "max_keywords": 5})
        self.assertEqual(runner.option_values(ctx)[0], 1)

    def test_maximum_smaller_than_minimum(self) -> None:
        ctx = self.Ctx({"min_keywords": 6, "max_keywords": 4})
        self.assertEqual(runner.option_values(ctx), (6, 6))

    def test_zero_means_unlimited(self) -> None:
        ctx = self.Ctx({"min_keywords": 2, "max_keywords": 0})
        self.assertEqual(runner.option_values(ctx), (2, 0))

    def test_bad_values_fall_back(self) -> None:
        ctx = self.Ctx({"min_keywords": "x", "max_keywords": None})
        self.assertEqual(runner.option_values(ctx), (3, runner.DEFAULT_MAXIMUM))


class PlanKeywordsCase(unittest.TestCase):
    def test_groups_by_datatype(self) -> None:
        table = _table(
            AlignRow(datatype="DOCUMENT", model_id="local/qwen"),
            AlignRow(datatype="IMAGE", model_id="local/clip"),
        )
        items = [FakeItem(id=1, type="DOCUMENT"), FakeItem(id=2, type="IMAGE")]
        plan = runner.plan_keywords(items, table=table, minimum=3, maximum=8)
        self.assertEqual([request.key for request in plan.requests], ["1", "2"])
        self.assertEqual([request.model_id for request in plan.requests], ["local/qwen", "local/clip"])
        self.assertEqual(plan.total, 2)

    def test_model_only_for_aligned_datatype(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        items = [FakeItem(id=1, type="DOCUMENT"), FakeItem(id=2, type="IMAGE")]
        plan = runner.plan_keywords(items, table=table)
        self.assertEqual([request.key for request in plan.requests], ["1"])
        self.assertEqual(len(plan.skipped), 1)
        self.assertIn("还没有可用模型", plan.skipped[0][1])

    def test_prompt_asks_for_keywords(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table, minimum=2, maximum=6)
        payload = plan.requests[0].payload
        prompt = payload["messages"][-1]["content"]
        self.assertIn("关键词", prompt)
        self.assertIn("2-6", prompt)

    def test_explicit_model_wins(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table, explicit_model="local/other")
        self.assertEqual(plan.requests[0].model_id, "local/other")

    def test_disabled_row_is_skipped(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen", enabled=False))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table)
        self.assertEqual(plan.requests, ())
        self.assertEqual(len(plan.skipped), 1)

    def test_item_without_id_is_skipped(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=None)], table=table)
        self.assertEqual(plan.skipped[0][1], "条目没有 id")

    def test_template_needs_registration(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", template="qwen-keyword"))
        unregistered = runner.plan_keywords([FakeItem(id=1)], table=table, registered={})
        self.assertEqual(unregistered.requests, ())
        registered = runner.plan_keywords(
            [FakeItem(id=1)], table=table, registered={"qwen-keyword": "local/qwen"}
        )
        self.assertEqual(registered.requests[0].model_id, "local/qwen")

    def test_alignment_is_carried_on_items(self) -> None:
        table = _table(
            AlignRow(datatype="DOCUMENT", model_id="local/qwen", align_model="local/vision")
        )
        plan = runner.plan_keywords([FakeItem(id=1)], table=table)
        self.assertEqual(plan.items[0].alignment_id, "local/vision")

    def test_total_counts_entries_without_requests(self) -> None:
        plan = runner.plan_keywords([FakeItem(id=1), FakeItem(id=2)], table=_table())
        self.assertEqual((plan.requests, plan.total), ((), 2))

    def test_purpose_label(self) -> None:
        plan = runner.plan_keywords([], table=_table())
        self.assertEqual(plan.purpose_label, "自动关键词")
        self.assertEqual(plan.pipeline.purpose, PURPOSE_KEYWORD)


class CollectKeywordsCase(unittest.TestCase):
    def setUp(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        self.plan = runner.plan_keywords(
            [FakeItem(id=1), FakeItem(id=2)], table=table, minimum=1, maximum=3
        )

    def test_parse_and_clamp(self) -> None:
        results = [_result("1", ["甲", "乙", "丙", "丁"])]
        collected = runner.collect_keywords(self.plan, results, parse=_words)
        self.assertEqual(collected["1"], ("甲", "乙", "丙"))

    def test_zero_maximum_keeps_all(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table, minimum=1, maximum=0)
        collected = runner.collect_keywords(plan, [_result("1", ["甲", "乙", "丙", "丁"])], parse=_words)
        self.assertEqual(collected["1"], ("甲", "乙", "丙", "丁"))

    def test_below_minimum_is_dropped(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table, minimum=3, maximum=8)
        collected = runner.collect_keywords(plan, [_result("1", ["甲"])], parse=_words)
        self.assertEqual(collected, {})

    def test_failed_result_is_dropped(self) -> None:
        results = [_result("1", ["甲"], ok=False)]
        self.assertEqual(runner.collect_keywords(self.plan, results, parse=_words), {})

    def test_duplicates_merge(self) -> None:
        results = [_result("1", ["甲", "乙"]), _result("1", ["乙", "丙"])]
        collected = runner.collect_keywords(self.plan, results, parse=_words)
        self.assertEqual(collected["1"], ("甲", "乙", "丙"))


class WriteKeywordsCase(unittest.TestCase):
    class Api:
        def __init__(self, *, changed=1, boom=()) -> None:
            self.calls: list[tuple] = []
            self.changed = changed
            self.boom = tuple(boom)

        def add_keywords(self, item_ids, words):
            self.calls.append((tuple(item_ids), tuple(words)))
            if words[0] in self.boom:
                raise RuntimeError("写不进去")
            return self.changed

    def test_groups_by_keyword(self) -> None:
        api = WriteKeywordsCase.Api()
        written, failed = runner.write_keywords({"1": ("甲", "乙"), "2": ("甲",)}, api=api)
        self.assertEqual(failed, ())
        self.assertEqual(written, 2)
        self.assertEqual(set(api.calls), {(("1", "2"), ("甲",)), (("1",), ("乙",))})

    def test_counts_real_changes(self) -> None:
        api = WriteKeywordsCase.Api(changed=0)
        written, failed = runner.write_keywords({"1": ("甲",)}, api=api)
        self.assertEqual((written, failed), (0, ()))

    def test_failure_is_recorded(self) -> None:
        api = WriteKeywordsCase.Api(boom=("乙",))
        written, failed = runner.write_keywords({"1": ("甲", "乙")}, api=api)
        self.assertEqual(written, 1)
        self.assertEqual(failed[0][0], "乙")

    def test_cancel_stops_writing(self) -> None:
        api = WriteKeywordsCase.Api()
        written, failed = runner.write_keywords({"1": ("甲", "乙")}, api=api, cancel=lambda: True)
        self.assertEqual((written, failed, api.calls), (0, (), []))


class RunKeywordsCase(unittest.TestCase):
    def setUp(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        self.plan = runner.plan_keywords(
            [FakeItem(id=1)], table=table, minimum=1, maximum=3
        )

    def test_requests_go_through_run_batch(self) -> None:
        calls: list[tuple] = []

        def fake(requests, *, on_progress=None, cancel=None, max_workers=None):
            calls.append((tuple(requests), on_progress, cancel, max_workers))
            return [_result(requests[0].key, ["甲", "乙"])]

        api = WriteKeywordsCase.Api()
        with mock.patch.object(pipeline_module, "model_api_optional", return_value=_model_api_stub(fake)):
            report = runner.run_keywords(self.plan, api=api, max_workers=2)
        self.assertEqual([request.key for request in calls[0][0]], ["1"])
        self.assertEqual(calls[0][3], 2)
        self.assertEqual(report.collected, {"1": ("甲", "乙")})
        self.assertEqual(report.written, 2)
        self.assertTrue(report.ok)

    def test_no_requests_skips_run_batch(self) -> None:
        plan = runner.plan_keywords([FakeItem(id=1)], table=_table())
        stub = _model_api_stub()
        with mock.patch.object(pipeline_module, "model_api_optional", return_value=stub):
            report = runner.run_keywords(plan, api=WriteKeywordsCase.Api())
        stub.run_batch.assert_not_called()
        self.assertEqual((report.collected, report.cancelled), ({}, False))

    def test_cancelled_needs_cancel_flag(self) -> None:
        with mock.patch.object(pipeline_module, "model_api_optional", return_value=_model_api_stub(lambda *a, **k: [])):
            report = runner.run_keywords(self.plan, api=WriteKeywordsCase.Api())
        self.assertFalse(report.cancelled)

    def test_cancelled_reported(self) -> None:
        with mock.patch.object(pipeline_module, "model_api_optional", return_value=_model_api_stub(lambda *a, **k: [])):
            report = runner.run_keywords(
                self.plan, api=WriteKeywordsCase.Api(), cancel=lambda: True
            )
        self.assertTrue(report.cancelled)

    def test_failed_writes_are_collected(self) -> None:
        with mock.patch.object(
            pipeline_module, "model_api_optional", return_value=_model_api_stub(lambda *a, **k: [_result("1", ["甲"])])
        ):
            report = runner.run_keywords(
                self.plan, api=WriteKeywordsCase.Api(boom=("甲",))
            )
        self.assertEqual(len(report.failed), 1)
        self.assertFalse(report.ok)


class SummaryCase(unittest.TestCase):
    def _report(self):
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        plan = runner.plan_keywords([FakeItem(id=1)], table=table, minimum=1, maximum=3)
        return runner.KeywordReport(plan=plan, collected={"1": ("甲", "乙")}, written=2)

    def test_summary_text(self) -> None:
        text = runner.summary_text(self._report())
        self.assertIn("扫描 1 个条目", text)
        self.assertIn("写入 2 个关键词", text)

    def test_summary_text_reports_failures_and_cancel(self) -> None:
        report = runner.KeywordReport(
            plan=self._report().plan, failed=(("甲", "boom"),), cancelled=True
        )
        text = runner.summary_text(report)
        self.assertIn("没写进去", text)
        self.assertIn("已取消", text)

    def test_preview_rows(self) -> None:
        rows = runner.preview_rows(self._report())
        self.assertEqual(rows, ("a.pdf  ←  甲、乙",))

    def test_preview_rows_empty_and_skipped(self) -> None:
        plan = runner.plan_keywords([FakeItem(id=1)], table=_table())
        rows = runner.preview_rows(runner.KeywordReport(plan=plan))
        self.assertEqual(rows[0], "没有条目出关键词。")
        self.assertIn("跳过", rows[1])

    def test_preview_rows_limit(self) -> None:
        table = _table(AlignRow(datatype="DOCUMENT", model_id="local/qwen"))
        items = [FakeItem(id=index) for index in range(1, 5)]
        plan = runner.plan_keywords(items, table=table, minimum=1, maximum=3)
        report = runner.KeywordReport(
            plan=plan, collected={str(index): ("甲",) for index in range(1, 5)}
        )
        rows = runner.preview_rows(report, limit=2)
        self.assertEqual(len(rows), 3)
        self.assertIn("还有 2 个条目", rows[-1])

    def test_groups_and_word_names(self) -> None:
        report = runner.KeywordReport(
            plan=self._report().plan, collected={"1": ("甲", "乙"), "2": ("乙",)}
        )
        self.assertEqual(report.groups["乙"], ("1", "2"))
        self.assertEqual(report.word_names, ("甲", "乙"))
        self.assertEqual(report.matched, ("1", "2"))


class ManifestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(PLUGIN_DIR, builtin=False)

    def test_identity(self) -> None:
        self.assertEqual(self.manifest.id, "auto_keyword")
        self.assertEqual(self.manifest.name, "自动关键词")
        self.assertEqual(self.manifest.class_name, "AutoKeywordPlugin")
        self.assertEqual(self.manifest.entry, "plugin.py")
        self.assertFalse(self.manifest.builtin)
        self.assertFalse(self.manifest.enabled)

    def test_dependencies(self) -> None:
        self.assertEqual(
            self.manifest.depends_ids,
            ("lib.autolabel", "lib.model", "builtin.lib.ui"),
        )

    def test_no_conflicts(self) -> None:
        self.assertEqual(tuple(self.manifest.conflicts), ())

    def test_options(self) -> None:
        keys = sorted(option.key for option in self.manifest.options)
        self.assertEqual(keys, ["max_keywords", "min_keywords"])

    def test_library_entry(self) -> None:
        self.assertTrue((PLUGIN_DIR / ".plugin" / "runner.py").is_file())
        self.assertTrue((PLUGIN_DIR / ".plugin" / "ui" / "page.py").is_file())


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
    def align(self, *, reload=False):
        return AlignTable(purpose=PURPOSE_KEYWORD, rows=())


def _fake_page_module():
    ui = types.ModuleType("dm_plugin.auto_keyword.ui")
    ui.__path__ = []
    page = types.ModuleType("dm_plugin.auto_keyword.ui.page")

    class AutoKeywordPage:
        def __init__(self, ctx, api) -> None:
            self.ctx = ctx
            self.api = api

    page.AutoKeywordPage = AutoKeywordPage
    ui.page = page
    return {
        "dm_plugin.auto_keyword.ui": ui,
        "dm_plugin.auto_keyword.ui.page": page,
    }, AutoKeywordPage


class PluginWiringCase(unittest.TestCase):
    def setUp(self) -> None:
        patches, self.page_class = _fake_page_module()
        self._patch = mock.patch.dict(sys.modules, patches)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.api = FakeAutolabelApi()
        self.ctx = FakeCtx(self.api)
        self.plugin = plugin_module.AutoKeywordPlugin()
        self.plugin.setup(self.ctx)

    def _points(self):
        return {key: (point, value) for point, value, key, _ in self.ctx.contributions}

    def test_requires_autolabel_library(self) -> None:
        self.assertEqual(self.ctx.required, "autolabel.open")

    def test_registers_page(self) -> None:
        key, title, factory, fields = self.ctx.pages[0]
        self.assertEqual((key, title), ("auto_keyword", "自动关键词"))
        self.assertEqual(fields["icon"], "DICTIONARY")
        self.assertEqual(fields["order"], 162)
        page = factory()
        self.assertIsInstance(page, self.page_class)
        self.assertIs(page.api, self.api)

    def test_contributes_two_endpoints(self) -> None:
        points = {point for point, _, _, _ in self.ctx.contributions}
        self.assertEqual(points, {"app.ui.manage.toolbar", "app.ui.manage.item_menu"})
        keys = sorted(key for _, _, key, _ in self.ctx.contributions)
        self.assertEqual(keys, ["auto_keyword.item", "auto_keyword.manage"])

    def test_toolbar_starts_generation_without_navigation(self) -> None:
        callback = self._points()["auto_keyword.manage"][1]["callback"]
        selection = types.SimpleNamespace(items=(FakeItem(id=1), FakeItem(id=2)))
        with mock.patch("app.sdk.ui.open_page") as open_page, mock.patch(
            "threading.Thread"
        ) as thread_cls:
            callback(selection)
        # 不跳页：直接在数据管理页起后台任务（线程被 mock 掉，不真跑）
        open_page.assert_not_called()
        thread_cls.assert_called_once()
        self.assertIn("在后台跑", self.ctx.host.toasts[0][1])

    def test_item_menu_starts_generation_without_navigation(self) -> None:
        callback = self._points()["auto_keyword.item"][1]["callback"]
        with mock.patch("app.sdk.ui.open_page") as open_page, mock.patch(
            "threading.Thread"
        ) as thread_cls:
            callback(types.SimpleNamespace(id=9, name="报告.pdf"))
        open_page.assert_not_called()
        thread_cls.assert_called_once()
        self.assertIn("在后台跑", self.ctx.host.toasts[0][1])

    def test_starts_generation_even_without_page(self) -> None:
        """宿主没提供界面、或用户不想跳页时也能生成：入口直接起后台任务。"""
        callback = self._points()["auto_keyword.item"][1]["callback"]
        with mock.patch("app.sdk.ui.open_page", return_value=False), mock.patch("threading.Thread"):
            callback(types.SimpleNamespace(id=9))
        self.assertIn("已开始生成关键词", self.ctx.host.toasts[0][0])


if __name__ == "__main__":
    unittest.main()
