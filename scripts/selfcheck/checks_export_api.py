"""L2 服务层 / 页面层检查：导出接口（`export.open`）与导出确认框。

不弹真对话框：确认框被换成假的，重点看接口层——默认目录、只算不写的计划、
快照映射、用户取消时的取舍，以及真写出来的 zip 里有没有东西。

接口由 `src/main.py` 在启动时 bootstrap，自检不跑 `main()`，所以这里自己注册一次
（结束后还原成原来的提供者，别影响别的检查）。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from . import fixtures
from .harness import Case, build_window, check, dispose_window, ensure_app


def _expect(problems: list[str], ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


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


def _clear(name: str):
    """把接口摘掉（模拟程序本体没提供），返回「还原用」的旧提供者。"""
    from app.core.plugins.extensions import extension_registry

    previous = (extension_registry.provider(name), extension_registry.provider_plugin(name))
    extension_registry.provide(name, None, "")
    return previous


class _FakeDialog:
    """替掉导出确认框：按类属性回答，记下被问到的内容。"""

    accepted = False
    chosen = ""
    instances: list["_FakeDialog"] = []

    def __init__(self, parent=None, **kwargs) -> None:
        self.kwargs = kwargs
        self._directory = str(kwargs.get("directory", ""))
        type(self).instances.append(self)

    def exec(self) -> bool:  # noqa: A003 - 对齐 Qt
        return bool(type(self).accepted)

    def directory(self) -> str:
        return type(self).chosen or self._directory

    def deleteLater(self) -> None:  # noqa: N802 - Qt 命名
        return None


def _patch_dialog():
    from unittest import mock

    return mock.patch("app.ui.components.export_dialog.ExportConfirmDialog", _FakeDialog)


@check("export_extension_api", "services")
def export_extension_api(case: Case) -> None:
    """export.open：注册后插件能算计划、能真导出；用户取消就不落文件。"""
    from app.core.config import export_dir
    from app.sdk import export as sdk
    from app.sdk.errors import SdkError
    from app.services import export_api

    problems: list[str] = []
    previous = _bootstrap(sdk.EXPORT_EXTENSION, export_api.api())
    try:
        _expect(problems, sdk.available() is True, "注册之后 available() 该是 True")
        _expect(
            problems,
            sdk.directory() == str(export_dir()),
            f"默认导出目录该是配置里的 {export_dir()}：{sdk.directory()}",
        )
        names = [row.name for row in sdk.variables()]
        for wanted in ("number", "date", "category"):
            _expect(problems, wanted in names, f"模板变量少了 {wanted}：{names}")

        fixture = fixtures.build(case)
        ids = [fixture.text_item]
        if fixture.file_item:
            ids.append(fixture.file_item)
        out = Path(case.root) / "export-api"

        plan = sdk.plan(ids, mode="top", directory=str(out))
        _expect(problems, plan.total == len(ids), f"计划该算到 {len(ids)} 个文件：{plan.total}")
        _expect(problems, bool(plan.names), f"计划该给出包名：{plan.packages}")
        _expect(
            problems,
            all(name.endswith(".zip") for name in plan.names),
            f"包名该以 .zip 结尾：{plan.names}",
        )
        _expect(problems, not out.exists() or not list(out.iterdir()), "计划阶段不该写文件")

        try:
            sdk.packages([], directory=str(out), confirm=False)
            problems.append("空选择该抛 SdkError")
        except SdkError:
            pass

        _FakeDialog.accepted = False
        _FakeDialog.chosen = ""
        _FakeDialog.instances = []
        with _patch_dialog():
            rejected = sdk.packages(ids, directory=str(out), confirm=True)
        _expect(problems, rejected is None, "用户取消时该返回 None")
        _expect(
            problems,
            not out.exists() or not list(out.iterdir()),
            f"用户取消后不该留下文件：{list(out.iterdir()) if out.exists() else []}",
        )

        moved = out / "换过的"
        _FakeDialog.accepted = True
        _FakeDialog.chosen = str(moved)
        with _patch_dialog():
            result = sdk.packages(ids, directory=str(out), confirm=True, message="自检导出")
        _expect(problems, result is not None, "确认之后该真的导出")
        if result is not None:
            _expect(problems, result.directory == str(moved), f"该听用户在框里选的目录：{result.directory}")
            _expect(problems, result.exported == len(ids), f"该导出 {len(ids)} 个文件：{result.exported}")
            _expect(problems, result.finished is True, "同步导出返回时就该是 finished")
            for row in result.packages:
                path = Path(row.path)
                if not path.is_file():
                    problems.append(f"包没落盘：{path}")
                    continue
                with zipfile.ZipFile(path) as archive:
                    members = archive.namelist()
                _expect(
                    problems,
                    any(name.startswith("清单-") for name in members),
                    f"包 {path.name} 里该有清单：{members}",
                )
        if _FakeDialog.instances:
            asked = _FakeDialog.instances[-1].kwargs
            _expect(problems, asked.get("summary") == "自检导出", f"框里的说明该用插件给的：{asked}")
            _expect(problems, bool(asked.get("names")), "框里该列出包名")
        else:
            problems.append("确认框没被弹出来")
    finally:
        _FakeDialog.accepted = False
        _FakeDialog.chosen = ""
        _restore(sdk.EXPORT_EXTENSION, previous)

    assert not problems, "导出接口：" + "；".join(problems[:12])


@check("export_without_extension", "services")
def export_without_extension(case: Case) -> None:
    """没注册接口时，SDK 要干脆地报「程序没有提供导出接口」，别半路炸。"""
    from app.sdk import export as sdk
    from app.sdk.errors import SdkError

    problems: list[str] = []
    previous = _clear(sdk.EXPORT_EXTENSION)
    try:
        _expect(problems, sdk.available() is False, "清掉接口后 available() 该是 False")
        for label, call in (
            ("directory", lambda: sdk.directory()),
            ("plan", lambda: sdk.plan([1])),
            ("packages", lambda: sdk.packages([1], confirm=False)),
        ):
            try:
                call()
                problems.append(f"没有接口时 {label}() 该抛 SdkError")
            except SdkError:
                pass
    finally:
        _restore(sdk.EXPORT_EXTENSION, previous)

    assert not problems, "导出接口缺位：" + "；".join(problems[:8])


@check("export_confirm_dialog", "pages")
def export_confirm_dialog(case: Case) -> None:
    """导出确认框：能让用户改目录、把包名摆出来（超过 8 个折成一行）。"""
    from PyQt6.QtWidgets import QWidget

    from app.ui.components.export_dialog import ExportConfirmDialog

    problems: list[str] = []
    ensure_app()
    host = QWidget()
    host.resize(800, 600)
    names = [f"包-{index}.zip" for index in range(1, 12)]
    try:
        dialog = ExportConfirmDialog(
            host,
            summary="导出选中项",
            directory="D:/起点",
            names=names,
            hint="提示语",
        )
        try:
            if dialog.directory() != "D:/起点":
                problems.append(f"框里该预填默认目录：{dialog.directory()}")
            text = dialog.previewLabel.text()
            if "包-1.zip" not in text:
                problems.append(f"预览该列出包名：{text!r}")
            if "还有 3 个" not in text:
                problems.append(f"超过 8 个要折起来：{text!r}")
            if dialog.names() != names[:8]:
                problems.append(f"回读的包名该只留前 8 个：{dialog.names()}")
            if dialog.yesButton.text() != "开始导出":
                problems.append(f"确认按钮文案不对：{dialog.yesButton.text()}")

            from unittest import mock

            with mock.patch(
                "app.ui.components.export_dialog.choose_export_directory",
                return_value="E:/改过的",
            ):
                dialog._choose_directory()
            if dialog.directory() != "E:/改过的":
                problems.append(f"选了目录之后该回填：{dialog.directory()}")
        finally:
            dispose_window(dialog)
    finally:
        dispose_window(host)

    assert not problems, "导出确认框：" + "；".join(problems[:8])


@check("export_dialog_options", "pages")
def export_dialog_options(case: Case) -> None:
    """导出对话框：分包方式、命名模板、编号起点与间隔都算进预览。"""
    from PyQt6.QtWidgets import QWidget

    from app.core.export import PlannedItem, name_packages, plan_packages
    from app.ui.components.export_dialog import ExportDialog

    problems: list[str] = []
    ensure_app()
    host = QWidget()
    host.resize(900, 700)
    items = (
        PlannedItem(key="1", name="甲.txt", category="影视/电影", user="默认用户"),
        PlannedItem(key="2", name="乙.txt", category="文档/笔记", user="默认用户"),
    )
    try:
        dialog = ExportDialog(
            host, items=items, directory="D:/导出", template="导出-{number}", user="默认用户"
        )
        try:
            if dialog.mode() != "single":
                problems.append(f"默认分包方式该是 single：{dialog.mode()}")
            if dialog.directory() != "D:/导出":
                problems.append(f"框里该预填导出目录：{dialog.directory()}")
            if dialog.names() != ["导出-1.zip"]:
                problems.append(f"一个包时预览该只有一个名字：{dialog.names()}")

            dialog.modeTabs.setCurrentItem("top")
            dialog._refresh_preview()
            expected = [
                row.filename
                for row in name_packages(
                    plan_packages(items, mode="top").packages,
                    "导出-{number}",
                    user="默认用户",
                    used=set(),
                )
            ]
            if dialog.mode() != "top" or dialog.names() != expected:
                problems.append(f"切成按分类打包后预览不对：{dialog.names()} != {expected}")
            if "· " not in dialog.previewLabel.text():
                problems.append(f"预览该逐行列包名：{dialog.previewLabel.text()!r}")

            # 起点 / 间隔：改数字要写回模板原文，预览立刻跟着变
            dialog.templateEdit.setText("导出-{number,1,1}")
            dialog._sync_from_template()
            dialog.startBox.setValue(3)
            dialog.stepBox.setValue(2)
            dialog._refresh_preview()
            if dialog.template() != "导出-{number,3,2}":
                problems.append(f"起点与间隔该写回模板：{dialog.template()!r}")
            if dialog.names()[:2] != ["导出-3.zip", "导出-5.zip"]:
                problems.append(f"编号该从 3 起、每次加 2：{dialog.names()}")

            # 模板里没有编号变量时编号控件该失效
            dialog.templateEdit.setText("导出-{date}")
            dialog._sync_from_template()
            if dialog.startBox.isEnabled() or dialog.stepBox.isEnabled():
                problems.append("模板里没有编号变量时，起点与间隔该禁用")
            if "编号" not in dialog.numberHint.text():
                problems.append(f"该提示怎么用编号：{dialog.numberHint.text()!r}")

            # 插入变量按钮：按下拉里选的变量写进模板
            dialog.templateEdit.clear()
            index = dialog._variable_names.index("category")
            dialog.variableBox.setCurrentIndex(index)
            dialog._insert_variable()
            if "{category}" not in dialog.template():
                problems.append(f"「插入」该把变量写进模板：{dialog.template()!r}")

            # 认不出的变量：预览照旧，只是要提示出来
            dialog.templateEdit.setText("导出-{没有这个}")
            dialog._sync_from_template()
            dialog._refresh_preview()
            if not dialog.warnLabel.text():
                problems.append("认不出的变量该在对话框里提示")
            if "没有这个" not in dialog.previewLabel.text():
                problems.append(f"认不出的变量该原样留在名字里：{dialog.previewLabel.text()!r}")
        finally:
            dispose_window(dialog)
    finally:
        dispose_window(host)

    assert not problems, "导出对话框：" + "；".join(problems[:10])


@check("manage_export_package_flow", "pages")
def manage_export_package_flow(case: Case) -> None:
    """数据管理页的「导出为压缩包」：先弹对话框，再按框里选的分包方式与模板落包。"""
    from unittest import mock

    from app.services import ExportService, ImportService
    from app.ui.pages import manage_page as manage_module

    class _FakeExportDialog:
        """替掉导出对话框：按类属性回答，把调用参数留给自己核对。"""

        accepted = True
        mode_value = "top"
        template_value = "自检-{number}"
        directory_value = ""
        instances: list["_FakeExportDialog"] = []

        def __init__(self, parent=None, **kwargs) -> None:
            self.kwargs = kwargs
            type(self).instances.append(self)

        def exec(self) -> bool:  # noqa: A003 - 对齐 Qt
            return bool(type(self).accepted)

        def mode(self) -> str:
            return type(self).mode_value

        def template(self) -> str:
            return type(self).template_value

        def directory(self) -> str:
            return type(self).directory_value

    fixture, window = build_window(case)
    problems: list[str] = []
    tips: dict[str, list[str]] = {"success": [], "warning": [], "error": []}
    try:
        app = ensure_app()
        page = window.manage_page
        ImportService(case.session).import_text("自检打包项", "打包内容")
        case.session.commit()
        page.refresh()
        app.processEvents()
        items = list(page._items)
        if len(items) < 2:
            problems.append(f"可见数据不足 2 项：{len(items)}")
            assert not problems, "导出对话框落包：" + "；".join(problems)
        page._selected = {item.id for item in items}

        out = Path(case.root) / "manage-export"
        _FakeExportDialog.instances = []
        _FakeExportDialog.directory_value = str(out)
        page.toast_success = lambda title, content="": tips["success"].append(title)
        page.toast_warning = lambda title, content="": tips["warning"].append(title)
        page.toast_error = lambda title, content="": tips["error"].append(title)

        with mock.patch.object(manage_module, "ExportDialog", _FakeExportDialog):
            page._on_export_zip()
        app.processEvents()

        if not _FakeExportDialog.instances:
            problems.append("「导出为压缩包」该先弹导出对话框")
        else:
            asked = _FakeExportDialog.instances[0].kwargs
            planned = asked.get("items") or ()
            keys = {row.key for row in planned}
            if keys != {str(item.id) for item in items}:
                problems.append(f"框里该拿到选中的数据：{sorted(keys)}")
            if not asked.get("directory"):
                problems.append("框里该预填导出目录")

        produced = sorted(path.name for path in out.glob("*.zip"))
        planned = ExportService(case.session).planned_items(items)
        tops = {row.top or "未分类" for row in planned}
        if len(produced) != len(tops):
            problems.append(f"按最顶层分类应出 {len(tops)} 个包，实际 {len(produced)}：{produced}")
        if not all(name.startswith("自检-") for name in produced):
            problems.append(f"包名该按框里的模板来：{produced}")
        for name in produced:
            with zipfile.ZipFile(out / name) as archive:
                members = archive.namelist()
            if not any(member.startswith("清单-") for member in members):
                problems.append(f"包 {name} 里该有清单：{members}")
        if not (out / "自检-1.zip").is_file():
            problems.append(f"第一个包的名字该是模板渲染出来的：{produced}")
        if "导出完成" not in tips["success"]:
            problems.append(f"导出后该提示完成：{tips}")
        if tips["error"]:
            problems.append(f"不该有失败提示：{tips['error']}")
    finally:
        _FakeExportDialog.accepted = True
        dispose_window(window)

    assert not problems, "导出对话框落包：" + "；".join(problems[:10])
