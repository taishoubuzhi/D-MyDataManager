"""插件载入：冲突只影响启用、不影响载入，且启用/禁用变更与不可用原因都要进控制台；另有插件导出的打包规则。"""

from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

from loguru import logger

from tests.harness import TempDir

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.core.plugins.plugin_core import (  # noqa: E402
    PluginError,
    PluginInfo,
    load_manifest,
    parse_manifest,
    resolve_dependencies,
)
from app.services.plugin_service import PluginService  # noqa: E402

PLUGIN_DIR = REPO / "plugins"
RULE_ID = "auto_tag.rule"
TAG_ID = "auto_tag"
KEY_RULE_ID = "auto_keyword.rule"
KEY_ID = "auto_keyword"


def _info(plugin_id: str, *, conflicts=(), enabled=True) -> PluginInfo:
    return PluginInfo(id=plugin_id, name=plugin_id, conflicts=tuple(conflicts), enabled=enabled)


def _scan_real_plugins() -> list[PluginInfo]:
    infos: list[PluginInfo] = []
    for folder in sorted(PLUGIN_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not (folder / "plugin.json").is_file():
            continue
        try:
            infos.append(load_manifest(folder, builtin=False))
        except PluginError:
            continue
    return infos


class ConflictCase(unittest.TestCase):
    """`resolve_dependencies` 的冲突判定：冲突只记「谁让位」，不拦载入。"""

    def test_both_enabled_keeps_both_loaded_and_marks_the_later_one(self) -> None:
        infos = [_info("a.rule", conflicts=["b.tag"]), _info("b.tag", conflicts=["a.rule"])]
        order, errors, yielded = resolve_dependencies(infos, enabled_ids={"a.rule", "b.tag"})
        self.assertEqual(errors, {})
        self.assertEqual([info.id for info in order], ["a.rule", "b.tag"])
        self.assertEqual(yielded, {"b.tag": ("a.rule",)})

    def test_peer_disabled_is_not_a_conflict(self) -> None:
        infos = [_info("a.rule", conflicts=["b.tag"]), _info("b.tag", conflicts=["a.rule"], enabled=False)]
        order, errors, yielded = resolve_dependencies(infos, enabled_ids={"a.rule"})
        self.assertEqual(errors, {})
        self.assertEqual(yielded, {})
        self.assertEqual(sorted(info.id for info in order), ["a.rule", "b.tag"])

    def test_no_enabled_set_falls_back_to_scan(self) -> None:
        infos = [_info("a.rule", conflicts=["b.tag"]), _info("b.tag", conflicts=["a.rule"])]
        _order, errors, yielded = resolve_dependencies(infos)
        self.assertEqual(errors, {})
        self.assertEqual(sorted(yielded), ["b.tag"])

    def test_real_manifests_allow_either_plugin_alone(self) -> None:
        infos = _scan_real_plugins()
        ids = {info.id for info in infos}
        self.assertLessEqual({RULE_ID, TAG_ID}, ids)
        for enabled in ({RULE_ID}, {TAG_ID}):
            order, errors, yielded = resolve_dependencies(infos, enabled_ids=enabled)
            self.assertEqual(errors, {}, f"只启用 {enabled} 时不该有冲突：{errors}")
            self.assertEqual(yielded, {}, f"只启用 {enabled} 时不该让位：{yielded}")
            self.assertIn(next(iter(enabled)), [info.id for info in order])

    def test_conflicting_manifests_leave_one_disabled(self) -> None:
        """互斥机制：两个都启用时只有一个胜出（清单里互指 `conflicts`）。"""
        left = PluginInfo(id="fake.left", name="左", conflicts=("fake.right",))
        right = PluginInfo(id="fake.right", name="右")
        order, errors, yielded = resolve_dependencies(
            [left, right], enabled_ids={"fake.left", "fake.right"}
        )
        self.assertEqual(errors, {})
        self.assertEqual(len(yielded), 1, "互斥的两个里只有一个让位")
        loser = next(iter(yielded))
        winner = "fake.right" if loser == "fake.left" else "fake.left"
        self.assertEqual(yielded[loser], (winner,))
        self.assertIn(loser, [info.id for info in order], "冲突的插件照常载入，只是不能启用")
        self.assertIn(winner, [info.id for info in order])

    def test_real_manifests_conflict_only_for_the_later_one(self) -> None:
        """真实清单：auto_tag 与 auto_tag.rule 不能同时启用（用户 m42668）。"""
        infos = _scan_real_plugins()
        order, errors, yielded = resolve_dependencies(infos, enabled_ids={RULE_ID, TAG_ID})
        self.assertEqual(errors, {})
        self.assertEqual(sorted(yielded), [RULE_ID])
        self.assertEqual(yielded[RULE_ID], (TAG_ID,))
        self.assertIn(TAG_ID, [info.id for info in order])
        self.assertIn(RULE_ID, [info.id for info in order], "冲突的插件照常载入，只是不能启用")

    def test_real_manifests_conflict_for_the_keyword_pair(self) -> None:
        """真实清单：auto_keyword 与 auto_keyword.rule 也不能同时启用（用户 m43110）。"""
        infos = _scan_real_plugins()
        order, errors, yielded = resolve_dependencies(infos, enabled_ids={KEY_RULE_ID, KEY_ID})
        self.assertEqual(errors, {})
        self.assertEqual(sorted(yielded), [KEY_RULE_ID], "载入顺序靠前的 auto_keyword 胜出")
        self.assertEqual(yielded[KEY_RULE_ID], (KEY_ID,))
        self.assertIn(KEY_ID, [info.id for info in order])
        self.assertIn(KEY_RULE_ID, [info.id for info in order], "冲突的插件照常载入，只是不能启用")

    def test_real_manifests_also_allow_either_keyword_plugin_alone(self) -> None:
        infos = _scan_real_plugins()
        for enabled in ({KEY_RULE_ID}, {KEY_ID}):
            order, errors, yielded = resolve_dependencies(infos, enabled_ids=enabled)
            self.assertEqual(errors, {}, f"只启用 {enabled} 时不该有冲突：{errors}")
            self.assertEqual(yielded, {}, f"只启用 {enabled} 时不该让位：{yielded}")
            self.assertIn(next(iter(enabled)), [info.id for info in order])


class DiscoverCase(unittest.TestCase):
    """走真实清单的发现流程：只启用一个能干净通过，两个都启用只有后者让位。"""

    def _service(self, enabled: set[str]) -> PluginService:
        tmp = TempDir("plugin-state")
        self.addCleanup(tmp.cleanup)
        state = tmp.path / "plugins.json"
        payload = {"version": 1, "plugins": {pid: {"enabled": True} for pid in enabled}}
        state.write_text(json.dumps(payload), encoding="utf-8")
        return PluginService(plugin_dir=PLUGIN_DIR, state_file=state)

    def test_either_plugin_can_be_the_enabled_one(self) -> None:
        for enabled in (RULE_ID, TAG_ID, KEY_RULE_ID, KEY_ID):
            with self.subTest(enabled=enabled):
                infos = {info.id: info for info in self._service({enabled}).discover()}
                self.assertEqual(infos[enabled].error, "", f"{enabled} 单独启用时不该报错")
                self.assertTrue(infos[enabled].enabled)
                self.assertEqual(infos[enabled].conflict_with, ())
                self.assertEqual(infos[enabled].state_label, "已启用")

    def test_both_enabled_leaves_the_later_one_disabled_with_reason(self) -> None:
        infos = {info.id: info for info in self._service({RULE_ID, TAG_ID}).discover()}
        self.assertEqual(infos[TAG_ID].error, "")
        self.assertTrue(infos[TAG_ID].enabled)
        self.assertEqual(infos[RULE_ID].error, "", "冲突不是载入失败，不该记 error")
        self.assertEqual(infos[RULE_ID].conflict_with, (TAG_ID,))
        self.assertEqual(infos[RULE_ID].state_label, "与插件冲突")
        self.assertFalse(infos[RULE_ID].enabled)

    def test_enabling_the_later_plugin_is_refused_and_logged(self) -> None:
        service = self._service({TAG_ID})
        records: list[str] = []
        sink = logger.add(lambda message: records.append(message), level="INFO")
        self.addCleanup(logger.remove, sink)
        self.assertFalse(service.set_enabled(RULE_ID, True), "与已启用插件冲突时必须拒绝启用")
        text = "".join(records)
        self.assertIn(f"插件启用失败：{RULE_ID}（与插件冲突：{TAG_ID}）", text)
        infos = {info.id: info for info in service.discover()}
        self.assertFalse(infos[RULE_ID].enabled, "被拒绝后必须保持禁用")

    def test_keyword_pair_conflicts_the_same_way(self) -> None:
        """关键词那一对与标签那一对同一套机制：靠前者胜出，后者显示「与插件冲突」。"""
        infos = {info.id: info for info in self._service({KEY_RULE_ID, KEY_ID}).discover()}
        self.assertEqual(infos[KEY_ID].error, "")
        self.assertTrue(infos[KEY_ID].enabled)
        self.assertEqual(infos[KEY_ID].conflict_with, ())
        self.assertEqual(infos[KEY_ID].state_label, "已启用")
        self.assertEqual(infos[KEY_RULE_ID].error, "", "冲突不是载入失败，不该记 error")
        self.assertEqual(infos[KEY_RULE_ID].conflict_with, (KEY_ID,))
        self.assertEqual(infos[KEY_RULE_ID].state_label, "与插件冲突")
        self.assertFalse(infos[KEY_RULE_ID].enabled)

    def test_enabling_the_keyword_rule_plugin_is_refused_and_logged(self) -> None:
        service = self._service({KEY_ID})
        records: list[str] = []
        sink = logger.add(lambda message: records.append(message), level="INFO")
        self.addCleanup(logger.remove, sink)
        self.assertFalse(service.set_enabled(KEY_RULE_ID, True), "与已启用插件冲突时必须拒绝启用")
        self.assertIn(f"插件启用失败：{KEY_RULE_ID}（与插件冲突：{KEY_ID}）", "".join(records))
        infos = {info.id: info for info in service.discover()}
        self.assertFalse(infos[KEY_RULE_ID].enabled, "被拒绝后必须保持禁用")

    def test_enable_and_disable_changes_are_logged(self) -> None:
        service = self._service(set())
        records: list[str] = []
        sink = logger.add(lambda message: records.append(message), level="INFO")
        self.addCleanup(logger.remove, sink)
        self.assertTrue(service.set_enabled(TAG_ID, True))
        self.assertIn(f"插件已启用：{TAG_ID}", "".join(records))
        records.clear()
        self.assertTrue(service.set_enabled(TAG_ID, False))
        self.assertIn(f"插件已禁用：{TAG_ID}", "".join(records))


class SummaryCase(unittest.TestCase):
    """载入汇总要把不可用插件的 id 与原因写出来（控制台/日志直接可读）。"""

    def _service(self) -> PluginService:
        return PluginService(plugin_dir=REPO / "plugins", state_file=REPO / ".configs" / "plugins.json")

    def test_discover_error_is_named(self) -> None:
        service = self._service()
        broken = PluginInfo(id="broken.one", name="broken.one", error="清单缺少 field", error_phase="manifest")
        service._infos = {"broken.one": broken}
        service._report = []
        text = service.loaded_summary()
        self.assertIn("1 个插件不可用", text)
        self.assertIn("broken.one（[manifest] 清单缺少 field）", text)

    def test_load_failure_is_named(self) -> None:
        service = self._service()
        service._infos = {"ok.one": PluginInfo(id="ok.one", name="ok.one", enabled=True)}
        service._report = [
            ("ok.one", "setup", True, "", 0.1),
            ("bad.setup", "setup", False, "插件初始化失败：boom", 0.1),
        ]
        text = service.loaded_summary()
        self.assertIn("1 个插件不可用", text)
        self.assertIn("bad.setup（[setup] 插件初始化失败：boom）", text)

    def test_healthy_load_has_no_failure_clause(self) -> None:
        service = self._service()
        service._infos = {"ok.one": PluginInfo(id="ok.one", name="ok.one", enabled=True)}
        service._report = [("ok.one", "setup", True, "", 0.1)]
        self.assertNotIn("不可用", service.loaded_summary())


class ManifestFieldCase(unittest.TestCase):
    """协议里取消的字段要当场报错并指明新写法；在用的字段照常收下。"""

    def _manifest(self, **extra) -> dict:
        data = {
            "id": "demo.plugin",
            "name": "示例插件",
            "version": "1.0",
            "api_version": ">=0.1",
            "entry": "plugin.py",
        }
        data.update(extra)
        return data

    def test_removed_fields_name_the_replacement(self) -> None:
        cases = (
            ("incompatible", "conflicts"),
            ("load_after", "depends"),
            ("manager_version", "api_version"),
            ("data", ".data/"),
        )
        for field, instead in cases:
            with self.assertRaises(PluginError) as ctx:
                parse_manifest(self._manifest(**{field: "other.plugin"}))
            text = str(ctx.exception)
            self.assertIn(f"插件清单已取消字段 {field}", text)
            self.assertIn(instead, text)

    def test_unknown_field_is_still_reported(self) -> None:
        with self.assertRaises(PluginError) as ctx:
            parse_manifest(self._manifest(nonsense=True))
        self.assertIn("插件清单出现未知字段", str(ctx.exception))

    def test_live_fields_still_parse(self) -> None:
        info = parse_manifest(
            self._manifest(conflicts=["other.plugin"], api_version=">=0.1", enabled=False)
        )
        self.assertEqual(info.conflicts, ("other.plugin",))
        self.assertEqual(info.api_version, ">=0.1")
        self.assertFalse(info.enabled)
        for gone in ("incompatible", "load_after", "manager_version"):
            self.assertFalse(hasattr(info, gone))

    def test_real_manifests_use_no_removed_fields(self) -> None:
        infos = _scan_real_plugins()
        self.assertTrue(infos)
        for info in infos:
            for field in ("incompatible", "load_after", "manager_version", "data"):
                self.assertNotIn(field, info.manifest, f"{info.id} 仍写着 {field}")


class ExportCase(unittest.TestCase):
    """插件导出：一个插件一个 zip，多个插件各一个 zip 再套总包，且导出的包能原样导回来。"""

    def _fixture(self) -> tuple[PluginService, Path]:
        tmp = TempDir("plugin-export")
        self.addCleanup(tmp.cleanup)
        plugin_dir = tmp.path / "plugins"
        for plugin_id, name in (("demo.alpha", "甲插件"), ("demo.beta", "乙插件")):
            folder = plugin_dir / plugin_id
            (folder / ".plugin").mkdir(parents=True)
            (folder / ".data").mkdir()
            (folder / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": plugin_id,
                        "name": name,
                        "version": "1.0.0",
                        "api_version": ">=1.0 <2.0",
                        "entry": "plugin.py",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (folder / "plugin.py").write_text("from app.sdk import Plugin\n", encoding="utf-8")
            (folder / ".plugin" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            (folder / ".data" / "state.json").write_text('{"n": 1}', encoding="utf-8")
            # 缓存不该被打进包里
            (folder / "__pycache__").mkdir()
            (folder / "__pycache__" / "plugin.cpython-313.pyc").write_bytes(b"\x00\x01")
        state_file = tmp.path / "plugins.json"
        state_file.write_text(json.dumps({"version": 1, "plugins": {}}), encoding="utf-8")
        return PluginService(plugin_dir=plugin_dir, state_file=state_file), tmp.path

    def test_single_plugin_becomes_one_zip_without_the_cache(self) -> None:
        service, root = self._fixture()
        result = service.export_plugins(["demo.alpha"], root / "out" / "demo.alpha")
        self.assertEqual(result.exported, ("demo.alpha",))
        self.assertEqual(result.skipped, ())
        self.assertEqual(result.path.name, "demo.alpha.zip", "没写后缀时要自己补上 .zip")
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
        self.assertIn("plugin.json", names)
        self.assertIn("plugin.py", names)
        self.assertIn(".plugin/helper.py", names)
        self.assertIn(".data/state.json", names)
        self.assertFalse([name for name in names if "__pycache__" in name or name.endswith(".pyc")])
        self.assertFalse(list(result.path.parent.glob("*.part")), "导出成功后不该留下半成品")

    def test_exported_zip_can_be_read_back_as_a_plugin(self) -> None:
        """导出的包要能被「导入插件包」那条路径原样读出清单（这里只解包 + 校验，不真正载入）。"""
        service, root = self._fixture()
        result = service.export_plugins(["demo.alpha"], root / "out" / "demo.alpha.zip")
        extracted = root / "again"
        extracted.mkdir()
        folder = PluginService._find_plugin_root(PluginService._extract_zip(result.path, extracted))
        info = load_manifest(folder)
        self.assertEqual(info.id, "demo.alpha")
        self.assertEqual(info.name, "甲插件")

    def test_many_plugins_are_zipped_one_by_one_then_wrapped(self) -> None:
        service, root = self._fixture()
        result = service.export_plugins(
            ["demo.alpha", "demo.beta", "demo.alpha"], root / "out" / "插件导出.zip"
        )
        self.assertEqual(result.exported, ("demo.alpha", "demo.beta"), "重复的 id 只导一次")
        with zipfile.ZipFile(result.path) as archive:
            self.assertEqual(sorted(archive.namelist()), ["demo.alpha.zip", "demo.beta.zip"])
            with zipfile.ZipFile(io.BytesIO(archive.read("demo.alpha.zip"))) as inner:
                self.assertIn("plugin.json", inner.namelist())
                self.assertNotIn("demo.alpha.zip", inner.namelist())

    def test_missing_plugin_dir_is_reported_not_dropped(self) -> None:
        service, root = self._fixture()
        result = service.export_plugins(["demo.alpha", "demo.gone"], root / "out" / "部分.zip")
        self.assertEqual(result.exported, ("demo.alpha",))
        self.assertEqual(result.skipped, ("demo.gone",))
        self.assertIn("1 个插件的目录找不到了", result.summary())

    def test_nothing_exportable_raises(self) -> None:
        service, root = self._fixture()
        for ids in ([], ["demo.gone"]):
            with self.subTest(ids=ids), self.assertRaises(PluginError):
                service.export_plugins(ids, root / "out" / "空.zip")


if __name__ == "__main__":
    unittest.main()
