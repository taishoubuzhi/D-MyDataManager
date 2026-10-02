"""自检套件入口（REWRITE.md §7）。

用法：
    .venv\\Scripts\\python.exe scripts\\selfcheck.py --list
    .venv\\Scripts\\python.exe scripts\\selfcheck.py
    .venv\\Scripts\\python.exe scripts\\selfcheck.py --layer data,services
    .venv\\Scripts\\python.exe scripts\\selfcheck.py --only schema_tables --keep --verbose

末行输出 `RESULT failures=N`，有失败时退出码为 1。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent / "selfcheck"
ROOT = PKG_DIR.parents[1]
for _path in (ROOT, ROOT / "src", ROOT / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _load_package():
    """按文件路径加载同目录的自检包：避免与本文件的模块名 `selfcheck` 冲突。"""
    spec = importlib.util.spec_from_file_location(
        "app_selfcheck",
        PKG_DIR / "__init__.py",
        submodule_search_locations=[str(PKG_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(_load_package().main(sys.argv[1:]))
