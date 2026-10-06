"""插件协议检查：清单白名单、数据引用、导入边界、插件类契约、多库与载入诊断。

方案见 .logs/_rewrite/plugin_refactor_plan.md 第 12 节；只走公开契约（清单文件、
app.core.plugins.plugin_core 的解析函数、app.services.plugin_service 的服务层 API）。
需要「坏插件 / 多库插件」这类样本时，一律写进用例自己的隔离插件目录。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .harness import ROOT, Case, check, install_builtin_plugins

from app.sdk.manifest import record_of

#: 仓库里的内置插件目录（自检环境的插件目录是另一个临时目录）。
BUILTIN_PLUGINS = ROOT / "plugins"

#: 插件源码禁止直接 import 的程序内部模块（方案第 1 节的 import 边界）。
FORBIDDEN_IMPORTS = (
    "app.core",
    "app.services",
    "app.repositories",
    "app.db",
    "app.data",
    "app.ui.pages",
    "app.ui.framework",
)

#: 事件广播用例驱动的示例插件 id（仓库自带）。
SAMPLE_ID = "example.ui_extension"


def _manifest_dirs() -> list[Path]:
    """仓库里所有含 plugin.json 的内置插件目录。"""
    return [item for item in sorted(BUILTIN_PLUGINS.iterdir()) if (item / "plugin.json").is_file()]


def _plugin_sources(folder: Path) -> list[Path]:
    """插件目录下的所有 .py 源码（跳过 __pycache__）。"""
    return [item for item in sorted(folder.rglob("*.py")) if "__pycache__" not in item.parts]


def _imports(source: Path) -> list[tuple[int, str, bool]]:
    """用 AST 取源码里的绝对 import 目标（文档字符串与注释里的示例不算）。

    第三个值是「是否在模块顶层」：顶层导入＝硬依赖，必须写进 `depends`；
    函数里的延迟导入（`pipeline.py` 那种「没启用模型库也能导入本模块」的写法）不强制声明。
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name, node.col_offset == 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module, node.col_offset == 0))
    return found


def _manifest_error(data: dict) -> str:
    """返回清单被拒绝的原因；清单合法时返回空串。"""
    from app.core.plugins.plugin_core import PluginError, parse_manifest

    try:
        parse_manifest(data)
    except PluginError as exc:
        return str(exc)
    return ""


def _write_plugin(root: Path, folder: str, manifest: dict, sources: dict[str, str]) -> Path:
    """在隔离插件目录里写一个夹具插件，返回它的目录。"""
    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name, text in sources.items():
        extra = target / name
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text(text, encoding="utf-8")
    return target


def _library_plugin(plugin_id: str, name: str, module: str, body: str) -> tuple[dict, dict[str, str]]:
    """库插件的（清单, 源码）：一个入口类 + 一个库模块。"""
    manifest = {
        "id": plugin_id,
        "name": name,
        "version": "1.0",
        "api_version": ">=1.0 <2.0",
        "description": f"{name}（自检夹具）",
        "author": "selfcheck",
        "entry": "plugin.py",
        "libraries": [{"name": plugin_id.rsplit(".", 1)[-1], "module": module, "description": name}],
    }
    entry = (
        "from app.sdk import Plugin\n\n\n"
        f"class {name}LibraryPlugin(Plugin):\n"
        f'    """{name}的库插件入口类。"""\n'
    )
    return manifest, {"plugin.py": entry, module: body}


