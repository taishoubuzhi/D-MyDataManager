"""一条命令跑完项目的验收门禁。

用法：
    .venv\\Scripts\\python.exe tests\\verify.py            # 全部步骤
    .venv\\Scripts\\python.exe tests\\verify.py --quick    # 只跑快的四步（编译 / 插件桩 / 可选依赖 / 打包冒烟）
    .venv\\Scripts\\python.exe tests\\verify.py --only unittest,selfcheck
    .venv\\Scripts\\python.exe tests\\verify.py --list     # 只列步骤名

每步打印 `ok   <名称>（耗时）` 或 `FAIL <名称>（耗时）`，失败时附子进程输出末尾；末行固定
`RESULT failures=N`，N>0 时退出码 1。子进程统一带 `PYTHONIOENCODING=utf-8` 与
`QT_QPA_PLATFORM=offscreen`（Windows 控制台与无头自检都稳）。

`selfcheck` 一步不直接跑四层合一：`pages` 层有 70 项 Qt offscreen 检查，在同一个长驻进程里跑到
第 ~60 项会偶发原生崩溃（退出码 3221225477 = 0xC0000005），所以这里把 `pages` 按 12 项一组拆进多个
独立进程，`data` / `services` / `flows` 各跑一个进程——检查项一个不少，只是换了进程边界。
分块之后仍偶发（崩溃点会漂移、同一块重跑必过），因此 selfcheck 各步遇到「原生崩溃且没有任何 FAIL 行」
时自动重跑一次；这是环境问题，不是检查失败。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 一次 selfcheck 进程里跑多少项 pages 检查（见模块 docstring）
PAGES_CHUNK = 12

QUICK = {"compileall", "stubs", "optional", "package"}


def _env() -> dict[str, str]:
    """子进程环境：UTF-8 输出 + 无头 Qt。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """跑一条命令并收走输出（不打印，失败时才由调用方展示）。"""
    return subprocess.run(
        argv,
        cwd=ROOT,
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _crashed(done: subprocess.CompletedProcess[str]) -> bool:
    """区分「解释器原生崩溃」与「检查失败」：退出码落在 Windows 状态码区间且没有 FAIL 行。"""
    code = done.returncode or 0
    return code >= 0xC0000000 and "FAIL" not in (done.stdout or "")


def _pages_names(python: str) -> list[str]:
    """列出 pages 层的检查名（`--list` 每行 `pages <名称> <说明>`）。"""
    done = _run([python, "scripts/selfcheck.py", "--list", "--layer", "pages"])
    names: list[str] = []
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "pages":
            names.append(parts[1])
    return names


def steps(python: str) -> list[tuple[str, list[str], bool]]:
    """验收步骤：`(名称, 命令, 是否属于 --quick)`。"""
    items: list[tuple[str, list[str], bool]] = [
        ("compileall", [python, "-m", "compileall", "-q", "src", "plugins", "scripts", "tests"], True),
        ("stubs", [python, "scripts/plugin_stubs.py", "--check"], True),
        ("unittest", [python, "-m", "unittest", "discover", "-s", "tests", "-t", "."], False),
    ]
    for layer in ("data", "services", "flows"):
        items.append((f"selfcheck:{layer}", [python, "scripts/selfcheck.py", "--layer", layer], False))
    names = _pages_names(python)
    for index in range(0, len(names), PAGES_CHUNK):
        chunk = names[index : index + PAGES_CHUNK]
        label = f"selfcheck:pages[{index // PAGES_CHUNK + 1}]"
        items.append((label, [python, "scripts/selfcheck.py", "--only", ",".join(chunk)], False))
    items += [
        ("pytest", [python, "-m", "pytest", "-q"], False),
        ("optional", [python, "tests/verify_optional_absence.py"], True),
        ("package", [python, "tests/smoke_checkout.py"], True),
    ]
    return items


def main(argv: list[str] | None = None) -> int:
    """跑步骤，返回失败数（0 = 全绿）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    python = sys.executable
    plan = steps(python)
    if "--list" in args:
        for name, _argv, quick in plan:
            print(f"{'quick' if quick else 'full '} {name}")
        print(f"共 {len(plan)} 步")
        return 0
    quick = "--quick" in args
    only: set[str] = set()
    if "--only" in args:
        only = {part.strip() for part in args[args.index("--only") + 1].split(",") if part.strip()}
    chosen = [(name, cmd) for name, cmd, is_quick in plan if (not quick or is_quick) and (not only or name in only)]
    if not chosen:
        print("没有可运行的步骤")
        print("RESULT failures=0")
        return 0

    failures = 0
    started = time.perf_counter()
    for name, cmd in chosen:
        began = time.perf_counter()
        done = _run(cmd)
        if done.returncode != 0 and name.startswith("selfcheck") and _crashed(done):
            # 本机已知问题：pages 的 Qt offscreen 检查偶发原生崩溃（0xC0000005），重跑一次
            print(f"     {name}：解释器原生崩溃（退出码 {done.returncode}），重跑一次", flush=True)
            done = _run(cmd)
        spent = time.perf_counter() - began
        if done.returncode == 0:
            print(f"ok   {name}（{spent:.1f}s）", flush=True)
            continue
        failures += 1
        print(f"FAIL {name}（{spent:.1f}s）退出码 {done.returncode}", flush=True)
        tail = [line for line in (done.stdout or "").splitlines() if line.strip()][-12:]
        if tail:
            print("      " + "\n      ".join(tail), flush=True)
    print(f"verify: {len(chosen) - failures}/{len(chosen)} 步通过，总耗时 {time.perf_counter() - started:.1f}s", flush=True)
    print(f"RESULT failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
