"""模型工具库自检：模板数据、model.open 调度接口、无插件退路、设置落盘、模型页装配与表单保存。"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import QPushButton

from .harness import Case, check, install_builtin_plugins

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "lib.model"
_PAGE_KEY = "model_manager"


def _data(name: str) -> dict:
    return json.loads((_PLUGIN_DIR / ".data" / name).read_text(encoding="utf-8"))


def _items(name: str) -> list[dict]:
    """模板文件都是统一清单格式，记录在 `items` 里。"""
    items = _data(name).get("items")
    assert isinstance(items, list), f"{name} 不是统一清单格式（缺 items）"
    return [dict(item) for item in items]


def _runtime_states(page) -> list[str]:
    """运行环境区每行显示的状态文字（安装/未安装/使用程序环境…）。"""
    from PyQt6.QtWidgets import QLabel

    heads = ("未安装", "已安装", "未登记", "使用程序环境", "运行环境模块未就绪", "状态未知")
    states: list[str] = []
    layout = page._runtime_layout
    for index in range(layout.count()):
        row = layout.itemAt(index).widget()
        if row is None or row.layout() is None:
            continue
        for slot in range(row.layout().count()):
            widget = row.layout().itemAt(slot).widget()
            if isinstance(widget, QLabel) and widget.text().startswith(heads):
                states.append(widget.text())
    return states


def _model_api():
    """模型工具库门面模块；每次现取，避免插件重载后手里还是旧模块对象。"""
    from dm_plugin.lib.model import api

    return api


def _registered_models() -> tuple:
    return tuple(_model_api().list_models())


def _dispose(widget) -> None:
    """确定性销毁插件控件：deleteLater 会留下悬空引用。"""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication

    widget.close()
    widget.setParent(None)
    sip.delete(widget)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@check("model_manifest_and_templates", "data")
def model_manifest_and_templates(case: Case) -> None:
    """清单声明 model.open 与三个只读模板，模板 JSON 的字段够页面直接用。

    `lib.model` 是 `.gitignore` 排除的本地插件，**干净检出里没有它**（打包冒烟
    `tests/smoke_checkout.py` 就在那种检出里跑 data 层）：目录不在就跳过，不当作失败。
    """
    if not (_PLUGIN_DIR / "plugin.json").is_file():
        return
    manifest = json.loads((_PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert manifest.get("id") == "lib.model", f"插件 id 不对：{manifest.get('id')}"
    assert "model.open" in (manifest.get("provides") or []), "清单没有提供 model.open"
    found = {item.stem for item in (_PLUGIN_DIR / ".data").glob("*.json")}
    assert {"model_list", "api_templates", "runtime_profiles"} <= found, f".data/ 缺模板：{sorted(found)}"

    problems: list[str] = []
    models = _items("model_list.json")
    if not models:
        problems.append("model_list.json 没有本地模型模板")
    for item in models:
        source = item.get("source") or {}
        runtime = item.get("runtime") or {}
        if not {"id", "name"} <= set(item) or not source.get("repo") or not source.get("file"):
            problems.append(f"本地模板字段不全：{item.get('id') or item}")
        if not runtime.get("backend"):
            problems.append(f"本地模板缺推理后端：{item.get('id') or item}")

    templates = _items("api_templates.json")
    if not templates:
        problems.append("api_templates.json 没有外部模型模板")
    for item in templates:
        if not {"id", "name", "base_url", "model"} <= set(item):
            problems.append(f"外部模板字段不全：{item.get('id') or item}")

    profiles = _items("runtime_profiles.json")
    if not profiles:
        problems.append("runtime_profiles.json 没有运行环境")
    for item in profiles:
        if not item.get("id") or not item.get("packages"):
            problems.append(f"运行环境字段不全：{item.get('id') or item}")

    assert not problems, "模型模板数据：" + "；".join(problems[:12])


@check("model_extension_api", "services")
def model_extension_api(case: Case) -> None:
    """载入内置插件后 model.open 接口可用：登记外部模型、按能力查询、落盘到隔离目录。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import api as models
    from dm_plugin.lib.model.errors import ModelError

    api = models.provider()
    assert api is not None, "载入内置插件后 model.open 接口不可用"
    assert models.available() is True, "available() 应为 True"
    assert _registered_models() == (), f"隔离环境初始不该有模型：{_registered_models()}"

    record = api.add_external(
        "自检云端",
        base_url="http://127.0.0.1:9/v1/",
        model="demo",
        capabilities=("chat", "embedding"),
        description="自检用外部模型",
    )
    problems: list[str] = []
    if record.kind != "external" or record.state != "ready":
        problems.append(f"外部模型应直接就绪：kind={record.kind} state={record.state}")
    if [item.id for item in models.list_models(kind="external")] != [record.id]:
        problems.append(f"按 kind 查不到刚登记的模型：{[item.id for item in models.list_models()]}")
    if set(models.capabilities()) != {"chat", "embedding"}:
        problems.append(f"能力并集不对：{models.capabilities()}")
    if models.model_by_id(record.id) is None:
        problems.append("model_by_id 取不到刚登记的模型")
    if models.loaded() != ():
        problems.append(f"没有加载过模型，loaded 应为空：{models.loaded()}")

    from dm_plugin.lib.model import paths as model_paths

    registry = model_paths.registry_file()
    if not registry.is_file():
        problems.append(f"登记表没有落盘：{registry}")
    if case.root not in registry.parents:
        problems.append(f"登记表没有落到隔离目录（跑测试不该写进真实程序目录）：{registry}")
    api.reload()
    if [item.id for item in models.list_models()] != [record.id]:
        problems.append("reload 之后模型丢失")
    api.remove(record.id)
    if _registered_models() != ():
        problems.append(f"删除后仍有模型：{[item.id for item in models.list_models()]}")

    try:
        models.acquire(model_id="local/不存在")
    except ModelError:
        pass
    else:
        problems.append("取不存在的模型应抛 ModelError")

    assert not problems, "model.open 接口：" + "；".join(problems[:12])


@check("model_templates_and_constants", "services")
def model_templates_and_constants(case: Case) -> None:
    """模板里的后端名、能力名与插件常量表对得上，页面按能力选后端才不会落空。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import constants

    problems: list[str] = []
    for item in _items("model_list.json"):
        backend = str((item.get("runtime") or {}).get("backend") or "")
        if backend not in constants.BACKENDS:
            problems.append(f"{item.get('id')} 的后端不在 BACKENDS：{backend}")
        for capability in item.get("capabilities") or []:
            if capability not in constants.CAPABILITIES:
                problems.append(f"{item.get('id')} 的能力不在目录：{capability}")
    for capability, backend in constants.CAPABILITY_BACKENDS.items():
        if capability not in constants.CAPABILITIES:
            problems.append(f"CAPABILITY_BACKENDS 有未知能力：{capability}")
        if backend not in constants.BACKENDS and backend not in constants.ADAPTERS:
            problems.append(f"{capability} 的默认后端不合法：{backend}")
    from dm_plugin.lib.model.worker import worker_main

    known = set(worker_main._BACKENDS)
    for capability, backend in constants.CAPABILITY_BACKENDS.items():
        if backend in constants.ADAPTERS:
            continue
        if backend not in known:
            problems.append(f"{capability} 指到 worker 没实现的后端：{backend}")
    for key, label in constants.STATE_LABELS.items():
        if key not in constants.STATES or not label:
            problems.append(f"状态标注不合法：{key}")
    assert not problems, "模型常量表：" + "；".join(problems[:12])


@check("model_api_without_plugin", "services")
def model_api_without_plugin(case: Case) -> None:
    """没有模型插件时门面完全退化成空/报错，调用方不该被吊死。"""
    from app.services.plugin_service import plugin_service

    install_builtin_plugins()  # 只为把 dm_plugin.lib.model 挂上并载入
    from dm_plugin.lib.model import api as models
    from dm_plugin.lib.model.errors import ModelError

    plugin_service.load()
    plugin_service.teardown_all()  # 再模拟「一个模型插件都没有」：模块对象留着、注入被清空

    problems: list[str] = []
    if models.provider() is not None:
        problems.append("隔离目录里不该有 model.open 接口")
    if models.available() is not False:
        problems.append("available() 应为 False")
    if models.list_models() != () or models.capabilities() != () or models.loaded() != ():
        problems.append("没有插件时列表类接口应返回空")
    if models.model_by_id("local/任意") is not None:
        problems.append("没有插件时 model_by_id 应返回 None")
    try:
        models.acquire(model_id="local/任意")
    except ModelError as exc:
        if "lib.model" not in str(exc):
            problems.append(f"报错应点明要启用哪个库：{exc}")
    else:
        problems.append("没有插件时 acquire 应抛 ModelError")
    assert not problems, "无模型插件退路：" + "；".join(problems[:12])


@check("model_settings_roundtrip_and_secret", "services")
def model_settings_roundtrip_and_secret(case: Case) -> None:
    """设置落 `.configs/models.json`：镜像、代理、并发、密钥都能存回，密钥打码不出原文。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import settings as settings_module

    settings = settings_module.load_settings()
    problems: list[str] = []
    if settings.base_url != settings_module.DEFAULT_BASE_URL:
        problems.append(f"默认下载网址不对：{settings.base_url}")
    if tuple(settings.mirrors) != tuple(settings_module.DEFAULT_MIRRORS):
        problems.append(f"默认镜像不对：{settings.mirrors}")

    settings.set_mirrors(["https://mirror.example.com", "https://mirror2.example.com"])
    settings.download["concurrent"] = 3
    settings.download["proxy"] = "http://127.0.0.1:7890"
    settings.runtime["idle_unload_sec"] = 120
    settings.runtime["device"] = "cpu"
    settings.set_secret("自检密钥", "sk-abcdefgh1234")
    if not settings.save():
        problems.append("settings.save() 返回失败")

    path = case.root / ".configs" / "models.json"
    if not path.is_file():
        problems.append(f"设置没有落到隔离配置目录：{path}")

    again = settings_module.load_settings()
    if list(again.mirrors) != ["https://mirror.example.com", "https://mirror2.example.com"]:
        problems.append(f"镜像没有存回：{again.mirrors}")
    if again.proxy != "http://127.0.0.1:7890" or again.concurrent != 3:
        problems.append(f"代理/并发没有存回：{again.proxy} {again.concurrent}")
    if again.idle_unload_sec != 120 or again.device != "cpu":
        problems.append(f"运行设置没有存回：{again.idle_unload_sec} {again.device}")
    if again.secret("自检密钥") != "sk-abcdefgh1234":
        problems.append("密钥没有存回")

    masked = settings_module.mask_secret("sk-abcdefgh1234")
    if "abcdefgh" in masked or not masked.endswith("***"):
        problems.append(f"密钥打码不正确：{masked}")
    if settings_module.mask_secret("") != "":
        problems.append("空密钥不该被打码成星号")

    assert not problems, "模型设置：" + "；".join(problems[:12])