@check("plugin_manifest_whitelist", "services")
def plugin_manifest_whitelist(case: Case) -> None:
    """内置插件清单：字段全在白名单内，类型字段 / 旧数据字段 / 未知字段都被拒绝。"""
    from app.core.plugins.plugin_core import PLUGIN_ID_PATTERN, PROTOCOL_FIELDS, load_manifest

    folders = _manifest_dirs()
    assert len(folders) >= 9, f"内置插件清单数量不对：{len(folders)}"
    ids: list[str] = []
    for folder in folders:
        info = load_manifest(folder, builtin=True)
        assert info.name, f"插件 {folder.name} 缺少名称"
        assert info.version, f"插件 {folder.name} 缺少版本"
        assert info.api_version, f"{folder.name} 清单缺少 api_version（适配的 SDK 版本）"
        assert PLUGIN_ID_PATTERN.match(info.id), f"插件 id 不合法：{info.id!r}"
        unknown = sorted(set(info.manifest) - set(PROTOCOL_FIELDS))
        assert not unknown, f"{info.id} 清单出现协议外的字段：{unknown}"
        ids.append(info.id)
    assert len(ids) == len(set(ids)), f"插件 id 重复：{sorted(ids)}"
    assert "builtin.lib.viewer" in ids and "builtin.lib.ui" in ids, f"缺少库插件：{sorted(ids)}"

    base = {"id": "demo.probe", "name": "探针", "version": "1.0", "api_version": ">=1.0 <2.0", "entry": "plugin.py"}
    removed_type = _manifest_error({**base, "kind": "viewer"})
    assert removed_type and "类型" in removed_type, f"声明 kind 的清单应被拒绝：{removed_type!r}"
    removed_data = _manifest_error({**base, "capabilities": ["x"]})
    assert removed_data and "扩展接口" in removed_data, f"声明 capabilities 的清单应被拒绝：{removed_data!r}"
    unknown_field = _manifest_error({**base, "nonsense": 1})
    assert unknown_field and "未知字段" in unknown_field, f"未知字段应被拒绝：{unknown_field!r}"
    bad_id = _manifest_error({**base, "id": "Demo.Probe"})
    assert bad_id and "id" in bad_id, f"非法 id 应被拒绝：{bad_id!r}"
    missing_api = _manifest_error({key: value for key, value in base.items() if key != "api_version"})
    assert missing_api and "api_version" in missing_api, f"缺少 api_version 应被拒绝：{missing_api!r}"
    bad_api = _manifest_error({**base, "api_version": ">=9.0"})
    assert bad_api and "SDK 版本" in bad_api, f"超出当前 SDK 的 api_version 应被拒绝：{bad_api!r}"


