"""插件协议检查：清单白名单、数据引用、导入边界、插件类契约、多库与载入诊断。

方案见 logs/_rewrite/plugin_refactor_plan.md 第 12 节；只走公开契约（清单文件、
app.core.plugin_core 的解析函数、app.services.plugin_service 的服务层 API）。
需要「坏插件 / 多库插件」这类样本时，一律写进用例自己的隔离插件目录。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .harness import ROOT, Case, check, install_builtin_plugins

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


def _imports(source: Path) -> list[tuple[int, str]]:
    """用 AST 取源码里的绝对 import 目标（文档字符串与注释里的示例不算）。"""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def _manifest_error(data: dict) -> str:
    """返回清单被拒绝的原因；清单合法时返回空串。"""
    from app.core.plugin_core import PluginError, parse_manifest

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
    from app.core.plugin_core import PLUGIN_ID_PATTERN, PROTOCOL_FIELDS, load_manifest

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
    assert "builtin.lib.viewer" in ids and "builtin.lib.dialog" in ids, f"缺少库插件：{sorted(ids)}"

    base = {"id": "demo.probe", "name": "探针", "version": "1.0", "api_version": ">=1.0 <2.0", "entry": "plugin.py"}
    removed_type = _manifest_error({**base, "kind": "viewer"})
    assert removed_type and "类型" in removed_type, f"声明 kind 的清单应被拒绝：{removed_type!r}"
    removed_data = _manifest_error({**base, "capabilities": ["x"]})
    assert removed_data and "data/" in removed_data, f"声明 capabilities 的清单应被拒绝：{removed_data!r}"
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
    from app.core.plugin_core import load_manifest

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
        assert payload.get("extensions"), f"{info.id} 的查看器数据缺少 extensions"
        assert payload.get("kind"), f"{info.id} 的查看器数据缺少 kind"
        assert str(payload.get("name") or "").strip(), f"{info.id} 的查看器数据缺少 name"


@check("plugin_imports", "services")
def plugin_imports(case: Case) -> None:
    """插件源码的 import 边界：只用 app.sdk / 已声明的库插件 / 三方包。"""
    from app.core.plugin_core import DM_PACKAGE, load_manifest
    from app.services.plugin_service import plugin_service

    infos = [load_manifest(folder, builtin=True) for folder in _manifest_dirs()]
    known = {info.id for info in infos} | {info.id for info in plugin_service.discover()}
    problems: list[str] = []
    for info in infos:
        allowed = set(info.depends_ids)
        for source in _plugin_sources(info.path):
            for lineno, name in _imports(source):
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
                    elif target != info.id and target not in allowed:
                        problems.append(f"{info.id}/{source.name}:{lineno} 引用了未声明的依赖：{target}")
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
    from app.core.plugin_core import import_entry, load_manifest, plugin_class, register_plugin_namespace
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
    from app.core import paths
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
    from app.core import paths
    from app.core.extensions import extension_registry
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
    from app.core import paths
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
    """扩展名只由插件 data/viewer.json 声明：注册表与清单一致，程序里不再写死扩展名表。"""
    from app.sdk import data as viewer_data
    from app.core.plugin_core import load_manifest
    from app.core.viewers import viewer_registry

    for legacy in LEGACY_EXTENSION_TABLES:
        assert not hasattr(viewer_data, legacy), f"扩展名表不应再写死在数据解析模块里：{legacy}"

    install_builtin_plugins()
    viewer_infos = [
        info for info in (load_manifest(folder, builtin=True) for folder in _manifest_dirs()) if "viewer" in info.data
    ]
    assert len(viewer_infos) >= 7, f"声明 viewer 数据的插件数量不对：{len(viewer_infos)}"
    declared: dict[str, list[str]] = {}
    for info in viewer_infos:
        payload = json.loads((info.path / info.data["viewer"]).read_text(encoding="utf-8"))
        expected = {str(item).lower().lstrip(".") for item in payload["extensions"]}
        registered = viewer_registry.by_id(info.id)
        assert registered is not None, f"{info.id} 没有注册查看器"
        assert set(registered.extensions) == expected, (
            f"{info.id} 注册的扩展名与 data/ 不一致：{sorted(set(registered.extensions) ^ expected)}"
        )
        assert registered.plugin_id == info.id, f"{info.id} 的查看器归属不对：{registered.plugin_id!r}"
        for extension in expected:
            declared.setdefault(extension, []).append(info.id)
    assert sorted(viewer_registry.plugin_ids()) == sorted(info.id for info in viewer_infos), (
        "查看器注册表登记的插件与清单不一致"
    )
    for extension, owners in sorted(declared.items()):
        found = viewer_registry.for_suffix(f"sample.{extension}")
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

