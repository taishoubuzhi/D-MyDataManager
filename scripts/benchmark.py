"""性能基准：把批 J 改过的热点（内容仓库读写、深校验、sha256、数据库整理）量一遍。

跑法（在仓库根目录）：

    .venv\\Scripts\\python.exe scripts\\benchmark.py                # 打一张 markdown 表
    .venv\\Scripts\\python.exe scripts\\benchmark.py --profile     # 顺带用 pyinstrument 采一次样

结果贴进 `docs/HELP.md` 的开发者段落。所有数据都在临时目录（`scripts/.tmp/`）里造，不碰真实 `.resources/`。

之所以不用 pytest-benchmark 常跑：它会把 `pytest -q` 门禁拖慢并在 CI 抖动，
基准要的是「同一台机器上前后对照」，独立脚本 + 固定负载更合适。
"""

from __future__ import annotations

import argparse
import gc
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tmpenv import redirect_paths, reset_config, scripts_tmp  # noqa: E402

#: 基准的临时根：`scripts/.tmp/benchmark`（见 scripts/tmpenv.py）
TMP = scripts_tmp("benchmark")
MB = 1 << 20
TEXT_MB = 4
BINARY_MB = 4
REPEATS = 3


def _payloads(root: Path) -> dict[str, Path]:
    """三份代表性负载：可压缩文本、不可压缩随机字节、已压缩容器。"""
    root.mkdir(parents=True, exist_ok=True)
    text = (("内容压缩基准测试：这是一行中文，用来测文本压缩率与吞吐。" * 20) + "\n") * 1200
    files = {
        "text.txt": text.encode("utf-8") * max(1, (TEXT_MB * MB) // max(len(text.encode("utf-8")), 1)),
        "noise.bin": bytes(range(256)) * ((BINARY_MB * MB) // 256),
        "shot.png": bytes.fromhex("89504e470d0a1a0a") + b"\x00" * (BINARY_MB * MB),
    }
    paths = {}
    for name, data in files.items():
        path = root / name
        path.write_bytes(data)
        paths[name] = path
    return paths


def _measure(label: str, size: int, action, repeats: int = REPEATS) -> dict:
    samples = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        action()
        samples.append(time.perf_counter() - start)
    best = min(samples)
    return {
        "item": label,
        "size_mb": size / MB,
        "best_ms": best * 1000,
        "median_ms": statistics.median(samples) * 1000,
        "mbps": (size / MB) / best if best else 0.0,
    }


def run() -> list[dict]:
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True, exist_ok=True)
    redirect_paths(TMP)
    reset_config(TMP)

    from app.db import database
    from app.services.content_store import (
        BlobStore,
        ContentStore,
        iter_decoded,
        sha256_of,
    )

    database.dispose_engine()
    database.init_db(force=True)
    session = database.new_session()
    store = ContentStore(session)
    sources = _payloads(TMP / "payloads")
    rows: list[dict] = []

    text = sources["text.txt"]
    noise = sources["noise.bin"]
    image = sources["shot.png"]
    text_size = text.stat().st_size
    noise_size = noise.stat().st_size
    image_size = image.stat().st_size

    rows.append(_measure("sha256_of（4 MB 文本）", text_size, lambda: sha256_of(text)))

    checksums: dict[str, str] = {}

    def put(name: str, path: Path) -> None:
        checksums[name] = store.put_file(path, name=path.name, mime="")[0]

    rows.append(_measure("put_file 冷写（文本 → zstd）", text_size, lambda: put("text", text)))
    rows.append(_measure("put_file 冷写（随机字节 → 回退 raw）", noise_size, lambda: put("noise", noise)))
    rows.append(_measure("put_file 冷写（PNG → 原样存）", image_size, lambda: put("image", image)))
    rows.append(_measure("put_file 重复入库（命中已有内容）", text_size, lambda: put("text", text)))

    text_sum = checksums["text"]
    noise_sum = checksums["noise"]
    image_sum = checksums["image"]
    rows.append(_measure("read_content（zstd 文本）", text_size, lambda: store.read_content(text_sum), repeats=5))
    rows.append(_measure("read_content（raw 随机字节）", noise_size, lambda: store.read_content(noise_sum), repeats=5))
    rows.append(
        _measure(
            "iter_content 流式（zstd 文本，1 MiB 分片）",
            text_size,
            lambda: sum(len(chunk) for chunk in store.iter_content(text_sum)),
            repeats=5,
        )
    )
    rows.append(
        _measure(
            "iter_decoded（raw 图片，1 MiB 分片）",
            image_size,
            lambda: sum(len(chunk) for chunk in iter_decoded(store.loose.path_of(store.rel_path_for(image_sum)), "raw")),
            repeats=5,
        )
    )
    rows.append(_measure("verify(quick)（3 份内容）", text_size + noise_size + image_size, lambda: store.verify("quick")))
    rows.append(
        _measure(
            "verify(deep)（3 份内容，解压 + 逐块 sha256）",
            text_size + noise_size + image_size,
            lambda: store.verify("deep"),
            repeats=2,
        )
    )

    loose = BlobStore(store.root)
    rows.append(_measure("BlobStore.total_size（3 份内容）", 0, loose.total_size, repeats=5))

    session.rollback()
    session.close()
    database.dispose_engine()
    return rows


def _table(rows: list[dict]) -> str:
    lines = ["| 项目 | 负载 | 最快耗时 | 中位耗时 | 吞吐 |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        size = f"{row['size_mb']:.1f} MB" if row["size_mb"] else "—"
        speed = f"{row['mbps']:.1f} MB/s" if row["mbps"] else "—"
        lines.append(f"| {row['item']} | {size} | {row['best_ms']:.1f} ms | {row['median_ms']:.1f} ms | {speed} |")
    return "\n".join(lines)


def _profile() -> None:
    from pyinstrument import Profiler

    profiler = Profiler()
    profiler.start()
    run()
    profiler.stop()
    print(profiler.output_text(unicode=True, color=False, show_all=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="内容仓库与数据库热点基准")
    parser.add_argument("--profile", action="store_true", help="用 pyinstrument 采一次样并打印调用树")
    parser.add_argument("--keep", action="store_true", help="保留临时目录（默认跑完删掉）")
    args = parser.parse_args(argv)
    try:
        if args.profile:
            _profile()
            return 0
        rows = run()
        print(_table(rows))
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
