"""自动标注共享库：批处理管线（离线用例，`run_batch` 用假的替换）。"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder)]
        sys.modules[name] = module


_register_plugin_namespace()

from app.sdk.models import BatchRequest, BatchResult  # noqa: E402
from dm_plugin.lib.autolabel import pipeline as pipeline_module  # noqa: E402
from dm_plugin.lib.autolabel.align import (  # noqa: E402
    PURPOSE_KEYWORD,
    PURPOSE_LABEL,
    AlignRow,
    AlignTable,
)
from dm_plugin.lib.autolabel.pipeline import (  # noqa: E402
    DEFAULT_TASK,
    PipelinePlan,
    align_texts,
    clamp,
    collect,
    default_payload,
    default_prompt,
    failed,
    merge_names,
    parse_names,
    pending_keys,
    plan,
    run,
)


class Item:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def table(*rows: AlignRow, purpose: str = PURPOSE_LABEL) -> AlignTable:
    return AlignTable(purpose=purpose, rows=rows)


REGISTERED = {"tpl": "local/tpl-model", "clip": "local/clip"}


class ParseNamesCase(unittest.TestCase):
    def test_json_array(self) -> None:
        self.assertEqual(parse_names('["表格", "数据"]'), ("表格", "数据"))

    def test_json_object_with_tags_key(self) -> None:
        self.assertEqual(parse_names('{"tags": ["甲", "乙"]}'), ("甲", "乙"))
        self.assertEqual(parse_names('{"甲": 1, "乙": 0}'), ("甲",))

    def test_json_in_code_fence(self) -> None:
        self.assertEqual(parse_names('```json\n["甲", "乙"]\n```'), ("甲", "乙"))

    def test_non_string_values_are_dropped(self) -> None:
        self.assertEqual(parse_names('["甲", 3, null, {"a": 1}, "乙"]'), ("甲", "乙"))

    def test_bullets_and_numbering(self) -> None:
        self.assertEqual(parse_names("- 报销\n* 发票\n2. 财务\n1) 预算"), ("报销", "发票", "财务", "预算"))

    def test_separators_and_quotes(self) -> None:
        self.assertEqual(parse_names('「甲」、乙;丙\n丁'), ("甲", "乙", "丙", "丁"))

    def test_dedupe_is_case_insensitive_by_default(self) -> None:
        self.assertEqual(parse_names("Budget\nbudget"), ("Budget",))
        self.assertEqual(parse_names("Budget\nbudget", keep_case=True), ("Budget", "budget"))

    def test_limit_and_known_filter(self) -> None:
        self.assertEqual(parse_names("甲\n乙\n丙", limit=2), ("甲", "乙"))
        self.assertEqual(parse_names("甲\n乙\n丙", known=("丙",)), ("丙",))
        self.assertEqual(parse_names("Budget", known=("budget",)), ("Budget",))
        self.assertEqual(parse_names("", known=("甲",)), ())

    def test_overlong_and_empty_entries_are_dropped(self) -> None:
        self.assertEqual(parse_names("甲\n" + "字" * 60 + "\n\n"), ("甲",))

    def test_non_text_input(self) -> None:
        self.assertEqual(parse_names(None), ())
        self.assertEqual(parse_names(12345), ("12345",))


class ClampCase(unittest.TestCase):
    def test_minimum_and_maximum(self) -> None:
        self.assertEqual(clamp(["a", "b", "c"], minimum=1, maximum=2), ("a", "b"))
        self.assertEqual(clamp(["a"], minimum=2, maximum=5), ())
        self.assertEqual(clamp(["a", "b"], minimum=0), ("a", "b"))

    def test_available_and_dedupe(self) -> None:
        self.assertEqual(clamp(["a", "b", "a"], available=("b",)), ("b",))
        self.assertEqual(clamp(["", "  "], minimum=0), ())

    def test_merge_names(self) -> None:
        self.assertEqual(merge_names(["甲", "乙"], ("乙", "丙"), limit=2), ("甲", "乙"))
        self.assertEqual(merge_names(None, ()), ())


class PromptCase(unittest.TestCase):
    def test_label_and_keyword_instructions_differ(self) -> None:
        item = Item(name="报告.pdf", suffix="pdf", file_path="a/报告.pdf")
        label = default_prompt(item, "DOCUMENT", purpose=PURPOSE_LABEL, minimum=2, maximum=5)
        keyword = default_prompt(item, "DOCUMENT", purpose=PURPOSE_KEYWORD, minimum=2, maximum=5)
        self.assertIn("2-5 个中文标签", label)
        self.assertIn("2-5 个中文关键词", keyword)
        self.assertIn("名称：报告.pdf", label)
        self.assertIn("类型：文档", label)
        self.assertNotIn("内容摘录", label)

    def test_text_excerpt_is_included(self) -> None:
        prompt = default_prompt(Item(name="a.txt"), "TEXT", text="正文内容")
        self.assertIn("内容摘录：\n正文内容", prompt)

    def test_payload_shape(self) -> None:
        payload = default_payload(Item(name="a.txt"), "提示词", temperature=0.5)
        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user"])
        self.assertEqual(payload["messages"][1]["content"], "提示词")
        self.assertEqual(payload["params"], {"temperature": 0.5})


class PlanCase(unittest.TestCase):
    def setUp(self) -> None:
        self.table = table(
            AlignRow(datatype="IMAGE", template="tpl", align_template="clip"),
            AlignRow(datatype="DOCUMENT", template="tpl"),
        )
        self.items = [
            Item(id="1", name="照片.jpg", suffix="jpg", type="image", file_path="a/照片.jpg"),
            Item(id="2", name="报告.pdf", suffix="pdf", type="document", file_path="b/报告.pdf"),
        ]

    def test_queues_one_request_per_item(self) -> None:
        plan_obj = plan(self.items, purpose=PURPOSE_LABEL, table=self.table, registered=REGISTERED)
        self.assertEqual([request.key for request in plan_obj.requests], ["1", "2"])
        self.assertEqual([request.model_id for request in plan_obj.requests], ["local/tpl-model"] * 2)
        self.assertEqual(plan_obj.requests[0].task, DEFAULT_TASK)
        self.assertEqual(plan_obj.item_of("1").alignment_id, "local/clip")
        self.assertEqual(plan_obj.total, 2)
        self.assertEqual(plan_obj.skipped, ())
        self.assertEqual(plan_obj.purpose_label, "自动标签")

    def test_missing_id_and_missing_model_are_skipped(self) -> None:
        plan_obj = plan(
            [Item(name="没有 id"), *self.items],
            purpose=PURPOSE_LABEL,
            table=self.table,
            registered={},
        )
        self.assertEqual([request.key for request in plan_obj.requests], [])
        self.assertEqual(plan_obj.total, 3)
        self.assertEqual(plan_obj.skip_reason("1"), "图片还没有可用模型")
        self.assertEqual(plan_obj.skipped[0][1], "条目没有 id")

    def test_explicit_model_overrides_table(self) -> None:
        plan_obj = plan(self.items, purpose=PURPOSE_LABEL, table=self.table, explicit_model="local/manual")
        self.assertEqual({request.model_id for request in plan_obj.requests}, {"local/manual"})

    def test_disabled_row_is_skipped(self) -> None:
        disabled = table(AlignRow(datatype="IMAGE", template="tpl", enabled=False))
        plan_obj = plan(self.items, purpose=PURPOSE_LABEL, table=disabled, registered=REGISTERED)
        self.assertEqual([request.key for request in plan_obj.requests], [])
        self.assertEqual(plan_obj.skipped[0][1], "图片还没有可用模型")

    def test_unknown_datatype_maps_to_other(self) -> None:
        other = table(AlignRow(datatype="OTHER", template="tpl"))
        plan_obj = plan(
            [Item(id="9", name="x", type="不认识")],
            purpose=PURPOSE_LABEL,
            table=other,
            registered=REGISTERED,
        )
        self.assertEqual(plan_obj.item_of("9").datatype, "OTHER")
        self.assertEqual(plan_obj.skip_reason("9"), "")

    def test_callers_tags_are_lifted_out_of_payload(self) -> None:
        def payload_builder(item, prompt):
            return {"messages": [], "params": {}, "_tags": ("规则标签",)}

        plan_obj = plan(
            self.items,
            table=self.table,
            registered=REGISTERED,
            payload_builder=payload_builder,
        )
        self.assertEqual(plan_obj.item_of("1").tags, ("规则标签",))
        self.assertNotIn("_tags", plan_obj.requests[0].payload)

    def test_prompt_builder_receives_item_and_datatype(self) -> None:
        seen: list[tuple[str, str]] = []

        def prompt_builder(item, datatype, text=""):
            seen.append((item.name, datatype))
            return f"问题：{item.name}"

        plan_obj = plan(self.items, table=self.table, registered=REGISTERED, prompt_builder=prompt_builder)
        self.assertEqual(seen, [("照片.jpg", "IMAGE"), ("报告.pdf", "DOCUMENT")])
        self.assertEqual(plan_obj.requests[0].payload["messages"][1]["content"], "问题：照片.jpg")

    def test_empty_prompt_or_payload_skips_the_item(self) -> None:
        items = self.items[:1]
        empty_prompt = plan(items, table=self.table, registered=REGISTERED, prompt_builder=lambda *a, **k: "")
        self.assertEqual([reason for _item, reason in empty_prompt.skipped], ["没法组织请求内容"])
        empty_payload = plan(
            items,
            table=self.table,
            registered=REGISTERED,
            payload_builder=lambda item, prompt: "不是字典",
        )
        self.assertEqual(empty_payload.requests, ())

    def test_no_table_skips_everything(self) -> None:
        plan_obj = plan(self.items, purpose=PURPOSE_LABEL)
        self.assertEqual(plan_obj.requests, ())
        self.assertEqual(plan_obj.total, 2)


class RunCase(unittest.TestCase):
    def setUp(self) -> None:
        self.table = table(AlignRow(datatype="IMAGE", template="tpl"))
        self.plan = plan(
            [Item(id="1", name="照片.jpg", type="image")],
            table=self.table,
            registered=REGISTERED,
        )

    def test_forwards_to_sdk_run_batch(self) -> None:
        calls: list[dict] = []

        def fake_run_batch(requests, **kwargs):
            calls.append({"requests": requests, **kwargs})
            return (BatchResult(key="1", ok=True, value='["甲"]', model_id="local/tpl-model"),)

        progress = lambda done, total: None  # noqa: E731
        cancel = lambda: False  # noqa: E731
        with mock.patch.object(pipeline_module, "run_batch", side_effect=fake_run_batch):
            results = run(self.plan, on_progress=progress, cancel=cancel, max_workers=2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["requests"], self.plan.requests)
        self.assertIs(calls[0]["on_progress"], progress)
        self.assertIs(calls[0]["cancel"], cancel)
        self.assertEqual(calls[0]["max_workers"], 2)
        self.assertTrue(results[0].ok)

    def test_empty_plan_does_not_call_sdk(self) -> None:
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            self.assertEqual(run(PipelinePlan()), ())

    def test_failed_results(self) -> None:
        results = (
            BatchResult(key="1", ok=True, value="甲"),
            BatchResult(key="2", ok=False, error="模型挂了"),
        )
        self.assertEqual([result.key for result in failed(results)], ["2"])


class CollectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.table = table(AlignRow(datatype="IMAGE", template="tpl"))
        self.plan = plan(
            [Item(id="1", name="照片.jpg", type="image"), Item(id="2", name="另一张.jpg", type="image")],
            table=self.table,
            registered=REGISTERED,
        )

    def test_parses_only_successful_results(self) -> None:
        results = (
            BatchResult(key="1", ok=True, value="甲、乙"),
            BatchResult(key="2", ok=False, error="模型挂了"),
        )
        self.assertEqual(collect(self.plan, results, minimum=1), {"1": ("甲", "乙")})

    def test_minimum_maximum_and_available(self) -> None:
        results = (
            BatchResult(key="1", ok=True, value="甲、乙、丙"),
            BatchResult(key="2", ok=True, value="丁"),
        )
        collected = collect(self.plan, results, minimum=2, maximum=2)
        self.assertEqual(collected, {"1": ("甲", "乙")})
        only_known = collect(self.plan, results, minimum=1, available=("丁",))
        self.assertEqual(only_known, {"2": ("丁",)})

    def test_known_filter_and_limit(self) -> None:
        results = (BatchResult(key="1", ok=True, value="甲、乙、丙"),)
        self.assertEqual(collect(self.plan, results, known=("乙", "丙"), limit=1), {"1": ("乙",)})

    def test_custom_parser_and_duplicate_keys(self) -> None:
        results = (
            BatchResult(key="1", ok=True, value="甲"),
            BatchResult(key="1", ok=True, value="乙"),
            BatchResult(key="", ok=True, value="丙"),
        )
        collected = collect(self.plan, results, parse=lambda value: (str(value),))
        self.assertEqual(collected, {"1": ("甲", "乙")})

    def test_pending_keys(self) -> None:
        plan_obj = plan(
            [Item(id="1", name="a", type="image"), Item(id="2", name="b", type="image")],
            table=self.table,
            registered=REGISTERED,
        )
        results = (BatchResult(key="2", ok=True, value="甲"),)
        self.assertEqual(pending_keys(plan_obj, results), ("1",))
        self.assertEqual(pending_keys(plan_obj, ()), ("1", "2"))


class AlignTextsCase(unittest.TestCase):
    """对齐模型：只有图片 / 音频会先读一遍，读不了要说清楚为什么。"""

    def setUp(self) -> None:
        self.table = table(
            AlignRow(datatype="IMAGE", template="tpl", align_template="clip"),
            AlignRow(datatype="TEXT", template="tpl", align_template="vector"),
            purpose=PURPOSE_KEYWORD,
        )
        self.registered = {**REGISTERED, "vector": "local/vector"}
        self.png = "D:/库/默认用户/未分类/logo_6.png"
        self.txt = "D:/库/默认用户/未分类/笔记.txt"

    def _align(self, items, *, registered=None, on_problem=None, is_file=None):
        """默认假装文件都在盘上；要测「文件不在盘上」就传 is_file=lambda _path: False。

        （不写真实临时文件：测试进程在沙箱里建不了文件，Path 的假路径也不该骗过存在性检查。）
        """
        return align_texts(
            items,
            table=self.table,
            registered=self.registered if registered is None else registered,
            purpose=PURPOSE_KEYWORD,
            on_problem=on_problem,
            is_file=is_file or (lambda _path: True),
        )

    def test_only_image_and_audio_are_aligned(self) -> None:
        seen: list = []

        def fake_run_batch(requests):
            seen.extend(requests)
            return tuple(
                BatchResult(key=request.key, ok=True, value={"caption": f"描述 {request.key}"})
                for request in requests
            )

        with mock.patch.object(pipeline_module, "run_batch", side_effect=fake_run_batch):
            texts = self._align(
                [
                    Item(id="1", type="image", file_path="a/a.png", abs_path=self.png),
                    Item(id="2", type="text", file_path="a/b.txt", abs_path=self.txt),
                ]
            )
        self.assertEqual([request.task for request in seen], ["caption"])
        self.assertEqual(seen[0].model_id, "local/clip")
        self.assertEqual(seen[0].payload, {"input": self.png})
        self.assertEqual(texts, {"1": "描述 1"})

    def test_missing_registration_is_reported(self) -> None:
        problems: list[str] = []
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            texts = self._align(
                [Item(id="1", type="image", abs_path=self.png)],
                registered={},
                on_problem=problems.append,
            )
        self.assertEqual(texts, {})
        self.assertTrue(any("还没登记" in message for problem in problems for message in [problem]), problems)

    def test_vector_alignment_is_explained_once(self) -> None:
        problems: list[str] = []
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            self._align(
                [
                    Item(id="1", type="text", abs_path=self.txt),
                    Item(id="2", type="text", abs_path=self.txt),
                ],
                on_problem=problems.append,
            )
        self.assertEqual(len(problems), 1)
        self.assertIn("只出向量", problems[0])

    def test_missing_path_is_reported(self) -> None:
        problems: list[str] = []
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            self._align([Item(id="1", type="image")], on_problem=problems.append)
        self.assertEqual(problems, ["条目「1」没有路径，跳过对齐模型"])

    def test_failure_does_not_block_and_is_reported(self) -> None:
        problems: list[str] = []
        with mock.patch.object(pipeline_module, "run_batch", side_effect=RuntimeError("worker 挂了")):
            texts = self._align(
                [Item(id="1", type="image", abs_path=self.png)],
                on_problem=problems.append,
            )
        self.assertEqual(texts, {})
        self.assertTrue(any("读不动" in problem for problem in problems), problems)

    def test_failed_result_is_reported(self) -> None:
        problems: list[str] = []

        def fake_run_batch(requests):
            return tuple(
                BatchResult(key=request.key, ok=False, error="模型没有返回内容")
                for request in requests
            )

        with mock.patch.object(pipeline_module, "run_batch", side_effect=fake_run_batch):
            self._align(
                [Item(id="1", type="image", abs_path=self.png)],
                on_problem=problems.append,
            )
        self.assertTrue(any("对齐失败" in problem for problem in problems), problems)

    def test_relative_path_is_reported_before_reaching_the_worker(self) -> None:
        """只有相对路径时不能直接丢给 worker：那边会报英文的 FileNotFoundError（用户 m42290）。"""
        problems: list[str] = []
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            self._align(
                [Item(id="1", type="image", file_path="默认用户/未分类/logo_6.png")],
                on_problem=problems.append,
                is_file=lambda _path: False,
            )
        self.assertEqual(
            problems, ["条目「1」的文件不在盘上：默认用户/未分类/logo_6.png"]
        )

    def test_abs_path_wins_over_relative_file_path(self) -> None:
        """绝对路径优先：条目给的是库内相对路径，worker 必须拿到盘上的真路径。"""
        seen: list = []

        def fake_run_batch(requests):
            seen.extend(requests)
            return tuple(BatchResult(key=request.key, ok=True, value={"caption": "一只猫"}) for request in requests)

        with mock.patch.object(pipeline_module, "run_batch", side_effect=fake_run_batch):
            self._align(
                [Item(id="1", type="image", file_path="a/a.png", abs_path=self.png)]
            )
        self.assertEqual(seen[0].payload, {"input": self.png})

    def test_without_handler_nothing_raises(self) -> None:
        with mock.patch.object(pipeline_module, "run_batch", side_effect=AssertionError("不该调用")):
            self.assertEqual(self._align([Item(id="1", type="image")]), {})


if __name__ == "__main__":
    unittest.main()
