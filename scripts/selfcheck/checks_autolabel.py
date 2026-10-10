"""自动标签 / 自动关键词四个插件（任务 2–4）的红线检查。

覆盖方案 `docs/AUTOLABEL_PLAN.md` §5 里的两条，外加一条「恢复出厂」的界面接线：

1. 插件界面只许走集成界面工具库（`qfluentwidgets` 只放行 `FluentIcon`，与 `model_ui_via_tool_library` 同一条判据）；
2. 四个插件都是外部插件（`builtin: false`）且默认不启用（`enabled: false`），互斥关系成对声明；
3. 带规则表的三页「恢复出厂」都要走共享库的 `restore_plan()`——被删掉的出厂规则已从表里消失、选不中，
   只按选中行恢复的话永远找不回来；
4. 自动标签页的规则弹窗要拿到候选标签，否则「从列表挑一个…」那一行根本不出现；
5. 规则弹窗的提示词框要一直能写字（以前跟着「类型」联动置灰，新规则默认「按字段匹配」，
   用户看到的是一个写不进去的提示词框）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .harness import ROOT, Case, check, ensure_app, install_builtin_plugins

#: 四个功能插件，以及它们共用的库插件（界面目录过同一条红线）。
PLUGIN_IDS = ("auto_tag.rule", "auto_tag", "auto_keyword", "auto_keyword.rule")
SHARED_LIB = "lib.autolabel"

#: 界面代码所在目录（`plugins/<id>/.plugin/ui/`，与插件核心的 `CODE_DIR_NAME` 一致）。
CODE_DIR = ".plugin"

#: 允许从 `qfluentwidgets` 直接拿的东西（图标是纯数据，不算界面控件）。
ALLOWED = {"FluentIcon"}

#: 必须成对出现的互斥关系。
MUTEX_PAIRS = (
    ("auto_tag.rule", "auto_tag"),
    ("auto_keyword.rule", "auto_keyword"),
)

#: 带规则表的三个页面：都要能「恢复出厂」。
RULE_PAGES = ("auto_tag.rule", "auto_tag", "auto_keyword.rule")

#: 规则弹窗挂的是「标签」、候选该来自库里标签的两个页面（关键词页挂的是关键词，候选走关键词库）。
TAG_RULE_PAGES = ("auto_tag.rule", "auto_tag")


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


def _restore_rule_problems(path: Path) -> list[str]:
    """看页面里的 `_restore_rule` 调了哪些恢复动作（不靠注释，直接看调用）。"""
    where = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_restore_rule"):
            continue
        called: set[str] = set()
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Attribute):
                called.add(inner.func.attr)
            elif isinstance(inner.func, ast.Name):
                called.add(inner.func.id)
        missing = [name for name in ("restore_plan", "restore_key", "restore_hidden") if name not in called]
        if missing:
            return [
                f"{where}: `_restore_rule` 少了 {'、'.join(missing)}："
                "被删掉的出厂规则不在表里、选不中，只恢复选中行的话永远找不回来"
            ]
        return []
    return [f"{where}: 找不到 `_restore_rule`"]


def _tag_rule_candidates_problems(path: Path) -> list[str]:
    """看自动标签页给规则弹窗传没传候选标签（不传就没有「从列表挑一个…」那一行）。"""
    where = path.relative_to(ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not any(isinstance(node, ast.FunctionDef) and node.name == "_tag_library" for node in ast.walk(tree)):
        return [f"{where}: 找不到 `_tag_library`：候选标签应来自库里已有的标签与现有规则用到的标签"]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "RuleDialog"
    ]
    if not calls:
        return [f"{where}: 找不到构造 RuleDialog 的地方"]
    # 新建与编辑各一处，只给其中一处补 library= 是常见的漏改
    missing = [node for node in calls if not any(keyword.arg == "library" for keyword in node.keywords)]
    if missing:
        lines = "、".join(str(node.lineno) for node in missing[:4])
        return [
            f"{where}: 第 {lines} 行构造 RuleDialog 时没传 `library=`："
            "弹窗里「从列表挑一个…」那一行不会出现，只能手打标签名"
        ]
    return []


def _drop_widget(widget) -> None:
    """立即销毁一个独立控件（整棵控件树排队销毁在本机 PyQt6 上会崩）。"""
    if widget is None:
        return
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication

    widget.close()
    widget.setParent(None)
    sip.delete(widget)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@check("autolabel_ui_via_tool_library", "pages")
def autolabel_ui_via_tool_library(case: Case) -> None:
    """四个插件与共享库的界面都只用 UI 工具库（`qfluentwidgets` 只放行 `FluentIcon`）。"""
    problems: list[str] = []
    for plugin_id in (*PLUGIN_IDS, SHARED_LIB):
        plugin_dir = ROOT / "plugins" / plugin_id
        assert plugin_dir.is_dir(), f"缺少插件目录：plugins/{plugin_id}"
        for path in sorted((plugin_dir / CODE_DIR / "ui").glob("*.py")):
            problems.extend(_ui_problems(path))
    assert not problems, "自动标签 / 关键词插件界面没走 UI 工具库：" + "；".join(problems[:8])


@check("autolabel_restore_factory_rules", "pages")
def autolabel_restore_factory_rules(case: Case) -> None:
    """三个规则页的「恢复出厂」必须同时覆盖「选中的规则」与「被删掉的出厂规则」。

    出厂规则删掉后就不在规则表里了，选不中；页面若只按当前选中行调 `restore_key`，
    删完再点「恢复出厂」永远恢复不了。这里钉住页面确实走了共享库的决策函数。
    """
    problems: list[str] = []
    for plugin_id in RULE_PAGES:
        path = ROOT / "plugins" / plugin_id / CODE_DIR / "ui" / "page.py"
        assert path.is_file(), f"缺少页面文件：plugins/{plugin_id}/{CODE_DIR}/ui/page.py"
        problems.extend(_restore_rule_problems(path))
    assert not problems, "「恢复出厂」接线不对：" + "；".join(problems[:8])


@check("autolabel_tag_rule_candidates", "pages")
def autolabel_tag_rule_candidates(case: Case) -> None:
    """自动标签页的规则弹窗要能直接从库里已有的标签里挑。

    候选是从页面传进弹窗的：不传 `library=`，共享控件就不建「从列表挑一个…」那一行，
    用户只能照着记忆手打标签名（而且打错一个错字就多出一个新标签）。
    """
    problems: list[str] = []
    for plugin_id in TAG_RULE_PAGES:
        path = ROOT / "plugins" / plugin_id / CODE_DIR / "ui" / "page.py"
        if not path.is_file():
            problems.append(f"缺少页面文件：plugins/{plugin_id}/{CODE_DIR}/ui/page.py")
            continue
        problems.extend(_tag_rule_candidates_problems(path))
    assert not problems, "「便捷挑标签」接线不对：" + "；".join(problems[:8])


@check("autolabel_rule_prompt_editable", "pages")
def autolabel_rule_prompt_editable(case: Case) -> None:
    """规则弹窗的提示词框必须一直能写字，写完自动变成「交模型判断」。

    以前它跟着「类型」联动置灰，而在能选两种类型的页面上新规则默认「按字段匹配」——
    用户打开弹窗看到的就是一个点不动、写不进去的提示词框（用户报的「提示词无法填写」）。
    """
    install_builtin_plugins()
    ensure_app()
    from PyQt6.QtWidgets import QWidget

    from dm_plugin.lib.autolabel.rules import KIND_PROMPT
    from dm_plugin.lib.autolabel.ui.controls import RuleDialog

    host = QWidget()
    dialog = RuleDialog(host, title="新建规则")
    try:
        prompt = dialog._prompt
        assert prompt is not None, "允许「交模型判断」时弹窗里必须有提示词框"
        assert prompt.isEnabled(), "提示词框不能跟着类型置灰：用户会以为提示词写不进去"
        assert not prompt.isReadOnly(), "提示词框不该是只读的"
        dialog._key.setText("shot")
        dialog._tags.setText("截图")
        prompt.setPlainText("这张图是不是截图？")
        assert dialog._kind.currentData() == KIND_PROMPT, "往提示词里写字应自动把类型切成「交模型判断」"
        assert dialog.yesButton.isEnabled(), f"提示词填完了仍不能保存：{dialog._hint.text()}"
        prompt.clear()
        assert not dialog.yesButton.isEnabled(), "「交模型判断」规则没写提示词不该允许保存"
        assert "没有填提示词" in dialog._hint.text(), f"提示词为空时该说清楚：{dialog._hint.text()}"
    finally:
        _drop_widget(dialog)
        _drop_widget(host)


@check("autolabel_manifests", "data")
def autolabel_manifests(case: Case) -> None:
    """四个插件是外部插件、默认不启用，依赖共享库，冲突关系成对声明。

    这四个插件是 `.gitignore` 排除的本地插件，**干净检出里没有它们**（打包冒烟
    `tests/smoke_checkout.py` 就在那种检出里跑 data 层）：插件目录不在就跳过，不当作失败。
    """
    manifests: dict[str, dict] = {}
    problems: list[str] = []
    for plugin_id in PLUGIN_IDS:
        folder = ROOT / "plugins" / plugin_id
        if not folder.is_dir():
            continue
        path = folder / "plugin.json"
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
        # 只在这两个插件都在这个检出里时才查互斥声明；缺的那半边上面已经跳过了。
        if left not in manifests or right not in manifests:
            continue
        for one, other in ((left, right), (right, left)):
            declared = manifests[one].get("conflicts") or ()
            if other not in declared:
                problems.append(f"{one}：冲突关系应声明 conflicts: [{other}]，实际 {declared!r}")
    assert not problems, "自动标签 / 关键词插件清单：" + "；".join(problems[:8])
