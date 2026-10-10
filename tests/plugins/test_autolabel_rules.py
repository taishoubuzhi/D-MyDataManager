"""自动标注共享库：规则模型、匹配引擎与用户规则文件（离线用例，不联网）。

`IsolatedCase` 把 `.configs/` 重定向到 `tests/.tmp/`，所以用户规则文件写的是临时目录。
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.harness import IsolatedCase

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "lib.autolabel"


def _register_plugin_namespace() -> None:
    """把插件目录挂成 `dm_plugin.*` 包（目录名带点，只能手工注册）。"""
    for name, folder in (
        ("dm_plugin", None),
        ("dm_plugin.builtin", None),
        ("dm_plugin.builtin.lib", None),
        ("dm_plugin.builtin.lib.ui", PLUGIN_DIR.parent / "builtin.lib.ui"),
        ("dm_plugin.lib", None),
        ("dm_plugin.lib.autolabel", PLUGIN_DIR),
    ):
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [] if folder is None else [str(folder), str(Path(folder) / ".plugin")]
        sys.modules[name] = module


_register_plugin_namespace()

from dm_plugin.lib.autolabel import plugin as plugin_module


def _stub_api(**methods):
    """替身模型门面：给返回值，或给一个异常实例当 side_effect。"""
    stub = mock.Mock()
    for name, value in methods.items():
        if isinstance(value, Exception):
            getattr(stub, name).side_effect = value
        else:
            getattr(stub, name).return_value = value
    return stub  # noqa: E402
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
    restore_plan,
)


class Item:
    """替身条目：规则只看这几个属性。"""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


class FakeCtx:
    """只实现 `ctx.data(key, default)`：直接读插件目录里的出厂 JSON。"""

    def data(self, key, default=None):
        path = PLUGIN_DIR / ".data" / f"{key}.json"
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))


class BrokenCtx:
    def data(self, key, default=None):
        raise OSError("出厂文件读不了")


def factory_rules() -> tuple[tuple[Rule, ...], list[str]]:
    payload = json.loads((PLUGIN_DIR / ".data" / "rules.json").read_text(encoding="utf-8"))
    return parse_rules(payload.get("items") or [], source=SOURCE_FACTORY)


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

    def test_renaming_rule_replaces_old_key(self) -> None:
        """改「规则标识」等于换一条规则，旧标识那条不能还留在表里。"""
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="mine", tags=("自定义",)))
        rule_set = rule_set.with_rule(Rule(key="mine2", tags=("自定义",)), replacing="mine")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "b", "c", "mine2"])

    def test_renaming_factory_rule_hides_factory(self) -> None:
        """改出厂规则的标识：旧标识按「删掉了」进隐藏名单，恢复出厂能找回原样。"""
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="b", tags=("乙改",)))
        rule_set = rule_set.with_rule(Rule(key="b2", tags=("乙改",)), replacing="b")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "c", "b2"])
        self.assertEqual(rule_set.user_payload["hidden"], ["b"])
        restored = rule_set.restore_key("b")
        self.assertEqual([rule.key for rule in restored.rules], ["a", "b", "c", "b2"])

    def test_renaming_to_same_key_keeps_single_rule(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="mine", tags=("自定义",)))
        rule_set = rule_set.with_rule(Rule(key="mine", tags=("自定义改",)), replacing="mine")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "b", "c", "mine"])
        self.assertEqual(rule_set.by_key("mine").tags, ("自定义改",))

    def test_deleting_factory_rule_hides_it(self) -> None:
        rule_set = RuleSet(factory=self.factory).without_key("b")
        self.assertEqual([rule.key for rule in rule_set.rules], ["a", "c"])
        self.assertEqual(rule_set.user_payload["hidden"], ["b"])
        self.assertTrue(rule_set.factory_changed("b"))
        self.assertEqual([rule.key for rule in rule_set.restore_key("b").rules], ["a", "b", "c"])

    def test_restore_all_deleted_factory_rules(self) -> None:
        """删掉出厂规则后点「恢复出厂」：被删的规则要从隐藏名单里找回来。

        出厂规则删掉后就从规则表里消失了，选不中，只能整批恢复；这条用例盯住
        `restore_hidden` 与 `restore_plan` 这条路，别再退化成「只能恢复选中的一条」。
        """
        rule_set = RuleSet(factory=self.factory).without_key("a").without_key("c")
        self.assertEqual([rule.key for rule in rule_set.rules], ["b"])
        self.assertEqual(restore_plan(rule_set, ""), ("hidden", ("a", "c")))
        self.assertEqual(restore_plan(rule_set, "b"), ("hidden", ("a", "c")))

        restored = rule_set.restore_hidden()
        self.assertEqual([rule.key for rule in restored.rules], ["a", "b", "c"])
        self.assertEqual(restored.hidden, ())
        self.assertEqual(restored.user_payload["hidden"], [])

    def test_restore_plan_prefers_selected_changed_rule(self) -> None:
        rule_set = RuleSet(factory=self.factory).with_rule(Rule(key="b", tags=("乙改",)))
        self.assertEqual(restore_plan(rule_set, "b"), ("key", ("b",)))

    def test_restore_plan_empty_when_nothing_to_restore(self) -> None:
        rule_set = RuleSet(factory=self.factory)
        self.assertEqual(restore_plan(rule_set, ""), ("", ()))
        self.assertEqual(restore_plan(rule_set, "a"), ("", ()))

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
        with mock.patch.object(plugin_module, "_model_api", return_value=_stub_api(templates=templates)):
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

        with mock.patch.object(plugin_module, "_model_api", return_value=_stub_api(list_models=(Record(),))):
            labels, values = plugin_module.model_choices()
        self.assertEqual(values, ("local/x",))
        self.assertEqual(labels, ("X 模型",))

    def test_model_api_failure_yields_empty_choices(self) -> None:
        with mock.patch.object(plugin_module, "_model_api", return_value=_stub_api(templates=RuntimeError("没有模型插件"))):
            self.assertEqual(plugin_module.registered_models(), {})
            self.assertEqual(plugin_module.template_choices(), ((), ()))
        with mock.patch.object(plugin_module, "_model_api", return_value=_stub_api(list_models=RuntimeError("没有模型插件"))):
            self.assertEqual(plugin_module.model_choices(), ((), ()))


class RuleDialogCase(IsolatedCase):
    """规则编辑弹窗：保存按钮要跟着输入实时变，否则新建规则永远存不下去。

    以前只有「类型」下拉连了校验，新建时规则标识为空 → 保存按钮一开始就是灰的，
    之后填什么都不再重新校验，用户把每个字段都填好也点不动保存。这里盯住按钮
    随输入启用 / 禁用的联动。
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, **kwargs):
        from PyQt6.QtWidgets import QWidget

        from dm_plugin.lib.autolabel.ui.controls import RuleDialog

        # MessageBoxBase 要一个宿主控件量尺寸，不能传 None
        host = QWidget()
        dialog = RuleDialog(host, **kwargs)
        dialog._host = host
        return dialog

    def _drop(self, dialog) -> None:
        host = getattr(dialog, "_host", None)
        self.drop_widget(dialog)
        self.drop_widget(host)

    def test_new_rule_save_button_follows_typed_fields(self) -> None:
        dialog = self._dialog(title="新建规则", taken=("suffix.pdf",), kinds=(KIND_MATCH,))
        try:
            self.assertFalse(dialog.yesButton.isEnabled())
            dialog._key.setText("suffix.new")
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertIn("没有要挂的标签", dialog._hint.text())
            dialog._tags.setText("文档")
            # 标签有了，但规则还是「匹配」类型且没填匹配内容，仍不能存
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertIn("没有填匹配内容", dialog._hint.text())
            dialog._pattern.setText("txt")
            self.assertTrue(dialog.yesButton.isEnabled())
            dialog._key.setText("suffix.pdf")
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertIn("规则标识已存在", dialog._hint.text())
            dialog._key.setText("")
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertIn("规则标识不能为空", dialog._hint.text())
        finally:
            self._drop(dialog)

    def test_edit_rule_keeps_save_button_enabled(self) -> None:
        rule = Rule(key="mine", op=OP_IN, pattern="pdf", tags=("文档",))
        dialog = self._dialog(title="编辑规则", rule=rule, taken=("other",), kinds=(KIND_MATCH,))
        try:
            self.assertTrue(dialog.yesButton.isEnabled())
            self.assertEqual(dialog.rule().key, "mine")
            self.assertEqual(dialog.rule().tags, ("文档",))
        finally:
            self._drop(dialog)

    def test_prompt_field_is_editable_and_switches_kind(self) -> None:
        """提示词框一直能写字；写完自动变成「交模型判断」规则。

        以前它跟着「类型」联动置灰，而新规则默认「按字段匹配」，用户看到的就是一个
        点不进去、写不了字的提示词框（用户报的「提示词填写无法进行填写」）。
        """
        dialog = self._dialog(title="新建规则")  # kinds=None：两种类型都能选
        try:
            self.assertIsNotNone(dialog._prompt)
            self.assertTrue(dialog._prompt.isEnabled())
            self.assertFalse(dialog._prompt.isReadOnly())
            self.assertEqual(dialog._kind.currentData(), KIND_MATCH)
            dialog._key.setText("shot")
            dialog._tags.setText("截图")
            self.assertFalse(dialog.yesButton.isEnabled())  # 匹配类型还缺匹配内容
            dialog._prompt.setPlainText("这张图是不是截图？")  # 直接往提示词里打字
            self.assertEqual(dialog._kind.currentData(), KIND_PROMPT)
            self.assertFalse(dialog._pattern.isEnabled())  # 匹配行跟着让位
            self.assertTrue(dialog.yesButton.isEnabled())
            self.assertEqual(dialog.rule().kind, KIND_PROMPT)
            self.assertEqual(dialog.rule().prompt, "这张图是不是截图？")
        finally:
            self._drop(dialog)

    def test_prompt_rule_without_text_cannot_be_saved(self) -> None:
        """「交模型判断」规则没有提示词等于不跑，保存按钮要拦住。"""
        dialog = self._dialog(title="新建规则")
        try:
            dialog._kind.setCurrentIndex(dialog._kind.findData(KIND_PROMPT))
            dialog._key.setText("shot")
            dialog._tags.setText("截图")
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertIn("没有填提示词", dialog._hint.text())
            dialog._prompt.setPlainText("看看是不是截图")
            self.assertTrue(dialog.yesButton.isEnabled())
        finally:
            self._drop(dialog)

    def test_opening_a_match_rule_does_not_switch_its_kind(self) -> None:
        """打开一条本来带着提示词文本的匹配规则，不能一进来就被悄悄改成模型规则。"""
        rule = Rule(key="mine", op=OP_IN, pattern="pdf", tags=("文档",), prompt="旧提示词")
        dialog = self._dialog(title="编辑规则", rule=rule, taken=())
        try:
            self.assertEqual(dialog._kind.currentData(), KIND_MATCH)
            self.assertIn("提示词用不上", dialog._hint.text())
            self.assertFalse(dialog.yesButton.isEnabled())
            dialog._prompt.clear()
            self.assertTrue(dialog.yesButton.isEnabled())
        finally:
            self._drop(dialog)

    def test_pick_row_lists_library_and_appends(self) -> None:
        """给了候选词就要出现「从列表挑一个…」那一行，挑中直接填进标签框。

        自动标签页以前没传 `library`，这一行根本不出现，用户只能手打标签名。
        """
        dialog = self._dialog(
            title="新建规则", taken=(), kinds=(KIND_MATCH,), library=("PDF", "文档")
        )
        try:
            self.assertIsNotNone(dialog._pick)
            self.assertEqual(dialog._pick.count(), 3)  # 占位项 + 两个候选
            dialog._on_pick("文档")
            self.assertEqual(dialog._tags.text(), "文档")
            dialog._on_pick("PDF")
            self.assertEqual(dialog._tags.text(), "文档、PDF")
            dialog._on_pick("文档")  # 重复挑同一个词不再追加
            self.assertEqual(dialog._tags.text(), "文档、PDF")
            self.assertEqual(dialog._pick.currentIndex(), 0)  # 挑完回到占位项，方便连着挑
        finally:
            self._drop(dialog)

    def test_pick_row_absent_without_library(self) -> None:
        dialog = self._dialog(title="新建规则", taken=(), kinds=(KIND_MATCH,))
        try:
            self.assertIsNone(dialog._pick)
        finally:
            self._drop(dialog)


if __name__ == "__main__":
    unittest.main()
