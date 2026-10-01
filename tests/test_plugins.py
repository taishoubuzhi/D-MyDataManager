"""插件发现、启用状态、加载与导入的单元测试。"""

from __future__ import annotations

import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core.plugins import (  # noqa: E402
    SOURCE_BUILTIN,
    SOURCE_EXTERNAL,
    PluginError,
    load_manifest,
    parse_manifest,
)
from app.core.viewers import viewer_registry  # noqa: E402
from app.services.plugin_service import PluginService, plugin_service  # noqa: E402

PLUGIN_MODULE = '''"""测试插件：注册一个 .dmx 查看器。"""


def _factory(path, parent=None):
    return None


def register(api):
    api.add_viewer("演示查看器", ("dmx",), factory=_factory, kind="text", description="测试用")
'''


def manifest(**fields) -> dict:
    data = {
        "id": "demo.viewer",
        "name": "演示插件",
        "version": "0.1.0",
        "kind": "viewer",
        "entry": "plugin.py",
        "extensions": ["dmx"],
        "description": "测试插件",
    }
    data.update(fields)
    return data


def install_plugin(root: Path, name: str = "demo.viewer", **fields) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(manifest(id=name, **fields), ensure_ascii=False), encoding="utf-8")
    (folder / "plugin.py").write_text(PLUGIN_MODULE, encoding="utf-8")
    return folder