@check("plugin_data_refs", "services")
def plugin_data_refs(case: Case) -> None:
    """清单的数据引用与库模块：键非空、文件存在且非空，查看器数据可解析。"""
    from app.core.plugins.plugin_core import load_manifest

    infos = [load_manifest(folder, builtin=True) for folder in _manifest_dirs()]
    for info in infos:
        for key, relative in info.data.items():
            assert key and relative, f"{info.id} 的 data 声明不完整：{key!r} -> {relative!r}"
            assert not Path(relative).is_absolute(), f"{info.id} 的 data 路径必须是相对路径：{relative}"
            assert (info.path / relative).is_file(), f"{info.id} 的 data 文件不存在：{relative}"
        for library in info.libraries:
            assert library.name and library.module, f"{info.id} 的 library 声明不完整：{library}"
            module_path = info.path / library.module
            assert module_path.is_file(), f"{info.id} 的库模块不存在：{library.module}"
            assert module_path.stat().st_size > 0, f"{info.id} 的库模块是空文件：{library.module}"

    viewer_infos = [info for info in infos if "viewer" in info.data]
    assert len(viewer_infos) >= 7, f"声明 viewer 数据的插件数量不对：{len(viewer_infos)}"
    for info in viewer_infos:
        payload = json.loads((info.path / info.data["viewer"]).read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{info.id} 的查看器数据必须是对象"
        record = record_of(payload, info.id)
        assert record, f"{info.id} 的查看器数据缺少 key = {info.id} 的记录"
        assert record.get("extensions"), f"{info.id} 的查看器数据缺少 extensions"
        assert record.get("kind"), f"{info.id} 的查看器数据缺少 kind"
        assert str(record.get("name") or "").strip(), f"{info.id} 的查看器数据缺少 name"


@check("plugin_imports", "services")
def plugin_imports(case: Case) -> None:
    """插件源码的 import 边界：只用 app.sdk / 已声明的库插件 / 三方包。"""
    from app.core.plugins.plugin_core import DM_PACKAGE, load_manifest
    from app.services.plugin_service import plugin_service

    infos = [load_manifest(folder, builtin=True) for folder in _manifest_dirs()]
    known = {info.id for info in infos} | {info.id for info in plugin_service.discover()}
    problems: list[str] = []
    for info in infos:
        allowed = set(info.depends_ids)
        for source in _plugin_sources(info.path):
            for lineno, name, top_level in _imports(source):
                if any(name == item or name.startswith(item + ".") for item in FORBIDDEN_IMPORTS):
                    problems.append(f"{info.id}/{source.name}:{lineno} 不该 import 程序内部模块 {name}")
                if (name == "app" or name.startswith("app.")) and not (name == "app.sdk" or name.startswith("app.sdk.")):
                    problems.append(f"{info.id}/{source.name}:{lineno} 插件只能用 SDK：{name}")
                if name == DM_PACKAGE or name.startswith(DM_PACKAGE + "."):
                    rest = name[len(DM_PACKAGE) + 1 :]
                    matches = [pid for pid in known if rest == pid or rest.startswith(pid + ".")]
                    target = max(matches, key=len) if matches else ""
                    if not target:
                        problems.append(f"{info.id}/{source.name}:{lineno} 引用了未知插件库：{name}")
                    elif top_level and target != info.id and target not in allowed:
                        problems.append(f"{info.id}/{source.name}:{lineno} 顶层引用了未声明的依赖：{target}")
    assert not problems, "插件 import 边界检查未通过：" + "；".join(problems)


@check("plugin_stubs_current", "services")
def plugin_stubs_current(case: Case) -> None:
    """IDE 桩一致：dm_plugin 是载入期合成包，stubs/ 里的 .pyi 必须跟着插件清单走。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("plugin_stubs", ROOT / "scripts" / "plugin_stubs.py")
    assert spec is not None and spec.loader is not None, "找不到 scripts/plugin_stubs.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problems = module.stale()
    assert not problems, "插件桩需要刷新（python scripts/plugin_stubs.py）：" + "；".join(problems)


@check("plugin_class_contract", "services")
def plugin_class_contract(case: Case) -> None:
    """每个插件类都继承 app.sdk.Plugin，元信息可注入，describe() 返回 (标题, 内容) 列表。"""
    from app.core.plugins.plugin_core import import_entry, load_manifest, plugin_class, register_plugin_namespace
    from app.sdk import Plugin

    infos = [load_manifest(folder, builtin=True) for folder in _manifest_dirs()]
    for info in infos:
        register_plugin_namespace(info)
    seen = 0
    for info in infos:
        if not info.entry:
            continue
        cls = plugin_class(info, import_entry(info))
        assert issubclass(cls, Plugin), f"{info.id} 的插件类没有继承 app.sdk.Plugin：{cls!r}"
        instance = cls()
        instance.attach(id=info.id, name=info.name, version=info.version, path=info.path, manifest=info.manifest)
        assert instance.id == info.id, f"{info.id} 的元信息注入失败：{instance.id!r}"
        assert instance.name == info.name, f"{info.id} 的名称注入失败：{instance.name!r}"
        rows = instance.describe()
        assert isinstance(rows, list), f"{info.id} 的 describe() 应返回列表：{type(rows)!r}"
        for row in rows:
            assert (
                isinstance(row, tuple) and len(row) == 2 and all(isinstance(part, str) for part in row)
            ), f"{info.id} 的 describe() 元素应为 (标题, 内容)：{row!r}"
        seen += 1
    assert seen >= 9, f"检查到的插件类太少：{seen}"


@check("plugin_multi_library", "services")
def plugin_multi_library(case: Case) -> None:
    """一个插件同时依赖并继承两个库插件仍能载入（库=插件，没有类型限制）。"""
    from app.core.runtime import paths
    from app.sdk import ExtensionPoint
    from app.services.plugin_service import plugin_service

    root = Path(paths.PLUGIN_DIR)
    alpha_manifest, alpha_sources = _library_plugin(
        "demo.lib.alpha",
        "甲库",
        "alpha_lib.py",
        'class AlphaBase:\n    """甲库提供的基类。"""\n\n    library_name = "alpha"\n',
    )
    beta_manifest, beta_sources = _library_plugin(
        "demo.lib.beta",
        "乙库",
        "beta_lib.py",
        'def beta_label() -> str:\n    """乙库提供的函数。"""\n    return "beta"\n',
    )
    _write_plugin(root, "demo.lib.alpha", alpha_manifest, alpha_sources)
    _write_plugin(root, "demo.lib.beta", beta_manifest, beta_sources)
    _write_plugin(
        root,
        "demo.app",
        {
            "id": "demo.app",
            "name": "双库插件",
            "version": "1.0",
            "api_version": ">=1.0 <2.0",
            "entry": "plugin.py",
            "depends": [{"id": "demo.lib.alpha"}, {"id": "demo.lib.beta"}],
        },
        {
            "plugin.py": (
                "from app.sdk import ExtensionPoint, Plugin, PluginContext, library, requires\n"
                "from dm_plugin.demo.lib.alpha.alpha_lib import AlphaBase\n\n\n"
                "class DemoAppPlugin(AlphaBase, Plugin):\n"
                '    """继承甲库的基类，同时调用乙库的函数。"""\n\n'
                "    def setup(self, ctx: PluginContext) -> None:\n"
                '        requires("demo.lib.beta")\n'
                '        beta = library("demo.lib.beta", "beta_lib")\n'
                "        ctx.contribute(\n"
                "            ExtensionPoint.HOME_KPI,\n"
                '            {"label": beta.beta_label(), "library": self.library_name},\n'
                '            key="demo",\n'
                "        )\n"
                '        ctx.provide("demo.tool", self)\n'
            )
        },
    )

    plugin_service.load()
    report = {row[0]: row for row in plugin_service.load_report()}
    for plugin_id in ("demo.lib.alpha", "demo.lib.beta", "demo.app"):
        row = report.get(plugin_id)
        assert row is not None and row[2], f"{plugin_id} 没载入成功：{row}"
    items = plugin_service.point_items(ExtensionPoint.HOME_KPI)
    assert len(items) == 1 and items[0].plugin_id == "demo.app", f"双库插件的贡献没登记：{items}"
    assert items[0].value.get("label") == "beta", f"library() 没取到乙库模块：{items[0].value!r}"
    assert items[0].value.get("library") == "alpha", f"没继承甲库的基类：{items[0].value!r}"
    assert plugin_service.has("demo.app", "demo.tool"), "插件通过 ctx.provide 暴露的接口没登记"
    assert not plugin_service.errors(), f"夹具插件不该有错误：{plugin_service.errors()}"


@check("plugin_load_report", "services")
def plugin_load_report(case: Case) -> None:
    """坏插件分别落在 manifest / import / setup 阶段，且不会影响别的插件。"""
    from app.core.runtime import paths
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service

    root = Path(paths.PLUGIN_DIR)
    _write_plugin(
        root,
        "demo.good",
        {"id": "demo.good", "name": "好插件", "version": "1.0", "api_version": ">=1.0 <2.0", "entry": "plugin.py"},
        {
            "plugin.py": (
                "from app.sdk import Plugin, PluginContext\n\n\n"
                "class GoodPlugin(Plugin):\n"
                '    """正常插件。"""\n\n'
                "    def setup(self, ctx: PluginContext) -> None:\n"
                '        ctx.provide("demo.good", self)\n'
            )
        },
    )
    _write_plugin(
        root,
        "demo.bad.manifest",
        {"id": "demo.bad.manifest", "name": "坏清单", "version": "1.0", "api_version": ">=1.0 <2.0", "kind": "viewer", "entry": "plugin.py"},
        {"plugin.py": "raise RuntimeError('不该被导入')\n"},
    )
    _write_plugin(
        root,
        "demo.bad.import",
        {"id": "demo.bad.import", "name": "坏入口", "version": "1.0", "api_version": ">=1.0 <2.0", "entry": "plugin.py"},
        {"plugin.py": "import selfcheck_missing_module_9f8a\n"},
    )
    _write_plugin(
        root,
        "demo.bad.setup",
        {"id": "demo.bad.setup", "name": "坏初始化", "version": "1.0", "api_version": ">=1.0 <2.0", "entry": "plugin.py"},
        {
            "plugin.py": (
                "from app.sdk import Plugin, PluginContext\n\n\n"
                "class BadSetupPlugin(Plugin):\n"
                '    """初始化就抛异常的插件。"""\n\n'
                "    def setup(self, ctx: PluginContext) -> None:\n"
                '        raise RuntimeError("自检故意的初始化失败")\n'
            )
        },
    )

    plugin_service.load()
    manifest_info = plugin_service.get("demo.bad.manifest")
    assert manifest_info is not None, "坏清单插件没被扫描到"
    assert manifest_info.error_phase == "manifest", f"清单错误应落在 manifest 阶段：{manifest_info.error_text}"
    assert manifest_info.error, "坏清单插件应带 error"
    assert "类型" in manifest_info.error, f"坏清单的错误信息不对：{manifest_info.error}"

    report = {row[0]: row for row in plugin_service.load_report()}
    imported = report.get("demo.bad.import")
    assert imported is not None and imported[1] == "import" and not imported[2], f"导入失败阶段不对：{imported}"
    assert imported[3], "导入失败应带原因"
    setup_failed = report.get("demo.bad.setup")
    assert setup_failed is not None and setup_failed[1] == "setup" and not setup_failed[2], f"初始化失败阶段不对：{setup_failed}"
    assert setup_failed[3], "初始化失败应带原因（异常详情走日志）"
    healthy = report.get("demo.good")
    assert healthy is not None and healthy[2] and healthy[1] == "setup", f"好插件应载入成功：{healthy}"
    assert "demo.good" in extension_registry.names(), "好插件被坏插件带崩了"
    assert "demo.bad.import" not in extension_registry.names(), "失败的插件不应留下接口"


@check("provides_note_recorded", "services")
def provides_note_recorded(case: Case) -> None:
    """清单声明了扩展接口却没注册：只记一条备注，不算载入失败；补上注册后自动清掉。"""
    from app.core.runtime import paths
    from app.services.plugin_service import PROVIDES_NOTE_PREFIX, plugin_service

    root = Path(paths.PLUGIN_DIR)
    manifest = {
        "id": "demo.declares.nothing",
        "name": "声明了没注册",
        "version": "1.0",
        "api_version": ">=1.0 <2.0",
        "entry": "plugin.py",
        "provides": ["demo.missing"],
    }
    silent = (
        "from app.sdk import Plugin\n\n\n"
        "class DeclaresNothingPlugin(Plugin):\n"
        '    """清单里声明了一个扩展接口，但 setup 里没有注册它。"""\n'
    )
    speaking = (
        "from app.sdk import Plugin, PluginContext\n\n\n"
        "class DeclaresNothingPlugin(Plugin):\n"
        '    """补上注册后的同一个插件。"""\n\n'
        "    def setup(self, ctx: PluginContext) -> None:\n"
        '        ctx.provide("demo.missing", self)\n'
    )
    _write_plugin(root, "demo.declares.nothing", manifest, {"plugin.py": silent})
    _write_plugin(
        root,
        "demo.declares.ok",
        {**manifest, "id": "demo.declares.ok", "name": "声明了就注册", "provides": ["demo.present"]},
        {
            "plugin.py": (
                "from app.sdk import Plugin, PluginContext\n\n\n"
                "class DeclaresOkPlugin(Plugin):\n"
                '    """声明并注册了扩展接口的插件。"""\n\n'
                "    def setup(self, ctx: PluginContext) -> None:\n"
                '        ctx.provide("demo.present", self)\n'
            )
        },
    )

    plugin_service.load()
    info = plugin_service.get("demo.declares.nothing")
    assert info is not None, "夹具插件没有被发现"
    assert not info.error, f"声明与注册不一致不该算载入失败：{info.error}"
    assert info.note.startswith(PROVIDES_NOTE_PREFIX), f"应记一条备注，实际 {info.note!r}"
    assert "demo.missing" in info.note, f"备注要写清是哪个接口：{info.note!r}"
    ok_info = plugin_service.get("demo.declares.ok")
    assert ok_info is not None and not ok_info.note, f"注册了的插件不该有备注：{getattr(ok_info, 'note', '')!r}"

    _write_plugin(root, "demo.declares.nothing", manifest, {"plugin.py": speaking})
    plugin_service.load()
    fixed = plugin_service.get("demo.declares.nothing")
    assert fixed is not None and fixed.note == "", f"补上注册后备注应清掉，实际 {fixed.note!r}"
    assert not fixed.error, f"补上注册后不该有错误：{fixed.error}"


#: 旧版写死在 app.core.viewer_data 里的扩展名表：重构后必须消失（否则与插件 data/ 重复声明）。
LEGACY_EXTENSION_TABLES = (
    "TEXT_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "ARCHIVE_EXTENSIONS",
    "SPREADSHEET_EXTENSIONS",
)



@check("plugin_viewer_extensions", "services")
def plugin_viewer_extensions(case: Case) -> None:
    """扩展名只由插件 .data/viewer.json 声明：注册表与清单一致，程序里不再写死扩展名表。"""
    from app.sdk import data as viewer_data
    from app.core.plugins.plugin_core import load_manifest
    from app.services.viewer_service import open_api

    for legacy in LEGACY_EXTENSION_TABLES:
        assert not hasattr(viewer_data, legacy), f"扩展名表不应再写死在数据解析模块里：{legacy}"

    install_builtin_plugins()
    registry = open_api()
    assert registry is not None, "载入内置插件后应提供 viewer.open 扩展接口"
    viewer_infos = [
        info for info in (load_manifest(folder, builtin=True) for folder in _manifest_dirs()) if "viewer" in info.data
    ]
    assert len(viewer_infos) >= 7, f"声明 viewer 数据的插件数量不对：{len(viewer_infos)}"
    declared: dict[str, list[str]] = {}
    for info in viewer_infos:
        payload = json.loads((info.path / info.data["viewer"]).read_text(encoding="utf-8"))
        record = record_of(payload, info.id)
        assert record, f"{info.id} 的查看器数据缺少 key = {info.id} 的记录"
        expected = {str(item).lower().lstrip(".") for item in record["extensions"]}
        registered = registry.viewer_by_id(info.id)
        assert registered is not None, f"{info.id} 没有注册查看器"
        assert set(registered.extensions) == expected, (
            f"{info.id} 注册的扩展名与 data/ 不一致：{sorted(set(registered.extensions) ^ expected)}"
        )
        assert registered.plugin_id == info.id, f"{info.id} 的查看器归属不对：{registered.plugin_id!r}"
        for extension in expected:
            declared.setdefault(extension, []).append(info.id)
    assert sorted(registry.plugin_ids()) == sorted(info.id for info in viewer_infos), (
        "查看器注册表登记的插件与清单不一致"
    )
    for extension, owners in sorted(declared.items()):
        found = registry.viewer_for(f"sample.{extension}")
        assert found is not None, f"扩展名 {extension} 解析不到查看器"
        if len(owners) == 1:
            assert found.plugin_id == owners[0], f"扩展名 {extension} 应解析给 {owners[0]}，实际 {found.plugin_id}"

@check("plugin_event_broadcast", "services")
def plugin_event_broadcast(case: Case) -> None:
    """事件广播：导入、删除、切换用户、迁移库目录与插件开关都能通知订阅者。"""
    from app.sdk import Events
    from app.services.import_service import ImportService
    from app.services.item_service import ItemService
    from app.services.library_service import LibraryService
    from app.services.plugin_service import plugin_service
    from app.services.user_service import UserService

    install_builtin_plugins()
    events = (
        Events.ITEM_IMPORTED,
        Events.ITEM_DELETED,
        Events.USER_CHANGED,
        Events.LIBRARY_CHANGED,
        Events.PLUGIN_ENABLED,
        Events.PLUGIN_DISABLED,
    )
    seen: dict[str, list[dict]] = {event: [] for event in events}

    def probe(event: str):
        def handler(**payload) -> None:
            seen[event].append(dict(payload))

        return handler

    for event in events:
        plugin_service.on("selfcheck.probe", event, probe(event))

    item = ImportService(case.session).import_text("事件广播笔记", "事件广播内容")
    assert item is not None, "导入文本失败，无法验证导入事件"
    ItemService(case.session).delete([item])
    user = UserService(case.session).create("事件广播用户")
    assert user is not None, "创建用户失败，无法验证用户切换事件"
    UserService(case.session).set_current(user)
    target = case.root / "library-moved"
    LibraryService(case.session).set_path(target)
    assert plugin_service.set_enabled(SAMPLE_ID, False), "禁用示例插件应成功"
    assert plugin_service.set_enabled(SAMPLE_ID, True), "重新启用示例插件应成功"

    for event, payloads in seen.items():
        assert payloads, f"没有收到事件：{event}"
    assert seen[Events.ITEM_IMPORTED][-1].get("item_id") == item.id, "导入事件应带条目 id"
    assert seen[Events.ITEM_IMPORTED][-1].get("name") == item.name, "导入事件应带条目名"
    assert seen[Events.ITEM_DELETED][-1].get("item_id") == item.id, "删除事件应带条目 id"
    assert seen[Events.USER_CHANGED][-1].get("name") == user.name, "用户切换事件应带用户名"
    assert seen[Events.USER_CHANGED][-1].get("user_id") == user.id, "用户切换事件应带用户 id"
    assert Path(seen[Events.LIBRARY_CHANGED][-1].get("path", "")) == target.resolve(), "库目录事件应带新路径"
    assert seen[Events.PLUGIN_ENABLED][-1].get("plugin_id") == SAMPLE_ID, "启用事件应带插件 id"
    assert seen[Events.PLUGIN_DISABLED][-1].get("plugin_id") == SAMPLE_ID, "禁用事件应带插件 id"


@check("plugin_load_summary", "services")
def plugin_load_summary(case: Case) -> None:
    """载入汇总要报总数与启用情况，并且真的播报到控制台。"""
    from loguru import logger

    from app.services.plugin_service import plugin_service

    install_builtin_plugins()
    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="INFO", format="{message}")
    try:
        plugin_service.load()
    finally:
        logger.remove(sink)

    infos = plugin_service.discover()
    enabled = sum(1 for info in infos if info.enabled)
    summary = plugin_service.loaded_summary()
    assert f"共 {len(infos)} 个（已启用 {enabled}、未启用 {len(infos) - enabled}）" in summary, (
        f"汇总没有报总数与启用情况：{summary}"
    )
    libraries = [info for info in infos if info.libraries]
    features = [info for info in infos if not info.libraries]
    assert f"库插件 {len(libraries)} 个" in summary, f"汇总没有报库插件数量：{summary}"
    assert f"功能插件 {len(features)} 个" in summary, f"汇总没有报功能插件数量：{summary}"
    assert any("插件扫描完成：发现 " in text for text in records), "插件扫描结果没有写到控制台"
    assert any("插件载入：共 " in text for text in records), "插件载入汇总没有写到控制台"
    # 不可用插件必须逐个报出 id 与原因（控制台一行一条 + 汇总句尾带原因）
    for info in infos:
        if not info.error:
            continue
        assert f"{info.id}（{info.error_text}）" in summary, f"汇总没有写清 {info.id} 的失败原因：{summary}"
        assert any(f"插件不可用：{info.id}" in text for text in records), f"{info.id} 的失败原因没有写到控制台"


@check("sdk_console_output", "services")
def sdk_console_output(case: Case) -> None:
    """控制台输出接口：默认落 loguru，程序提供实现时转发，插件侧用 ctx.console 播报。"""
    from loguru import logger

    from app.core.plugins.extensions import extension_registry
    from app.sdk import console as console_api
    from app.sdk.context import PluginContext

    assert callable(console_api.write) and callable(console_api.stage), "SDK 缺少控制台播报函数"
    assert isinstance(PluginContext.console, property), "插件上下文没有 console 属性"

    records: list[str] = []
    sources: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="DEBUG", format="{message}")
    # 来源走 loguru 的 extra（统一格式里的「来源」列），不在消息正文里拼前缀
    source_sink = logger.add(
        lambda message: sources.append(str(message.record["extra"].get("source") or "")),
        level="DEBUG",
        format="{message}",
    )
    previous = extension_registry.provider(console_api.CONSOLE_EXTENSION)
    owner = extension_registry.provider_plugin(console_api.CONSOLE_EXTENSION)
    try:
        extension_registry.provide(console_api.CONSOLE_EXTENSION, None, "")
        assert not console_api.available(), "没有实现时 available() 应为假"
        console_api.info("自检：默认输出", source="selfcheck")
        console_api.stage("自检阶段", "开始")
        console_api.progress(1, 2, "数数")
        assert any("自检：默认输出" in text for text in records), f"默认输出没落到日志：{records}"
        assert "selfcheck" in sources, f"来源没有带进日志：{sources}"
        assert any("[自检阶段] 开始" in text for text in records), f"阶段前缀不对：{records}"
        assert any("进度 数数 1/2" in text for text in records), f"进度格式不对：{records}"

        calls: list[dict[str, str]] = []

        class _FakeOutput:
            def write(self, *, level: str = "info", message: str = "", source: str = "", stage: str = "") -> None:
                calls.append({"level": level, "message": message, "source": source, "stage": stage})

        extension_registry.provide(console_api.CONSOLE_EXTENSION, _FakeOutput(), "console-selfcheck")
        assert console_api.available(), "提供实现后 available() 应为真"
        console_api.console_for("lib.model").success("转发成功")
        assert calls == [
            {"level": "success", "message": "转发成功", "source": "lib.model", "stage": ""}
        ], f"程序接口没收到转发：{calls}"
    finally:
        logger.remove(source_sink)
        logger.remove(sink)
        extension_registry.drop_plugin("console-selfcheck")
        extension_registry.provide(console_api.CONSOLE_EXTENSION, previous, owner)


@check("plugin_incremental_toggle", "services")
def plugin_incremental_toggle(case: Case) -> None:
    """启停插件只动该插件（与依赖它的插件），不再把整仓插件重载一遍。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service

    install_builtin_plugins()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    api = AppUiApi()
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    plugin_service.load()
    target = "auto_keyword"
    library = "lib.model"

    def loaded() -> dict[str, int]:
        """已载入插件 → 实例地址；地址没变就说明没被重建。"""
        return {pid: id(plugin) for pid, plugin in plugin_service._plugins.items()}

    try:
        before = loaded()
        assert target not in before, f"自检前提：{target} 默认不该启用，实际在 {sorted(before)}"

        plugin_service.set_enabled(target, True)
        plugin_service.apply_changes((target,))
        after_enable = loaded()
        assert target in after_enable, f"启用后应载入 {target}"
        touched = {pid for pid in set(before) | set(after_enable) if before.get(pid) != after_enable.get(pid)}
        assert touched == {target}, f"增量启用只该动 {target}，实际动了 {sorted(touched)}"

        plugin_service.set_enabled(target, False)
        plugin_service.apply_changes((target,))
        after_disable = loaded()
        assert target not in after_disable, f"禁用后应卸载 {target}"
        touched = {pid for pid in set(after_enable) | set(after_disable) if after_enable.get(pid) != after_disable.get(pid)}
        assert touched == {target}, f"增量禁用只该动 {target}，实际动了 {sorted(touched)}"

        # 禁用被依赖的库：依赖它的插件要连带卸载
        plugin_service.set_enabled(target, True)
        plugin_service.apply_changes((target,))
        plugin_service.set_enabled(library, False)
        plugin_service.apply_changes((library,))
        after_chain = loaded()
        assert library not in after_chain, f"禁用后应卸载 {library}"
        assert target not in after_chain, f"依赖 {library} 的 {target} 应被连带卸载"
    finally:
        plugin_service.set_enabled(target, False)
        plugin_service.set_enabled(library, True)
        plugin_service.load()
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else api)


