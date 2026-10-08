"""自检：整库包（导出 / 新增式导入 / 覆盖式导入 / 设置页三张卡）。

分两层：`services` 层直接驱动 `DatabaseBundleService` 与 `import_replace`；
`pages` 层驱动设置页「备份与迁移」里的三张卡，文件对话框、确认框与重启都换成桩。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import fixtures
from .checks_archive_bundle import _ToastRecorder
from .harness import Case, build_window, check, dispose_window


def _expect(problems: list[str], ok: bool, message: str) -> None:
    """记录一条不满足的判据（不抛异常，便于一次收集全部问题）。"""
    if not ok:
        problems.append(message)


@check("database_bundle_round_trip", "services")
def database_bundle_round_trip(case: Case) -> None:
    """整库包：清单在最前、带数据库快照与库内文件，新增式导入不删数据、结构太新的包要拒绝。"""
    import sqlite3

    from app.db import database
    from app.repositories import ItemRepository
    from app.services import DatabaseBundleError, DatabaseBundleService, import_replace, inspect_package

    fixtures.build(case)
    session = case.session
    items = ItemRepository(session)
    before = len(items.all())
    assert before >= 1, "自检夹具应至少造出一个数据项"

    service = DatabaseBundleService(session)
    target = case.root / "自检整库包.zip"
    problems: list[str] = []

    result = service.export(target)
    _expect(problems, result.path == target and target.is_file(), f"整库包应写到 {target}")
    _expect(problems, result.counts.get("items") == before, f"清单里应记下 {before} 个数据项，实际 {result.counts.get('items')}")
    _expect(problems, result.files >= 1, f"应带上库内文件，实际 {result.files}")
    _expect(problems, not (case.root / "自检整库包.zip.part").exists(), "导出后不该留下 .part")

    with zipfile.ZipFile(target) as pack:
        names = pack.namelist()
        payload = json.loads(pack.read("数据库包.json").decode("utf-8"))
        broken = pack.testzip()
    _expect(problems, names[0] == "数据库包.json", f"清单应写在包的最前面，实际 {names[:1]}")
    _expect(problems, "data/data.db" in names, "包里应带数据库快照 data/data.db")
    _expect(problems, any(name.startswith("library/") for name in names), "包里应带库文件夹里的文件")
    _expect(problems, payload.get("format") == "dm-database", f"清单格式应为 dm-database，实际 {payload.get('format')}")
    _expect(problems, broken is None, f"包里的内容应能完整读出，坏成员 {broken}")

    info = inspect_package(target)
    _expect(problems, info.items == before, f"读清单应得到 {before} 个数据项，实际 {info.items}")
    _expect(problems, info.users >= 1, f"读清单应得到用户数，实际 {info.users}")
    _expect(problems, info.schema_version == database.SCHEMA_VERSION, f"结构版本应为 {database.SCHEMA_VERSION}，实际 {info.schema_version}")
    _expect(problems, "数据项" in info.summary(), f"摘要该说清有多少数据项，实际 {info.summary()}")

    report = service.import_merge(target)
    _expect(problems, report.mode == "merge" and report.merge, f"应是新增式导入，实际 {report.mode}")
    _expect(problems, report.items == before, f"应并进 {before} 个数据项，实际 {report.items}")
    _expect(problems, report.users == 0, f"同名用户应认出而不是新建，实际新建 {report.users}")
    _expect(problems, report.missing == 0, f"自检数据不该缺内容，实际缺 {report.missing}")
    _expect(problems, len(items.all()) == before * 2, f"新增式导入后数据项应翻倍，实际 {len(items.all())}")
    _expect(problems, "新增式导入完成" in report.summary(), f"摘要该说明导入方式，实际 {report.summary()}")

    # 结构版本比本程序新的包一律拒绝，且不碰现有数据
    snapshot = case.root / "selfcheck-newer.db"
    database.backup_database_file(snapshot)
    conn = sqlite3.connect(snapshot)
    conn.execute("UPDATE app_meta SET value=? WHERE key='schema_version'", (str(database.SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    newer = case.root / "太新.zip"
    payload["schema_version"] = database.SCHEMA_VERSION + 1
    with zipfile.ZipFile(newer, "w") as pack:
        pack.writestr("数据库包.json", json.dumps(payload, ensure_ascii=False))
        pack.writestr("data/data.db", snapshot.read_bytes())
    for action in (lambda: service.import_merge(newer), lambda: import_replace(newer)):
        try:
            action()
        except DatabaseBundleError:
            pass
        else:
            problems.append("结构版本比本程序新的整库包应被拒绝")
    _expect(problems, len(items.all()) == before * 2, f"被拒绝的包不该改动任何数据，实际 {len(items.all())}")
    assert not problems, "整库包检查未通过：" + "；".join(problems)


@check("database_page_backup_actions", "pages")
def database_page_backup_actions(case: Case) -> None:
    """设置页：导出整库、导入整库（新增式）、导入整库（覆盖式）三张卡都能走通。"""
    import app.ui.pages.settings_page as settings_module

    from app.core import config
    from app.repositories import ItemRepository

    package = case.root / "页面整库包.zip"
    calls = {"save": 0, "open": 0, "restart": 0}

    class _FakeFileDialog:
        """只实现设置页用到的两个文件对话框：导出目标与导入来源。"""

        @staticmethod
        def getSaveFileName(*_args, **_kwargs):
            calls["save"] += 1
            return str(package), "整库包 (*.zip)"

        @staticmethod
        def getOpenFileName(*_args, **_kwargs):
            calls["open"] += 1
            return str(package), "整库包 (*.zip)"

    original_dialog = settings_module.QFileDialog
    original_confirm = settings_module.confirm
    original_restart = settings_module.restart_application
    settings_module.QFileDialog = _FakeFileDialog
    settings_module.confirm = lambda *_args, **_kwargs: True
    settings_module.restart_application = lambda: calls.__setitem__("restart", calls["restart"] + 1)

    _fixture, window = build_window(case)
    page = window.settings_page

    def count() -> int:
        """页面可能换过会话（覆盖式导入会重建），每次都按当前会话数。"""
        return len(ItemRepository(page.session).all())

    before = count()
    problems: list[str] = []
    try:
        with _ToastRecorder(page) as recorder:
            _expect(problems, page._db_export_card.isEnabled(), "默认用户下「导出整库」应可用")
            page._on_export_database()
            _expect(problems, calls["save"] == 1, "应弹一次导出对话框")
            _expect(problems, package.is_file(), f"应在 {package} 落下整库包")
            _expect(problems, "已导出整库包" in recorder.titles("success"), f"导出后应提示完成：{recorder.messages}")

            page._on_import_database_merge()
            _expect(problems, calls["open"] == 1, "应弹一次导入对话框")
            _expect(problems, count() == before * 2, f"新增式导入后数据项应翻倍，实际 {count()}")
            _expect(problems, "导入完成" in recorder.titles("success"), f"导入后应提示完成：{recorder.messages}")

            page._on_import_database_replace()
            _expect(problems, calls["open"] == 2, "覆盖式导入也该弹导入对话框")
            _expect(problems, count() == before, f"覆盖式导入后数据项应回到包里的 {before} 个，实际 {count()}")
            _expect(problems, calls["restart"] == 1, "覆盖式导入后应请求重启程序")
            _expect(
                problems,
                bool(list(config.db_file().parent.glob("data.db.bak-*")))
                or bool(list(config.db_file().parent.glob("library.bak-*"))),
                "覆盖式导入应把旧数据库或旧库文件夹留一份备份",
            )
            _expect(problems, not recorder.titles("error"), f"整条流程不该有失败提示：{recorder.messages}")
    finally:
        settings_module.QFileDialog = original_dialog
        settings_module.confirm = original_confirm
        settings_module.restart_application = original_restart
        dispose_window(window)
    assert not problems, "设置页整库检查未通过：" + "；".join(problems)