class ManifestCase(unittest.TestCase):
    def test_parse_manifest_accepts_viewer_plugin(self) -> None:
        info = parse_manifest(manifest())
        self.assertEqual(info.id, "demo.viewer")
        self.assertEqual(info.kind, "viewer")
        self.assertEqual(info.kind_label, "打开方式")
        self.assertEqual(info.extensions, ("dmx",))
        self.assertEqual(info.source_label, "外部")
        self.assertEqual(info.state_label, "已启用")
        self.assertEqual(info.extensions_text, "dmx")

    def test_parse_manifest_normalizes_extension_text(self) -> None:
        info = parse_manifest(manifest(extensions=".DMX, txt txt"))
        self.assertEqual(info.extensions, ("dmx", "txt"))

    def test_parse_manifest_rejects_bad_input(self) -> None:
        with self.assertRaises(PluginError):
            parse_manifest([])
        with self.assertRaises(PluginError):
            parse_manifest(manifest(id=""))
        with self.assertRaises(PluginError):
            parse_manifest(manifest(id="有中文"))
        with self.assertRaises(PluginError):
            parse_manifest(manifest(kind="theme"))
        with self.assertRaises(PluginError):
            parse_manifest(manifest(entry=""))
        with self.assertRaises(PluginError):
            parse_manifest(manifest(extensions=""))

    def test_parse_manifest_allows_builtin_without_entry(self) -> None:
        info = parse_manifest(manifest(entry=""), source=SOURCE_BUILTIN)
        self.assertTrue(info.builtin)
        self.assertEqual(info.source_label, "内置")

    def test_load_manifest_requires_existing_entry(self) -> None:
        with TempDir("manifest") as tmp:
            folder = Path(tmp) / "demo.viewer"
            folder.mkdir()
            (folder / "plugin.json").write_text(json.dumps(manifest()), encoding="utf-8")
            with self.assertRaises(PluginError):
                load_manifest(folder)
            (folder / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
            self.assertEqual(load_manifest(folder).id, "demo.viewer")


class PluginServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TempDir("plugins")
        self.root = Path(self._tmp.name)
        self.plugin_dir = self.root / "plugins"
        self.state_file = self.root / "plugins.json"
        self.service = PluginService(plugin_dir=self.plugin_dir, state_file=self.state_file)

    def tearDown(self) -> None:
        plugin_service.load_viewers()
        viewer_registry.clear()
        self._tmp.cleanup()

    def test_builtin_plugins_are_seven(self) -> None:
        builtin = self.service.builtin()
        self.assertEqual(len(builtin), 7)
        self.assertTrue(all(info.builtin for info in builtin))
        self.assertEqual(len(self.service.discover()), 7)

    def test_filters_by_query_state_and_source(self) -> None:
        install_plugin(self.plugin_dir)
        self.assertEqual(len(self.service.all(source=SOURCE_EXTERNAL)), 1)
        self.assertEqual(len(self.service.all(query="dmx")), 1)
        self.assertEqual(len(self.service.all(query="演示插件")), 1)
        self.assertEqual(self.service.all(query="不存在的插件"), [])
        self.service.set_enabled("demo.viewer", False)
        self.assertEqual(len(self.service.all(state="disabled")), 1)
        self.assertEqual(self.service.all(source=SOURCE_EXTERNAL)[0].enabled, False)

    def test_broken_manifest_is_reported_as_error(self) -> None:
        folder = self.plugin_dir / "broken"
        folder.mkdir(parents=True)
        (folder / "plugin.json").write_text("{ not json", encoding="utf-8")
        errors = self.service.all(state="error")
        self.assertEqual(len(errors), 1)
        self.assertFalse(errors[0].enabled)
        self.assertTrue(errors[0].error)
        self.assertEqual(errors[0].state_label, "异常")

    def test_state_is_persisted_and_note_editable(self) -> None:
        install_plugin(self.plugin_dir)
        self.service.set_enabled("demo.viewer", False)
        self.service.update("demo.viewer", name="改名插件", note="备注内容")
        reopened = PluginService(plugin_dir=self.plugin_dir, state_file=self.state_file)
        info = reopened.get("demo.viewer")
        self.assertFalse(info.enabled)
        self.assertEqual(info.name, "改名插件")
        self.assertEqual(info.note, "备注内容")
        self.assertIn("备注内容", json.dumps(json.loads(self.state_file.read_text(encoding="utf-8")), ensure_ascii=False))

    def test_load_viewers_registers_and_unregisters_external_plugin(self) -> None:
        install_plugin(self.plugin_dir)
        count = self.service.load_viewers()
        self.assertEqual(count, 8)
        viewer = viewer_registry.for_suffix("dmx")
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer.plugin_id, "demo.viewer")
        self.assertEqual(viewer.name, "演示查看器")

        self.service.set_enabled("demo.viewer", False)
        self.assertEqual(self.service.load_viewers(), 7)
        self.assertIsNone(viewer_registry.for_suffix("dmx"))

    def test_import_and_remove_plugin_folder(self) -> None:
        source = install_plugin(self.root / "incoming", name="demo.viewer")
        info = self.service.import_plugin(source)
        self.assertEqual(info.id, "demo.viewer")
        self.assertTrue((self.plugin_dir / "demo.viewer" / "plugin.json").is_file())
        with self.assertRaisesRegex(PluginError, "已存在"):
            self.service.import_plugin(source)
        self.assertEqual(self.service.import_plugin(source, overwrite=True).id, "demo.viewer")

        self.assertTrue(self.service.remove("demo.viewer"))
        self.assertFalse((self.plugin_dir / "demo.viewer").exists())
        self.assertIsNone(self.service.get("demo.viewer"))

    def test_import_plugin_zip_and_reject_unsafe_paths(self) -> None:
        package = self.root / "demo.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("demo.viewer/plugin.json", json.dumps(manifest()))
            archive.writestr("demo.viewer/plugin.py", PLUGIN_MODULE)
        self.assertEqual(self.service.import_plugin(package).id, "demo.viewer")

        unsafe = self.root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../evil/plugin.json", json.dumps(manifest()))
        with self.assertRaisesRegex(PluginError, "不安全"):
            self.service.import_plugin(unsafe)

        broken = self.root / "broken.zip"
        broken.write_bytes(b"not a zip")
        with self.assertRaises(PluginError):
            self.service.import_plugin(broken)

    def test_builtin_plugins_cannot_be_removed(self) -> None:
        self.assertFalse(self.service.remove("builtin.text"))
        self.assertFalse(self.service.remove("未知插件"))


if __name__ == "__main__":
    unittest.main()
