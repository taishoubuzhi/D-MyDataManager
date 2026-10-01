"""测试数据生成：现场造出一批覆盖各种类型的文件与文本。

生成的数据刻意保持多样：图片用 Pillow 真实编码（PNG/JPEG/GIF/BMP/WebP/TIFF/ICO、
带透明的 RGBA、动画 GIF、超大 JPEG、损坏的 PNG），音频里有真实的 WAV，压缩包里有
真实的 ZIP/TAR/GZ/BZ2/XZ，其余视频、音频、文档、演示、可执行文件用带正确魔数的
占位内容表示，足以覆盖类型识别、封面、特征、查重、存档与导出等分支。
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import sqlite3
import tarfile
import wave
import zipfile
from pathlib import Path

TEXT_LIMIT = 512 * 1024  # 与 import_service._TEXT_LIMIT 保持一致
BIG_TEXT_BYTES = 600 * 1024

CHINESE_TEXT = """# 学习笔记：机器学习基础

## 一、监督学习
- 线性回归：最小化均方误差，解析解与梯度下降两条路。
- 逻辑回归：sigmoid 输出概率，交叉熵损失。
- 支持向量机：最大化间隔，核技巧处理非线性。

## 二、无监督学习
1. K-Means 聚类：迭代分配与更新质心。
2. 主成分分析：协方差矩阵特征分解，降到低维保留方差。

