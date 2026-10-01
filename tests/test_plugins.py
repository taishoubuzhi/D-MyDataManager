"""插件协议、发现、依赖排序、载入与导入的单元测试。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core import paths, viewer_data  # noqa: E402
from app.core.extensions import extension_registry  # noqa: E402
from app.core.plugin_kinds import KIND_PAGE, KIND_VIEWER, PluginKindSpec, plugin_kinds, valid_kind_name  # noqa: E402
from app.core.plugins import (  # noqa: E402
    SOURCE_EXTERNAL,
    PluginError,
    load_manifest,
    parse_manifest,
    sort_by_dependency,
)
from app.core.viewers import viewer_registry  # noqa: E402
from app.services.plugin_service import PluginApi, PluginService  # noqa: E402

#: 内置插件是随仓库分发的资源，直接指向仓库目录，避免受测试harness的路径重定向影响
REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins"

PLUGIN_MODULE = '''"""测试插件：注册一个 .dmx 查看器。"""


def _factory(path, parent=None):
    return None


def register(api):
    host = api.has("dialog")
    api.add_viewer("演示查看器", ("dmx",), factory=_factory, kind="text", host="dialog" if host else "",
                   description="测试用")
'''

#: 内置查看器清单里的扩展名应当与扫描模块保持一致
BUILTIN_EXTENSIONS = {
    "builtin.image": viewer_data.IMAGE_EXTENSIONS,
    "builtin.video": viewer_data.VIDEO_EXTENSIONS,
    "builtin.audio": viewer_data.AUDIO_EXTENSIONS,
    "builtin.text": viewer_data.TEXT_EXTENSIONS,
    "builtin.markdown": ("md", "markdown"),
    "builtin.archive": viewer_data.ARCHIVE_EXTENSIONS,
    "builtin.spreadsheet": viewer_data.SPREADSHEET_EXTENSIONS,
}


def manifest(**fields) -> dict:
    data = {
        "id": "demo.viewer",
        "name": "演示插件",
        "version": "0.1.0",
        "kind": KIND_VIEWER,
        "entry": "plugin.py",
        "extensions": ["dmx"],
        "description": "测试插件",
        "author": "测试者",
        "manager_version": "0.1.0",
        "depends": [],
        "provides": ["viewer"],
        "capabilities": ["演示功能"],
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
        self.assertEqual(info.kind, KIND_VIEWER)
        self.assertEqual(info.kind_label, "打开方式")
        self.assertEqual(info.extensions, ("dmx",))
        self.assertEqual(info.source_label, "外部")
        self.assertEqual(info.state_label, "已启用")
        self.assertEqual(info.extensions_text, "dmx")
        self.assertEqual(info.depends_text, "无")
        self.assertEqual(info.provides_text, "viewer")
        self.assertEqual(info.capabilities_text, "演示功能")
        self.assertEqual(info.manager_text, "0.1.0")
        self.assertEqual(info.author_text, "测试者")
        self.assertEqual(info.version_text, "0.1.0")

    def test_parse_manifest_normalizes_extension_text(self) -> None:
        info = parse_manifest(manifest(extensions=".DMX, txt txt"))
        self.assertEqual(info.extensions, ("dmx", "txt"))

    def test_parse_manifest_accepts_page_plugin_without_extensions(self) -> None:
        info = parse_manifest({"id": "demo.page", "name": "演示页面", "kind": KIND_PAGE, "entry": "plugin.py"})
        self.assertEqual(info.kind_label, "弹窗页面")
        self.assertEqual(info.extensions, ())
        self.assertEqual(info.extensions_text, "—")

    def test_parse_manifest_rejects_bad_input(self) -> None:
        for bad in (
            [],
            manifest(id=""),
            manifest(id="有中文"),
            manifest(name=""),
            manifest(entry=""),
            manifest(extensions=""),
            manifest(manager_version="99.0"),
            manifest(depends=["bad id!"]),
            manifest(depends=["demo.viewer"]),
            manifest(provides=["Bad-Name"]),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(PluginError):
                    parse_manifest(bad)

    def test_parse_manifest_allows_builtin_without_entry(self) -> None:
        info = parse_manifest(manifest(entry=""), builtin=True)
        self.assertTrue(info.builtin)
        self.assertEqual(info.source_label, "内置")

    def test_parse_manifest_reports_manager_version(self) -> None:
        with self.assertRaisesRegex(PluginError, "需要管理器版本"):
            parse_manifest(manifest(manager_version="2.0"))

    def test_load_manifest_requires_existing_entry(self) -> None:
        with TempDir("manifest") as tmp:
            folder = Path(tmp) / "demo.viewer"
            folder.mkdir()
            (folder / "plugin.json").write_text(json.dumps(manifest()), encoding="utf-8")
            with self.assertRaises(PluginError):
                load_manifest(folder)
            (folder / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
            self.assertEqual(load_manifest(folder).id, "demo.viewer")

    def test_sort_by_dependency_orders_and_reports(self) -> None:
        base = parse_manifest(manifest(id="demo.base"))
        viewer = parse_manifest(manifest(id="demo.viewer", depends=["demo.base"]))
        missing = parse_manifest(manifest(id="demo.missing", depends=["demo.nope"]))
        left = parse_manifest(manifest(id="demo.a", depends=["demo.b"]))
        right = parse_manifest(manifest(id="demo.b", depends=["demo.a"]))
        order, errors = sort_by_dependency([viewer, base, missing, left, right])
        self.assertEqual([info.id for info in order], ["demo.base", "demo.viewer"])
        self.assertIn("缺少依赖插件：demo.nope", errors["demo.missing"])
        self.assertIn("循环", errors["demo.a"])
        self.assertIn("循环", errors["demo.b"])


class PluginKindCase(unittest.TestCase):
    """插件类型由清单累积登记：可以自定义、重复声明只合并、类型表随时增长。"""

    def tearDown(self) -> None:
        plugin_kinds.unregister("theme")
        plugin_kinds.unregister("panorama")
        viewer_registry.unregister_plugin("demo.viewer")

    def test_builtin_kinds_are_registered(self) -> None:
        self.assertEqual(plugin_kinds.ids(), (KIND_VIEWER, KIND_PAGE))
        viewer = plugin_kinds.get(KIND_VIEWER)
        page = plugin_kinds.get(KIND_PAGE)
        assert viewer is not None and page is not None
        self.assertEqual(viewer.name, "打开方式")
        self.assertEqual(viewer.text, "打开方式（viewer）")
        self.assertEqual(viewer.contributor, "add_viewer")
        self.assertTrue(viewer.requires_extensions)
        self.assertEqual(page.contributor, "")
        self.assertFalse(page.requires_extensions)
        self.assertEqual(plugin_kinds.label("未知类型"), "未知类型")

    def test_kind_names_are_validated(self) -> None:
        self.assertTrue(valid_kind_name("theme"))
        self.assertTrue(valid_kind_name("my.plugin-kind_2"))
        self.assertFalse(valid_kind_name("Theme"))
        self.assertFalse(valid_kind_name("2theme"))
        self.assertFalse(valid_kind_name(""))

    def test_manifest_registers_its_own_kind(self) -> None:
        info = parse_manifest(manifest(kind="theme", kind_label="主题", kind_description="配色方案"))
        self.assertEqual(info.kind, "theme")
        self.assertEqual(info.kind_label, "主题")
        self.assertIn(("theme", "主题"), plugin_kinds.labels())
        spec = plugin_kinds.get("theme")
        assert spec is not None
        self.assertEqual(spec.description, "配色方案")
        self.assertEqual(spec.plugins, ("demo.viewer",))
        self.assertEqual(spec.order, 100)
        self.assertFalse(spec.requires_extensions)
        # 类型没写显示名时用 id 兜底
        self.assertTrue(plugin_kinds.unregister("theme"))
        named = plugin_kinds.register(PluginKindSpec("theme", plugins=("demo.viewer",)))
        self.assertEqual(named.name, "theme")
        self.assertEqual(plugin_kinds.label("theme"), "theme")

    def test_two_plugins_can_declare_the_same_kind(self) -> None:
        first = parse_manifest(manifest(kind="theme", kind_label="主题"))
        second = parse_manifest(manifest(id="demo.other", kind="theme"))
        self.assertEqual(first.kind_label, "主题")
        self.assertEqual(second.kind_label, "主题")  # 后声明的插件不必再写显示名
        spec = plugin_kinds.get("theme")
        assert spec is not None
        self.assertEqual(spec.name, "主题")
        self.assertEqual(set(spec.plugins), {"demo.viewer", "demo.other"})
        merged = plugin_kinds.register(PluginKindSpec("theme", "另一个名字"))
        self.assertEqual(merged.name, "主题")  # 先声明的显示名优先，重复登记不报错
        with self.assertRaises(ValueError):
            plugin_kinds.register(PluginKindSpec("Theme", "非法"))
        self.assertTrue(plugin_kinds.unregister("theme"))
        self.assertFalse(plugin_kinds.unregister("theme"))

    def test_kind_requires_extensions_can_be_declared_by_manifest(self) -> None:
        with self.assertRaisesRegex(PluginError, "至少要声明一个扩展名"):
            parse_manifest(manifest(kind="panorama", kind_requires_extensions=True, extensions=""))
        self.assertIsNone(plugin_kinds.get("panorama"))  # 校验失败的类型不会登记

    def test_invalid_kind_is_rejected(self) -> None:
        with self.assertRaisesRegex(PluginError, "插件类型不合法"):
            parse_manifest(manifest(kind="Theme"))

    def test_generic_add_routes_to_viewer_registry(self) -> None:
        api = PluginApi("demo.viewer", plugin_name="演示插件")
        viewer = api.add("viewer", "演示查看器", ("dmx",), factory=lambda path, parent: None)
        self.assertEqual(viewer.plugin_id, "demo.viewer")
        self.assertIs(viewer_registry.for_suffix("dmx"), viewer)
        self.assertEqual(len(api.registered), 1)

    def test_custom_kind_entries_are_recorded(self) -> None:
        api = PluginApi("demo.plugin")
        entry = api.add("theme", "演示主题", palette="dark")
        self.assertEqual(entry.kind, "theme")
        self.assertEqual(entry.plugin_id, "demo.plugin")
        self.assertEqual(entry.name, "演示主题")
        self.assertEqual(entry.fields, {"palette": "dark"})
        self.assertEqual(api.entries("theme"), (entry,))
        self.assertEqual(api.entries(), (entry,))
        self.assertEqual(api.add("page", "演示页面").kind, "page")
        self.assertEqual(len(api.entries()), 2)
        with self.assertRaisesRegex(PluginError, "插件类型不合法"):
            api.add("Theme", "非法")


class BuiltinProtocolCase(unittest.TestCase):
    """磁盘上的内置插件必须符合统一协议。"""

    def test_builtin_plugins_are_on_disk(self) -> None:
        found = {folder.name for folder in REPO_PLUGIN_DIR.iterdir() if (folder / "plugin.json").exists()}
        self.assertSetEqual(found, set(BUILTIN_EXTENSIONS) | {"builtin.dialog"})

    def test_builtin_manifest_extensions_match_viewer_data(self) -> None:
        for plugin_id, expected in BUILTIN_EXTENSIONS.items():
            info = load_manifest(REPO_PLUGIN_DIR / plugin_id)
            with self.subTest(plugin=plugin_id):
                self.assertEqual(info.extensions, tuple(expected))
                self.assertEqual(info.depends, ("builtin.dialog",))
                self.assertEqual(info.provides, ("viewer",))
                self.assertTrue(info.builtin)
                self.assertTrue(info.entry)

    def test_builtin_plugins_declare_register_entry(self) -> None:
        for plugin_id in BUILTIN_EXTENSIONS:
            folder = REPO_PLUGIN_DIR / plugin_id
            source = (folder / "plugin.py").read_text(encoding="utf-8")
            with self.subTest(plugin=plugin_id):
                self.assertIn("def register(api)", source)
                self.assertIn('api.require("dialog")', source)

    def test_service_loads_every_builtin_plugin(self) -> None:
        with TempDir("builtin") as tmp:
            service = PluginService(plugin_dir=REPO_PLUGIN_DIR, state_file=Path(tmp) / "plugins.json")
            self._assert_service_loads(service)

    def _assert_service_loads(self, service: PluginService) -> None:
        infos = {info.id: info for info in service.discover()}
        self.assertEqual(len(service.all(kind=KIND_VIEWER)), 7)
        self.assertEqual(len(service.all(kind=KIND_PAGE)), 1)
        self.assertEqual(load_manifest(REPO_PLUGIN_DIR / "builtin.dialog").provides, ("dialog",))
        self.assertEqual(infos["builtin.dialog"].depends, ())
        self.assertEqual(service.load_viewers(), 7)
        self.assertIn("dialog", extension_registry.names())


class PluginServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TempDir("plugins")
        self.root = Path(self._tmp.name)
        self.plugin_dir = self.root / "plugins"
        shutil.copytree(REPO_PLUGIN_DIR, self.plugin_dir)
        self.state_file = self.root / "plugins.json"
        self.service = PluginService(plugin_dir=self.plugin_dir, state_file=self.state_file)

    def tearDown(self) -> None:
        viewer_registry.clear()
        extension_registry.clear()
        self._tmp.cleanup()

    def test_builtin_plugins_cover_viewers_and_page(self) -> None:
        builtin = self.service.builtin()
        self.assertEqual(len(builtin), 8)
        self.assertTrue(all(info.builtin for info in builtin))
        self.assertEqual(len(self.service.all(kind=KIND_VIEWER)), 7)
        self.assertEqual(len(self.service.discover()), 8)

    def test_filters_by_query_state_and_source(self) -> None:
        install_plugin(self.plugin_dir)
        self.assertEqual(len(self.service.all(source=SOURCE_EXTERNAL)), 1)
        self.assertEqual(len(self.service.all(query="dmx")), 1)
        self.assertEqual(len(self.service.all(query="演示插件")), 1)
        self.assertEqual(self.service.all(query="不存在的插件"), [])
        self.service.set_enabled("demo.viewer", False)
        self.assertEqual(len(self.service.all(state="disabled")), 1)
        self.assertFalse(self.service.all(source=SOURCE_EXTERNAL)[0].enabled)

    def test_missing_dependency_is_reported(self) -> None:
        install_plugin(self.plugin_dir, name="demo.broken", depends=["builtin.nope"])
        info = self.service.get("demo.broken")
        self.assertFalse(info.enabled)
        self.assertEqual(info.state_label, "异常")
        self.assertIn("缺少依赖插件：builtin.nope", info.error)
        self.assertEqual(self.service.load_viewers(), 7)

    def test_dependency_cycle_is_reported(self) -> None:
        install_plugin(self.plugin_dir, name="demo.a", depends=["demo.b"])
        install_plugin(self.plugin_dir, name="demo.b", depends=["demo.a"])
        errors = {info.id: info.error for info in self.service.all(state="error")}
        self.assertIn("循环", errors["demo.a"])
        self.assertIn("循环", errors["demo.b"])

    def test_disabling_dialog_plugin_blocks_dependents(self) -> None:
        self.assertTrue(self.service.set_enabled("builtin.dialog", False))
        self.assertEqual(self.service.load_viewers(), 0)
        self.assertEqual(len(self.service.all(state="error")), 7)
        self.assertEqual(extension_registry.names(), ())

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
        stored = json.dumps(json.loads(self.state_file.read_text(encoding="utf-8")), ensure_ascii=False)
        self.assertIn("备注内容", stored)

    def test_load_viewers_registers_and_unregisters_external_plugin(self) -> None:
        install_plugin(self.plugin_dir)
        self.assertEqual(self.service.load_viewers(), 8)
        viewer = viewer_registry.for_suffix("dmx")
        self.assertIsNotNone(viewer)
        self.assertEqual(viewer.plugin_id, "demo.viewer")
        self.assertEqual(viewer.name, "演示查看器")
        self.assertEqual(viewer.host, "dialog")

        self.service.set_enabled("demo.viewer", False)
        self.assertEqual(self.service.load_viewers(), 7)
        self.assertIsNone(viewer_registry.for_suffix("dmx"))

    def test_viewer_registry_exposes_dialog_host(self) -> None:
        self.assertEqual(self.service.load_viewers(), 7)
        self.assertTrue(all(viewer.host == "dialog" for viewer in viewer_registry.all()))
        self.assertIsNotNone(extension_registry.provider("dialog"))

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

    def test_import_marks_fake_builtin_as_external(self) -> None:
        source = install_plugin(self.root / "fake", name="demo.fake", builtin=True)
        info = self.service.import_plugin(source)
        self.assertFalse(info.builtin)
        copied = json.loads((self.plugin_dir / "demo.fake" / "plugin.json").read_text(encoding="utf-8"))
        self.assertFalse(copied["builtin"])
        self.assertTrue(self.service.remove("demo.fake"))

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
        self.assertTrue((self.plugin_dir / "builtin.text").exists())


if __name__ == "__main__":
    unittest.main()
