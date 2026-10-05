"""自动标注共享库：规则模型、匹配引擎与用户规则文件（离线用例，不联网）。

`IsolatedCase` 把 `.configs/` 重定向到 `tests/_tmp/`，所以用户规则文件写的是临时目录。
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.lib.autolabel` 包（目录名带点，只能手工注册）。"""
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

from dm_plugin.lib.autolabel import plugin as plugin_module  # noqa: E402
from dm_plugin.lib.autolabel.rules import (  # noqa: E402
    FIELD_NAME,
    FIELD_PATH,
    FIELD_SUFFIX,
    FIELD_TEXT,
    FIELD_TYPE,
    KIND_MATCH,
    KIND_PROMPT,
    OP_CONTAINS,
    OP_GLOB,
    OP_IN,
    OP_IS,
    OP_REGEX,
    OP_STARTSWITH,
    SOURCE_FACTORY,
    SOURCE_USER,
    Rule,
    RuleSet,
    dump_rules,
    parse_rules,
)


class Item:
    """替身条目：规则只看这几个属性。"""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


class FakeCtx:
    """只实现 `ctx.data(key, default)`：直接读插件目录里的出厂 JSON。"""

    def data(self, key, default=None):
        path = PLUGIN_DIR / "data" / f"{key}.json"
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))


class BrokenCtx:
    def data(self, key, default=None):
        raise OSError("出厂文件读不了")


def factory_rules() -> tuple[tuple[Rule, ...], list[str]]:
    payload = json.loads((PLUGIN_DIR / "data" / "rules.json").read_text(encoding="utf-8"))
    return parse_rules(payload, source=SOURCE_FACTORY)


class RuleMatchCase(unittest.TestCase):
    def test_type_field(self) -> None:
        rule = Rule(key="t", field=FIELD_TYPE, op=OP_IS, pattern="image", tags=("图片",))
        self.assertTrue(rule.match(Item(type="image")))
        self.assertFalse(rule.match(Item(type="video")))

    def test_suffix_in_splits_on_commas(self) -> None:
        rule = Rule(key="s", field=FIELD_SUFFIX, op=OP_IN, pattern="doc, docx，rtf", tags=("文档",))
        self.assertTrue(rule.match(Item(suffix="docx")))
        self.assertFalse(rule.match(Item(suffix="pdf")))

    def test_name_contains_ignores_case(self) -> None:
        rule = Rule(key="n", field=FIELD_NAME, op=OP_CONTAINS, pattern="dsc", tags=("照片",))
        self.assertTrue(rule.match(Item(name="DSC_0001.jpg")))

    def test_path_startswith(self) -> None:
        rule = Rule(key="p", field=FIELD_PATH, op=OP_STARTSWITH, pattern="reports", tags=("报表",))
        self.assertTrue(rule.match(Item(file_path="reports/2026/月报.xlsx")))
        self.assertFalse(rule.match(Item(file_path="archive/月报.xlsx")))

    def test_path_glob(self) -> None:
        rule = Rule(key="g", field=FIELD_PATH, op=OP_GLOB, pattern="*/reports/*", tags=("报表",))
        self.assertTrue(rule.match(Item(file_path="2026/reports/月报.xlsx")))
        self.assertFalse(rule.match(Item(file_path="2026/drafts/月报.xlsx")))

    def test_regex_is_case_insensitive(self) -> None:
        rule = Rule(key="r", field=FIELD_NAME, op=OP_REGEX, pattern=r"^dsc_\d+", tags=("照片",))
        self.assertTrue(rule.match(Item(name="dsc_0007.jpg")))

    def test_text_field_uses_argument(self) -> None:
        rule = Rule(key="x", field=FIELD_TEXT, op=OP_CONTAINS, pattern="发票", tags=("发票",))
        self.assertTrue(rule.match(Item(), text="这是一张增值税发票"))
        self.assertFalse(rule.match(Item()))

    def test_disabled_rule_never_matches(self) -> None:
        rule = Rule(key="d", op=OP_IN, pattern="pdf", tags=("文档",), enabled=False)
        self.assertFalse(rule.match(Item(suffix="pdf")))
        self.assertFalse(rule.usable)

    def test_prompt_rule_is_not_a_match_rule(self) -> None:
        rule = Rule(key="pr", kind=KIND_PROMPT, prompt="看看是不是截图", tags=("截图",))
        self.assertTrue(rule.model_ready)
        self.assertFalse(rule.match(Item(name="a.png")))

    def test_problems_are_hints_not_crashes(self) -> None:
        self.assertEqual(Rule(key="ok", op=OP_IN, pattern="pdf", tags=("文档",)).problems, ())
        self.assertEqual(Rule(key="none", op=OP_IN, pattern="pdf").problems, ("没有要挂的标签",))
        self.assertIn("没有填匹配内容", Rule(key="empty", tags=("文档",)).problems)
        self.assertTrue(
            any(
                "未知的匹配方式" in text
                for text in Rule(key="op", op="nope", pattern="x", tags=("a",)).problems
            )
        )
        self.assertTrue(
            any(
                "未知的匹配字段" in text
                for text in Rule(key="f", field="size", op=OP_IN, pattern="1", tags=("a",)).problems
            )
        )
        self.assertTrue(
            any(
                "未知的规则类型" in text
                for text in Rule(key="k", kind="script", op=OP_IN, pattern="x", tags=("a",)).problems
            )
        )
        bad = Rule(key="re", op=OP_REGEX, pattern="[", tags=("a",))
        self.assertTrue(any("正则表达式有问题" in text for text in bad.problems))
        self.assertFalse(bad.usable)


