"""查看器数据层（编码探测、表格解析、压缩包列表）的单元测试。"""

from __future__ import annotations

import gzip
import sys
import tarfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.harness import TempDir  # noqa: E402
from app.core.viewer_data import (  # noqa: E402
    archive_members,
    archive_read,
    csv_rows,
    decode_text,
    human_size,
    image_info,
    is_archive,
    is_text_file,
    looks_binary,
    read_text,
    xlsx_sheets,
)

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
  Target="worksheets/sheet1.xml"/></Relationships>"""

_SHARED = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
 <si><t>姓名</t></si><si><t>小张</t></si></sst>"""

_SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
 <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>数量</t></is></c></row>
 <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42</v></c></row>
</sheetData></worksheet>"""


def make_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _RELS)
        archive.writestr("xl/sharedStrings.xml", _SHARED)
        archive.writestr("xl/worksheets/sheet1.xml", _SHEET)


class ViewerDataCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TempDir("viewer")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    # ------------------------------------------------------------------ 文本
    def test_human_size_scales_units(self) -> None:
        self.assertEqual(human_size(0), "0 B")
        self.assertIn("KB", human_size(1536))
        self.assertIn("MB", human_size(3 * 1024 * 1024))

    def test_decode_text_detects_gb18030(self) -> None:
        text, used = decode_text("中文内容".encode("gb18030"))
        self.assertEqual(text, "中文内容")
        self.assertEqual(used, "gb18030")

    def test_decode_text_reads_utf8(self) -> None:
        text, used = decode_text("中文内容".encode("utf-8"))
        self.assertEqual(text, "中文内容")
        self.assertIn(used, ("utf-8", "utf-8-sig"))

    def test_looks_binary_detects_nul_bytes(self) -> None:
        self.assertTrue(looks_binary(b"PK\x00\x01"))
        self.assertFalse(looks_binary("纯文本".encode("utf-8")))

    def test_read_text_reports_encoding_and_truncation(self) -> None:
        path = self.write("note.txt", "一二三四五六七八九十".encode("utf-8"))
        text, encoding, truncated = read_text(path, limit=6)
        self.assertTrue(truncated)
        self.assertTrue(encoding)
        self.assertTrue("一二三四五六七八九十".startswith(text))
        self.assertLess(len(text), 10)
        text, _encoding, truncated = read_text(path)
        self.assertFalse(truncated)
        self.assertEqual(text, "一二三四五六七八九十")

    def test_is_text_file_uses_suffix_and_content(self) -> None:
        self.assertTrue(is_text_file(self.write("a.py", b"print(1)\n")))
        self.assertFalse(is_text_file(self.write("b.png", b"\x89PNG\r\n\x1a\n\x00\x00")))

    # ------------------------------------------------------------------ 表格
    def test_xlsx_sheets_reads_shared_and_inline_strings(self) -> None:
        path = self.root / "表格.xlsx"
        make_xlsx(path)
        sheets = xlsx_sheets(path)
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0].name, "数据")
        self.assertEqual(sheets[0].rows[0], ["姓名", "数量"])
        self.assertEqual(sheets[0].rows[1], ["小张", "42"])
        self.assertFalse(sheets[0].truncated)

    def test_xlsx_sheets_marks_truncation(self) -> None:
        path = self.root / "表格.xlsx"
        make_xlsx(path)
        sheets = xlsx_sheets(path, max_rows=1)
        self.assertEqual(len(sheets[0].rows), 1)
        self.assertTrue(sheets[0].truncated)

    def test_csv_rows_sniffs_delimiter(self) -> None:
        path = self.write("data.csv", "姓名;数量\n小张;42\n".encode("utf-8"))
        sheets = csv_rows(path)
        self.assertEqual(sheets[0].rows[0], ["姓名", "数量"])
        self.assertEqual(sheets[0].rows[1], ["小张", "42"])
        self.assertTrue(csv_rows(path, max_rows=1)[0].truncated)

    # ---------------------------------------------------------------- 压缩包
    def test_archive_members_and_read_for_zip(self) -> None:
        path = self.root / "包.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.txt", "hello")
            archive.writestr("folder/b.txt", "world")
        self.assertTrue(is_archive(path))
        names = [member.name for member in archive_members(path)]
        self.assertEqual(names, ["a.txt", "folder/b.txt"])
        self.assertEqual(archive_read(path, "a.txt"), b"hello")

    def test_archive_members_and_read_for_tar(self) -> None:
        source = self.write("a.txt", b"hello")
        path = self.root / "包.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            archive.add(source, arcname="a.txt")
        names = [member.name for member in archive_members(path)]
        self.assertEqual(names, ["a.txt"])
        self.assertEqual(archive_read(path, "a.txt"), b"hello")

    def test_single_stream_gz_is_reported_as_one_member(self) -> None:
        path = self.root / "log.gz"
        with gzip.open(path, "wb") as handle:
            handle.write(b"hello gz")
        members = archive_members(path)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].name, "log")
        self.assertEqual(members[0].size, 8)

    def test_archive_members_of_plain_file_is_empty(self) -> None:
        self.assertEqual(archive_members(self.write("plain.dat", b"x" * 16)), [])
        self.assertFalse(is_archive(self.root / "plain.dat"))

    # ------------------------------------------------------------------ 图片
    def test_image_info_reads_png_header(self) -> None:
        from PIL import Image

        path = self.root / "图.png"
        Image.new("RGB", (12, 7), "red").save(path)
        info = image_info(path)
        self.assertEqual((info["width"], info["height"]), (12, 7))
        self.assertEqual(info["format"], "PNG")


if __name__ == "__main__":
    unittest.main()