@check("model_page_build_and_cards", "pages")
def model_page_build_and_cards(case: Case) -> None:
    """模型页注册进主窗口：空态可见，登记外部模型后出现卡片并带对应动作按钮。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        window.switchTo(page)
        page.refresh()

        if page._empty.isHidden():
            problems.append("没有模型时空态应可见")
        if page._queue_empty.isHidden():
            problems.append("没有下载任务时队列空态应可见")
        if not page._runtime_layout.count():
            problems.append("运行环境区没有列出任何运行环境")

        toolbar_texts = {button.text() for button in page.findChildren(QPushButton)}
        for label in ("扫描目录", "选择权重文件"):
            if label not in toolbar_texts:
                problems.append(f"模型区工具条缺少「{label}」按钮")
        if not callable(getattr(page, "_pick_files", None)):
            problems.append("页面缺少单文件导入入口 _pick_files")

        profiles = page._runtime_profiles()
        if not profiles or not all(item.get("id") for item in profiles):
            problems.append(f"运行环境清单不完整：{profiles}")

        record = _model_api().provider().add_external(
            "自检云端",
            base_url="http://127.0.0.1:9/v1",
            model="demo",
            capabilities=("chat",),
        )
        page.refresh()
        card = page._card_map.get(record.id)
        if card is None:
            problems.append("登记外部模型后页面上没有卡片")
        else:
            for key in ("edit", "delete", "test"):
                if card.action_button(key) is None:
                    problems.append(f"卡片缺少动作按钮：{key}")
            for key in ("download", "open_dir"):
                button = card.action_button(key)
                if button is not None and not button.isHidden():
                    problems.append(f"外部模型不该显示「{key}」按钮")
        if not page._empty.isHidden():
            problems.append("有模型之后空态应隐藏")

        # 本地模型上次加载失败（state=error）时，权重还在，「加载 / 测试 / 更换权重」都得留着：
        # 早先这几颗按钮跟着 ready 走，一出错整排消失，只能刷新页面才回来。
        from dm_plugin.lib.model.constants import STATE_ERROR
        from dm_plugin.lib.model.paths import local_dir

        local = page._api.add_local("自检本地", capabilities=("chat",))
        weights = local_dir(local.id) / "demo.gguf"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"x" * 8)
        local.files = ("demo.gguf",)
        local.state = STATE_ERROR
        page._render_cards()
        local_card = page._card_map.get(local.id)
        if local_card is None:
            problems.append("登记本地模型后页面上没有卡片")
        else:
            for key in ("load", "test", "download", "replace"):
                button = local_card.action_button(key)
                if button is None or button.isHidden():
                    problems.append(f"权重还在、上次加载失败时「{key}」按钮不该消失")
            # 「下载」与「更换权重」是两个独立按钮：前者只补缺的文件，后者才换一整组。
            download = local_card.action_button("download")
            if download is not None and "更换权重" in download.toolTip():
                problems.append(f"「下载」按钮不该再兼「更换权重」：{download.toolTip()}")
            replace = local_card.action_button("replace")
            if replace is not None and "更换权重" not in replace.toolTip():
                problems.append(f"有权重的本地模型应显示「更换权重」：{replace.toolTip()}")

        assert not problems, "模型管理页：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("model_logs_latest_and_cleanup", "pages")
def model_logs_latest_and_cleanup(case: Case) -> None:
    """模型日志：卡片上能看最新一份、最新的排最后、删模型连日志一起删。"""
    import importlib

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    from dm_plugin.lib.model import paths as paths_module
    from dm_plugin.lib.model.record import slugify

    page_module = importlib.import_module("dm_plugin.lib.model.ui.page")

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        window.switchTo(page)
        page.refresh()

        local = page._api.add_local("自检日志", capabilities=("chat",))
        # 老版本是「一次运行一个时间戳文件」，界面和删除都得认这些
        legacy = paths_module.logs_dir() / f"{slugify(local.id)}-20260101-010101.log"
        legacy.write_text("老日志\n", encoding="utf-8")
        page.refresh()

        card = page._card_map.get(local.id)
        if card is None:
            problems.append("登记本地模型后页面上没有卡片")
        else:
            button = card.action_button("log")
            if button is None or button.isHidden():
                problems.append("模型有日志时卡片应给出「日志」按钮")

        shown: dict = {}

        class FakeLogDialog:
            def __init__(self, parent=None, *, name="", path=None, text=""):
                shown.update(name=name, path=path, text=text)

            def exec(self):
                return 1

        class FakeDeleteDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return 1

        original_log = page_module.ModelLogDialog
        original_delete = page_module.DeleteModelDialog
        page_module.ModelLogDialog = FakeLogDialog
        page_module.DeleteModelDialog = FakeDeleteDialog
        stable = paths_module.model_log_file(local.id)
        try:
            page._show_log(local)
            if "老日志" not in str(shown.get("text") or ""):
                problems.append(f"「日志」该显示这条模型现有的日志：{shown.get('text')!r}")
            if shown.get("path") != legacy:
                problems.append(f"「日志」该读最新的那份：{shown.get('path')}")

            stable.write_text("新日志\n", encoding="utf-8")
            if paths_module.model_log_files(local.id)[-1] != stable:
                problems.append("同一个模型的新日志该排在最后（最新的那份）")

            page._delete(local)
        finally:
            page_module.ModelLogDialog = original_log
            page_module.DeleteModelDialog = original_delete

        if stable.exists() or legacy.exists():
            problems.append(f"删模型要连日志一起删：新={stable.exists()} 老={legacy.exists()}")
        if page._api.model_by_id(local.id) is not None:
            problems.append("删模型该把登记也去掉")

        assert not problems, "模型日志：" + "；".join(problems[:8])
    finally:
        dispose_window(window)

@check("model_ui_via_tool_library", "pages")
def model_ui_via_tool_library(case: Case) -> None:
    """模型插件的界面只从 UI 工具库（+ SDK）搭：qfluentwidgets 只许直接拿 FluentIcon。"""
    import ast

    allowed = {"FluentIcon"}
    problems: list[str] = []
    for path in sorted((_PLUGIN_DIR / "ui").glob("*.py")):
        where = path.relative_to(_PLUGIN_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").split(".")[0] == "qfluentwidgets":
                for alias in node.names:
                    if alias.name not in allowed:
                        problems.append(
                            f"{where}:{node.lineno} 直接 import 了 qfluentwidgets 的 {alias.name}："
                            "界面控件该从 dm_plugin.builtin.lib.ui.plugin 拿（缺的补进工具库）"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if str(alias.name).split(".")[0] == "qfluentwidgets":
                        problems.append(f"{where}:{node.lineno} 直接 import qfluentwidgets（{alias.name}）：界面控件该走 UI 工具库")
    assert not problems, "模型插件界面没走 UI 工具库：" + "；".join(problems[:8])


@check("model_page_settings_form", "pages")
def model_page_settings_form(case: Case) -> None:
    """页面每项设置即改即用（没有「保存设置」），落到 .configs/models.json，重载表单能读回。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model import settings as settings_module

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._device_probed = True  # 自检不真的去探设备（要拉起解释器，慢）
        window.switchTo(page)
        if page._selected_device() not in {"auto", "cpu"}:
            problems.append(f"推理设备下拉默认应是自动选择：{page._selected_device()}")
        if not callable(getattr(page, "_detect_devices", None)):
            problems.append("模型页缺少设备探测入口 _detect_devices")

        original_run_async = page._run_async
        fake_payload = {
            "devices": (
                {"id": "cpu", "name": "12th Gen Intel(R) Core(TM) i9-12900H（处理器）", "usable": True},
                {"id": "cuda:0", "name": "NVIDIA GeForce RTX 3050 Ti Laptop GPU，4096 MiB（CUDA 0）", "usable": True},
                {"id": "cuda:1", "name": "NVIDIA GeForce RTX 4090（CUDA 1）", "usable": False},
            ),
            "others": ("Intel(R) Iris(R) Xe Graphics",),
            "python": "fake",
        }
        page._run_async = lambda work, on_done=None, on_finally=None: (on_done(fake_payload), on_finally())
        try:
            page._detect_devices()
        finally:
            page._run_async = original_run_async
        labels = [page.device_box.itemText(index) for index in range(page.device_box.count())]
        keys = [page.device_box.itemData(index) for index in range(page.device_box.count())]
        if "cuda:0" not in keys or not any("RTX 3050 Ti" in label for label in labels):
            problems.append(f"设备下拉没显示真实显卡型号：{labels}")
        if "cuda:1" in keys:
            problems.append(f"当前程序用不了的设备不该塞进下拉：{labels}")
        if "i9-12900H" not in " ".join(labels):
            problems.append(f"设备下拉没显示真实 CPU 型号：{labels}")
        hint_text = page._device_hint.text()
        if "可用" not in hint_text or "RTX 4090" not in hint_text or "Iris" not in hint_text:
            problems.append(f"设备说明没写清可用设备与用不了的显卡：{hint_text}")

        if hasattr(page, "base_edit") or hasattr(page, "mirror_edit"):
            problems.append("总下载网址 / 镜像网址不该再有可编辑输入框（它们是系统默认，只读展示）")
        # 提示行跟着「下载源（模型）」的选项走：官方 / 镜像各写出自己那串地址
        page._select_download_source("official")
        page._sync_download_source_row()
        if settings_module.DEFAULT_BASE_URL not in page._download_source_hint.text():
            problems.append(f"选「HuggingFace官方」时没写出官方地址：{page._download_source_hint.text()}")
        page._select_download_source("mirror")
        page._sync_download_source_row()
        if settings_module.DEFAULT_MIRRORS[0] not in page._download_source_hint.text():
            problems.append(f"选「HF-Mirror镜像」时没写出镜像地址：{page._download_source_hint.text()}")
        # 高控件（选择框 + 提示 + 列表）的标签必须贴行顶，否则默认垂直居中会与选择项错位
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel

        tall_labels = [label for label in page.findChildren(QLabel) if label.text() == "下载源（模型）"]
        if not tall_labels:
            problems.append("设置区找不到「下载源（模型）」标签")
        else:
            host = tall_labels[0].parentWidget()
            row = host.layout() if host is not None else None
            alignment = row.itemAt(0).alignment() if row is not None else Qt.AlignmentFlag(0)
            if not (alignment & Qt.AlignmentFlag.AlignTop):
                problems.append("「下载源（模型）」标签没有贴行顶，会和后面的选择项错位")
        if any("保存设置" in button.text() for button in page.findChildren(QPushButton)):
            problems.append("设置区还有「保存设置」按钮：每一项都该即改即用")
        page._pump_probe = lambda: None  # 自检不真的去 import torch 探包
        page.proxy_edit.setText("http://127.0.0.1:7890")
        # 代理是输入框：失焦 / 回车才提交，即改即存就挂在这个信号上
        page.proxy_edit.editingFinished.emit()
        page.concurrent_box.setValue(3)
        page.max_resident_box.setValue(2)
        page.idle_box.setValue(120)
        page._select_device("cpu")
        page.system_env_box.setChecked(True)  # 勾上就该立刻刷新运行环境区，不该还要再点保存

        toggled_states = _runtime_states(page)
        if not toggled_states:
            problems.append("勾选高级选项后运行环境区没有可读的状态标签")
        elif "未安装" in toggled_states:
            problems.append(f"勾选高级选项后运行环境区还写着未安装：{toggled_states}")
        plain_text = page._device_hint_text("CPU（处理器）", ["Intel(R) Iris(R) Xe Graphics"], has_cuda=False)
        if "Iris" not in plain_text:
            problems.append(f"设备说明漏掉了本程序不拿来推理的显卡：{plain_text}")

        # 没有「保存设置」可点了：上面每拨一下控件就应该已经落盘
        saved = settings_module.load_settings()
        # 总下载网址 / 镜像网址是系统默认，界面上只读展示：保存设置不该把它们改掉
        if saved.base_url != settings_module.DEFAULT_BASE_URL:
            problems.append(f"保存设置把系统默认的总下载网址改了：{saved.base_url}")
        if tuple(saved.mirrors) != tuple(settings_module.DEFAULT_MIRRORS):
            problems.append(f"保存设置把系统默认的镜像改了：{saved.mirrors}")
        if saved.proxy != "http://127.0.0.1:7890" or saved.concurrent != 3:
            problems.append(f"代理/并发没有保存：{saved.proxy} {saved.concurrent}")
        if saved.max_resident != 2 or saved.idle_unload_sec != 120:
            problems.append(f"常驻/空闲设置没有保存：{saved.max_resident} {saved.idle_unload_sec}")
        if saved.device != "cpu" or not saved.allow_system_env:
            problems.append(f"设备/安装模式没有保存：{saved.device} {saved.allow_system_env}")

        page.proxy_edit.setText("")  # 界面上先留一个和设置文件不一样的值
        page._load_settings_into_form()
        reloaded_source = str(page.source_box.currentData() or "")
        wanted = {
            "official": settings_module.DEFAULT_BASE_URL,
            "mirror": settings_module.DEFAULT_MIRRORS[0],
        }.get(reloaded_source)
        if wanted and wanted not in page._download_source_hint.text():
            problems.append(f"重载表单后下载源提示没跟着选项走：{page._download_source_hint.text()}")
        if page._selected_device() != "cpu":
            problems.append(f"重载表单没有读回设备：{page._selected_device()}")
        if page.proxy_edit.text() != "http://127.0.0.1:7890":
            problems.append(f"重载表单没有读回代理：{page.proxy_edit.text()}")
        if page._loading_settings:
            problems.append("重载表单后 _loading_settings 没有复位：后面的改动都存不进去")
        reloaded = settings_module.load_settings()
        if reloaded.proxy != "http://127.0.0.1:7890":
            problems.append(f"回填表单时把设置写坏了（不该触发即改即存）：{reloaded.proxy}")

        assert not problems, "模型页设置表单：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("model_page_github_source", "pages")
