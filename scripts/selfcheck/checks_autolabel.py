"""自动标签 / 自动关键词三个插件（任务 2–4）的红线检查。

覆盖方案 `docs/AUTOLABEL_PLAN.md` §5 里的两条：

1. 插件界面只许走集成界面工具库（`qfluentwidgets` 只放行 `FluentIcon`，与 `model_ui_via_tool_library` 同一条判据）；
2. 三个插件都是外部插件（`builtin: false`）且默认不启用（`enabled: false`），互斥关系成对声明。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .harness import ROOT, Case, check

#: 三个功能插件，以及它们共用的库插件（界面目录过同一条红线）。
PLUGIN_IDS = ("auto_tag.rule", "auto_tag", "auto_keyword")
SHARED_LIB = "lib.autolabel"

#: 允许从 `qfluentwidgets` 直接拿的东西（图标是纯数据，不算界面控件）。
ALLOWED = {"FluentIcon"}

#: 必须成对出现的互斥关系。
MUTEX_PAIRS = (
    ("auto_tag.rule", "auto_tag"),
)


def _ui_problems(path: Path) -> list[str]:
    """扫一个界面文件里的 `qfluentwidgets` 直连。"""
    where = path.relative_to(ROOT).as_posix()
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and str(node.module or "").split(".")[0] == "qfluentwidgets":
            for alias in node.names:
                if alias.name not in ALLOWED:
                    problems.append(
                        f"{where}:{node.lineno} 直接 import 了 qfluentwidgets 的 {alias.name}："
                        "界面控件该从 dm_plugin.builtin.lib.ui.plugin 拿（缺的补进工具库）"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if str(alias.name).split(".")[0] == "qfluentwidgets":
                    problems.append(f"{where}:{node.lineno} 直接 import qfluentwidgets（{alias.name}）：界面控件该走 UI 工具库")
    return problems


def _dependency_ids(data: dict) -> set[str]:
    ids: set[str] = set()
    for dep in data.get("depends") or ():
        ids.add(dep if isinstance(dep, str) else str(dep.get("id") or ""))
    return ids


@check("autolabel_ui_via_tool_library", "pages")
def autolabel_ui_via_tool_library(case: Case) -> None:
    """三个插件与共享库的界面都只用 UI 工具库（`qfluentwidgets` 只放行 `FluentIcon`）。"""
    problems: list[str] = []
    for plugin_id in (*PLUGIN_IDS, SHARED_LIB):
        plugin_dir = ROOT / "plugins" / plugin_id
        assert plugin_dir.is_dir(), f"缺少插件目录：plugins/{plugin_id}"
        for path in sorted((plugin_dir / "ui").glob("*.py")):
            problems.extend(_ui_problems(path))
    assert not problems, "自动标签 / 关键词插件界面没走 UI 工具库：" + "；".join(problems[:8])


@check("autolabel_manifests", "data")
def autolabel_manifests(case: Case) -> None:
    """三个插件是外部插件、默认不启用，依赖共享库，冲突关系成对声明。"""
    manifests: dict[str, dict] = {}
    problems: list[str] = []
    for plugin_id in PLUGIN_IDS:
        path = ROOT / "plugins" / plugin_id / "plugin.json"
        assert path.is_file(), f"缺少插件清单：plugins/{plugin_id}/plugin.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        manifests[plugin_id] = data
        if data.get("id") != plugin_id:
            problems.append(f"{plugin_id}：清单 id 是 {data.get('id')!r}")
        if data.get("builtin") is not False:
            problems.append(f"{plugin_id}：外部插件的清单应写 builtin: false，实际 {data.get('builtin')!r}")
        if data.get("enabled") is not False:
            problems.append(f"{plugin_id}：默认不启用应写 enabled: false，实际 {data.get('enabled')!r}")
        if SHARED_LIB not in _dependency_ids(data):
            problems.append(f"{plugin_id}：依赖里缺少共享库 {SHARED_LIB}")
    for left, right in MUTEX_PAIRS:
        for one, other in ((left, right), (right, left)):
            declared = manifests[one].get("conflicts") or ()
            if other not in declared:
                problems.append(f"{one}：冲突关系应声明 conflicts: [{other}]，实际 {declared!r}")
    keyword = manifests["auto_keyword"]
    if keyword.get("conflicts"):
        problems.append(f"auto_keyword：关键词与标签插件互不冲突，不该声明 conflicts，实际 {keyword['conflicts']!r}")
    assert not problems, "自动标签 / 关键词插件清单：" + "；".join(problems[:8])
