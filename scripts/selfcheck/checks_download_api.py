"""L2 服务层检查：下载器 / pip 安装器对插件开放的扩展接口。

不联网：下载走一份「把固定字节写进 .part」的桩替换 `DownloadManager._fetch`，
pip 安装用空依赖清单（引擎碰到空清单直接返回解释器，不动 pip）。

接口由 `src/main.py` 在启动时 bootstrap，自检不跑 `main()`，所以这里自己注册一次
（结束后还原成原来的提供者，别影响别的检查）。
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

from .harness import ROOT, Case, check, dispose_window, ensure_app, install_builtin_plugins

PAYLOAD = b"selfcheck-download-api-payload"
#: UI 工具库必须转发出来的下载器 / pip 安装器视图工具。
UI_TOOL_NAMES = (
    "AddDownloadDialog",
    "DownloadListView",
    "PipTaskView",
    "add_download_dialog",
    "download_list",
    "pip_task_list",
)


def _expect(problems: list[str], ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


class _FakeDialog:
    """替掉新建下载对话框：直接给出目标和地址，别真的弹窗。"""

    accepted = False
    chosen: list[str] = []
    directory = ""

    def __init__(self, parent=None, **kwargs) -> None:
        self.kwargs = kwargs
        type(self).directory = str(kwargs.get("directory", ""))

    def exec(self) -> bool:  # noqa: A003 - 对齐 Qt
        return type(self).accepted

    def urls(self) -> list[str]:
        return list(type(self).chosen)

    def target(self):
        from pathlib import Path

        return Path(type(self).directory) / "a.bin"

    def deleteLater(self) -> None:  # noqa: N802 - Qt 命名
        return None


def _bootstrap(name: str, provider):
    """注册接口并返回「还原用」的旧提供者。"""
    from app.core.plugins.extensions import extension_registry
    from app.services.plugin_service import plugin_service

    previous = (extension_registry.provider(name), extension_registry.provider_plugin(name))
    plugin_service.bootstrap(name, provider)
    return previous


def _restore(name: str, previous) -> None:
    from app.core.plugins.extensions import extension_registry

    provider, owner = previous
    extension_registry.provide(name, provider, owner or "")


@check("download_extension_api", "services")
def download_extension_api(case: Case) -> None:
    """download.open：注册后插件能发起、查询、叫停下载，地址为空与用户取消都不落任务。"""
    from unittest import mock

    from app.sdk import download as sdk
    from app.core.config import download_dir
    from app.core.download import service as download_service
    from app.core.download.engine import DownloadManager, STATE_DONE
    from app.sdk.errors import SdkError
    from app.services import download_api

    problems: list[str] = []
    _expect(problems, sdk.available() is False, "没有程序本体时 available() 该是 False")
    try:
        sdk.jobs()
    except SdkError:
        pass
    else:
        problems.append("没有程序本体时取下载列表该抛 SdkError")

    previous = _bootstrap(sdk.DOWNLOAD_EXTENSION, download_api.api())
    target = Path(case.root) / "downloads" / "api.bin"
    target.parent.mkdir(parents=True, exist_ok=True)

    def fake_fetch(self, job, url, part):
        part.write_bytes(PAYLOAD)
        job.done_bytes = len(PAYLOAD)
        job.total_bytes = len(PAYLOAD)

    try:
        _expect(problems, sdk.available() is True, "注册后 available() 该是 True")
        _expect(
            problems,
            sdk.default_dir() == str(download_dir()),
            f"默认下载目录该跟配置同步：{sdk.default_dir()} != {download_dir()}",
        )
        _expect(problems, sdk.jobs() == (), f"隔离环境初始不该有下载任务：{sdk.jobs()}")
        _expect(problems, sdk.find("download-nothing") is None, "取不存在的任务该返回 None")
        for name in ("pause", "resume", "cancel", "retry", "forget"):
            if getattr(sdk, name)("download-nothing"):
                problems.append(f"对不存在的任务调 {name} 该返回 False")
        for name in ("pause_all", "resume_all", "cancel_all", "clear_finished"):
            if getattr(sdk, name)() != 0:
                problems.append(f"空队列上 {name} 该返回 0")

        try:
            sdk.enqueue((), target)
        except SdkError:
            pass
        else:
            problems.append("地址为空时 enqueue 该抛 SdkError")

        from app.ui.components import download_view as view_module

        with mock.patch.object(view_module, "AddDownloadDialog", _FakeDialog):
            _FakeDialog.accepted = False
            _FakeDialog.chosen = []
            _expect(
                problems,
                sdk.request(("https://example.com/a.bin",)) is None,
                "用户取消确认框时 request 该返回 None",
            )
            _FakeDialog.accepted = True
            _FakeDialog.chosen = []
            asked = sdk.request(("https://example.com/a.bin",), target=str(target))
            _expect(problems, asked is None, "确认框里地址被清空时 request 该返回 None")

        with mock.patch.object(view_module, "AddDownloadDialog", _FakeDialog), mock.patch.object(
            DownloadManager, "_fetch", fake_fetch
        ):
            _FakeDialog.accepted = True
            _FakeDialog.chosen = ["https://example.com/asked.bin"]
            asked_id = sdk.request(("https://example.com/a.bin",), target=str(target), name="asked.bin")
            _expect(problems, asked_id is not None, "用户确认之后该真的排上任务")
            _expect(
                problems,
                asked_id is None
                or _wait_until(lambda: (sdk.find(asked_id) or None) and sdk.find(asked_id).finished),
                "确认后发起的下载没有在超时内结束",
            )
            if asked_id is not None:
                _expect(problems, sdk.forget(asked_id) is True, "确认后发起的任务该能忘掉")

            job_id = sdk.enqueue(
                ("https://example.com/api.bin",), target, label="自检下载"
            )
            _expect(
                problems,
                _wait_until(lambda: (sdk.find(job_id) or None) and sdk.find(job_id).finished),
                f"下载没有在超时内结束：{(sdk.find(job_id) or None) and sdk.find(job_id).state}",
            )
            ref = sdk.find(job_id)
            _expect(problems, ref is not None and ref.state == STATE_DONE, f"下载状态不对：{ref and ref.state}")
            _expect(problems, ref is not None and ref.label == "自检下载", f"label 没带上：{ref and ref.label}")
            _expect(problems, target.is_file(), f"下载没有落盘：{target}")
            if ref is not None:
                _expect(problems, ref.name == "自检下载", f"有标签时 ref.name 该用标签：{ref.name}")
                _expect(problems, ref.finished is True, "结束的任务 finished 该是 True")
                _expect(problems, sdk.forget(job_id) is True, "终态任务该能忘掉")
                _expect(problems, sdk.find(job_id) is None, "忘掉之后不该还能查到")
                _expect(problems, sdk.forget(job_id) is False, "忘掉两次该返回 False")
        _expect(
            problems,
            sdk.jobs() == () or all(item.finished for item in sdk.jobs()),
            f"隔离环境跑完不该留下没结束的任务：{sdk.jobs()}",
        )
    finally:
        download_service.shutdown(wait=2.0)
        _restore(sdk.DOWNLOAD_EXTENSION, previous)

    assert not problems, "download.open 接口：" + "；".join(problems[:12])


@check("pip_extension_api", "services")
def pip_extension_api(case: Case) -> None:
    """pip.install：注册后插件能发起、看着、叫停安装；空依赖清单不动 pip，用户拒绝不落任务。"""
    from app.sdk import pip as sdk
    from app.core.pip import tasks as tasks_module
    from app.sdk.errors import SdkError
    from app.services import pip_api

    problems: list[str] = []
    _expect(problems, sdk.available() is False, "没有程序本体时 available() 该是 False")
    try:
        sdk.jobs()
    except SdkError:
        pass
    else:
        problems.append("没有程序本体时取安装列表该抛 SdkError")

    previous = _bootstrap(sdk.PIP_EXTENSION, pip_api.api())
    log = Path(case.root) / "pip" / "selfcheck.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    api = pip_api.api()
    try:
        _expect(problems, sdk.available() is True, "注册后 available() 该是 True")
        _expect(problems, api.current_tasks() is None, "没用过之前不该建出任务表")
        _expect(problems, api.is_running() is False, "没用过之前 is_running() 该是 False")
        _expect(problems, sdk.jobs() == (), f"隔离环境初始不该有安装任务：{sdk.jobs()}")
        _expect(problems, sdk.find("pip-nothing") is None, "取不存在的安装任务该返回 None")
        for name in ("pause", "resume", "cancel", "forget"):
            if getattr(sdk, name)("pip-nothing"):
                problems.append(f"对不存在的任务调 {name} 该返回 False")
        _expect(problems, sdk.clear_finished() == 0, "空任务表上 clear_finished 该返回 0")

        _expect(problems, sdk.venv_python("E:/venv") == Path("E:/venv/Scripts/python.exe"), "venv_python 路径不对")
        _expect(problems, sdk.import_name("Pillow") == "PIL", f"import_name(Pillow) = {sdk.import_name('Pillow')}")
        _expect(problems, sdk.check_wheels(()) == [], "空 whl 清单不该报问题")
        missing = sdk.missing_program_packages(("definitely-not-installed-selfcheck-pkg",))
        _expect(
            problems,
            missing == ("definitely-not-installed-selfcheck-pkg",),
            f"没装的依赖该被认出来：{missing}",
        )

        ref = sdk.install(sys.executable, log=str(log), packages=(), title="自检安装", confirm=False)
        _expect(problems, ref is not None, "空依赖清单也该建出一条安装任务")
        if ref is not None:
            _expect(problems, ref.state in ("pending", "running", "done"), f"刚发起的任务状态不对：{ref.state}")
            _expect(problems, sdk.wait(ref.id, timeout=20.0) is True, "安装任务没有在超时内结束")
            ref = sdk.find(ref.id)
            _expect(problems, ref is not None and ref.state == "done", f"空依赖清单该直接装好：{ref and ref.state}")
        _expect(problems, api.is_running() is True, "建过任务表之后 is_running() 该是 True")
        _expect(problems, sdk.clear_finished() >= 1, "跑完的任务该能被清理")

        asked: list[dict] = []

        def fake_ask(*args, **kwargs):
            asked.append({**kwargs, "args": args})
            return False

        original_ask = api._ask
        api._ask = fake_ask  # type: ignore[method-assign]
        try:
            rejected = api.install(sys.executable, log=str(log), packages=("demo",), title="装依赖")
            _expect(problems, rejected is None, "用户拒绝时 install 该返回 None")
            _expect(problems, len(asked) == 1, f"确认框该弹一次：{asked}")
            if asked:
                _expect(problems, "demo" in str(asked[0]), f"确认框正文该带上要装的依赖：{asked[0]}")
        finally:
            api._ask = original_ask  # type: ignore[method-assign]
        _expect(
            problems,
            not any(item.state == "pending" for item in sdk.jobs()),
            f"用户拒绝之后不该留下排队中的任务：{sdk.jobs()}",
        )
    finally:
        pip_api.shutdown(wait=2.0)
        _restore(sdk.PIP_EXTENSION, previous)

    assert not problems, "pip.install 接口：" + "；".join(problems[:12])


@check("task_view_via_tool_library", "pages")
def task_view_via_tool_library(case: Case) -> None:
    """UI 工具库要转发下载器 / pip 安装器的视图工具：名字齐全、能直接搭出控件。"""
    from app.sdk import ui as sdk_ui

    problems: list[str] = []
    install_builtin_plugins()
    import dm_plugin.builtin.lib.ui.ui_tools as tools

    ensure_app()
    for name in UI_TOOL_NAMES:
        if not hasattr(tools, name):
            problems.append(f"UI 工具库没有 {name}")
        elif name not in tools.__all__:
            problems.append(f"UI 工具库的 __all__ 漏了 {name}")
        if not hasattr(sdk_ui, name):
            problems.append(f"SDK 界面模块没有 {name}")

    from PyQt6.QtWidgets import QWidget

    host = QWidget()
    host.resize(800, 600)
    try:
        listing = tools.download_list(parent=host)
        if listing.table.columnCount() != 8:
            problems.append(f"下载列表该有 8 列：{listing.table.columnCount()}")
        tasks = tools.pip_task_list(parent=host)
        if tasks.table.columnCount() != 4:
            problems.append(f"安装列表该有 4 列：{tasks.table.columnCount()}")
        dialog = tools.add_download_dialog(
            parent=host,
            title="自检下载",
            urls=("https://example.com/a.bin", "  "),
            hint="自检",
        )
        if dialog.urls() != ["https://example.com/a.bin"]:
            problems.append(f"对话框该吃掉空行：{dialog.urls()}")
        if dialog.titleLabel.text() != "自检下载":
            problems.append(f"对话框标题没跟上：{dialog.titleLabel.text()}")
        dispose_window(dialog)
        dispose_window(listing)
        dispose_window(tasks)
    finally:
        dispose_window(host)

    assert not problems, "下载器 / 安装器的视图工具：" + "；".join(problems[:12])


@check("task_view_sources_use_library", "services")
def task_view_sources_use_library(case: Case) -> None:
    """内置页只能用 SDK 转发的视图工具，不许自己 import 插件侧的工具库。"""
    blocked = "dm_plugin.builtin.lib.ui"
    problems: list[str] = []
    for path in (ROOT / "src" / "app" / "ui").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
            elif isinstance(node, ast.Import):
                module = ",".join(str(alias.name) for alias in node.names)
            if blocked in module:
                where = path.relative_to(ROOT).as_posix()
                problems.append(f"{where}:{node.lineno} 直接 import 了插件侧的工具库：{module}")
    assert not problems, "程序本体不该反向依赖插件：" + "；".join(problems[:8])
