"""打包冒烟（批 L）：造一份「干净检出」，在它里面启动程序并跑门禁子集。

真正的发布产物由 PyAppify 启动器在**用户机器上**生成（按 tag 克隆 `git_url` → 建独立
venv → `pip install -r requirements.txt`）。本机没有 `pyappify.exe` 时造不出启动器，
但产物的**输入**就是这份 git 检出，所以这里验证两件事：

1. 载荷只含该有的东西——`.venv/`、`PyQt-Fluent-Widgets/`（上游参考仓库）、`.packages/`、`stubs/`、
   `.resources/`、`.configs/`、任何层级的 `.tmp/` 都不该出现（都是 .gitignore 排除的），而
   `pyappify.yml` / `requirements.txt` / `src/main.py` / `plugins/` / `docs/`（随包分发的文档）/
   `scripts/selfcheck.py` / `scripts/tmpenv.py` / `tests/harness.py` / `icons/icon.png` 必须在；
2. 在这份检出里，程序能编译、能启动（走 `src/main.py --self-check`
   的启动路径）并跑通门禁子集。

载荷清单用 `git ls-files --cached --others --exclude-standard`：已跟踪的 + 未跟踪但没被
忽略的，正好等于「下一次提交会包含的文件」，也就是将来打 tag 时的检出内容。

跑法（仓库根目录）：

    .venv\\Scripts\\python.exe tests\\smoke_checkout.py [--keep]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tmpenv import tests_tmp  # noqa: E402

SMOKE_DIR = tests_tmp("package-smoke")
STEP_TIMEOUT = 900

#: 不该出现在载荷里的顶层名字（.gitignore 已经排除的东西，出现就说明打包输入选错了）
FORBIDDEN = (
    ".venv",
    "PyQt-Fluent-Widgets",
    ".packages",
    "stubs",
    ".resources",
    ".configs",
    ".logs",
    "cache",
    "exports",
    "pyappify_dist",
    ".git",
)

#: 必须在载荷里的文件
#: 注意：AGENTS.md 与 docs/index/（开发用索引）在 .gitignore 里被排除，不进版本库也不随发布
#: 分发，所以这里不校验它们——载荷清单按 `git ls-files --exclude-standard` 算，被忽略的
#: 文件永远不在清单里。
REQUIRED = (
    "pyappify.yml",
    "requirements.txt",
    "src/main.py",
    "plugins/lib.model/plugin.json",
    "scripts/selfcheck.py",
    "scripts/tmpenv.py",
    "tests/harness.py",
    "README.md",
    "docs/PLUGIN.md",
    "docs/HELP.md",
    "docs/SDK.md",
    "docs/PLUGIN_PROTOCOL.md",
    "docs/SCRIPTS.md",
    "docs/TESTS.md",
    "icons/icon.png",
)

#: 门禁子集：都不依赖界面与真实数据，跑得快
TESTS = (
    "tests.core.test_core_module_data",
    "tests.core.test_manifest",
    "tests.core.test_capabilities",
    "tests.core.test_jsonio",
    "tests.core.test_mime",
    "tests.core.test_security",
)


def _payload_files() -> list[Path]:
    """下一次提交会包含的文件（相对仓库根）。"""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def _stage(files: list[Path]) -> Path:
    shutil.rmtree(SMOKE_DIR, ignore_errors=True)
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    for relative in files:
        source = ROOT / relative
        if not source.is_file():
            continue  # 清单里可能有已删除但还没提交的条目
        target = SMOKE_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return SMOKE_DIR


def _check_payload(copy: Path, files: list[Path], problems: list[str]) -> None:
    tops = {item.parts[0] for item in files if item.parts}
    for name in FORBIDDEN:
        if name in tops:
            problems.append(f"载荷里不该出现 {name}/（.gitignore 应当排除它）")
    for name in REQUIRED:
        if not (copy / name).is_file():
            problems.append(f"载荷缺少 {name}")
    # 临时目录（任何层级的 .tmp/）不该被带进去
    for path in files:
        if ".tmp" in path.parts:
            problems.append(f"载荷里不应有临时目录：{path}")
            break
    # pycache / pyc 不该被带进去
    for path in copy.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            problems.append(f"载荷里不应有编译缓存：{path.relative_to(copy)}")
            break
    text = (copy / "pyappify.yml").read_text(encoding="utf-8")
    for key, pattern in (
        ("requires_python", r'requires_python:\s*"([^"]+)"'),
        ("git_url", r'git_url:\s*"([^"]+)"'),
        ("main_script", r'main_script:\s*"([^"]+)"'),
        ("requirements", r'requirements:\s*"([^"]+)"'),
    ):
        found = re.search(pattern, text)
        if found is None:
            problems.append(f"pyappify.yml 缺少 {key}")
    version = re.search(r'requires_python:\s*"([^"]+)"', text)
    # pyappify 启动器的 KNOWN_PATCHES 硬编码到 3.13（最高 3.13.5），写 3.14 会在 setup 阶段
    # 直接失败（Unsupported major.minor version for resolving latest patch: 3.14）。
    if version is not None and not version.group(1).startswith(("3.13", "3.12", "3.11")):
        problems.append(
            f"pyappify.yml 的 requires_python 需是 pyappify 支持的系列（3.13/3.12/3.11），实际 {version.group(1)}"
        )


def _run(label: str, args: list[str], cwd: Path) -> tuple[bool, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("PYTHONPATH", None)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, *args],
            cwd=cwd,
            capture_output=True,
            timeout=STEP_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label}：超时（>{STEP_TIMEOUT}s）"
    seconds = time.perf_counter() - started
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    if result.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-6:])
        return False, f"{label}：rc={result.returncode}（{seconds:.1f}s）\n{tail}"
    return True, f"{label}：{seconds:.1f}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包冒烟：干净检出 + 启动 + 门禁子集")
    parser.add_argument("--keep", action="store_true", help="保留干净检出目录（默认跑完删掉）")
    args = parser.parse_args(argv)

    problems: list[str] = []
    rows: list[str] = []
    try:
        files = _payload_files()
        copy = _stage(files)
        rows.append(f"载荷文件：{len(files)} 个 → {copy}")
        _check_payload(copy, files, problems)

        if not problems:
            for label, command in (
                ("compileall", ["-m", "compileall", "-q", "src", "plugins", "scripts", "tests"]),
                ("启动 + 自检 data 层", ["src/main.py", "--self-check", "--layer", "data"]),
                ("门禁子集 unittest", ["-m", "unittest", *TESTS]),
            ):
                ok, message = _run(label, command, copy)
                rows.append(("ok   " if ok else "FAIL ") + message)
                if not ok:
                    problems.append(message)
    finally:
        if not args.keep:
            shutil.rmtree(SMOKE_DIR, ignore_errors=True)

    print("\n".join(rows))
    print("PROBLEMS:", len(problems))
    for item in problems:
        print("  -", item)
    print("PACKAGE-SMOKE OK" if not problems else "PACKAGE-SMOKE FAILED")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
