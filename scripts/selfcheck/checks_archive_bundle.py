"""自检：存档包（导出 / 导入 / 存档页上的两个入口）。

分两层：`services` 层直接驱动 `ArchiveBundleService`；`pages` 层驱动存档页的
「导出所选存档」与「导入存档包」入口，文件对话框与确认框都换成桩。
"""

from __future__ import annotations

import json
import zipfile

from . import fixtures
from .harness import Case, build_window, check, dispose_window


def _expect(problems: list[str], ok: bool, message: str) -> None:
    """记录一条不满足的判据（不抛异常，便于一次收集全部问题）。"""
    if not ok:
        problems.append(message)


class _ToastRecorder:
    """把页面上的 toast_* 换成记录器：既不真弹提示条，又能断言提示文案。"""

    def __init__(self, page) -> None:
        self.page = page
        self.messages: list[tuple[str, str]] = []
        self._originals = (page.toast_success, page.toast_warning, page.toast_error)

    def __enter__(self) -> "_ToastRecorder":
        def record(kind: str):
            def handler(title: str, content: str = "") -> None:
                self.messages.append((kind, str(title)))

            return handler

        self.page.toast_success = record("success")
        self.page.toast_warning = record("warning")
        self.page.toast_error = record("error")
        return self

    def __exit__(self, *_exc: object) -> bool:
        (
            self.page.toast_success,
            self.page.toast_warning,
            self.page.toast_error,
        ) = self._originals
        return False

    def titles(self, kind: str) -> list[str]:
        return [title for item_kind, title in self.messages if item_kind == kind]


@check("archive_bundle_round_trip", "services")
def archive_bundle_round_trip(case: Case) -> None:
    """存档包：清单在最前面、内容按校验和打包、导入只重建存档记录且重名加后缀。"""
    from app.services import ArchiveBundleService, ArchiveService
    from app.services.archive_bundle import CONTENT_DIR, FORMAT, MANIFEST_NAME

    fixtures.build(case)
    session = case.session
    archive = ArchiveService(session).create(name="自检存档", note="自检备注")
    assert archive is not None, "创建自检存档失败"
    session.commit()

    service = ArchiveBundleService(session)
    problems: list[str] = []
    target = case.root / "自检存档包.zip"

    result = service.export([archive], target)
    _expect(problems, result.path == target and target.is_file(), f"存档包应写到 {target}")
    _expect(problems, result.archives == 1, f"应导出 1 份存档，实际 {result.archives}")
    _expect(problems, result.entries >= 1, f"应带上条目，实际 {result.entries}")
    _expect(problems, result.files >= 1, f"应带上内容，实际 {result.files}")
    _expect(problems, result.missing == 0, f"自检数据不该缺内容，实际缺 {result.missing}")

    with zipfile.ZipFile(target) as pack:
        names = pack.namelist()
        payload = json.loads(pack.read(MANIFEST_NAME).decode("utf-8"))
    _expect(problems, names[0] == MANIFEST_NAME, f"清单应写在包的最前面，实际 {names[:1]}")
    _expect(problems, payload.get("format") == FORMAT, f"清单格式应为 {FORMAT}")
    checksums = {
        str(entry.get("checksum") or "")
        for row in payload.get("archives") or []
        for entry in row.get("entries") or []
        if entry.get("checksum")
    }
    _expect(problems, bool(checksums), "清单里应记下内容的校验和")
    _expect(
        problems,
        all(f"{CONTENT_DIR}/{checksum}" in names for checksum in checksums),
        "清单里每个校验和都应在包里有一份内容",
    )

    info = service.inspect(target)
    _expect(problems, info.names == ("自检存档",), f"包内存档名应为「自检存档」，实际 {info.names}")
    _expect(problems, info.entries == result.entries, f"包内条目数应为 {result.entries}，实际 {info.entries}")
    _expect(problems, info.files == result.files, f"包内内容份数应为 {result.files}，实际 {info.files}")

    again = service.import_bundle(target)
    _expect(problems, again.archives == 1, f"应导入 1 份存档，实际 {again.archives}")
    _expect(problems, again.names == ("自检存档（导入）",), f"重名应加后缀，实际 {again.names}")
    _expect(problems, again.missing == 0, f"内容都该写回，实际缺 {again.missing}")
    _expect(
        problems,
        {"自检存档", "自检存档（导入）"} <= {item.name for item in ArchiveService(session).history(limit=100)},
        "导入后两份存档都该在历史里",
    )
    with zipfile.ZipFile(target) as pack:  # 导出的包不该留下 .part
        _expect(problems, pack.testzip() is None, "包里的内容应能完整读出")
    _expect(problems, not (case.root / "自检存档包.zip.part").exists(), "导出后不该留下 .part")
    assert not problems, "存档包检查未通过：" + "；".join(problems)


