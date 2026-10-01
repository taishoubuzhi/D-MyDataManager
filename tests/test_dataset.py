import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gzip
import bz2
import lzma
import sqlite3
import tarfile
import wave
import zipfile

from app.db.models import DataType, guess_type
from app.services import sha256_of
from tests.dataset import TEXT_LIMIT
from tests.harness import IsolatedCase


class DatasetCase(IsolatedCase):
    """测试语料自身的自检：类型、可读性与真实容器格式。"""

    def test_every_data_type_is_covered(self):
        covered = {guess_type(path.name) for path in self.corpus.all_files}
        missing = set(DataType) - covered
        self.assertEqual(missing, set(), f"语料未覆盖的类型：{missing}")

    def test_files_exist_and_are_readable(self):
        self.assertGreater(len(self.corpus.all_files), 60)
        for path in self.corpus.all_files:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_size, len(path.read_bytes()))

    def test_duplicate_pair_shares_content(self):
        origin = sha256_of(self.corpus.by_name("dup_origin.png"))
        copy = sha256_of(self.corpus.by_name("dup_copy.png"))
        other = sha256_of(self.corpus.by_name("dup_other.png"))
        self.assertEqual(origin, copy)
        self.assertNotEqual(origin, other)

    def test_big_text_exceeds_import_limit(self):
        self.assertGreater(self.corpus.by_name("big_text.txt").stat().st_size, TEXT_LIMIT)

    def test_images_are_decodable(self):
        from PIL import Image

        for path in self.corpus.by_suffix(".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico"):
            if path.name == "broken.png":
                with self.assertRaises(OSError):
                    Image.open(path).load()
                continue
            with self.subTest(path=path.name):
                with Image.open(path) as image:
                    self.assertGreater(image.size[0], 0)
                    self.assertGreater(image.size[1], 0)

    def test_animated_gif_has_multiple_frames(self):
        from PIL import Image

        with Image.open(self.corpus.by_name("anim.gif")) as image:
            self.assertGreater(getattr(image, "n_frames", 1), 1)

    def test_real_container_formats(self):
        self.assertTrue(zipfile.is_zipfile(self.corpus.by_name("bundle.zip")))
        self.assertTrue(zipfile.is_zipfile(self.corpus.by_name("notes.docx")))
        self.assertTrue(tarfile.is_tarfile(self.corpus.by_name("backup.tar")))
        self.assertGreater(len(gzip.decompress(self.corpus.by_name("logs.gz").read_bytes())), 0)
        self.assertGreater(len(bz2.decompress(self.corpus.by_name("payload.bz2").read_bytes())), 0)
        self.assertGreater(len(lzma.decompress(self.corpus.by_name("disk.xz").read_bytes())), 0)
        with wave.open(str(self.corpus.by_name("tone.wav"))) as handle:
            self.assertGreater(handle.getnframes(), 0)
        self.assertTrue(self.corpus.by_name("store.db").read_bytes().startswith(b"SQLite format 3"))
        self.assertTrue(self.corpus.by_name("report.pdf").read_bytes().startswith(b"%PDF-1.4"))

    def test_video_and_audio_magic(self):
        expected = {
            "clip.mp4": b"\x00\x00\x00\x18ftyp",
            "movie.mkv": b"\x1a\x45\xdf\xa3",
            "old.avi": b"RIFF",
            "stream.flv": b"FLV",
            "song.mp3": b"ID3",
            "lossless.flac": b"fLaC",
            "radio.ogg": b"OggS",
        }
        for name, magic in expected.items():
            with self.subTest(name=name):
                self.assertTrue(self.corpus.by_name(name).read_bytes().startswith(magic))

    def test_nested_layout_and_hidden_entries(self):
        self.assertTrue((self.corpus.root / "空目录").is_dir())
        self.assertTrue((self.corpus.root / "子目录A" / "深" / "socket.py").is_file())
        self.assertTrue((self.corpus.root / ".datamanager" / "meta.json").is_file())
        self.assertTrue(self.corpus.by_name(".hidden_secret.txt").name.startswith("."))

    def test_special_names_are_classified(self):
        self.assertIs(guess_type("UPPER.TXT"), DataType.TEXT)
        self.assertIs(guess_type("no_extension"), DataType.OTHER)
        self.assertIs(guess_type("weird name (v2) [draft].txt"), DataType.TEXT)
        self.assertIs(guess_type("site.tar.gz"), DataType.ARCHIVE)
