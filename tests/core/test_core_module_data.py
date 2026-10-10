"""core 模块数据：`app.core.runtime.module_data` 的读取与校验，以及三个实际数据文件与代码常量的一致性。

批 A2：core 模块把「固定、可整体替换、对外可见」的数据
放进同目录的 `<模块名>.json`，由最小加载器读取；这里保证加载器本身的行为、以及
`runtime.json` / `plugins.json` / `plugin_options.json` 与模块导出的常量不脱节。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unittest
from dataclasses import replace
from pathlib import Path

from app.core.manifest import ManifestKit, ManifestRegistry, manifest_kit
from app.core.plugins import plugin_core, plugin_options
from app.core.runtime import logging_setup, security, version
from app.core.runtime.module_data import ModuleData, ModuleDataError, load_module_data
from tmpenv import tests_tmp

_TMP = tests_tmp("core-module-data")
_KEEP_TMP = os.environ.get("DM_KEEP_TMP") == "1"


class RealModuleDataCase(unittest.TestCase):
    """真实数据文件 → 模块导出常量，两边必须一致。"""

    def test_runtime_data_matches_version_module(self):
        data = load_module_data(version.__file__, "runtime")
        self.assertEqual(data.meta["id"], "core.runtime")
        self.assertEqual(data.meta["kind"], "module-data")
        self.assertEqual(data.value("app_name"), version.APP_NAME)
        self.assertEqual(data.value("app_version"), version.APP_VERSION)
        self.assertEqual(data.value("manager_version"), version.MANAGER_VERSION)

    def test_plugins_data_matches_plugin_core(self):
        data = load_module_data(plugin_core.__file__, "plugins")
        self.assertEqual(data.value("manifest_name"), plugin_core.MANIFEST_NAME)
        self.assertEqual(data.value("source_builtin"), plugin_core.SOURCE_BUILTIN)
        self.assertEqual(data.value("source_external"), plugin_core.SOURCE_EXTERNAL)
        self.assertEqual(data.value("dm_package"), plugin_core.DM_PACKAGE)
        self.assertEqual(tuple(data.value("phases")), plugin_core.PHASES)
        self.assertEqual(frozenset(data.value("protocol_fields")), plugin_core.PROTOCOL_FIELDS)
        self.assertEqual(tuple(data.value("removed_type_fields")), plugin_core.REMOVED_TYPE_FIELDS)
        self.assertEqual(tuple(data.value("removed_data_fields")), plugin_core.REMOVED_DATA_FIELDS)
        self.assertEqual(dict(data.value("removed_protocol_fields")), plugin_core.REMOVED_PROTOCOL_FIELDS)
        self.assertEqual(
            re.compile(data.value("plugin_id_pattern")).pattern,
            plugin_core.PLUGIN_ID_PATTERN.pattern,
        )

    def test_option_data_matches_plugin_options(self):
        data = load_module_data(plugin_options.__file__, "plugins")
        labels = dict(data.value("option_kind_labels"))
        self.assertEqual(labels, plugin_options.OPTION_KIND_LABELS)
        self.assertEqual(set(labels), set(plugin_options.OPTION_KINDS))
        self.assertEqual(
            re.compile(data.value("option_key_pattern")).pattern,
            plugin_options._KEY_PATTERN.pattern,
        )
        self.assertEqual(tuple(data.value("option_true_texts")), plugin_options._TRUE_TEXTS)
        self.assertEqual(tuple(data.value("option_false_texts")), plugin_options._FALSE_TEXTS)

    def test_logging_data_matches_logging_setup(self):
        data = load_module_data(logging_setup.__file__, "runtime")
        self.assertEqual(data.value("log_format"), logging_setup.DEFAULT_FORMAT)
        self.assertEqual(data.value("log_glob"), logging_setup.LOG_GLOB)
        self.assertEqual(
            tuple(data.value("log_legacy_formats")),
            (logging_setup.LEGACY_DEFAULT_FORMAT, logging_setup.LEGACY_SOURCE_FORMAT),
        )
        specs = data.value("log_modes")
        self.assertEqual(
            {spec["name"] for spec in specs},
            {
                logging_setup.MODE_SINGLE,
                logging_setup.MODE_SESSION,
                logging_setup.MODE_DAILY,
                logging_setup.MODE_SIZE,
            },
        )
        self.assertEqual({spec["name"]: spec["label"] for spec in specs}, logging_setup.MODE_LABELS)
        self.assertEqual(
            {spec["name"]: bool(spec["uses_file_size"]) for spec in specs},
            logging_setup.MODE_USES_FILE_SIZE,
        )
        self.assertEqual(
            {spec["name"]: bool(spec["uses_keep_days"]) for spec in specs},
            logging_setup.MODE_USES_KEEP_DAYS,
        )

    def test_security_data_matches_security(self):
        data = load_module_data(security.__file__, "runtime")
        self.assertEqual(int(data.value("password_hash_iterations")), security.ITERATIONS)
        self.assertEqual(data.value("password_hash_algorithm"), "sha256")

    def test_every_managed_manifest_loads(self):
        """已统一格式的清单都要真的读得进来（结构 + schema），而不是等运行期才炸。

        登记表里的路径按当前 `paths` 现算，隔离用例会把 `PLUGIN_DIR` 指到临时目录，
        所以插件清单一律按「仓库根 / plugins /<owner>/.data/<文件名>」重新定位；
        `core.*` 的路径在 `src/app/core/` 下，不受重定向影响。

        插件清单缺席只可能是「那份插件不在这个检出里」（本地插件被 `.gitignore` 排除、
        不进发布载荷，打包冒烟 `tests/smoke_checkout.py` 就是在干净检出里跑这条用例的）——
        所以缺席要连带核对插件目录本身也不在，否则就是登记表路径写错了。
        """
        repo = Path(__file__).resolve().parents[2]
        entries = []
        for entry in manifest_kit.entries():
            if entry.owner == "core":
                entries.append(entry)
            elif entry.managed:
                entries.append(replace(entry, path=repo / "plugins" / entry.owner / ".data" / entry.path.name))
        kit = ManifestKit(ManifestRegistry(entries))
        seen = 0
        skipped: list[tuple[str, str]] = []
        for entry in kit.entries():
            if not entry.managed:
                continue
            if not entry.path.is_file():
                skipped.append((entry.id, entry.owner))
                continue
            data = kit.load(entry.id)  # 结构 + JSON Schema 校验
            self.assertTrue(data.items, f"{entry.id} 没有 items")
            self.assertTrue(data.version, f"{entry.id} 没有 version")
            self.assertEqual(data.meta.get("id"), entry.id, f"{entry.id} 的清单 id 与登记不符")
            seen += 1
        # 3 份 core 里 2 份受管（core.plugin_state 是历史格式、不受管）+ 9 份内置插件（7 个查看器 + 2 个编辑器）；
        # 本地插件（lib.model / lib.autolabel / auto_*）不在干净检出里，所以不能按 16 份卡。
        self.assertGreaterEqual(seen, 11, "受管的清单至少应有 11 份可读（2 份 core + 9 份内置插件）")
        for entry_id, owner in skipped:
            self.assertFalse(
                (repo / "plugins" / owner).is_dir(),
                f"{entry_id} 的插件目录在，清单却缺席（登记表路径写错了？）",
            )


class LoaderCase(unittest.TestCase):
    """加载器：合法数据能读出来，各种坏数据报的错要指得清。"""

    def setUp(self):
        shutil.rmtree(_TMP, ignore_errors=True)
        _TMP.mkdir(parents=True, exist_ok=True)
        self.module = _TMP / "sample.py"
        self.module.write_text("", encoding="utf-8")

    def tearDown(self):
        if not _KEEP_TMP:
            shutil.rmtree(_TMP, ignore_errors=True)

    def _write(self, payload, name: str = "sample", raw: str | None = None) -> Path:
        path = _TMP / f"{name}.json"
        text = raw if raw is not None else json.dumps(payload, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _payload(**over) -> dict:
        payload = {
            "manifest": "1",
            "id": "test.sample",
            "version": "1",
            "kind": "module-data",
            "items": [{"key": "a", "value": 1}],
        }
        payload.update(over)
        return payload

    def _assert_error(self, match: str) -> None:
        with self.assertRaises(ModuleDataError) as ctx:
            load_module_data(self.module, "sample")
        self.assertIn(match, str(ctx.exception))

    def test_load_reads_values_and_extra_fields(self):
        self._write(self._payload(items=[{"key": "a", "value": 1}, {"key": "b", "value": "x", "label": "B"}]))
        data = load_module_data(self.module, "sample")
        self.assertIsInstance(data, ModuleData)
        self.assertEqual(data.value("a"), 1)
        self.assertEqual(data.value("b"), "x")
        self.assertEqual(data.items["b"]["label"], "B")
        self.assertNotIn("key", data.items["b"])

    def test_missing_file_is_named(self):
        self._assert_error("清单文件不存在")

    def test_broken_json_is_named(self):
        self._write(self._payload(), raw="{ not json")
        self._assert_error("不是合法 JSON")

    def test_top_level_must_be_object(self):
        self._write([], raw="[]")
        self._assert_error("顶层必须是对象")

    def test_missing_required_key_is_named(self):
        for key in ("manifest", "id", "version", "kind", "items"):
            with self.subTest(missing=key):
                payload = self._payload()
                payload.pop(key)
                self._write(payload)
                self._assert_error("缺少必须项")

    def test_items_must_be_list(self):
        self._write(self._payload(items={"a": 1}))
        self._assert_error("items 必须是数组")

    def test_item_must_carry_key(self):
        self._write(self._payload(items=[{"value": 1}]))
        self._assert_error("缺少 key")

    def test_duplicate_key_is_refused(self):
        self._write(self._payload(items=[{"key": "a", "value": 1}, {"key": "a", "value": 2}]))
        self._assert_error("重复")

    def test_unknown_key_is_refused(self):
        self._write(self._payload())
        data = load_module_data(self.module, "sample")
        with self.assertRaises(ModuleDataError) as ctx:
            data.value("nope")
        self.assertIn("缺少数据项 'nope'", str(ctx.exception))

    def test_item_without_value_is_refused(self):
        self._write(self._payload(items=[{"key": "a", "label": "A"}]))
        data = load_module_data(self.module, "sample")
        with self.assertRaises(ModuleDataError) as ctx:
            data.value("a")
        self.assertIn("没有 value 字段", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
