"""可选能力探测（批 K）：能查、缺了有中文提示、装/没装都不影响启动。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from app.core import capabilities


class CapabilityCase(unittest.TestCase):
    def setUp(self):
        capabilities.refresh()

    def tearDown(self):
        capabilities.refresh()

    def test_report_covers_every_registered_name(self):
        rows = capabilities.report()
        self.assertEqual(tuple(item.name for item in rows), capabilities.names())
        self.assertGreaterEqual(len(rows), 15)
        for item in rows:
            with self.subTest(name=item.name):
                self.assertIn(item.kind, (capabilities.KIND_PYTHON, capabilities.KIND_TOOL, capabilities.KIND_STDLIB))
                self.assertTrue(item.unlocks, "每个能力都要说明装上能干什么")
                self.assertTrue(item.fallback, "每个能力都要说明没装会有什么结果")
                if item.available:
                    self.assertEqual(item.hint, "", "可用时不该有安装提示")

    def test_required_capabilities_of_this_machine(self):
        """本机（开发环境）这些必须可用，否则批 J 的加速就白做了。"""
        for name in ("orjson", "fastjsonschema", "argon2", "watchdog", "puremagic", "charset_normalizer", "zstd"):
            with self.subTest(name=name):
                self.assertTrue(capabilities.capability(name).available, f"{name} 应可用")

    def test_unknown_name_is_refused(self):
        with self.assertRaises(KeyError):
            capabilities.capability("not-a-capability")

    def test_missing_dependency_gives_chinese_hint_with_mirror(self):
        def fake(module: str):
            return (False, "") if module == "orjson" else (True, "x.py")

        with mock.patch.object(capabilities, "_module_available", side_effect=fake):
            item = capabilities.capability("orjson")
            self.assertFalse(item.available)
            self.assertIn("缺 orjson", item.hint)
            self.assertIn("pip install orjson", item.hint)
            self.assertIn(capabilities.PYPI_MIRROR, item.hint)
            self.assertIn("--no-cache-dir", item.hint)
            self.assertIn("orjson", [row.name for row in capabilities.missing()])
            self.assertIn(
                f"pip install orjson -i {capabilities.PYPI_MIRROR} --no-cache-dir",
                capabilities.install_commands(),
            )

    def test_summary_text_reports_missing_and_full_states(self):
        gone = capabilities.missing()
        text = capabilities.summary_text()
        if gone:
            self.assertIn(f"{len(gone)}/", text)
            for item in gone:
                self.assertIn(item.name, text)
        else:
            self.assertIn("全部可用", text)
        with (
            mock.patch.object(capabilities, "_module_available", return_value=(True, "x.py")),
            mock.patch.object(capabilities, "_tool_path", return_value="C:/tool.exe"),
        ):
            capabilities.refresh()
            self.assertIn("全部可用", capabilities.summary_text())

    def test_tool_capabilities_have_tool_hints(self):
        for name in ("bsdtar",):
            with self.subTest(name=name):
                item = capabilities.capability(name)
                self.assertEqual(item.kind, capabilities.KIND_TOOL)
                if not item.available:
                    self.assertTrue(item.hint, f"{name} 缺失时必须有安装提示")

    def test_media_engine_is_a_python_package_with_hint(self):
        """媒体引擎（PyAV）是随 requirements.txt 装的 Python 包，缺失时必须有安装提示。"""
        item = capabilities.capability("av")
        self.assertEqual(item.kind, capabilities.KIND_PYTHON)
        if not item.available:
            self.assertTrue(item.hint, "av 缺失时必须有安装提示")

    def test_configure_tools_points_rarfile_at_bsdtar(self):
        changed = capabilities.configure_tools()
        tar = capabilities.capability("bsdtar")
        if not tar.available or not capabilities.capability("rarfile").available:
            self.assertEqual(changed, [], "没有 rarfile / bsdtar 时应当什么都不做")
            return
        import rarfile

        self.assertEqual(rarfile.BSDTAR_TOOL, tar.detail)
        self.assertEqual(capabilities.configure_tools(), [], "已经配好就不该再改")

    def test_python_executable_is_reported(self):
        self.assertTrue(capabilities.python_executable())

    def test_requirements_install_every_python_package_except_two(self):
        """默认安装清单：除 huggingface_hub / hf-transfer 外都在 requirements.txt 里。"""
        root = Path(__file__).resolve().parents[2]
        self.assertFalse((root / "requirements-optional.txt").exists(), "可选依赖文件已并入 requirements.txt")
        declared: set[str] = set()
        for raw in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                declared.add(re.split(r"[<>=!~\s]", line, maxsplit=1)[0].lower())
        skipped = {"huggingface_hub", "hf-transfer"}
        for dist in capabilities.python_packages():
            with self.subTest(dist=dist):
                if dist.lower() in skipped:
                    self.assertNotIn(dist.lower(), declared, f"{dist} 属于故意不装的那两个")
                else:
                    self.assertIn(dist.lower(), declared, f"{dist} 应随 requirements.txt 默认装")


if __name__ == "__main__":
    unittest.main()