@check("plugin_change_paths", "services")
def plugin_change_paths(case: Case) -> None:
    """变更路径（增量启停 / 卸载 / 重载 / 状态重置）都要重新对齐程序本体接口并真正重导插件代码。"""
    import sys
    from typing import Sequence

    from app.core.manifest import manifest_kit
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import STATE_MANIFEST_ID, plugin_service

    class RecordingUi(AppUiApi):
        """记录每次 `sync_plugins()` 调用，用来验证「载入插件后都会被调用一次」。"""

        def __init__(self) -> None:
            super().__init__()
            self.synced: list[tuple[str, ...]] = []

        def sync_plugins(self, plugin_ids: Sequence[str]) -> tuple[str, ...]:
            self.synced.append(tuple(plugin_ids))
            return super().sync_plugins(plugin_ids)

    install_builtin_plugins()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    ui = RecordingUi()
    plugin_service.bootstrap(APP_UI_EXTENSION, ui)
    plugin_service.load()
    target = "auto_keyword"
    state_file = plugin_service.state_file
    raw_state = state_file.read_text(encoding="utf-8") if state_file.is_file() else None
    try:
        assert ui.synced, "整体载入后应调用一次 sync_plugins()"

        before = len(ui.synced)
        plugin_service.set_enabled(target, True)
        plugin_service.apply_changes((target,))
        assert len(ui.synced) > before, "增量启用后应重新同步程序本体接口"
        assert target in plugin_service._plugins, f"启用后应载入 {target}"

        module_name = f"dm_plugin.{target}.plugin"
        old_module = sys.modules.get(module_name)
        old_plugin = plugin_service._plugins[target]
        assert old_module is not None, f"{module_name} 应当在 sys.modules 里"
        assert plugin_service.reload(target) == 1, f"重载 {target} 应返回 1"
        assert sys.modules.get(module_name) is not None, "重载后插件模块应重新挂上"
        assert sys.modules[module_name] is not old_module, "重载后插件模块应是重新导入的对象"
        assert plugin_service._plugins[target] is not old_plugin, "重载后插件实例应被重建"

        plugin_service.teardown(target)
        assert module_name not in sys.modules, "卸载后插件模块应被丢掉"

        plugin_service.set_enabled(target, True)
        plugin_service.set_enabled(target, False)  # 这一写会把「已启用」那份状态备份下来
        assert manifest_kit.backups(STATE_MANIFEST_ID), "写插件状态时应自动留一份备份"
        plugin_service.reset_state()
        assert plugin_service.get(target).enabled, "重置后应回到备份里的那份状态（已启用）"
    finally:
        if raw_state is None:
            state_file.unlink(missing_ok=True)
        else:
            state_file.write_text(raw_state, encoding="utf-8")
        plugin_service.set_enabled(target, False)
        plugin_service.load()
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else ui)