def model_page_github_source(case: Case) -> None:
    """GitHub 下载源：三类选项、只有自定义能填前缀、改一下立刻存盘、提示写出取址顺序。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model import settings as settings_module

    original = settings_module.load_settings()
    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._device_probed = True
        page._pump_probe = lambda: None  # 自检不真的去探包
        window.switchTo(page)

        if not hasattr(page, "github_box") or not hasattr(page, "github_custom"):
            problems.append("设置区没有 GitHub 下载源控件")
        else:
            keys = tuple(page.github_box.itemData(index) for index in range(page.github_box.count()))
            if keys != tuple(settings_module.GITHUB_SOURCE_KEYS):
                problems.append(f"GitHub 下载源下拉的选项不对：{keys}")
            labels = tuple(page.github_box.itemText(index) for index in range(page.github_box.count()))
            if labels != tuple(label for _key, label in settings_module.GITHUB_SOURCES):
                problems.append(f"GitHub 下载源下拉的显示名不对：{labels}")
            # 三个镜像各是一个独立选项，选谁就写谁的前缀（不再是一串兜底）
            for key, prefix in settings_module.GITHUB_MIRRORS.items():
                page._select_github_source(key)
                page._sync_github_row()
                if prefix not in page._github_hint.text():
                    problems.append(f"选「{key}」时提示没写出它自己的镜像前缀：{page._github_hint.text()}")
            page._select_github_source("official")
            page._sync_github_row()
            if page.github_custom.isEnabled():
                problems.append("选「官方」时自定义前缀不该能填")
            page._select_github_source("custom")
            page._sync_github_row()
            if not page.github_custom.isEnabled():
                problems.append("选「自定义」时该能填前缀")
            page.github_custom.setText("https://ghproxy.net/")
            page._save_github_source()  # 即改即存，没有「保存设置」可点
            saved = settings_module.load_settings()
            if saved.github_source != "custom" or saved.github_custom != "https://ghproxy.net":
                problems.append(
                    f"自定义 GitHub 下载源没存盘（尾斜杠要去掉）：{saved.github_source} {saved.github_custom}"
                )
            if "ghproxy.net" not in page._github_hint.text():
                problems.append(f"提示行没写出真正会用的前缀：{page._github_hint.text()}")
            page.github_custom.setText("https://example.invalid")
            page._load_settings_into_form()
            if page.github_box.currentData() != "custom" or page.github_custom.text() != "https://ghproxy.net":
                problems.append(
                    f"重载表单没读回 GitHub 下载源：{page.github_box.currentData()} {page.github_custom.text()}"
                )

        assert not problems, "模型页 GitHub 下载源：" + "；".join(problems[:8])
    finally:
        settings = settings_module.load_settings()
        settings.github_source = original.github_source
        settings.github_custom = original.github_custom
        settings.save()
        dispose_window(window)


@check("model_runtime_probe_batch", "pages")
def model_runtime_probe_batch(case: Case) -> None:
    """依赖探测：一次批量问完所有 profile，行内提示只放短话、完整清单进悬停提示。"""
    from PyQt6.QtWidgets import QLabel
    from qfluentwidgets import StrongBodyLabel

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._device_probed = True  # 自检不真的去探设备（要拉起解释器，慢）

        class _FakeProbe:
            """假探测模块：记下调用次数，只给 llama-cpp 报缺一个包。"""

            def __init__(self) -> None:
                self.calls = 0
                self.groups: dict = {}

            def missing_program_group(self, groups):
                self.calls += 1
                self.groups = dict(groups)
                return {key: (("llama-cpp-python>=0.3.2",) if key == "llama-cpp" else ()) for key in groups}

        fake = _FakeProbe()
        original_probe = page._probe_module
        original_async = page._run_async
        original_allow = bool(page._settings.runtime.get("allow_system_env"))
        page._probe_module = lambda: fake
        page._run_async = lambda work, on_done=None, on_finally=None: (on_done(work()), on_finally())
        page._program_env_cache.clear()
        page._state_tooltip.clear()
        page._probe_pending = {
            "llama-cpp": {"id": "llama-cpp", "packages": ["llama-cpp-python>=0.3.2"]},
            "piper": {"id": "piper", "packages": ["piper-tts>=1.2"]},
        }
        try:
            page._pump_probe()

            if fake.calls != 1:
                problems.append(f"依赖探测没有合并成一次批量调用：{fake.calls} 次")
            if sorted(fake.groups) != ["llama-cpp", "piper"]:
                problems.append(f"批量探测没有把所有 profile 一起问：{sorted(fake.groups)}")
            state = page._program_env_cache.get("llama-cpp", "")
            if "llama-cpp-python>=0.3.2" not in state or len(state) > 40:
                problems.append(f"缺依赖的行内提示太长或丢了包名：{state}")
            tooltip = page._state_tooltip.get("llama-cpp", "")
            if "llama-cpp-python>=0.3.2" not in tooltip:
                problems.append(f"悬停提示没给完整依赖清单：{tooltip}")
            if page._program_env_cache.get("piper") != "使用程序环境（已装）":
                problems.append(f"装齐的依赖没有显示已装：{page._program_env_cache.get('piper')}")

            # 真刷一遍运行环境区：行内文字必须短，完整清单必须挂在悬停提示上
            page._settings.runtime["allow_system_env"] = True
            page._fill_runtime()
        finally:
            page._settings.runtime["allow_system_env"] = original_allow
            page._probe_module = original_probe
            page._run_async = original_async

        longest = 0
        without_tooltip = 0
        for index in range(page._runtime_layout.count()):
            row = page._runtime_layout.itemAt(index).widget()
            if row is None or row.layout() is None:
                continue
            for slot in range(row.layout().count()):
                widget = row.layout().itemAt(slot).widget()
                if not isinstance(widget, QLabel) or not widget.text().startswith("使用程序环境"):
                    continue
                longest = max(longest, len(widget.text()))
                if not widget.toolTip().strip():
                    without_tooltip += 1
        if longest > 40:
            problems.append(f"运行环境行的状态文字还是太长（{longest} 字），会把区域撑高")
        if without_tooltip:
            problems.append(f"有 {without_tooltip} 行状态没有悬停提示，看不到完整依赖")

        clipped: list[str] = []
        for index in range(page._runtime_layout.count()):
            row = page._runtime_layout.itemAt(index).widget()
            if row is None or row.layout() is None:
                continue
            for slot in range(row.layout().count()):
                widget = row.layout().itemAt(slot).widget()
                if not isinstance(widget, StrongBodyLabel):
                    continue
                text = widget.text().strip()
                if not text:
                    continue
                if widget.fontMetrics().horizontalAdvance(text) > widget.width():
                    if not widget.wordWrap() or widget.toolTip().strip() != text:
                        clipped.append(text)
        if clipped:
            problems.append("运行环境行的环境名会被截断（要折行并给完整悬停提示）：" + "、".join(clipped[:6]))

        assert not problems, "运行环境依赖探测：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


def _row_problems(page, expected: dict) -> list[str]:
    """`expected`：key → (可安装, 可卸载, 可看日志, 可本地装 whl)。"""
    problems: list[str] = []
    for key, want in expected.items():
        row = page._runtime_rows.get(key)
        if row is None:
            problems.append(f"运行环境区没有 {key} 这一行")
            continue
        have = tuple(bool(row[name].isEnabled()) for name in ("install", "uninstall", "log", "whl"))
        if have != want:
            problems.append(f"{key} 的按钮应是（安装/卸载/日志/本地 whl）{want}，实际 {have}")
    return problems


@check("model_runtime_buttons", "pages")
def model_runtime_buttons(case: Case) -> None:
    """行内按钮跟着安装状态走，且卸载要落在当前模式的落点上（程序环境 / profile 目录）。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._device_probed = True
        module = page._runtime_module()
        assert module is not None, "运行环境模块应该可用"

        isolated = {"llama-cpp": "未安装", "piper": "已安装"}
        original_state = page._profile_state
        original_probe = page._pump_probe
        original_confirm = page.confirm
        original_async = page._run_async
        original_allow = bool(page._settings.runtime.get("allow_system_env"))
        real_uninstall = module.uninstall
        real_uninstall_system = module.uninstall_system
        calls: list[tuple[str, str]] = []

        def fake_state(profile):
            key = str(profile.get("id") or "")
            if page._settings.runtime.get("allow_system_env"):
                return page._program_env_cache.get(key, "使用程序环境（检测中…）")
            return isolated.get(key, "未安装")

        log_path = module.log_file("piper")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("fake", encoding="utf-8")

        page._profile_state = fake_state
        page._pump_probe = lambda: None
        page._program_env_cache.clear()
        page._state_tooltip.clear()
        try:
            page._fill_runtime()
            problems.extend(_row_problems(page, {"llama-cpp": (True, False, False, True), "piper": (False, True, True, False)}))

            page._settings.runtime["allow_system_env"] = True
            page._program_env_cache.clear()
            page._program_env_cache.update(
                {
                    "llama-cpp": "使用程序环境（缺 llama-cpp-python>=0.3.2）",
                    "piper": "使用程序环境（已装）",
                }
            )
            page._fill_runtime()
            problems.extend(_row_problems(page, {"llama-cpp": (True, False, False, True), "piper": (False, True, True, False)}))

            module.uninstall = lambda profile_id: calls.append(("isolated", str(profile_id))) or True
            module.uninstall_system = lambda profile, on_line=None: calls.append(("system", profile.id)) or module.system_python()
            page.confirm = lambda *args, **kwargs: True
            page._run_async = lambda work, on_done=None, on_finally=None: (on_done(work()), on_finally())
            page._uninstall_profile({"id": "piper", "name": "piper", "packages": ["piper-tts>=1.2"]})
            if calls != [("system", "piper")]:
                problems.append(f"程序环境模式的「卸载」没有落在程序解释器上：{calls}")
            calls.clear()
            page._settings.runtime["allow_system_env"] = False
            page._uninstall_profile({"id": "piper", "name": "piper", "packages": ["piper-tts>=1.2"]})
            if calls != [("isolated", "piper")]:
                problems.append(f"隔离模式的「卸载」没有删运行环境目录：{calls}")
        finally:
            page._profile_state = original_state
            page._pump_probe = original_probe
            page.confirm = original_confirm
            page._run_async = original_async
            page._settings.runtime["allow_system_env"] = original_allow
            module.uninstall = real_uninstall
            module.uninstall_system = real_uninstall_system
            page._program_env_cache.clear()
            page._state_tooltip.clear()

        assert not problems, "运行环境按钮适配：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("model_p4_system_install", "pages")
