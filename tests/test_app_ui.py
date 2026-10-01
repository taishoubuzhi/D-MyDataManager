"""`app.ui` 界面扩展接口与程序本体扩展接口（bootstrap）的单元测试。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core.app_ui import APP_UI_EXTENSION, AppUiApi, PageSpec  # noqa: E402
from app.core.extensions import extension_registry  # noqa: E402
from app.core.viewers import viewer_registry  # noqa: E402
from app.services.plugin_service import PluginService  # noqa: E402

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins"

PAGE_PLUGIN_MANIFEST = {
    "id": "demo.page",
    "name": "演示页面插件",
    "version": "1.0.0",
    "kind": "page",
    "description": "用 app.ui 登记一个导航页面。",
    "author": "tests",
    "manager_version": "0.1.0",
    "entry": "plugin.py",
    "depends": [],
    "provides": ["demo"],
}

PAGE_PLUGIN_ENTRY = '''"""演示插件：借助程序本体的 app.ui 接口登记导航页面。"""

PLUGIN_NAME = "演示页面插件"


def register(api) -> None:
    ui = api.require("app.ui")
    ui.add_page(
        "demo",
        "演示页面",
        lambda: _build(api),
        icon="HOME",
        plugin_id=api.plugin_id,
    )


def _build(api):
    return None
'''


class PageSpecCase(unittest.TestCase):
    def setUp(self) -> None:
        self.api = AppUiApi()

    def test_add_page_keeps_fields_and_route(self) -> None:
        spec = self.api.add_page("demo.page", "演示页", lambda: None, icon="home", bottom=True, plugin_id="p1")
        self.assertIsInstance(spec, PageSpec)
        self.assertEqual(spec.key, "demo.page")
        self.assertEqual(spec.title, "演示页")
        self.assertEqual(spec.icon, "home")
        self.assertTrue(spec.bottom)
        self.assertEqual(spec.plugin_id, "p1")
        self.assertEqual(spec.route, "plugin.demo.page")
        self.assertEqual(self.api.pages(), (spec,))
        self.assertEqual(len(self.api), 1)

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.api.add_page("Demo", "演示页", lambda: None)
        with self.assertRaises(ValueError):
            self.api.add_page("demo", "", lambda: None)
        with self.assertRaises(ValueError):
            self.api.add_page("demo", "演示页", None)

    def test_same_plugin_can_update_its_page(self) -> None:
        self.api.add_page("demo", "旧标题", lambda: None, plugin_id="p1")
        spec = self.api.add_page("demo", "新标题", lambda: None, plugin_id="p1")
        self.assertEqual(spec.title, "新标题")
        self.assertEqual(len(self.api), 1)

    def test_two_plugins_cannot_share_a_key(self) -> None:
        self.api.add_page("demo", "演示页", lambda: None, plugin_id="p1")
        with self.assertRaises(ValueError):
            self.api.add_page("demo", "另一个", lambda: None, plugin_id="p2")

    def test_remove_page_and_clear(self) -> None:
        self.api.add_page("demo", "演示页", lambda: None, plugin_id="p1")
        self.assertTrue(self.api.remove_page("demo"))
        self.assertFalse(self.api.remove_page("demo"))
        self.api.add_page("demo", "演示页", lambda: None, plugin_id="p1")
        self.api.clear()
        self.assertEqual(self.api.pages(), ())

    def test_sync_plugins_drops_pages_of_unloaded_plugins(self) -> None:
        self.api.add_page("demo", "演示页", lambda: None, plugin_id="p1")
        self.api.add_page("host", "宿主页", lambda: None, plugin_id="p2")
        self.assertEqual(self.api.sync_plugins(["p1"]), ("host",))
        self.assertEqual([spec.key for spec in self.api.pages()], ["demo"])
        self.assertEqual(self.api.sync_plugins(("p1", "p2")), ())
        self.assertEqual(len(self.api), 1)


class BootstrapCase(unittest.TestCase):
    """程序本体接口：每次载入插件后重新提供，插件载入阶段即可 require。"""

    def setUp(self) -> None:
        self._tmp = TempDir("appui")
        self.root = Path(self._tmp.name)
        self.plugin_dir = self.root / "plugins"
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.service = PluginService(plugin_dir=self.plugin_dir, state_file=self.root / "plugins.json")
        self.app_ui = AppUiApi()
        self.service.bootstrap(APP_UI_EXTENSION, self.app_ui)

    def tearDown(self) -> None:
        viewer_registry.clear()
        extension_registry.clear()
        self._tmp.cleanup()

    def _install_page_plugin(self) -> None:
        folder = self.plugin_dir / "demo.page"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "plugin.json").write_text(
            json.dumps(PAGE_PLUGIN_MANIFEST, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "plugin.py").write_text(PAGE_PLUGIN_ENTRY, encoding="utf-8")

    def test_bootstrap_is_provided_immediately(self) -> None:
        self.assertEqual(self.service.bootstrap_names(), (APP_UI_EXTENSION,))
        self.assertIs(extension_registry.provider(APP_UI_EXTENSION), self.app_ui)
        self.assertEqual(extension_registry.provider_plugin(APP_UI_EXTENSION), "")

    def test_bootstrap_survives_reload(self) -> None:
        self.service.load_viewers()
        self.assertIs(extension_registry.provider(APP_UI_EXTENSION), self.app_ui)

    def test_plugin_registers_page_through_app_ui(self) -> None:
        self._install_page_plugin()
        self.assertEqual(self.service.load_viewers(), 0)
        pages = self.app_ui.pages()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].key, "demo")
        self.assertEqual(pages[0].title, "演示页面")
        self.assertEqual(pages[0].plugin_id, "demo.page")
        self.assertEqual(self.service.errors(), {})

    def test_page_disappears_after_plugin_is_disabled(self) -> None:
        self._install_page_plugin()
        self.service.load_viewers()
        self.assertEqual(len(self.app_ui), 1)
        self.service.set_enabled("demo.page", False)
        self.service.load_viewers()
        self.assertEqual(self.app_ui.pages(), ())


if __name__ == "__main__":
    unittest.main()
