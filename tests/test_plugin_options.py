"""插件选项（options）协议的单元测试：清单解析、取值归一化与状态读写。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core.extensions import extension_registry  # noqa: E402
from app.core.plugin_options import (  # noqa: E402
    OPTION_BOOL,
    OPTION_CHOICE,
    OPTION_TEXT,
    PluginOptionError,
    coerce_option,
    defaults,
    parse_options,
    settings_text,
    valid_option_key,
)
from app.core.plugins import PluginError, parse_manifest  # noqa: E402
from app.core.viewers import viewer_registry  # noqa: E402
from app.services.plugin_service import PluginService  # noqa: E402

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins"


def manifest(**fields) -> dict:
    data = {
        "id": "demo.viewer",
        "name": "演示插件",
        "version": "0.1.0",
        "kind": "viewer",
        "entry": "plugin.py",
        "extensions": ["dmx"],
        "description": "测试插件",
        "author": "测试者",
        "manager_version": "0.1.0",
        "depends": [],
        "provides": ["viewer"],
    }
    data.update(fields)
    return data


class OptionSpecCase(unittest.TestCase):
    """选项声明的解析与取值归一化。"""

    def test_list_form_parses_three_kinds(self) -> None:
        specs = parse_options(
            [
                {"key": "auto_fit", "label": "打开时自适应", "kind": OPTION_BOOL, "default": True},
                {"key": "title", "kind": OPTION_TEXT, "default": "无"},
                {
                    "key": "zoom_step",
                    "kind": OPTION_CHOICE,
                    "choices": [["1.1", "小"], ["1.5", "大"]],
                    "default": "1.5",
                },
            ]
        )
        self.assertEqual([spec.key for spec in specs], ["auto_fit", "title", "zoom_step"])
        self.assertEqual(specs[0].default, True)
        self.assertEqual(specs[1].name, "title")
        self.assertEqual(specs[2].kind_label, "单选")
        self.assertEqual(specs[2].choices, (("1.1", "小"), ("1.5", "大")))

    def test_dict_form_is_supported(self) -> None:
        specs = parse_options({"auto_fit": {"label": "打开时自适应", "kind": OPTION_BOOL}})
        self.assertEqual([spec.key for spec in specs], ["auto_fit"])
        # 字典形式的键同样必须是合法选项名
        with self.assertRaises(PluginOptionError):
            parse_options({"备注": None})

    def test_dict_value_may_be_a_plain_label(self) -> None:
        specs = parse_options({"title": "标题文字"})
        self.assertEqual(specs[0].kind, OPTION_TEXT)
        self.assertEqual(specs[0].name, "标题文字")
        self.assertEqual(specs[0].default, "")

    def test_bool_default_is_false_without_explicit_default(self) -> None:
        spec = parse_options([{"key": "smooth", "kind": OPTION_BOOL}])[0]
        self.assertIs(spec.default, False)

    def test_bool_coercion_accepts_chinese_and_unknown_falls_back(self) -> None:
        spec = parse_options([{"key": "smooth", "kind": OPTION_BOOL, "default": True}])[0]
        self.assertIs(coerce_option(spec, "否"), False)
        self.assertIs(coerce_option(spec, "开"), True)
        self.assertIs(coerce_option(spec, "随便"), True)  # 回退默认值
        self.assertEqual(spec.label_of(False), "关")
        self.assertEqual(spec.label_of("1"), "开")

    def test_choice_default_is_the_first_item(self) -> None:
        spec = parse_options([{"key": "zoom_step", "kind": OPTION_CHOICE, "choices": ["1.1", "1.5"]}])[0]
        self.assertEqual(spec.default, "1.1")
        self.assertEqual(spec.label_of("1.5"), "1.5")

    def test_choice_coercion_falls_back_to_default(self) -> None:
        spec = parse_options(
            [{"key": "zoom_step", "kind": OPTION_CHOICE, "choices": {"1.1": "小", "1.5": "大"}}]
        )[0]
        self.assertEqual(coerce_option(spec, "1.5"), "1.5")
        self.assertEqual(coerce_option(spec, "9"), "1.1")
        self.assertEqual(spec.label_of("1.5"), "大")

    def test_choice_without_choices_is_error(self) -> None:
        with self.assertRaises(PluginOptionError):
            parse_options([{"key": "zoom_step", "kind": OPTION_CHOICE}])

    def test_choice_default_not_in_choices_is_error(self) -> None:
        with self.assertRaises(PluginOptionError):
            parse_options(
                [{"key": "zoom_step", "kind": OPTION_CHOICE, "choices": ["1.1"], "default": "2.0"}]
            )

    def test_invalid_kind_and_key_are_errors(self) -> None:
        with self.assertRaises(PluginOptionError):
            parse_options([{"key": "zoom", "kind": "number"}])
        with self.assertRaises(PluginOptionError):
            parse_options([{"key": "Zoom", "kind": OPTION_TEXT}])
        self.assertTrue(valid_option_key("zoom.step-1"))
        self.assertFalse(valid_option_key("zoom step"))
        self.assertFalse(valid_option_key(""))

    def test_missing_and_duplicate_key_are_errors(self) -> None:
        with self.assertRaises(PluginOptionError):
            parse_options([{"label": "没有 key"}])
        with self.assertRaises(PluginOptionError):
            parse_options([{"key": "a"}, {"key": "a"}])

    def test_non_list_or_dict_is_error(self) -> None:
        with self.assertRaises(PluginOptionError):
            parse_options("auto_fit")

    def test_empty_value_means_no_options(self) -> None:
        self.assertEqual(parse_options(None), ())
        self.assertEqual(parse_options(""), ())

    def test_defaults_and_settings_text(self) -> None:
        specs = parse_options(
            [
                {"key": "auto_fit", "label": "自适应", "kind": OPTION_BOOL, "default": True},
                {"key": "zoom_step", "label": "步长", "kind": OPTION_CHOICE, "choices": ["1.1", "1.5"]},
            ]
        )
        self.assertEqual(defaults(specs), {"auto_fit": True, "zoom_step": "1.1"})
        self.assertEqual(settings_text(specs, {"auto_fit": False}), "自适应=关")
        self.assertEqual(
            settings_text(specs, {"auto_fit": True, "zoom_step": "1.5"}),
            "自适应=开、步长=1.5",
        )
        self.assertEqual(settings_text(specs, {}), "")


class ManifestOptionsCase(unittest.TestCase):
    def test_options_flow_into_plugin_info(self) -> None:
        info = parse_manifest(
            manifest(options=[{"key": "auto_fit", "label": "自适应", "kind": OPTION_BOOL, "default": True}])
        )
        self.assertTrue(info.has_options)
        self.assertEqual(info.settings, {"auto_fit": True})
        self.assertEqual(info.option_spec("auto_fit").kind, OPTION_BOOL)
        self.assertIsNone(info.option_spec("nope"))
        self.assertIn("自适应", info.settings_text)

    def test_plugin_without_options(self) -> None:
        info = parse_manifest(manifest())
        self.assertFalse(info.has_options)
        self.assertEqual(info.options, ())
        self.assertEqual(info.settings_text, "默认")

    def test_bad_options_raise_plugin_error(self) -> None:
        with self.assertRaises(PluginError):
            parse_manifest(manifest(options=[{"key": "zoom", "kind": "number"}]))


class ServiceOptionCase(unittest.TestCase):
    """插件服务侧的选项读写与列表筛选 / 排序。"""

    def setUp(self) -> None:
        self._tmp = TempDir("plugin_options")
        self.root = Path(self._tmp.name)
        self.plugin_dir = self.root / "plugins"
        shutil.copytree(REPO_PLUGIN_DIR, self.plugin_dir)
        self.state_file = self.root / "plugins.json"
        self.service = PluginService(plugin_dir=self.plugin_dir, state_file=self.state_file)

    def tearDown(self) -> None:
        viewer_registry.clear()
        extension_registry.clear()
        self._tmp.cleanup()

    def test_builtin_image_declares_options(self) -> None:
        info = self.service.get("builtin.image")
        self.assertTrue(info.has_options)
        self.assertEqual([spec.key for spec in info.options], ["fit_on_open", "zoom_step", "smooth_scaling"])
        self.assertEqual(info.settings, {"fit_on_open": True, "zoom_step": "1.25", "smooth_scaling": True})

    def test_set_option_persists_and_reset_restores_default(self) -> None:
        self.service.set_option("builtin.image", "zoom_step", "1.5")
        self.service.set_option("builtin.image", "fit_on_open", "否")
        info = self.service.get("builtin.image")
        self.assertEqual(info.settings["zoom_step"], "1.5")
        self.assertIs(info.settings["fit_on_open"], False)
        stored = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["plugins"]["builtin.image"]["options"]["zoom_step"], "1.5")
        self.assertEqual(self.service.options_of("builtin.image")["fit_on_open"], False)
        self.assertTrue(self.service.reset_options("builtin.image"))
        self.assertFalse(self.service.reset_options("builtin.image"))
        self.assertEqual(
            self.service.options_of("builtin.image"),
            {"fit_on_open": True, "zoom_step": "1.25", "smooth_scaling": True},
        )

    def test_unknown_plugin_or_option_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.set_option("nope.plugin", "zoom_step", "1.5")
        with self.assertRaises(ValueError):
            self.service.set_option("builtin.image", "nope_option", "1.5")

    def test_author_kind_and_query_filters(self) -> None:
        self.assertEqual(self.service.authors(), ("D-MyDataManager",))
        self.assertEqual([info.id for info in self.service.all(kind="page")], ["builtin.dialog"])
        self.assertEqual(len(self.service.all(author="D-MyDataManager")), 11)
        self.assertEqual(self.service.all(author="不存在"), [])
        self.assertEqual([info.id for info in self.service.all(query="图片")], ["builtin.image"])

    def test_order_and_reverse(self) -> None:
        names = [info.name for info in self.service.all(order="name")]
        self.assertEqual(names, sorted(names))
        self.assertEqual(
            [info.name for info in self.service.all(order="name", reverse=True)],
            sorted(names, reverse=True),
        )
        # 类型排序按类型显示名（kind_label）分组：同类型插件排在一起
        kinds = [info.kind_label for info in self.service.all(order="kind")]
        self.assertEqual(kinds, sorted(kinds))
        self.assertEqual(len(kinds), 11)
        self.assertGreaterEqual(len(set(kinds)), 2)
        default_order = [info.id for info in self.service.all()]
        self.assertEqual([info.id for info in self.service.all(reverse=True)], list(reversed(default_order)))
        self.assertEqual(sorted(info.id for info in self.service.all(order="source")), sorted(default_order))
        self.assertEqual(sorted(info.id for info in self.service.all(order="state")), sorted(default_order))


if __name__ == "__main__":
    unittest.main()