def model_p4_system_install(case: Case) -> None:
    """P4 程序环境安装模式：先弹两次确认，确认后才用 `ensure_system` 装进程序解释器。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model import runtime as runtime_module

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        for name in ("ensure_system", "system_python", "system_requirements_path", "system_log_file"):
            if not callable(getattr(runtime_module, name, None)):
                problems.append(f"运行环境模块缺少 P4 接口：{name}")

        profiles = page._runtime_profiles()
        if not profiles:
            problems.append("没有读到运行环境清单，无法验证 P4 安装流程")
        else:
            profile = profiles[0]
            key = str(profile.get("id") or "")
            original_async = page._run_async
            original_confirm = page.confirm
            original_settings = dict(page._settings.runtime)
            original_ensure = runtime_module.ensure_system
            original_venv_ensure = runtime_module.ensure
            asked: list[str] = []
            started: list[str] = []
            called: dict[str, object] = {}

            def record_start(work, on_done=None, on_finally=None, on_failed=None):
                started.append("run")
                called["work"] = work
                called["on_failed"] = on_failed
                called["on_finally"] = on_finally

            def finish(callback) -> None:
                """模拟后台线程收尾：真线程跑完会撤掉「安装中」这一行，守卫随之放开。"""
                if callable(callback):
                    callback()

            def fake_system_ensure(prof, *, on_line=None, **kwargs):
                called["system"] = prof
                return runtime_module.system_python()

            def fake_venv_ensure(prof, *, on_line=None, **kwargs):
                called["venv"] = prof
                return runtime_module.python_path(getattr(prof, "id", ""))

            try:
                page._settings.runtime["allow_system_env"] = True
                page._run_async = record_start
                page.confirm = lambda title, detail="", **kw: (asked.append(title), False)[1]
                page._install_profile(profile)
                if len(asked) != 1 or started:
                    problems.append(f"第一次取消后不该开始安装：确认 {len(asked)} 次、启动 {len(started)} 次")

                asked.clear()
                runtime_module.ensure_system = fake_system_ensure
                page.confirm = lambda title, detail="", **kw: (asked.append(title), True)[1]
                page._install_profile(profile)
                if len(asked) != 2:
                    problems.append(f"程序环境模式应当二次确认，实际确认 {len(asked)} 次：{asked}")
                if len(started) != 1 or "system" in called:
                    problems.append(f"确认前不该动手装依赖：started={started} called={sorted(called)}")
                if not callable(called.get("on_failed")):
                    problems.append("安装任务的失败回调没接上：暂停 / 取消要靠它收尾")
                work = called.pop("work", None)
                if not callable(work):
                    problems.append("程序环境模式没有把安装任务交给后台线程")
                else:
                    work()
                    installed = called.get("system")
                    if getattr(installed, "id", "") != key:
                        problems.append(f"装进程序环境时用错了 profile：{getattr(installed, 'id', '')} != {key}")
                finish(called.get("on_finally"))

                # 关掉高级选项：回到独立 venv，只确认一次
                asked.clear()
                started.clear()
                called.clear()
                page._settings.runtime["allow_system_env"] = False
                runtime_module.ensure = fake_venv_ensure
                page.confirm = lambda title, detail="", **kw: (asked.append(title), True)[1]
                page._install_profile(profile)
                if len(asked) != 1:
                    problems.append(f"独立 venv 模式只该确认一次，实际 {len(asked)} 次")
                work = called.pop("work", None)
                if callable(work):
                    work()
                if "venv" not in called or "system" in called:
                    problems.append(f"关掉高级选项后应走独立 venv 安装：called={sorted(called)}")
                finish(called.get("on_finally"))
            finally:
                page._run_async = original_async
                page.confirm = original_confirm
                runtime_module.ensure_system = original_ensure
                runtime_module.ensure = original_venv_ensure
                page._settings.runtime.clear()
                page._settings.runtime.update(original_settings)

        assert not problems, "P4 程序环境安装：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("model_dialog_form_scroll", "pages")
def model_dialog_form_scroll(case: Case) -> None:
    """新建本地 / 外部模型的表单要能滚动：窗口不高时能力复选框不能叠在一起。"""
    from PyQt6.QtWidgets import QApplication
    from qfluentwidgets import CheckBox, SingleDirectionScrollArea

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model.ui.dialogs import ExternalModelDialog, LocalModelDialog

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        window.resize(1280, 620)  # 故意矮：表单被压扁时能力网格行就会重叠
        window.show()
        QApplication.processEvents()
        dialogs = [
            ("本地", LocalModelDialog(window, templates=page._template_list("model_list"), profiles=page._runtime_profiles())),
            ("外部", ExternalModelDialog(window, templates=page._template_list("api_templates"))),
        ]
        for label, dialog in dialogs:
            try:
                dialog.show()
                QApplication.processEvents()
                area = dialog.findChild(SingleDirectionScrollArea)
                if area is None or area.widget() is None:
                    problems.append(f"{label}模型弹窗的表单没有放进可滚动区域")
                    continue
                holder = dialog.capability_holder
                boxes = list(holder.findChildren(CheckBox))
                if len(boxes) != len(dialog.capability_boxes):
                    problems.append(f"{label}模型弹窗的能力复选框少了：{len(boxes)} != {len(dialog.capability_boxes)}")
                for index, box in enumerate(boxes):
                    if not box.isVisible():
                        problems.append(f"{label}模型弹窗的能力复选框看不见：{box.text()}")
                    if box.width() <= 0 or box.height() <= 0:
                        problems.append(f"{label}模型弹窗的能力复选框没被布局（宽高为 0）：{box.text()}")
                    for other in boxes[index + 1:]:
                        if box.geometry().intersects(other.geometry()):
                            problems.append(f"{label}模型弹窗的能力复选框叠在一起：{box.text()} / {other.text()}")
            finally:
                _dispose(dialog)
    finally:
        dispose_window(window)
    assert not problems, "模型弹窗表单：" + "；".join(problems[:12])


@check("model_dialog_template_reset", "pages")
def model_dialog_template_reset(case: Case) -> None:
    """选了模板再改字段：模板下拉要自动回到「（不使用模板）」；程序化填表不能被自己打断。"""
    from PyQt6.QtWidgets import QApplication

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model.ui.dialogs import ExternalModelDialog, LocalModelDialog

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        dialogs = [
            ("本地", LocalModelDialog(window, templates=page._template_list("model_list"), profiles=page._runtime_profiles())),
            ("外部", ExternalModelDialog(window, templates=page._template_list("api_templates"))),
        ]
        for label, dialog in dialogs:
            try:
                if dialog.template_box.count() <= 1:
                    problems.append(f"{label}模型弹窗没有可选模板，模板复位无法验证")
                    continue
                dialog.template_box.setCurrentIndex(1)
                QApplication.processEvents()
                if dialog.template_box.currentIndex() != 1:
                    problems.append(f"{label}模型弹窗选了模板后下拉自己跳回去了（程序化填表被当成用户改动）")
                    continue
                edit = dialog.name_edit
                edit.setText("改过的名字")
                edit.textEdited.emit("改过的名字")
                if dialog.template_box.currentIndex() != 0:
                    problems.append(f"{label}模型弹窗改了字段后模板下拉没有复位：index={dialog.template_box.currentIndex()}")
                dialog.template_box.setCurrentIndex(0)
                dialog.template_box.setCurrentIndex(1)
                if dialog.template_box.currentIndex() != 1:
                    problems.append(f"{label}模型弹窗再次选模板仍然站不住：index={dialog.template_box.currentIndex()}")
            finally:
                _dispose(dialog)
    finally:
        dispose_window(window)
    assert not problems, "模型弹窗模板：" + "；".join(problems[:12])


@check("model_page_card_cleanup", "pages")
def model_page_card_cleanup(case: Case) -> None:
    """删掉模型后卡片必须从网格上收走，不能留下点不动的僵尸卡。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()
    from dm_plugin.lib.model.ui.cards import ModelCard

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._pump_probe = lambda: None
        page._profile_state = lambda profile: "未安装"
        record = page._api.add_local("自检卡片回收", description="删掉后不该留下僵尸卡")
        page.refresh()
        card = page._card_map.get(record.id)
        if card is None:
            problems.append("新建的模型没有出现在模型卡片里")
        if page._api.model_by_id(record.id) is None:
            problems.append("新建的模型查不回来")

        page._api.remove(record.id, delete_files=False)
        page._render_cards()
        if record.id in page._card_map:
            problems.append("删掉的模型还留在 _card_map 里")
        if card is not None:
            if card.parent() is not None:
                problems.append("删掉的卡片没有从网格上摘下来")
            if card in page._grid.findChildren(ModelCard):
                problems.append("删掉的卡片还挂在网格里（僵尸卡）")
    finally:
        dispose_window(window)
    assert not problems, "模型卡片回收：" + "；".join(problems[:12])