class RulePayloadCase(unittest.TestCase):
    def test_parse_dict_payload(self) -> None:
        rules, errors = parse_rules({"version": 1, "rules": [{"key": "a", "tags": ["甲标签"]}]})
        self.assertEqual(errors, [])
        self.assertEqual([rule.key for rule in rules], ["a"])
        self.assertEqual(rules[0].tags, ("甲标签",))

    def test_parse_bare_list(self) -> None:
        rules, errors = parse_rules([{"key": "a"}, {"key": "b"}])
        self.assertEqual([rule.key for rule in rules], ["a", "b"])
        self.assertEqual(errors, [])

    def test_missing_and_duplicate_keys_are_reported(self) -> None:
        rules, errors = parse_rules([{"tags": ["甲"]}, {"key": "a"}, {"key": "a"}])
        self.assertEqual([rule.key for rule in rules], ["a"])
        self.assertTrue(any("第 1 条规则缺少 key" in text for text in errors))
        self.assertTrue(any("规则 key 重复：a" in text for text in errors))

    def test_dump_round_trip(self) -> None:
        rules, _ = parse_rules([{"key": "a", "field": FIELD_NAME, "op": OP_CONTAINS, "pattern": "x"}])
        payload = dump_rules(rules)
        self.assertEqual(payload["version"], 1)
        again, errors = parse_rules(payload)
        self.assertEqual(errors, [])
        self.assertEqual(again[0].to_dict(), rules[0].to_dict())


