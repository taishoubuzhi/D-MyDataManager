"""依赖探测单测：声明解析、标记 JSON、问一个真解释器「装没装」。

探测本身要起子进程，所以这里挑了两个**确定性**的样本：一定装着的 `pip`，和一定不在的
一个乱名字。不联网、不装包。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from app.core.pip import probe
from app.core.pip.probe import (
    MISSING_MARK,
    import_name,
    marked,
    missing_program_group,
    missing_program_packages,
    probe_python,
    spec_base,
)

ABSENT = "definitely-not-installed-package-xyz"


class SpecTests(unittest.TestCase):
    def test_spec_base_keeps_only_the_name(self) -> None:
        self.assertEqual(spec_base("faster-whisper>=1.0"), "faster-whisper")
        self.assertEqual(spec_base("numpy[extra]; python_version<'3.13'"), "numpy")
        self.assertEqual(spec_base("  torch  "), "torch")
        self.assertEqual(spec_base(""), "")

    def test_import_name_uses_table_then_guess(self) -> None:
        self.assertEqual(import_name("Pillow==10"), "PIL")
        self.assertEqual(import_name("faster-whisper>=1.0"), "faster_whisper")
        self.assertEqual(import_name("onnxruntime-gpu"), "onnxruntime")
        self.assertEqual(import_name("my-cool.pkg>=1"), "my_cool_pkg")
        self.assertEqual(import_name(""), "")

    def test_probe_python_defaults_to_current(self) -> None:
        self.assertEqual(probe_python(None), Path(sys.executable))
        self.assertEqual(probe_python("other"), Path("other"))


class MarkedTests(unittest.TestCase):
    def test_takes_the_last_marker_line(self) -> None:
        body = f"noise\n{MISSING_MARK}[\"a\"]\nmore noise\n{MISSING_MARK}[\"b\"]\n"
        self.assertEqual(marked(body), ["b"])

    def test_bad_json_and_missing_marker_give_none(self) -> None:
        self.assertIsNone(marked(f"{MISSING_MARK}{{not json\n"))
        self.assertIsNone(marked("nothing here\n"))
        self.assertIsNone(marked(""))


class MissingTests(unittest.TestCase):
    def test_empty_input_needs_no_subprocess(self) -> None:
        self.assertEqual(missing_program_packages([]), ())
        self.assertEqual(missing_program_group({}), {})

    def test_reports_only_the_absent_declaration(self) -> None:
        found = missing_program_packages(["pip", ABSENT, "  "], python=sys.executable)
        self.assertEqual(found, (ABSENT,))

    def test_bogus_interpreter_reports_unknown(self) -> None:
        self.assertIsNone(missing_program_packages(["pip"], python=Path("no-such-python.exe")))

    def test_group_uses_one_subprocess_for_all_keys(self) -> None:
        report = missing_program_group(
            {"a": ["pip"], "b": [ABSENT], "c": []}, python=sys.executable
        )
        self.assertEqual(report, {"a": (), "b": (ABSENT,)})


if __name__ == "__main__":
    unittest.main()