@check("archive_page_bundle_actions", "pages")
def archive_page_bundle_actions(case: Case) -> None:
    """存档页：勾选后「导出所选存档」能落包，「导入存档包」能重建记录。"""
    import app.ui.pages.archive_page as archive_module

    from app.services import ArchiveService

    target = case.root / "页面存档包.zip"
    calls = {"save": 0, "open": 0}

    class _FakeFileDialog:
        """只实现存档页用到的两个文件对话框：导出目标与导入来源。"""

        @staticmethod
        def getSaveFileName(*_args, **_kwargs):
            calls["save"] += 1
            return str(target), "存档包 (*.zip)"

        @staticmethod
        def getOpenFileNames(*_args, **_kwargs):
            calls["open"] += 1
            return [str(target)], "存档包 (*.zip)"

    original_dialog = archive_module.QFileDialog
    original_confirm = archive_module.confirm
    archive_module.QFileDialog = _FakeFileDialog
    archive_module.confirm = lambda *_args, **_kwargs: True

    _fixture, window = build_window(case)
    page = window.archive_page
    archive = ArchiveService(page.session).create(name="页面自检存档", note="")
    assert archive is not None, "创建自检存档失败"
    page.session.commit()
    page._reload_archives()
    problems: list[str] = []
    try:
        with _ToastRecorder(page) as recorder:
            _expect(problems, page.import_button.text() == "导入存档包", f"头部按钮文案应为「导入存档包」，实际 {page.import_button.text()}")
            _expect(problems, page.batch_export_button.text() == "导出所选存档", f"批量按钮文案应为「导出所选存档」，实际 {page.batch_export_button.text()}")

            page._on_batch_export()  # 没勾选：只提示，不弹文件框
            _expect(problems, calls["save"] == 0, "没勾选存档时不该弹导出对话框")
            _expect(problems, "未选择存档" in recorder.titles("warning"), "没勾选存档时应提示「未选择存档」")

            page.select_all()
            _expect(problems, page.batch_export_button.isEnabled(), "勾选后「导出所选存档」应可用")
            page._on_batch_export()
            _expect(problems, calls["save"] == 1, "勾选后应弹一次导出对话框")
            _expect(problems, target.is_file(), f"应在 {target} 落下一个存档包")
            _expect(problems, "导出完成" in recorder.titles("success"), "导出后应提示完成")

            before = len(page.service.history(limit=200))
            page._on_import_bundle()
            _expect(problems, calls["open"] == 1, "应弹一次导入对话框")
            names = {item.name for item in page.service.history(limit=200)}
            _expect(problems, "页面自检存档（导入）" in names, f"应导入出带后缀的存档，实际 {sorted(names)}")
            _expect(problems, len(page.service.history(limit=200)) == before + 1, "导入后存档应多一份")
            _expect(problems, "导入完成" in recorder.titles("success"), "导入后应提示完成")
            _expect(problems, not recorder.titles("error"), f"整条流程不该有失败提示：{recorder.messages}")
    finally:
        archive_module.QFileDialog = original_dialog
        archive_module.confirm = original_confirm
        dispose_window(window)
    assert not problems, "存档页存档包检查未通过：" + "；".join(problems)