class RuleSetCase(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = (
            Rule(key="a", tags=("甲",)),
            Rule(key="b", tags=("乙",)),
            Rule(key="c", op=OP_IN, pattern="pdf", tags=("文档",)),
        )

    def test_user_override_keeps_factory_position(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="b", tags=("乙改",)))
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "b", "c"])
        self.assertEqual(rule_set.by_key("b").tags, ("乙改",))
        self.assertEqual(rule_set.by_key("b").source, SOURCE_USER)

    def test_user_addition_goes_last(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="mine", tags=("自定义",)))
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "b", "c", "mine"])

    def test_deleting_factory_rule_hides_it(self) -> None:
        rule_set = RuleSet(factory=self.factory).without_key("b")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "c"])
        self.assertEqual(rule_set.user_payload["hidden"], ["b"])
        self.assertTrue(rule_set.factory_changed("b"))
        self.assertEqual([rule.key for rule in rule_set.restore_key("b").rules], ["a", "b", "c"])

    def test_deleting_user_rule_removes_it(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="mine", tags=("自定义",)))
        rule_set = rule_set.without_key("mine")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "b", "c"])
        self.assertEqual(rule_set.user_payload["hidden"], [])

    def test_only_differences_are_written(self) -> None:
        unchanged = RuleSet(factory=self.factory).with_rule(Rule(key="b", tags=("乙",)))
        self.assertEqual(unchanged.user_payload["rules"], [])
        changed = unchanged.with_rule(Rule(key="b", tags=("乙改",)))
        self.assertEqual([rule["key"] for rule in changed.user_payload["rules"]], ["b"])

    def test_match_tags_dedupes_in_order(self) -> None:
        rule_set = RuleSet(
            factory=(
                Rule(key="a", op=OP_IN, pattern="pdf", tags=("文档", "PDF")),
                Rule(key="b", field=FIELD_NAME, op=OP_CONTAINS, pattern="报告", tags=("PDF", "报告")),
            )
        )
        self.assertEqual(rule_set.match_tags(Item(name="年度报告.pdf", suffix="pdf")), ("文档", "PDF", "报告"))

    def test_prompt_rules_can_be_prefiltered(self) -> None:
        rule_set = RuleSet(
            factory=(
                Rule(key="shot", kind=KIND_PROMPT, field=FIELD_SUFFIX, op=OP_IN, pattern="png",
                     prompt="看看是不是截图", tags=("截图",)),
                Rule(key="bad", kind=KIND_PROMPT, tags=("说明",)),
            )
        )
        self.assertEqual([rule.key for rule in rule_set.prompt_rules()], ["shot"])
        self.assertEqual([rule.key for rule in rule_set.prompt_rules(Item(suffix="png"))], ["shot"])
        self.assertEqual(rule_set.prompt_rules(Item(suffix="jpg")), ())

    def test_clear_user_returns_factory_view(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="mine", tags=("自定义",)))
        self.assertEqual([rule.key for rule in rule_set.clear_user().rules], ["a", "b", "c"])

    def test_from_payload_ignores_unknown_hidden_and_identical_user_rules(self) -> None:
        payload = {"version": 1, "rules": [Rule(key="b", tags=("乙",)).to_dict()], "hidden": ["b", "zzz"]}
        rule_set = RuleSet.from_payload(payload, self.factory)
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "c"])
        self.assertEqual(rule_set.hidden, ("b",))
        self.assertEqual(rule_set.user, ())

    def test_from_payload_reports_broken_user_file(self) -> None:
        rule_set = RuleSet.from_payload({"version": 1, "rules": [{"tags": ["甲"]}]}, self.factory)
        self.assertTrue(any("缺少 key" in text for text in rule_set.errors))
        self.assertEqual(len(rule_set.rules), 3)


class FactoryRulesCase(unittest.TestCase):
    def test_factory_rules_load_clean(self) -> None:
        rules, errors = factory_rules()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(rules), 20)
        self.assertEqual(len({rule.key for rule in rules}), len(rules))

    def test_expected_matches(self) -> None:
        rule_set = RuleSet(factory=factory_rules()[0])
        cases = (
            (Item(name="报告的.pdf", suffix="pdf", type="document", file_path="a/报告.pdf"), ("文档", "PDF")),
            (Item(name="照片.jpg", suffix="jpg", type="image", file_path="b/照片.jpg"), ("图片", "照片")),
            (Item(name="main.py", suffix="py", type="code", file_path="c/main.py"), ("代码", "Python")),
            (Item(name="截图 1.png", suffix="png", type="image", file_path="d/截图 1.png"), ("图片", "照片", "截图")),
        )
        for item, expected in cases:
            with self.subTest(item=item.name):
                self.assertEqual(rule_set.match_tags(item), expected)

    def test_disabled_sample_prompt_rule_exists(self) -> None:
        rules, _ = factory_rules()
        sample = [rule for rule in rules if rule.kind == KIND_PROMPT]
        self.assertTrue(sample)
        self.assertTrue(all(not rule.enabled for rule in sample))


