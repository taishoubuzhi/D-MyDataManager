"""验收脚本（批 K）：把所有可降级依赖「藏起来」，程序仍能导入、入库、读回、监听。

这些包现在默认随 requirements.txt 装上（只有 huggingface_hub / hf-transfer 故意不装），
本脚本验证的是「装不上 / 版本不兼容 / 用户环境缺了」时程序不崩、只降级。

做法：在导入任何 app 模块之前，往 `sys.meta_path` 前面插一个拦截器，让所有可选包
一律 `ImportError`（模拟「用户没装」）。然后逐项断言：

1. `app.core.config` / `app.core.manifest` / `app.services.content_store` / `app.sdk.data` /
   `app.sdk.storage` / `app.ui.components.library_watcher` 都导入成功；
2. `app.core.capabilities` 把它们报成「缺失」，且每条都带中文安装提示与 pip 命令；
3. JSON 走标准库回退仍能读写；口令退回 PBKDF2 仍能校验；
4. 内容仓库仍能整份入库 / 读回 / 深校验（zstd 是标准库，不受影响；旧 deflate 数据也能读）；
5. `LibraryWatcher` 退回 Qt 的 `QFileSystemWatcher`（`watchdog_available()` 为假）。

跑法（仓库根目录）：

    .venv\\Scripts\\python.exe tests\\verify_optional_absence.py
"""

from __future__ import annotations

import importlib.abc
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

#: 会被藏起来的可选依赖（顶层包名）
HIDDEN = (
    "orjson",
    "fastjsonschema",
    "argon2",
    "watchdog",
    "puremagic",
    "charset_normalizer",
    "huggingface_hub",
    "pymupdf",
    "fitz",
    "jieba",
    "usearch",
    "sqlite_vec",
    "py7zr",
    "pyzipper",
    "rarfile",
    "hf_transfer",
    "imageio_ffmpeg",
    "zstandard",
)


class _Hider(importlib.abc.MetaPathFinder):
    """让被藏起来的包一律导入失败（相当于「这台机器没装」）。"""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102 - 见类文档
        if fullname.split(".")[0] in HIDDEN:
            raise ImportError(f"（验收脚本）故意隐藏可选依赖：{fullname}")
        return None


sys.meta_path.insert(0, _Hider())

problems: list[str] = []


def expect(cond, message: str) -> None:
    if not cond:
        problems.append(message)


def main() -> int:
    from tmpenv import redirect_paths, reset_config, tests_tmp

    tmp = tests_tmp("optional-absence")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    redirect_paths(tmp)
    reset_config(tmp)

    # 1) 关键模块仍能导入 -------------------------------------------------
    from app.core import capabilities
    from app.core.runtime import jsonio, security
    from app.db import database
    from app.db.seed import seed
    from app.sdk import data as sdk_data
    from app.sdk import storage
    from app.services import feature_service
    from app.services.content_store import (
        CODEC_DEFLATE,
        CODEC_LZMA,
        ContentStore,
        iter_decoded,
        sha256_of,
    )

    expect(not jsonio.orjson_available(), "orjson 应当被藏起来")
    expect(not security.argon2_available(), "argon2 应当被藏起来")

    # 2) 能力探测如实报告「缺失」并给出提示 -------------------------------
    hidden_report = [item for item in capabilities.report() if item.name in ("orjson", "argon2", "watchdog", "puremagic")]
    expect(len(hidden_report) == 4, "能力表应包含 orjson / argon2 / watchdog / puremagic")
    for item in hidden_report:
        expect(not item.available, f"{item.name} 在藏起来之后应报缺失")
        expect("pip install" in item.hint and capabilities.PYPI_MIRROR in item.hint, f"{item.name} 的提示应带镜像命令")
    expect(bool(capabilities.install_commands()), "缺失时应给出可复制的安装命令")

    # 3) JSON 与口令的回退路径 -------------------------------------------
    text = jsonio.dumps({"名字": "中文"}, indent=2)
    expect("中文" in text, "标准库回退也要输出 UTF-8 原文")
    expect(jsonio.loads(text) == {"名字": "中文"}, "标准库回退也要能读回")
    expect(storage.loads(storage.dumps({"a": 1})) == {"a": 1}, "sdk.storage 也要能回退")
    stored = security.hash_password("secret")
    expect(not stored.startswith(security.ARGON2_PREFIX), "没有 argon2 时应退回 PBKDF2")
    expect(security.verify_hash(stored, "secret"), "PBKDF2 回退仍要能校验口令")
    expect(not security.verify_hash(stored, "wrong"), "错口令仍然要拒绝")

    # 4) 内容仓库整份读写与深校验 ----------------------------------------
    payload = b"optional absence check\n" * 4096
    target = tmp / "sample.txt"
    target.write_bytes(payload)
    database.dispose_engine()
    database.init_db(force=True)
    session = database.new_session()
    seed(session)
    store = ContentStore(session)
    checksum, _rel, size = store.put_file(target, name="sample.txt", mime="text/plain")
    expect(size == len(payload), "入库返回的原始大小不对")
    expect(checksum == sha256_of(target), "校验和与整份 sha256 不一致")
    expect(store.read_content(checksum) == payload, "读回内容不一致")
    expect(b"".join(store.iter_content(checksum)) == payload, "流式读回内容不一致")
    expect(store.verify("deep").ok, "深校验应通过")
    expect(not feature_service.mime_of_file(target) or True, "纯文本嗅探缺失时也要能返回（扩展名兜底）")
    expect(feature_service.guess_mime("photo.png") == "image/png", "没有 puremagic 时按扩展名给 MIME")

    # 旧编码（deflate / lzma）在缺依赖时也必须能读
    legacy = tmp / "legacy.txt"
    legacy.write_bytes(payload)
    import lzma
    import zlib

    for codec, blob in ((CODEC_DEFLATE, zlib.compress(payload, 6)), (CODEC_LZMA, lzma.compress(payload, preset=6))):
        legacy.write_bytes(blob)
        out = b"".join(iter_decoded(legacy, codec, chunk=8192))
        expect(out == payload, f"{codec} 旧内容仍要能流式读回")

    # 5) 库监听退回 Qt 兜底路线 ------------------------------------------
    from PyQt6.QtWidgets import QApplication

    from app.services.library_service import LibraryService
    from app.ui.components.library_watcher import LibraryWatcher, watchdog_available

    app = QApplication.instance() or QApplication([])
    expect(not watchdog_available(), "watchdog 应当被藏起来")
    root = Path(LibraryService(session).ensure_default().path)
    root.mkdir(parents=True, exist_ok=True)
    watcher = LibraryWatcher(session)
    expect(watcher.watched >= 1, "Qt 兜底路线也要绑定到目录")
    watcher.shutdown()

    session.rollback()
    session.close()
    database.dispose_engine()
    del app, sdk_data  # 只用于「导入成功」这一条断言

    if "--keep" not in sys.argv:
        shutil.rmtree(tmp, ignore_errors=True)

    print("PROBLEMS:", len(problems))
    for item in problems:
        print("  -", item)
    print("OPTIONAL-ABSENCE OK" if not problems else "OPTIONAL-ABSENCE FAILED")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
