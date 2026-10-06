"""MIME 与编码探测（批 J4）：内容嗅探优先于扩展名，但同类时扩展名更具体。"""

from __future__ import annotations

import unittest

from app.sdk.data import DETECT_LIMIT, decode_text, detect_encoding
from app.services.feature_service import guess_mime, mime_of_file
from tests.harness import TempDir

_PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64
_ZIP = b"PK\x03\x04" + b"\x00" * 64


class EncodingCase(unittest.TestCase):
    """编码 sniff：优先 charset-normalizer，兜底仍是固定顺序且必定成功。"""

    def test_utf8_and_ascii_report_utf8(self):
        self.assertEqual(decode_text("你好".encode("utf-8")), ("你好", "utf-8"))
        self.assertEqual(decode_text(b"hello world"), ("hello world", "utf-8"))

    def test_utf8_bom_keeps_sig(self):
        raw = "\ufeff你好".encode("utf-8")
        text, used = decode_text(raw)
        self.assertEqual(used, "utf-8-sig")
        self.assertEqual(text, "你好")  # BOM 不留在正文里

    def test_gb18030_is_decoded_correctly(self):
        raw = "这是一个用来测试编码探测的中文文件，内容要足够长才有判断依据。".encode("gb18030")
        self.assertTrue(detect_encoding(raw), "charset-normalizer 应给出一个编码名")
        text, used = decode_text(raw)
        self.assertEqual(text, "这是一个用来测试编码探测的中文文件，内容要足够长才有判断依据。")
        self.assertNotEqual(used, "utf-8", "gb18030 的字节不该被当成 utf-8")

    def test_explicit_encoding_wins(self):
        self.assertEqual(decode_text(b"\xc4\xe3\xba\xc3", "gb18030"), ("你好", "gb18030"))

    def test_broken_text_still_decodes(self):
        text, used = decode_text(b"\xff\xfe\x00\x41")  # 半截 utf-16 片段
        self.assertTrue(text is not None and used)

    def test_detect_on_empty_is_empty(self):
        self.assertEqual(detect_encoding(b""), "")
        self.assertEqual(DETECT_LIMIT, 64 * 1024)


class MimeCase(unittest.TestCase):
    """MIME：内容嗅探只在「扩展名不可靠或跨类冲突」时说话。"""

    def setUp(self):
        self.tmp = TempDir(prefix="mime")
        self.root = self.tmp.__enter__()

    def tearDown(self):
        self.tmp.__exit__(None, None, None)

    def _write(self, name: str, data: bytes):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_extension_used_for_same_family(self):
        path = self._write("notes.md", "# 标题\n正文\n".encode("utf-8"))
        self.assertEqual(guess_mime(path.name, path), "text/markdown")

    def test_content_wins_when_family_differs(self):
        path = self._write("fake.txt", _PNG)
        self.assertEqual(mime_of_file(path), "image/png")
        self.assertEqual(guess_mime(path.name, path), "image/png", "改名成 .txt 的图片应按内容认")

    def test_extension_wins_for_same_application_family(self):
        path = self._write("bundle.zip", _ZIP)
        mime = guess_mime(path.name, path)
        self.assertIn("zip", mime, "zip 不该被嗅探成 docx")
        self.assertNotIn("wordprocessingml", mime)

    def test_content_used_when_extension_unknown(self):
        path = self._write("blob.zzz", _PNG)
        self.assertEqual(guess_mime(path.name, path), "image/png")
        # 纯文本也可能被嗅探认出（puremagic 对文本有兜底）：扩展名不可用时以内容为准
        path2 = self._write("plain.zzz", b"just text, nothing magic here\n")
        self.assertEqual(guess_mime(path2.name, path2), "text/plain")

    def test_fallback_when_nothing_identifies(self):
        """内容和扩展名都给不出答案时，才用兜底 MIME。"""
        self.assertEqual(guess_mime("no-extension"), "application/octet-stream")
        self.assertEqual(guess_mime("blob.zzz"), "application/octet-stream")

    def test_extension_only_when_no_source(self):
        self.assertEqual(guess_mime("photo.png"), "image/png")
        self.assertEqual(guess_mime("no-extension"), "application/octet-stream")

    def test_unreadable_source_falls_back_to_extension(self):
        missing = self.root / "gone.png"
        self.assertEqual(mime_of_file(missing), "")
        self.assertEqual(guess_mime(missing.name, missing), "image/png")


if __name__ == "__main__":
    unittest.main()
