"""插件布局检查：协议 v2 的四元目录（含 `PLUGIN.md`）与清单字段。

只走公开契约：`app.core.plugins.plugin_core` 的 `layout_problems()` /
`PROTOCOL_FIELDS` / `SCAN`，以及仓库里的内置插件目录本身。
"""

from __future__ import annotations

import json
from pathlib import Path

from .harness import ROOT, Case, check

#: 仓库里的内置插件目录
BUILTIN_PLUGINS = ROOT / "plugins"


def _folders() -> list[Path]:
    return [item for item in sorted(BUILTIN_PLUGINS.iterdir()) if (item / "plugin.json").is_file()]


@check("plugin_layout_is_v2", "services")
def plugin_layout_is_v2(case: Case) -> None:
    """每个插件根目录只留 `plugin.py` / `plugin.json` / `PLUGIN.md` / `.data/` / `.plugin/`。"""
    from app.core.plugins.plugin_core import layout_problems

    folders = _folders()
    assert folders, "没找到任何内置插件"
    problems: list[str] = []
    for folder in folders:
        for problem in layout_problems(folder):
            problems.append(f"{folder.name}：{problem}")
    assert not problems, "插件目录不符合协议 v2：" + "；".join(problems[:8])


@check("plugin_manifest_is_v2", "services")
def plugin_manifest_is_v2(case: Case) -> None:
    """清单只用协议 v2 字段：没有 `data` / `manager_version`，`entry` 固定 `plugin.py`。"""
    from app.core.plugins.plugin_core import ENTRY_NAME, PROTOCOL_FIELDS

    problems: list[str] = []
    for folder in _folders():
        manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
        unknown = sorted(set(manifest) - set(PROTOCOL_FIELDS))
        if unknown:
            problems.append(f"{folder.name} 出现协议外字段：{unknown}")
        if manifest.get("entry") != ENTRY_NAME:
            problems.append(f"{folder.name} 的 entry 不是 {ENTRY_NAME}：{manifest.get('entry')!r}")
        for gone in ("data", "manager_version"):
            if gone in manifest:
                problems.append(f"{folder.name} 仍写着已取消字段 {gone}")
    assert not problems, "插件清单不符合协议 v2：" + "；".join(problems[:8])