@check("model_page_queue_rows", "pages")
def model_page_queue_rows(case: Case) -> None:
    """下载行：全名给悬停提示、显示具体进度与阶段、忙等条、终态才能移除、失败只播报一次。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    class _FakeJob:
        """只实现页面用到的字段，把队列行的渲染逻辑单独测出来。"""

        def __init__(self, job_id, label, state, *, total=0, done=0, error="", phase="", note=""):
            self.id = job_id
            self.label = label
            self.state = state
            self.total_bytes = total
            self.done_bytes = done
            self.error = error
            self.speed = 0.0
            self.eta = 0.0
            self.phase = phase
            self.note = note

        @property
        def progress(self):
            return self.done_bytes / self.total_bytes if self.total_bytes else 0.0

        @property
        def detail_label(self):
            return self.note or self.phase

        @property
        def state_label(self):
            return {"running": "下载中", "paused": "已暂停", "error": "失败", "done": "已完成", "cancelled": "已取消"}.get(
                self.state, self.state
            )

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            pass

    class _FakeManager:
        def __init__(self, jobs):
            self._jobs = list(jobs)

        def jobs(self):
            return list(self._jobs)

        def forget(self, job_id):
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs if job.id != job_id]
            return len(self._jobs) != before

        def clear_finished(self):
            finished = [job for job in self._jobs if job.state in ("done", "error", "cancelled")]
            self._jobs = [job for job in self._jobs if job not in finished]
            return len(finished)

    _fixture, window = build_window(case)
    problems: list[str] = []
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        long_label = "Qwen2.5-7B-Instruct-GGUF · Qwen2.5-7B-Instruct-Q4_K_M.gguf"
        running = _FakeJob("a", long_label, "running", total=1024 * 1024, done=256 * 1024, phase="接收数据")
        busy = _FakeJob("b", "没给总量的源 · model.gguf", "running", done=4096)
        failed = _FakeJob("c", "连不上的源 · model.gguf", "error", error="连接失败")
        page._downloads = _FakeManager([running, busy, failed])
        announced: list[str] = []
        page._announce_job_failure = lambda job: announced.append(job.id)
        page._timer.stop()
        page._refresh_queue()

        row = page._job_rows.get("a")
        if row is None:
            problems.append("下载行没有按 job 建出来")
        else:
            if row._name.toolTip() != long_label:
                problems.append("下载行没有把全名放进悬停提示")
            if not row._name.wordWrap():
                problems.append("下载行名字要能折行")
            if row._name.minimumWidth() < 160:
                problems.append(f"下载行名字的最小宽度太小：{row._name.minimumWidth()}")
            outer = row.layout()
            if outer is None or outer.indexOf(row._bar) < 0:
                problems.append("下载行没有改成「上排名字 / 下排进度条」两段式")
            text = row._state.text()
            if "25%" not in text or "已下载" in text:
                problems.append(f"下载行没有显示具体进度：{text}")
            if "接收数据" not in text:
                problems.append(f"下载行没有显示当前阶段：{text}")
            if row._forget.isEnabled():
                problems.append("还在下载的记录不该能移除")
        busy_row = page._job_rows.get("b")
        if busy_row is not None and busy_row._bar.maximum() != 0:
            problems.append("总量未知的下载应该用忙等进度条")
        failed_row = page._job_rows.get("c")
        if failed_row is not None:
            if not failed_row._forget.isEnabled():
                problems.append("失败的记录应当能移除")
            if failed_row._pause.isEnabled() or failed_row._cancel.isEnabled():
                problems.append("已结束的下载行还留着能点的暂停 / 取消")
        if announced != ["c"]:
            problems.append(f"失败只该播报一次：{announced}")
        page._refresh_queue()
        if announced != ["c"]:
            problems.append(f"同一个失败被重复播报：{announced}")
        if not page._clear_jobs_button.isEnabled():
            problems.append("有已结束的记录时「清空已结束」应该可用")

        page._forget_job("c")
        if "c" in page._job_rows:
            problems.append("移除记录后那一行没有消失")
        page._downloads._jobs.append(_FakeJob("d", "下完的 · model.gguf", "done", total=100, done=100))
        page._refresh_queue()
        page._clear_finished_jobs()
        if "d" in page._job_rows or any(job.id == "d" for job in page._downloads.jobs()):
            problems.append("「清空已结束」没有把已完成的记录清掉")
        page._timer.stop()
    finally:
        dispose_window(window)
    assert not problems, "下载队列行：" + "；".join(problems[:12])


@check("model_page_runtime_installing", "pages")
def model_page_runtime_installing(case: Case) -> None:
    """点「安装」后那一行立刻变成进度条 + pip 输出 + 计时、按钮锁住；装完回到真实状态。"""
    import time as time_module

    from PyQt6.QtWidgets import QPushButton
    from qfluentwidgets import IndeterminateProgressBar

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    _fixture, window = build_window(case)
    problems: list[str] = []
    page = None
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        page._pump_probe = lambda: None
        page._profile_state = lambda profile: "未安装"
        profiles = page._runtime_profiles()
        if not profiles:
            problems.append("没有读到运行环境清单")
        else:
            key = str(profiles[0].get("id") or "")
            page._install_timer.stop()
            page._installing[key] = {
                "name": "自检",
                "start": time_module.monotonic(),
                "lines": ["Collecting piper-tts"],
                "state": "running",
                "source": "本地文件：piper-tts-1.2-cp311-win_amd64.whl",
            }
            page._fill_runtime()
            row = page._runtime_rows.get(key) or {}
            label = row.get("install_state")
            if label is None:
                problems.append("安装中的那一行没有进度文案控件")
            else:
                if not label.parent().findChildren(IndeterminateProgressBar):
                    problems.append("安装中的那一行没有忙等进度条")
                buttons = {button.text(): button for button in label.parent().findChildren(QPushButton)}
                for text in ("暂停", "取消"):
                    if text not in buttons or not buttons[text].isEnabled():
                        problems.append(f"安装中的那一行没有能点的「{text}」")
                if "安装" in buttons:
                    problems.append("安装中的那一行还留着「安装」按钮")
                page._tick_install()
                text = label.text()
                if "安装中" not in text or "piper-tts" not in text:
                    problems.append(f"安装中的那一行没有显示 pip 输出：{text}")
                # 来源要写清：本地 whl 安装就说本地文件，不能还拿 pip 源冒充
                tip = label.toolTip()
                if "本地文件" not in tip or "piper-tts-1.2-cp311-win_amd64.whl" not in tip:
                    problems.append(f"安装行的悬停提示没写出本地文件来源：{tip}")

            page._pause_profile(key)
            control = page._install_ctl.get(key)
            if control is None or not control.should_pause():
                problems.append("点「暂停」没有把暂停请求记下来")
            if control is not None and control.should_cancel():
                problems.append("点「暂停」不该顺手置上取消位")
            page._fill_runtime()
            pausing_row = page._runtime_rows.get(key) or {}
            pausing_label = pausing_row.get("install_state")
            pausing_text = pausing_label.text() if pausing_label is not None else ""
            if "正在暂停" not in pausing_text:
                problems.append(f"点「暂停」后那一行没有立刻改口：{pausing_text}")

            # 安装线程收尾后就是已暂停：进度条换成「已暂停」，按钮变「继续 / 取消」
            page._installing[key]["state"] = "paused"
            page._fill_runtime()
            paused_row = page._runtime_rows.get(key) or {}
            paused_label = paused_row.get("install_state")
            if paused_label is None or "已暂停" not in paused_label.text():
                problems.append(f"暂停后那一行没有显示「已暂停」：{paused_label.text() if paused_label else None}")
            elif paused_label.parent().findChildren(IndeterminateProgressBar):
                problems.append("已暂停的那一行还在转进度条")
            paused_buttons = {} if paused_label is None else {b.text(): b for b in paused_label.parent().findChildren(QPushButton)}
            if "继续" not in paused_buttons or not paused_buttons["继续"].isEnabled():
                problems.append("暂停后没有能点的「继续」")
            if "取消" not in paused_buttons or not paused_buttons["取消"].isEnabled():
                problems.append("暂停后没有能点的「取消」")

            page._cancel_install(key)
            control = page._install_ctl.get(key)
            if control is None or not control.should_cancel():
                problems.append("点「取消」没有把取消请求记下来")
            if control is not None and control.should_pause():
                problems.append("点「取消」没有清掉暂停位")
            cancelling_row = page._runtime_rows.get(key) or {}
            cancelling_label = cancelling_row.get("install_state")
            cancelling_text = cancelling_label.text() if cancelling_label is not None else ""
            if "正在取消" not in cancelling_text:
                problems.append(f"点「取消」后那一行还写着安装中：{cancelling_text}")

            page._installing.pop(key, None)
            page._install_ctl.pop(key, None)
            page._install_timer.stop()
            page._fill_runtime()
            after = page._runtime_rows.get(key) or {}
            if after.get("install") is None or after.get("state_text") != "未安装":
                problems.append("安装结束后那一行没有回到真实状态")
    finally:
        if page is not None:
            page._installing.clear()
            page._install_ctl.clear()
            page._install_timer.stop()
        dispose_window(window)
    assert not problems, "运行环境安装中提示：" + "；".join(problems[:12])


@check("model_runtime_parallel_install", "pages")
def model_runtime_parallel_install(case: Case) -> None:
    """一键补全的调度：默认并发一次全开；选「挨个装」则一次一个、收尾再补下一个；同一环境不重复开线程。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    import dm_plugin.lib.model.ui.page as page_module

    _fixture, window = build_window(case)
    problems: list[str] = []
    page = None
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"

        class FakeDialog:
            """替掉「并发 / 挨个」选择弹窗：直接点「开始安装」，顺序由 sequential_mode 决定。"""

            sequential_mode = False

            def __init__(self, *args: object, **kwargs: object) -> None:
                self.detail = str(kwargs.get("detail") or "")

            def exec(self) -> int:
                return 1

            def sequential(self) -> bool:
                return bool(FakeDialog.sequential_mode)

        original_dialog = page_module.CompleteRuntimesDialog
        original_plan = page._complete_plan
        original_install = page._install_profile
        original_async = page._run_async
        calls: list[str] = []
        try:
            page._device_has_cuda = False
            page_module.CompleteRuntimesDialog = FakeDialog
            page._complete_plan = lambda: [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"}]
            page._install_profile = lambda profile, **kwargs: calls.append(str(profile.get("id")))

            # 并发（默认）：正在装的也再排一次，由 _install_profile 自己挡
            FakeDialog.sequential_mode = False
            page._installing["a"] = {"name": "A", "start": 0.0, "lines": [], "state": "running"}
            page._complete_runtimes()
            if calls != ["a", "b", "c"]:
                problems.append(f"并发模式该把队列一次全开（正在装的也再排一次、由 _install_profile 挡）：{calls}")
            if page._complete_queue:
                problems.append("并发模式该一次抽干队列，不该剩下等着前一个装完")
            if page._complete_sequential:
                problems.append("并发模式不该把 _complete_sequential 留成 True")

            # 挨个：一次只开一个，收尾时再补下一个
            page._installing.clear()
            FakeDialog.sequential_mode = True
            calls.clear()
            page._complete_runtimes()
            if calls != ["a"]:
                problems.append(f"挨个模式该一次只开一个：{calls}")
            if [str(item.get("id")) for item in page._complete_queue] != ["b", "c"]:
                problems.append(f"挨个模式剩下的该留在队列里：{page._complete_queue}")
            page._installing["a"] = {"name": "A", "start": 0.0, "lines": [], "state": "running"}
            page._pump_complete_queue()
            if calls != ["a"]:
                problems.append(f"挨个模式下还有环境在装时不该再开下一个：{calls}")
            page._installing.clear()
            page._pump_complete_queue()
            if calls != ["a", "b"]:
                problems.append(f"上一个收尾后该接着开下一个：{calls}")
            page._pump_complete_queue()
            if calls != ["a", "b", "c"] or page._complete_queue:
                problems.append(f"挨个模式该把队列走完：{calls} / {page._complete_queue}")

            # 真正的守卫：同一个运行环境已经在装时，_install_profile 连线程都不该开
            page._install_profile = original_install
            profiles = page._runtime_profiles()
            if profiles:
                key = str(profiles[0].get("id") or "")
                page._installing[key] = {"name": "自检", "start": 0.0, "lines": [], "state": "running"}
                started: list[str] = []
                page._run_async = lambda work, on_done=None, on_finally=None, on_failed=None: started.append("x")
                try:
                    page._install_profile({"id": key, "name": "自检", "packages": []}, auto=True)
                finally:
                    page._installing.pop(key, None)
                if started:
                    problems.append("同一个运行环境已经在装，不该再开一条安装线程")
        finally:
            page_module.CompleteRuntimesDialog = original_dialog
            page._complete_plan = original_plan
            page._install_profile = original_install
            page._run_async = original_async
            page._installing.clear()
    finally:
        if page is not None:
            page._installing.clear()
            page._install_ctl.clear()
            page._install_timer.stop()
        dispose_window(window)
    assert not problems, "运行环境补全调度：" + "；".join(problems[:12])


@check("model_local_wheels_source", "pages")
def model_local_wheels_source(case: Case) -> None:
    """本地 whl 安装：界面与日志里的「来源」写的是那些本地文件，不能拿 pip 源冒充。"""
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    _fixture, window = build_window(case)
    problems: list[str] = []
    page = None
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"
        profiles = page._runtime_profiles()
        if not profiles:
            problems.append("没有读到运行环境清单")
        else:
            profile = profiles[0]
            key = str(profile.get("id") or "")
            wheel = "piper-tts-1.2-cp311-win_amd64.whl"
            stages: list[str] = []
            started: list[str] = []
            original_stage = page._console_stage
            original_async = page._run_async
            original_confirm = page.confirm
            original_timer = page._install_timer.isActive()
            try:
                page._console_stage = lambda title, text="": stages.append(f"{title}｜{text}")
                page._run_async = lambda work, on_done=None, on_finally=None, on_failed=None: started.append("x")
                page.confirm = lambda *args, **kwargs: True
                page._install_profile(profile, wheels=[wheel])
                joined = " ".join(stages)
                if wheel not in joined or "本地文件" not in joined:
                    problems.append(f"本地 whl 安装没把来源写成那些文件：{joined}")
                if "pip 源" in joined:
                    problems.append(f"本地 whl 安装不该说来源是 pip 源：{joined}")
                row = page._runtime_rows.get(key) or {}
                label = row.get("install_state")
                tip = label.toolTip() if label is not None else ""
                if wheel not in tip or "本地文件" not in tip or "pip 源" in tip:
                    problems.append(f"安装行的悬停提示没写成「本地文件」：{tip}")
            finally:
                page._console_stage = original_stage
                page._run_async = original_async
                page.confirm = original_confirm
                page._installing.clear()
                page._install_ctl.clear()
                if not original_timer:
                    page._install_timer.stop()
                page._fill_runtime()
    finally:
        if page is not None:
            page._installing.clear()
            page._install_ctl.clear()
            page._install_timer.stop()
        dispose_window(window)
    assert not problems, "本地 whl 安装来源：" + "；".join(problems[:12])


@check("model_pip_mirror_settings", "services")
def model_pip_mirror_settings(case: Case) -> None:
    """安装源：默认官方、预设镜像地址正确、自定义源生效、能存回设置。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import settings as settings_module

    problems: list[str] = []
    if tuple(key for key, _label, _url in settings_module.PIP_MIRRORS) != settings_module.PIP_MIRROR_KEYS:
        problems.append(f"安装源 key 列表不一致：{settings_module.PIP_MIRROR_KEYS}")
    labels = [label for _key, label, _url in settings_module.PIP_MIRRORS]
    for wanted in ("官方 PyPI（默认）", "清华 TUNA", "阿里云", "中科大", "自定义地址"):
        if wanted not in labels:
            problems.append(f"安装源少了「{wanted}」：{labels}")
    if settings_module.DEFAULT_PIP_MIRROR != "official":
        problems.append(f"默认安装源应当是官方 PyPI：{settings_module.DEFAULT_PIP_MIRROR}")
    if settings_module.pip_mirror_url("official") != "":
        problems.append("选官方源时应当交给 pip 用默认地址（不传 --index-url）")
    if settings_module.pip_mirror_url("tuna") != "https://pypi.tuna.tsinghua.edu.cn/simple":
        problems.append(f"清华源地址不对：{settings_module.pip_mirror_url('tuna')}")
    if settings_module.pip_mirror_url("custom", "  https://pypi.example.com/simple ") != "https://pypi.example.com/simple":
        problems.append("自定义源没有去首尾空白或没带上")
    if settings_module.pip_mirror_url("no-such-key") != "":
        problems.append("未知安装源该回落到 pip 默认地址")

    settings = settings_module.load_settings()
    if settings.pip_mirror != "official" or settings.index_url:
        problems.append(f"默认安装源不对：{settings.pip_mirror} / {settings.index_url!r}")
    settings.pip_mirror = "ustc"
    if settings.index_url != "https://pypi.mirrors.ustc.edu.cn/simple/":
        problems.append(f"选中科大源 index_url 不对：{settings.index_url}")
    settings.pip_mirror = "no-such-key"
    if settings.pip_mirror != "official":
        problems.append(f"非法安装源该回落到官方：{settings.pip_mirror}")
    settings.pip_mirror = "custom"
    settings.pip_mirror_custom = "https://pypi.example.com/simple"
    if settings.index_url != "https://pypi.example.com/simple":
        problems.append(f"自定义源 index_url 不对：{settings.index_url}")
    if not settings.save():
        problems.append("安装源 save() 返回失败")
    again = settings_module.load_settings()
    if again.pip_mirror != "custom" or again.pip_mirror_custom != "https://pypi.example.com/simple":
        problems.append(f"安装源没有存回：{again.pip_mirror} / {again.pip_mirror_custom}")
    if again.index_url != "https://pypi.example.com/simple":
        problems.append(f"读回的 index_url 不对：{again.index_url}")
    assert not problems, "安装源设置：" + "；".join(problems[:12])


@check("model_runtime_stop_and_marker", "services")
def model_runtime_stop_and_marker(case: Case) -> None:
    """运行环境：半成品不算已安装（认完成标记）、暂停与取消各有自己的异常、discard 清干净。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import runtime as runtime_module

    problems: list[str] = []
    if runtime_module.installed("selfcheck"):
        problems.append("没装过的运行环境不该算已安装")
    marker = runtime_module.marker_path("selfcheck")
    if marker.name != "installed.json" or marker.parent != runtime_module.venv_dir("selfcheck").parent:
        problems.append(f"完成标记的位置不对：{marker}")

    profile = runtime_module.RuntimeProfile(id="selfcheck", name="自检环境", packages=("nonexistent-package-xyz",))

    def stop(*, cancel: bool = False) -> object:
        """跑一次 ensure：开关提前置真，应当立刻被叫停。"""
        call = {"should_cancel": lambda: True} if cancel else {"should_pause": lambda: True}
        try:
            runtime_module.ensure(profile, **call)
        except Exception as exc:  # noqa: BLE001 - 这里就是要看抛的是什么
            return exc
        return None

    cancelled = stop(cancel=True)
    if not isinstance(cancelled, runtime_module.RuntimeStopped) or cancelled.paused:
        problems.append(f"取消应当抛 RuntimeStopped(paused=False)：{cancelled!r}")
    paused = stop()
    if not isinstance(paused, runtime_module.RuntimeStopped) or not paused.paused:
        problems.append(f"暂停应当抛 RuntimeStopped(paused=True)：{paused!r}")
    if runtime_module.venv_dir("selfcheck").exists():
        problems.append("被叫停的安装不该留下运行环境目录")

    venv = runtime_module.venv_dir("selfcheck")
    venv.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")
    if not runtime_module.discard("selfcheck"):
        problems.append("discard 没把半成品运行环境删掉")
    if venv.parent.exists():
        problems.append(f"discard 之后运行环境目录还在：{venv.parent}")
    if not runtime_module.discard("selfcheck"):
        problems.append("discard 对已经不存在的运行环境也该返回 True")
    assert not problems, "运行环境暂停/取消与完成标记：" + "；".join(problems[:12])


@check("model_runtime_profile_twins", "services")
def model_runtime_profile_twins(case: Case) -> None:
    """CPU / GPU 双胞胎环境：模型写 `llama-cpp`、盘上只有 `llama-cpp-gpu` 时也要能跑。"""
    install_builtin_plugins()
    from dm_plugin.lib.model import runtime as runtime_module
    from dm_plugin.lib.model.adapters.worker import WorkerAdapter
    import shutil
    import sys
    import types

    problems: list[str] = []
    if runtime_module.twin_ids("llama-cpp") != ("llama-cpp-gpu",):
        problems.append(f"CPU 版的双胞胎该是 -gpu：{runtime_module.twin_ids('llama-cpp')}")
    if runtime_module.twin_ids("llama-cpp-gpu") != ("llama-cpp",):
        problems.append(f"GPU 版的双胞胎该是 CPU 版：{runtime_module.twin_ids('llama-cpp-gpu')}")
    if runtime_module.twin_ids("") != ():
        problems.append("空 id 不该编出一个双胞胎")
    if runtime_module.resolve_id("") != "":
        problems.append("空 id 该原样回空串")
    if runtime_module.resolve_id("selfcheck-missing") != "selfcheck-missing":
        problems.append("两边都没装时该原样回请求的 id")

    gpu_python = runtime_module.python_path("llama-cpp-gpu")
    gpu_python.parent.mkdir(parents=True, exist_ok=True)
    gpu_python.write_bytes(b"")
    try:
        if runtime_module.resolve_id("llama-cpp") != "llama-cpp-gpu":
            problems.append("只装了 GPU 版时，模型声明 CPU 版也该解析到 GPU 版")
        if runtime_module.resolve_id("llama-cpp-gpu") != "llama-cpp-gpu":
            problems.append("精确的那套装了就该用精确的")
        if "llama-cpp-gpu" not in runtime_module.installed_ids():
            problems.append(f"installed_ids 该列出解释器在盘上的 profile：{runtime_module.installed_ids()}")
        record = types.SimpleNamespace(
            id="local/selfcheck",
            runtime={"adapter": "worker", "backend": "fake", "profile": "llama-cpp", "params": {}},
            files=(),
        )
        settings = types.SimpleNamespace(allow_system_env=False)
        adapter = WorkerAdapter(record, settings)
        if adapter._pick_python() != gpu_python:
            problems.append("worker 适配器该认 GPU 双胞胎环境（模型写 CPU 版也能跑）")
        adapter = WorkerAdapter(record, types.SimpleNamespace(allow_system_env=True))
        if adapter._pick_python() != Path(sys.executable):
            problems.append("勾了「允许装进程序环境」时该直接用程序解释器（与运行环境区的显示一致）")
        record.runtime["profile"] = "selfcheck-missing"
        adapter = WorkerAdapter(record, settings)
        try:
            adapter._pick_python()
        except Exception as exc:  # noqa: BLE001 - 要看文案里有没有列出手头装了什么
            text = str(exc)
            if "尚未安装" not in text or "llama-cpp-gpu" not in text:
                problems.append(f"没装时报错该说清楚并列出已经装了什么：{text}")
        else:
            problems.append("profile 没装、又不许用程序环境时该报错")
    finally:
        shutil.rmtree(runtime_module.venv_dir("llama-cpp-gpu").parent, ignore_errors=True)
    assert not problems, "运行环境双胞胎解析：" + "；".join(problems[:12])


@check("model_download_facade", "services")
def model_download_facade(case: Case) -> None:
    """模型下载器只是程序本体队列的门面：`.part` 贴着目标文件、`active()` 给条数、
    `shutdown()` 落盘但**不关**共享队列、`model_id` 记在插件自己这边。"""
    install_builtin_plugins()
    from app.core import download as core
    from app.core.journals import JournalStore
    from dm_plugin.lib.model.download import downloader

    problems: list[str] = []
    target = Path(case.root) / "model.gguf"
    part = downloader.part_path(target)
    if part != target.with_name(target.name + ".part"):
        problems.append(f"`.part` 该贴着目标文件：{part}")
    if part.parent != target.parent:
        problems.append("`.part` 与目标文件不在同一个目录")

    queue = core.DownloadManager(
        core.DownloadOptions(concurrent=1, retries=0, timeout=1.0),
        index=JournalStore(core.DOWNLOAD_KIND, root=Path(case.root) / "journals"),
        name="selfcheck",
    )
    manager = downloader.DownloadManager(queue=queue)
    # 自检不联网：谁也别被取走，任务就停在「排队中」看形状
    original_take = core.DownloadManager._take_locked
    core.DownloadManager._take_locked = lambda self: None
    try:
        job = manager.enqueue("local/selfcheck", ["http://127.0.0.1:9/model.gguf"], target)
        if job.target != target:
            problems.append(f"任务视图该给出 Path 目标：{job.target!r}")
        if job.model_id != "local/selfcheck":
            problems.append(f"任务视图该带上 model_id：{job.model_id!r}")
        if job.urls != ("http://127.0.0.1:9/model.gguf",):
            problems.append(f"任务视图该给出地址：{job.urls!r}")
        if job.label != "model.gguf":
            problems.append(f"没给标签时该用文件名：{job.label!r}")
        if not isinstance(manager.active(), int):
            problems.append("active() 该给条数，不是列表")
        if len(manager.jobs()) != 1:
            problems.append(f"队列里该只有一条任务：{len(manager.jobs())}")
        if manager.forget(job.id):
            problems.append("还没结束的任务不该能被摘掉")
        manager.pause(job.id)
        manager.shutdown()
        if not manager.jobs():
            problems.append("shutdown 只该落盘，不该关掉共享队列")
        if manager.active() != 0:
            problems.append("暂停之后不该还有任务在跑")
    finally:
        core.DownloadManager._take_locked = original_take
        try:
            manager.cancel_all()
        finally:
            queue.shutdown(wait=1.0)
    assert not problems, "模型下载器门面：" + "；".join(problems[:12])


@check("model_crash_recovery", "services")
def model_crash_recovery(case: Case) -> None:
    """异常退出：安装留「没装完」标记、下载留断点信息，重开后接着装 / 接着下。"""
    install_builtin_plugins()
    from app.core import download as core
    from app.core.journals import JournalStore
    from dm_plugin.lib.model import runtime as runtime_module
    from dm_plugin.lib.model.download import downloader

    problems: list[str] = []
    profile_id = "selfcheck-crash"
    pending = runtime_module.pending_path(profile_id)
    if pending.parent != runtime_module.venv_dir(profile_id).parent:
        problems.append(f"安装标记的位置不对：{pending}")
    if runtime_module.interrupted(profile_id):
        problems.append("还没开始装就说「上次安装中断」")

    python = runtime_module.python_path(profile_id)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    profile = runtime_module.RuntimeProfile(id=profile_id, name="自检环境", packages=("nonexistent-package-xyz",))
    seen: list[bool] = []
    # 安装引擎已经搬进程序本体，插件这一层只是转发：要拦住「真去跑 pip」，得替换
    # 程序本体引擎里的 `stream`（插件那个 `_stream` 名字现在只是转发的壳，换掉它没用）。
    from app.core.pip import engine as pip_engine

    original_stream = pip_engine.stream

    def fake_stream(command, *, log, on_line=None, should_cancel=None, should_pause=None, control=None):
        seen.append(runtime_module.interrupted(profile_id))
        return 0

    try:
        pip_engine.stream = fake_stream
        runtime_module.ensure(profile, on_line=lambda _line: None)
    finally:
        pip_engine.stream = original_stream
    if seen != [True]:
        problems.append(f"安装进行中应当认出「上次安装中断」：{seen}")
    if runtime_module.interrupted(profile_id):
        problems.append("装好之后不该再算「安装中断」")
    if not runtime_module.marker_path(profile_id).exists():
        problems.append("装好之后应当有完成标记")
    runtime_module.discard(profile_id)

    target = Path(case.root) / "model.gguf"
    part = downloader.part_path(target)
    if part != target.with_name(target.name + ".part"):
        problems.append(f"`.part` 该贴着目标文件：{part}")
    store = JournalStore(core.DOWNLOAD_KIND, root=Path(case.root) / "journals")
    options = core.DownloadOptions(concurrent=1, retries=0, timeout=1.0)
    # 自检不联网：谁也别被取走，任务就一直停在「排队中」
    original_take = core.DownloadManager._take_locked
    core.DownloadManager._take_locked = lambda self: None
    first_queue = core.DownloadManager(options, index=store, name="selfcheck")
    second_queue = None
    try:
        first = downloader.DownloadManager(queue=first_queue)
        first.enqueue("local/selfcheck", ["http://127.0.0.1:9/model.gguf"], target)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"x" * 32)
        first.shutdown()  # 插件退场：只落盘
        first_queue.shutdown(wait=1.0)  # 程序本体退出：任务留成「已退出，下次启动继续」
        if not part.exists():
            problems.append("退出时不该把没下完的 `.part` 删掉")

        # 重开：新的队列 + 新的门面，读同一份任务索引与同一份插件记录
        second_queue = core.DownloadManager(options, index=store, name="selfcheck")
        second = downloader.DownloadManager(queue=second_queue)
        restored = second.resume_leftovers()
        picked = [item for item in second.jobs() if Path(item.target) == target]
        if restored < 1 or len(picked) != 1:
            problems.append(f"半成品该被接回队列且只接一次：restored={restored} picked={len(picked)}")
        elif picked[0].model_id != "local/selfcheck":
            problems.append(f"接回来的任务该认得自己属于哪个模型：{picked[0].model_id!r}")
        elif picked[0].label != "model.gguf":
            problems.append(f"接回来的任务该记得自己的标签：{picked[0].label!r}")
        elif "model.gguf" not in str(picked[0].urls[0]):
            problems.append(f"接回来的任务该记得下载地址：{picked[0].urls}")
    finally:
        core.DownloadManager._take_locked = original_take
        if second_queue is not None:
            second_queue.shutdown(wait=1.0)
        first_queue.shutdown(wait=1.0)
        part.unlink(missing_ok=True)
    assert not problems, "异常退出后的恢复：" + "；".join(problems[:12])


