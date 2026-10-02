"""自检套件命令行：分层或按名挑选检查，末行统一输出 `RESULT failures=N`。"""

from __future__ import annotations

import argparse

from .harness import LAYERS, load, run, select


def build_parser() -> argparse.ArgumentParser:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="D-MyDataManager 自检套件")
    parser.add_argument("--list", action="store_true", help="只列出检查，不运行")
    parser.add_argument("--layer", default="", help=f"只跑这些分层，逗号分隔（可选 {','.join(LAYERS)}）")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="只跑这些检查，逗号分隔；可重复传参",
    )
    parser.add_argument("--json", action="store_true", help="按行输出 JSON 结果（末行仍是 RESULT）")
    parser.add_argument("--keep-db", action="store_true", help="保留临时数据库与目录，并打印路径")
    parser.add_argument("--keep", action="store_true", help="--keep-db 的别名")
    parser.add_argument("--verbose", action="store_true", help="失败时打印完整堆栈")
    return parser


def _split(values: list[str] | str) -> list[str]:
    """把 `a,b` 与重复传参摊平成名字列表。"""
    raw = [values] if isinstance(values, str) else list(values)
    names: list[str] = []
    for value in raw:
        names.extend(part.strip() for part in str(value).split(",") if part.strip())
    return names


def main(argv: list[str] | None = None) -> int:
    """跑检查；有失败返回 1。"""
    args = build_parser().parse_args(argv)
    layers = _split(args.layer) or list(LAYERS)
    checks = load(layers)
    if args.list:
        for item in checks:
            summary = item.doc.splitlines()[0] if item.doc else ""
            print(f"{item.layer:8} {item.name:26} {summary}")
        print(f"共 {len(checks)} 项检查")
        return 0
    try:
        chosen = select(checks, _split(args.only))
    except ValueError as exc:
        print(f"参数错误：{exc}")
        return 2
    if not chosen:
        print("没有可运行的检查")
        print("RESULT failures=0")
        return 0
    results = run(
        chosen,
        keep=args.keep_db or args.keep,
        verbose=args.verbose,
        as_json=args.json,
    )
    return 1 if any(not result.ok for result in results) else 0
