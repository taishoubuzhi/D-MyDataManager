"""JSON 读写（批 J6）：优先 orjson、缺依赖退回标准库，两者语义一致。"""

from __future__ import annotations

import unittest
from unittest import mock

from app.core.runtime import jsonio
from tests.harness import TempDir


class JsonIoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir(prefix="jsonio")
        self.root = self.tmp.__enter__()

    def tearDown(self):
        self.tmp.__exit__(None, None, None)

    def test_orjson_is_used_when_available(self):
        self.assertTrue(jsonio.orjson_available(), "本机应装了 orjson")

    def test_loads_accepts_text_and_bytes(self):
        self.assertEqual(jsonio.loads('{"a": 1}'), {"a": 1})
        self.assertEqual(jsonio.loads(b'{"a": 1}'), {"a": 1})
        self.assertEqual(jsonio.loads('"你好"'), "你好")

    def test_dumps_returns_utf8_text_without_escapes(self):
        text = jsonio.dumps({"名字": "中文"})
        self.assertIsInstance(text, str)
        self.assertIn("中文", text)
        self.assertNotIn("\\u", text)
        self.assertIsInstance(jsonio.dump_bytes({"a": 1}), bytes)

    def test_indent_and_sort_keys(self):
        text = jsonio.dumps({"b": 1, "a": 2}, indent=2, sort_keys=True)
        self.assertIn("\n", text)
        self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_round_trip_through_file(self):
        path = self.root / "sub" / "data.json"
        payload = {"items": [{"key": "甲", "value": 1}], "n": None}
        self.assertTrue(jsonio.write_json(path, payload))
        self.assertEqual(jsonio.read_json(path, None), payload)

    def test_read_json_returns_default_on_missing_and_broken(self):
        self.assertEqual(jsonio.read_json(self.root / "nope.json", {"d": 1}), {"d": 1})
        broken = self.root / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        self.assertEqual(jsonio.read_json(broken, []), [])

    def test_write_json_reports_failure(self):
        target = self.root / "folder"
        target.mkdir()
        self.assertFalse(jsonio.write_json(target, {"a": 1}), "把目录当文件写应当返回 False")

    def test_exceptions_match_stdlib_types(self):
        with self.assertRaises(ValueError):
            jsonio.loads("{ not json")
        with self.assertRaises(TypeError):
            jsonio.dumps(object())

    def test_stdlib_fallback_is_equivalent(self):
        payload = {"名字": "中文", "list": [1, 2, {"k": None}]}
        with mock.patch.object(jsonio, "_orjson", None):
            self.assertFalse(jsonio.orjson_available())
            text = jsonio.dumps(payload, indent=2)
            self.assertIn("中文", text)
            self.assertEqual(jsonio.loads(text), payload)
            path = self.root / "fallback.json"
            self.assertTrue(jsonio.write_json(path, payload))
            self.assertEqual(jsonio.read_json(path, None), payload)
            with self.assertRaises(ValueError):
                jsonio.loads("{ not json")


if __name__ == "__main__":
    unittest.main()
