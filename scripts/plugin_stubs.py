"""为合成包 `dm_plugin` 生成 IDE 桩（.pyi）。

`dm_plugin.<插件 id>` 是载入期间在 `sys.modules` 里合成的包，磁盘上并不存在，
所以 PyCharm 之类 IDE 里 `from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin`
会标红。本脚本按仓库里的插件清单一比一生成桩目录：

    stubs/dm_plugin/<插件 id 的点号路径>/{__init__.pyi,plugin.pyi}

把 `stubs` 标成源码根（仓库的 .idea/D-MyDataManager.iml 已经配好）之后，
IDE 就能解析这些导入，插件作者不用再看红字。

用法：

    python scripts/plugin_stubs.py          # 生成 / 刷新桩
    python scripts/plugin_stubs.py --check  # 只检查桩是否与清单一致（自检用）
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins"
STUB_DIR = ROOT / "stubs" / "dm_plugin"

HEADER = "# 由 scripts/plugin_stubs.py 生成，请勿手改：dm_plugin 是运行期合成包，这里只是给 IDE 用的桩。\n"

#: 桩里按类声明的名字（驼峰就当类，全大写常量与普通函数当值）
_CLASS_HINT = re.compile(r"^[A-Z][a-z]")

#: 入口文件里「看起来像插件类」的定义
_CLASS_DEF = re.compile(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?:", re.MULTILINE)


def _read_all(path: Path) -> list[str]:
    """从源码里取 `__all__`（没有就返回空列表）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return []
            return [str(item) for item in value]
    return []


def _entry_class(manifest: dict, entry: Path) -> str:
    """入口类名：清单 class 字段优先，否则取入口文件里唯一的 Plugin 子类。"""
    declared = str(manifest.get("class") or "").strip()
    if declared:
        return declared
    try:
        source = entry.read_text(encoding="utf-8")
    except OSError:
        return ""
    found: list[str] = []
    for match in _CLASS_DEF.finditer(source):
        bases = [item.strip() for item in (match.group(2) or "").split(",") if item.strip()]
        if any(base.endswith("Plugin") for base in bases):
            found.append(match.group(1))
    if len(found) == 1:
        return found[0]
    return found[-1] if found else ""


def _package_init(plugin_id: str) -> str:
    return HEADER + f'"""合成包：{plugin_id}（运行期由插件核心登记，这里只是 IDE 桩）。"""\n'


def _plugin_stub(info: dict, folder: Path) -> str:
    """一个插件的 plugin.pyi：入口类 + 库导出的名字。"""
    plugin_id = str(info.get("id") or folder.name)
    entry_name = str(info.get("entry") or "plugin.py")
    lines = [HEADER.rstrip("\n"), f'"""插件 {plugin_id} 的公开面（入口模块）。"""', "", "from typing import Any", "", "from app.sdk import Plugin", ""]
    entry_class = _entry_class(info, folder / entry_name)
    if entry_class:
        lines += [f"class {entry_class}(Plugin): ...", ""]
    exported: list[str] = []
    for library in info.get("libraries") or ():
        module = str(library.get("module") or "")
        if module:
            exported += [
                name
                for name in _read_all(folder / module)
                if name not in exported and name != entry_class
            ]
    if exported:
        lines.append("# 库导出的名字（见同目录实现）")
        for name in exported:
            lines.append(f"class {name}: ..." if _CLASS_HINT.match(name) else f"{name}: Any")
        lines.append("")
    return "\n".join(lines)


def build(plugin_dir: Path = PLUGIN_DIR) -> dict[str, str]:
    """算出所有桩文件：相对 stubs 目录的路径 -> 内容。"""
    files: dict[str, str] = {"dm_plugin/__init__.pyi": _package_init("dm_plugin")}
    for folder in sorted(Path(plugin_dir).iterdir()):
        manifest = folder / "plugin.json"
        if not folder.is_dir() or not manifest.is_file():
            continue
        info = json.loads(manifest.read_text(encoding="utf-8"))
        plugin_id = str(info.get("id") or folder.name)
        parts = plugin_id.split(".")
        prefix = "dm_plugin"
        for part in parts[:-1]:
            prefix = f"{prefix}/{part}"
            files.setdefault(f"{prefix}/__init__.pyi", _package_init(plugin_id))
        leaf = f"dm_plugin/{'/'.join(parts)}"
        files[f"{leaf}/__init__.pyi"] = _package_init(plugin_id)
        files[f"{leaf}/plugin.pyi"] = _plugin_stub(info, folder)
    return files


def write(files: dict[str, str], stub_dir: Path = STUB_DIR) -> list[str]:
    """把桩写到磁盘，返回写过的相对路径。"""
    written: list[str] = []
    root = Path(stub_dir).parent if Path(stub_dir).name == "dm_plugin" else Path(stub_dir)
    existing = {path.relative_to(root).as_posix() for path in root.rglob("*.pyi")} if root.exists() else set()
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")
            written.append(name)
    for stale in sorted(existing - set(files)):
        (root / stale).unlink()
        written.append("-" + stale)
    folders = [path for path in root.rglob("*") if path.is_dir()]
    for folder in sorted(folders, key=lambda path: len(path.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return written


def stale(plugin_dir: Path = PLUGIN_DIR, stub_dir: Path = STUB_DIR) -> list[str]:
    """列出与清单不一致的桩（缺失 / 内容过时 / 多余）。"""
    files = build(plugin_dir)
    root = Path(stub_dir).parent
    problems: list[str] = []
    for name, content in files.items():
        target = root / name
        if not target.is_file():
            problems.append(f"缺少桩：{name}")
        elif target.read_text(encoding="utf-8") != content:
            problems.append(f"桩已过时：{name}")
    existing = {path.relative_to(root).as_posix() for path in root.rglob("*.pyi")} if root.exists() else set()
    problems += [f"多余的桩：{name}" for name in sorted(existing - set(files))]
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--check" in args:
        problems = stale()
        if problems:
            print("\n".join(problems))
            print(f"共 {len(problems)} 个桩需要刷新：python scripts/plugin_stubs.py")
            return 1
        print("插件桩与清单一致")
        return 0
    written = write(build())
    for name in written:
        print(("删除 " if name.startswith("-") else "写入 ") + name.lstrip("-"))
    print(f"插件桩已就绪：{len(written)} 个文件有变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