class AutolabelFileCase(IsolatedCase):
    def test_load_and_save_rules(self) -> None:
        ctx = FakeCtx()
        rule_set = plugin_module.load_rules(ctx)
        self.assertEqual(rule_set.errors, ())
        self.assertGreaterEqual(len(rule_set.rules), 20)

        changed = rule_set.with_rule(Rule(key="我的规则", tags=("自定义",))).without_key("suffix.pdf")
        self.assertTrue(plugin_module.save_rules(ctx, changed))
        path = plugin_module.rules_file()
        self.assertTrue(path.is_file())

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([rule["key"] for rule in payload["rules"]], ["我的规则"])
        self.assertEqual(payload["hidden"], ["suffix.pdf"])

        again = plugin_module.load_rules(ctx)
        self.assertIsNone(again.by_key("suffix.pdf"))
        self.assertEqual(again.by_key("我的规则").tags, ("自定义",))
        self.assertNotIn("suffix.pdf", [rule.key for rule in again.rules])

    def test_load_rules_without_user_file(self) -> None:
        plugin_module.rules_file().unlink(missing_ok=True)
        rule_set = plugin_module.load_rules(FakeCtx())
        self.assertEqual(rule_set.user, ())
        self.assertEqual(rule_set.hidden, ())

    def test_broken_factory_file_only_records_error(self) -> None:
        rules, errors = plugin_module.factory_rules(BrokenCtx())
        self.assertEqual(rules, ())
        self.assertTrue(any("出厂规则读取失败" in text for text in errors))

    def test_api_caches_and_reloads(self) -> None:
        api = plugin_module.AutoLabelApi(FakeCtx())
        first = api.rules()
        self.assertIs(first, api.rules())
        self.assertTrue(api.save_rules(first.clear_user()))
        self.assertTrue(api.rules().factory)
        self.assertEqual(api.rules().user, ())
        self.assertIn(plugin_module.RULES_FILE, api.path_text)
        self.assertIn(plugin_module.ALIGN_FILE, api.path_text)
        api.reload()
        self.assertEqual(len(api.rules().rules), len(first.rules))
        self.assertEqual(len(api.align().purposes), 2)

    def test_registered_models_and_choices(self) -> None:
        templates = (
            {"id": "qwen2.5-1.5b-instruct-gguf", "name": "Qwen2.5 1.5B", "size_bytes": 1117320736,
             "lightweight": True, "registered_id": "local/qwen2.5-1.5b-instruct-gguf"},
            {"id": "qwen3-8b-gguf", "name": "Qwen3 8B", "size_bytes": 5027783488, "lightweight": False},
        )
        with mock.patch.object(plugin_module.model_sdk, "templates", return_value=templates):
            self.assertEqual(
                plugin_module.registered_models(),
                {"qwen2.5-1.5b-instruct-gguf": "local/qwen2.5-1.5b-instruct-gguf"},
            )
            labels, values = plugin_module.template_choices(lightweight_only=True)
        self.assertIn("qwen2.5-1.5b-instruct-gguf", values)
        self.assertIn("GB", labels[0])
        self.assertIn("轻量", labels[0])

        class Record:
            id = "local/x"
            name = "X 模型"

        with mock.patch.object(plugin_module.model_sdk, "list_models", return_value=(Record(),)):
            labels, values = plugin_module.model_choices()
        self.assertEqual(values, ("local/x",))
        self.assertEqual(labels, ("X 模型",))

    def test_model_sdk_failure_yields_empty_choices(self) -> None:
        with mock.patch.object(plugin_module.model_sdk, "templates", side_effect=RuntimeError("没有模型插件")):
            self.assertEqual(plugin_module.registered_models(), {})
            self.assertEqual(plugin_module.template_choices(), ((), ()))
        with mock.patch.object(plugin_module.model_sdk, "list_models", side_effect=RuntimeError("没有模型插件")):
            self.assertEqual(plugin_module.model_choices(), ((), ()))


if __name__ == "__main__":
    unittest.main()