@check("model_page_mirror_and_delete_dialog", "pages")
def model_page_mirror_and_delete_dialog(case: Case) -> None:
    """安装源下拉（只有自定义才可编辑）与删除弹窗（默认只删登记，外部目录连勾都点不动）。"""
    import types

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    from dm_plugin.lib.model import settings as settings_module
    from dm_plugin.lib.model.paths import local_dir
    from dm_plugin.lib.model.ui.dialogs import DeleteModelDialog

    _fixture, window = build_window(case)
    problems: list[str] = []
    page = None
    dialog = None
    blank = None
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"

        labels = [page.pip_mirror_box.itemText(index) for index in range(page.pip_mirror_box.count())]
        for wanted in ("官方 PyPI（默认）", "清华 TUNA", "阿里云", "中科大", "自定义地址"):
            if wanted not in labels:
                problems.append(f"安装源下拉少了「{wanted}」：{labels}")
        page._select_pip_mirror("tuna")
        page._sync_pip_mirror_row()
        if page.pip_mirror_custom.isEnabled():
            problems.append("选了预设镜像后，自定义地址输入框应该是灰的")
        if "tuna.tsinghua" not in page._pip_mirror_hint.text():
            problems.append(f"安装源提示没写出实际地址：{page._pip_mirror_hint.text()}")
        page._select_pip_mirror("custom")
        page.pip_mirror_custom.setText("https://pypi.example.com/simple")
        page._save_pip_mirror()
        if not page.pip_mirror_custom.isEnabled():
            problems.append("选了自定义后，地址输入框应该能编辑")
        saved = settings_module.load_settings()
        if saved.pip_mirror != "custom" or saved.index_url != "https://pypi.example.com/simple":
            problems.append(f"安装源没有落盘：{saved.pip_mirror} / {saved.index_url}")

        folder = case.root / "weights" / "demo"
        dialog = DeleteModelDialog(window, name="演示模型", folder=folder)
        if hasattr(dialog, "files_box") or hasattr(dialog, "delete_files"):
            problems.append("删除弹窗不该再有「同时删除权重文件」的勾选：权重一律不跟着登记删")
        if folder.name not in dialog.hint.text():
            problems.append(f"删除弹窗没把「权重目录会保留」写清楚：{dialog.hint.text()}")
        if "清理未使用的权重" not in dialog.hint.text():
            problems.append("删除弹窗该告诉用户去哪儿清理没用到的权重")

        blank = DeleteModelDialog(window, name="外部模型", folder=None, note="外部模型只有登记信息。")
        if "外部模型只有登记信息" not in blank.detail.text():
            problems.append(f"没有权重目录时该显示备注：{blank.detail.text()}")

        def record(**kwargs: object) -> object:
            return types.SimpleNamespace(**kwargs)

        if page._deletable_dir(record(is_local=False, id="ext/1", source={})) is not None:
            problems.append("外部模型不该有可删的权重目录")
        if page._deletable_dir(record(is_local=True, id="local/scanned", source={"path": "D:/user/models"})) is not None:
            problems.append("扫描登记进来的模型绝不能被当成可删目录")
        target = page._deletable_dir(record(is_local=True, id="local/demo", source={}))
        if target is None or Path(target) != local_dir("local/demo"):
            problems.append(f"本地下下来的模型该能删自己的目录：{target}")
    finally:
        for widget in (dialog, blank):
            if widget is not None:
                _dispose(widget)
        if page is not None:
            page._install_timer.stop()
        dispose_window(window)
    assert not problems, "安装源与删除弹窗：" + "；".join(problems[:12])