## 三、实践提醒
反向传播时要检查梯度消失；数据集划分注意类别均衡；学习率用余弦退火更稳。
结论：先把数据洗干净，再谈模型复杂度。Special characters: 100% & <tag> "quoted"。
"""

LOG_TEXT = """2026-10-01 08:00:00.001 | INFO     | app.core.startup:init_log - 日志初始化完成
2026-10-01 08:00:00.184 | DEBUG    | app.db.database:init_db - 数据表已就绪
2026-10-01 08:00:01.920 | WARNING  | app.services.import_service:import_file - 内容重复，按策略跳过：dup_copy.png
2026-10-01 08:00:02.004 | ERROR    | app.services.archive_service:create - 存档内容丢失，跳过 1 项
2026-10-01 08:00:03.512 | INFO     | app.ui.main_window:__init__ - Main window navigation initialized
"""

CODE_SAMPLES = {
    "script.py": "def add(a, b):\n    return a + b\n\n\nif __name__ == '__main__':\n    print(add(1, 2))\n",
    "app.js": "export const sum = (xs) => xs.reduce((a, b) => a + b, 0);\nconsole.log(sum([1, 2, 3]));\n",
    "types.ts": "export interface Item {\n  id: number;\n  name: string;\n}\n",
    "Main.java": "public class Main {\n    public static void main(String[] args) {\n        System.out.println(\"hello\");\n    }\n}\n",
    "hello.c": "#include <stdio.h>\nint main(void) { printf(\"hi\\n\"); return 0; }\n",
    "engine.cpp": "class Engine {\npublic:\n    void run();\n};\n",
    "engine.h": "#pragma once\nstruct Point { int x; int y; };\n",
    "game.cs": "namespace Game { class Player { public int Hp { get; set; } } }\n",
    "server.go": "package main\n\nfunc main() { println(\"server\") }\n",
    "lib.rs": "pub fn fib(n: u64) -> u64 { if n < 2 { n } else { fib(n - 1) + fib(n - 2) } }\n",
    "task.rb": "puts (1..5).map { |i| i * i }.inspect\n",
    "index.php": "<?php echo 'hello'; ?>\n",
    "deploy.sh": "#!/bin/bash\nset -euo pipefail\necho \"deploying\"\n",
    "setup.ps1": "param([string]$Name)\nWrite-Host \"install $Name\"\n",
    "schema.sql": "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n",
    "config.json": '{"theme": "dark", "coverSize": 256, "tags": ["重要", "待整理"]}\n',
    "feed.xml": '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>合集</title></channel></rss>\n',
    "settings.yaml": "app:\n  name: D-MyDataManager\n  workers: 2\n",
    "tasks.yml": "- name: 导入\n  run: import\n- name: 存档\n  run: archive\n",
    "pyproject.toml": '[project]\nname = "demo"\nversion = "0.1.0"\n',
    "page.html": '<!doctype html>\n<html lang="zh"><body><h1>标题</h1></body></html>\n',
    "style.css": "body { margin: 0; font-family: system-ui; }\n.card { border-radius: 8px; }\n",
}


def _gradient_bytes(size=(320, 240), fmt="PNG"):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        draw.line((0, y, size[0], y), fill=(y % 256, (y * 3) % 256, 200 - y % 200))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def _photo_bytes():
    from PIL import Image

    image = Image.effect_noise((1600, 1200), 64).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _screenshot_bytes():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (800, 600), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    for x in range(0, 800, 40):
        draw.line((x, 0, 800 - x, 600), fill=(x % 256, 120, 200, 255), width=6)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _animated_gif_bytes():
    from PIL import Image, ImageDraw

    frames = []
    for shift in (0, 120):
        frame = Image.new("RGB", (32, 32), (20, 20, 20))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((shift % 20, 4, shift % 20 + 12, 28), fill=(shift + 40, 60, 180))
        frames.append(frame)
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)
    return buffer.getvalue()


def _ico_bytes():
    from PIL import Image

    image = Image.new("RGB", (64, 64), (255, 128, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="ICO", sizes=[(16, 16), (32, 32)])
    return buffer.getvalue()


def _wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(int(12000 * ((i % 20) - 10) / 10).to_bytes(2, "little", signed=True) for i in range(4000)))
    return buffer.getvalue()


def _zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("readme.txt", "压缩包内的说明文件\n")
        archive.writestr("data/nested.json", '{"ok": true}\n')
    return buffer.getvalue()


def _tar_bytes():
    buffer = io.BytesIO()
    payload = b"tar payload\n"
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("notes.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _sqlite_bytes():
    """造一个真实的 SQLite 文件；沙箱禁写系统临时目录，因此在 tests/_scratch 下生成再删除。"""
    scratch = Path(__file__).resolve().parent / "_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "store.db"
    try:
        connection = sqlite3.connect(str(path))
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO t (name) VALUES ('测试行')")
        connection.commit()
        connection.close()
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
        for extra in ("-wal", "-shm"):
            (scratch / f"store.db{extra}").unlink(missing_ok=True)


def _ooxml_bytes(kind="docx"):
    members = {
        "docx": ["word/document.xml", "[Content_Types].xml"],
        "xlsx": ["xl/workbook.xml", "[Content_Types].xml"],
        "pptx": ["ppt/presentation.xml", "[Content_Types].xml"],
        "odt": ["content.xml", "mimetype"],
        "epub": ["mimetype", "OEBPS/content.opf"],
    }[kind]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in members:
            archive.writestr(name, f"<{name.replace('.', '_')}/>")
    return buffer.getvalue()


def _pdf_bytes():
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    return body


OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ASF_HEADER = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"
EBML_HEADER = b"\x1a\x45\xdf\xa3"
MP4_HEADER = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41mdat"


def _data(name: str) -> bytes:
    """按文件名造出内容（未列出的名字走默认分支）。"""
    suffix = name.lower()
    if suffix.endswith(".png"):
        if name.startswith("broken"):
            return b"\x89PNG\r\n\x1a\n" + b"not-a-real-png" * 4
        if name.startswith("screenshot"):
            return _screenshot_bytes()
        if name.startswith("dup_other"):
            return _gradient_bytes((48, 32))
        if name.startswith("dup_"):
            return _gradient_bytes((96, 72))
        return _gradient_bytes()
    if suffix.endswith((".jpg", ".jpeg")):
        return _photo_bytes()
    if suffix.endswith(".gif"):
        return _animated_gif_bytes()
    if suffix.endswith(".bmp"):
        return _gradient_bytes((160, 120))
    if suffix.endswith(".webp"):
        return _gradient_bytes((200, 150), "WEBP")
    if suffix.endswith(".tiff"):
        return _gradient_bytes((180, 140), "TIFF")
    if suffix.endswith(".ico"):
        return _ico_bytes()
    if suffix.endswith(".wav"):
        return _wav_bytes()
    if suffix.endswith(".mp3"):
        return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 32
    if suffix.endswith(".flac"):
        return b"fLaC\x00\x00\x00\x22" + b"\x00" * 64
    if suffix.endswith(".m4a"):
        return b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00M4A mp42isom"
    if suffix.endswith(".aac"):
        return b"\xff\xf1\x50\x80" + b"\x00" * 96
    if suffix.endswith(".ogg"):
        return b"OggS\x00\x02" + b"\x00" * 96
    if suffix.endswith(".wma"):
        return ASF_HEADER + b"\x00" * 128
    if suffix.endswith(".ape"):
        return b"MAC \x0f\x96" + b"\x00" * 96
    if suffix.endswith((".mp4", ".m4v")):
        return MP4_HEADER + b"\x00" * 256
    if suffix.endswith(".mkv") or suffix.endswith(".webm"):
        return EBML_HEADER + b"\x42\x86\x81\x01" + b"\x00" * 200
    if suffix.endswith(".avi"):
        return b"RIFF\x24\x01\x00\x00AVI LIST" + b"\x00" * 128
    if suffix.endswith(".mov"):
        return b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 128
    if suffix.endswith(".wmv"):
        return ASF_HEADER + b"\x00" * 160
    if suffix.endswith(".flv"):
        return b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00" * 64
    if suffix.endswith((".mpg", ".mpeg")):
        return b"\x00\x00\x01\xba\x21\x00\x01\x00" + b"\x00" * 128
    if suffix.endswith(".pdf"):
        return _pdf_bytes()
    if suffix.endswith(".docx"):
        return _ooxml_bytes("docx")
    if suffix.endswith(".xlsx"):
        return _ooxml_bytes("xlsx")
    if suffix.endswith(".pptx"):
        return _ooxml_bytes("pptx")
    if suffix.endswith(".odt"):
        return _ooxml_bytes("odt")
    if suffix.endswith(".odp") or suffix.endswith(".ods"):
        return _ooxml_bytes("odt")
    if suffix.endswith(".epub"):
        return _ooxml_bytes("epub")
    if suffix.endswith((".doc", ".xls", ".ppt")):
        return OLE_HEADER + b"\x00" * 256
    if suffix.endswith(".mobi"):
        return b"BOOKMOBI\x00" + b"\x00" * 128
    if suffix.endswith(".rtf"):
        return "{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Calibri;}}\\par Hello 世界\\par}".encode("utf-8")
    if suffix.endswith(".csv"):
        return ("名称,数量,备注\n" '"笔记, 复习",12,"含逗号, 引号"\n' "视频,3,普通\n").encode("utf-8")
    if suffix.endswith(".tsv"):
        return "名称\t数量\n笔记\t12\n".encode("utf-8")
    if suffix.endswith(".md"):
        return CHINESE_TEXT.encode("utf-8")
    if suffix.endswith(".log"):
        return LOG_TEXT.encode("utf-8")
    if suffix.endswith(".txt"):
        if name.startswith("big_text"):
            line = "机器学习与数据管理测试数据 abcdefghijklmnopqrstuvwxyz 0123456789\n"
            return (line * (BIG_TEXT_BYTES // len(line.encode("utf-8")) + 1)).encode("utf-8")
        if name.startswith("empty"):
            return b""
        return CHINESE_TEXT.encode("utf-8")
    if suffix.endswith((".zip",)):
        return _zip_bytes()
    if suffix.endswith(".tar"):
        return _tar_bytes()
    if suffix.endswith(".tar.gz"):
        return gzip.compress(_tar_bytes())
    if suffix.endswith(".gz"):
        return gzip.compress(b"gzip payload\n" * 32)
    if suffix.endswith(".bz2"):
        return bz2.compress(b"bzip2 payload\n" * 32)
    if suffix.endswith(".xz"):
        return lzma.compress(b"xz payload\n" * 32)
    if suffix.endswith(".7z"):
        return b"7z\xbc\xaf\x27\x1c" + b"\x00" * 96
    if suffix.endswith(".rar"):
        return b"Rar!\x1a\x07\x00" + b"\x00" * 96
    if suffix.endswith(".exe"):
        return b"MZ\x90\x00\x03" + b"\x00" * 200
    if suffix.endswith(".dll"):
        return b"MZ\x90\x00\x03" + b"\x00" * 180
    if suffix.endswith(".iso"):
        return b"\x00" * 32768 + b"CD001" + b"\x00" * 128
    if suffix.endswith(".ttf"):
        return b"\x00\x01\x00\x00\x00\x0f\x00\x80" + b"\x00" * 128
    if suffix.endswith(".psd"):
        return b"8BPS\x00\x01" + b"\x00" * 128
    if suffix.endswith(".db"):
        return _sqlite_bytes()
    if suffix.endswith(".py"):
        snippet = CODE_SAMPLES["script.py"]
    else:
        snippet = CODE_SAMPLES.get(name)
    if snippet is not None:
        return snippet.encode("utf-8")
    if name == "no_extension":
        return "这个文件没有扩展名，应当归入其他类型。\n".encode("utf-8")
    return ("unknown payload for " + name + "\n").encode("utf-8")


TOP_LEVEL_FILES = (
    # 图片
    "photo_gradient.png", "photo_photo.jpg", "anim.gif", "bitmap.bmp", "picture.webp",
    "scan.tiff", "favicon.ico", "broken.png", "screenshot.png",
    "dup_origin.png", "dup_copy.png", "dup_other.png",
    # 视频
    "clip.mp4", "movie.mkv", "old.avi", "trailer.mov", "camera.wmv", "stream.flv",
    "web.webm", "phone.m4v", "disk.mpg", "tape.mpeg",
    # 音频
    "tone.wav", "song.mp3", "lossless.flac", "voice.m4a", "track.aac", "radio.ogg",
    "album.wma", "vinyl.ape",
    # 文档
    "report.pdf", "notes.docx", "manual.doc", "article.odt", "readme.rtf", "book.epub",
    "novel.mobi", "guide.md",
    # 表格
    "budget.xlsx", "legacy.xls", "data.csv", "export.tsv", "sheet.ods",
    # 演示
    "slides.pptx", "pitch.odp", "talk.ppt",
    # 压缩包
    "bundle.zip", "backup.tar", "site.tar.gz", "logs.gz", "payload.bz2", "disk.xz",
    "files.7z", "old.rar",
    # 代码
    "script.py", "app.js", "types.ts", "Main.java", "hello.c", "engine.cpp", "engine.h",
    "game.cs", "server.go", "lib.rs", "task.rb", "index.php", "deploy.sh", "setup.ps1",
    "schema.sql", "config.json", "feed.xml", "settings.yaml", "tasks.yml", "pyproject.toml",
    "page.html", "style.css",
    # 文本
    "学习笔记.txt", "server.log", "empty.txt", "big_text.txt", "no_extension", "UPPER.TXT",
    "weird name (v2) [draft].txt", ".hidden_secret.txt",
    # 其他
    "program.exe", "library.dll", "blob.bin", "image.iso", "font.ttf", "design.psd",
    "store.db", "mystery.xyz",
)

NESTED_FILES = (
    "子目录A/说明.txt",
    "子目录A/深/socket.py",
    "图片素材/pic.png",
    "学习资料/课程笔记.md",
    ".datamanager/meta.json",
)


class Corpus:
    """测试语料：根目录散放各种文件，另有嵌套子目录用于目录导入 / 扫描。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: list[Path] = []
        self.nested: list[Path] = []
        self._build()

    def _build(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in TOP_LEVEL_FILES:
            path = self.root / name
            path.write_bytes(_data(name))
            self.files.append(path)
        # 内容完全相同的两份（用于查重），第三份内容不同
        self.files[self.files.index(self.root / "dup_copy.png")].write_bytes(
            (self.root / "dup_origin.png").read_bytes()
        )
        for name in NESTED_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_data(Path(name).name))
            self.nested.append(path)
        (self.root / "空目录").mkdir(exist_ok=True)

    @property
    def all_files(self) -> list[Path]:
        return [*self.files, *self.nested]

    def by_name(self, name: str) -> Path:
        for path in self.all_files:
            if path.name == name:
                return path
        raise KeyError(name)

    def by_suffix(self, *suffixes: str) -> list[Path]:
        wanted = tuple(suffix.lower() for suffix in suffixes)
        return [p for p in self.all_files if p.suffix.lower() in wanted]

    def of_type(self, data_type) -> list[Path]:
        from app.db.models import guess_type

        return [p for p in self.all_files if guess_type(p.name) is data_type]


def build_corpus(root: Path) -> Corpus:
    return Corpus(root)