@check("model_weight_replace_and_cleanup", "pages")
def model_weight_replace_and_cleanup(case: Case) -> None:
    """换权重（类型校验 / 清空 / 自动匹配模板）与「清理未使用的权重」候选清单。"""
    import shutil
    import types

    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    from .harness import build_window, dispose_window, ensure_app

    ensure_app()
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    install_builtin_plugins()

    from dm_plugin.lib.model.paths import local_dir, local_root
    from dm_plugin.lib.model.ui.dialogs import CleanupWeightsDialog, ReplaceWeightsDialog

    _fixture, window = build_window(case)
    problems: list[str] = []
    page = None
    replace = None
    cleanup = None
    ghost = None
    try:
        page = window._plugin_pages.get(_PAGE_KEY)
        assert page is not None, f"模型插件应把管理页注册成插件页面 {_PAGE_KEY}"

        def record(**kwargs: object) -> object:
            return types.SimpleNamespace(**kwargs)

        gguf = record(runtime={"backend": "llama_cpp"}, source={}, files=("a.gguf",))
        fits, why = page._weights_fit(gguf, ["D:/x/model.gguf", "D:/x/config.json"])
        if not fits:
            problems.append(f"gguf 权重该判成匹配：{why}")
        fits, why = page._weights_fit(gguf, ["D:/x/model.safetensors"])
        if fits:
            problems.append("llama_cpp 收到 safetensors 该判成不匹配")
        elif "safetensors" not in why:
            problems.append(f"不匹配的原因该点出文件名：{why}")
        if not isinstance(page._template_for_suffixes({".safetensors"}), dict):
            problems.append("safetensors 该能从本地模板里自动匹配到一条（transformers / sentence_transformers 那几档）")

        replace = ReplaceWeightsDialog(window, name="演示模型", files=("a.gguf",), folder=local_dir("local/demo"))
        if replace.choice() != ("cancel", []):
            problems.append(f"没选文件时该什么都不做：{replace.choice()}")
        replace.download_radio.setChecked(True)
        if replace.choice()[0] != "download":
            problems.append(f"选了重新下载该回 download：{replace.choice()}")
        replace.files_radio.setChecked(True)
        replace._paths = ["D:/x/b.gguf"]
        if replace.choice() != ("files", ["D:/x/b.gguf"]):
            problems.append(f"选好文件该回 files + 路径：{replace.choice()}")

        cleanup = CleanupWeightsDialog(
            window,
            entries=[{"name": "ghost", "path": case.root / "ghost", "size": 10, "size_text": "10 B", "kind": "权重目录"}],
        )
        if cleanup.selected():
            problems.append("清理弹窗默认不该勾任何一项")
        cleanup._set_all(True)
        if [item["name"] for item in cleanup.selected()] != ["ghost"]:
            problems.append(f"全选之后该把 ghost 算上：{cleanup.selected()}")

        # 候选清单：没人用到的才列出来；登记在用的绝不列
        ghost = local_root() / "ghost-weights"
        (ghost / "sub").mkdir(parents=True, exist_ok=True)
        (ghost / "sub" / "a.gguf").write_bytes(b"x" * 32)
        used = page._api.add_local("in-use", capabilities=(), description="自检")
        used_folder = local_dir(used.id)
        (used_folder / "keep.bin").write_bytes(b"y")
        names = [item["name"] for item in page._cleanup_candidates()]
        if ghost.name not in names:
            problems.append(f"没人用的权重目录该出现在清理清单里：{names}")
        if used_folder.name in names:
            problems.append(f"登记在用的权重目录不该被当成垃圾：{names}")

        empty = local_root() / "empty-weights"
        empty.mkdir(parents=True, exist_ok=True)
        names = [item["name"] for item in page._cleanup_candidates()]
        if "empty-weights" not in names:
            problems.append(f"空的权重目录也该列出来（让用户能清掉），实际只列了 {names}")
        empty.rmdir()
        if local_dir("") != local_root() or (local_root() / "model").exists():
            problems.append("空 id 的 local_dir 该直接回权重根目录，不再凭空造 local/model")

        # 清空权重：自己下下来的目录可以清；「扫描目录」登记的用户目录绝不能碰
        dropped = page._api.add_local("dropme", capabilities=(), description="自检")
        folder = local_dir(dropped.id)
        (folder / "old.gguf").write_bytes(b"z")
        dropped.sync_files()
        page._api.update(dropped)
        page._drop_weights(dropped)
        if (folder / "old.gguf").exists():
            problems.append("换权重前清空该把旧文件删掉")

        scanned = case.root / "user-weights"
        scanned.mkdir(parents=True, exist_ok=True)
        (scanned / "mine.gguf").write_bytes(b"z")
        scanned_record = page._api.add_local("scanned", capabilities=(), description="自检", source={"path": str(scanned)})
        scanned_record.sync_files()
        page._api.update(scanned_record)
        page._drop_weights(scanned_record)
        if not (scanned / "mine.gguf").exists():
            problems.append("「扫描目录」登记的目录绝不能清：那是用户自己的文件")
    finally:
        if ghost is not None:
            shutil.rmtree(ghost, ignore_errors=True)
        for widget in (replace, cleanup):
            if widget is not None:
                _dispose(widget)
        if page is not None:
            page._install_timer.stop()
        dispose_window(window)
    assert not problems, "换权重与清理未使用权重：" + "；".join(problems[:12])
