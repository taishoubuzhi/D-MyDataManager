"""L2 页面检查：存档页、查看器配置页、插件页与图片查看器。

移植自旧门禁 `scripts/dev_check_ui.py` 的 _check_archive_tabs / _check_archive_owner /
_check_archive_pin / _check_archive_table / _check_open_with / _check_plugins /
_check_plugin_pages / _check_image_viewer，只走公开契约（页面属性、页面公开方法、
服务层 API），数据一律在本用例的临时目录与全新数据库上自造。
"""

from __future__ import annotations

import json
from pathlib import Path

from .fixtures import SAMPLE_IMAGE
from .harness import (
    ROOT,
    Case,
    build_window,
    check,
    dispose_window,
    ensure_app,
    install_builtin_plugins,
)

#: 仓库里的内置插件目录；自检环境的插件目录是空的，插件相关检查要先把它装进去。
BUILTIN_PLUGINS = ROOT / "plugins"


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
        """按种类取出记录到的提示标题。"""
        return [title for item_kind, title in self.messages if item_kind == kind]


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



def _builtin_plugin_ids() -> list[str]:
    """读仓库内置插件清单，返回插件 id（用作独立于服务层的期望值）。"""
    ids: list[str] = []
    for source in sorted(BUILTIN_PLUGINS.iterdir()):
        manifest = source / "plugin.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        ids.append(str(data.get("id")))
    return ids


def _point_counts() -> dict[str, int]:
    """每个扩展点上登记了贡献的插件数（从服务层读回，供贡献筛选断言）。"""
    from app.sdk import ExtensionPoint
    from app.services.plugin_service import plugin_service

    counts: dict[str, int] = {}
    for point in ExtensionPoint.values():
        ids = {item.plugin_id for item in plugin_service.point_items(point)}
        if ids:
            counts[point] = len(ids)
    return counts


def _seed_archives(case: Case) -> tuple[int, int]:
    """再造两个存档（第二个标记为已标记），返回 (普通存档 id, 已标记存档 id)。"""
    from app.services.archive_service import ArchiveService

    service = ArchiveService(case.session)
    plain = service.create(name="自检存档甲", note="未标记")
    pinned = service.create(name="自检存档乙", note="已标记")
    assert plain is not None and pinned is not None, "创建自检存档失败"
    assert service.set_pinned(pinned, True), "标记自检存档失败"
    case.session.commit()
    return int(plain.id), int(pinned.id)


def _set_number_filter(bar, key: str, mode: str, first: str, second: str = "") -> None:
    """像用户那样驱动数值筛选：选模式、填输入框（第二个只在区间模式用）。"""
    from app.ui.components.data_table import NUMBER_MODES

    field = bar._fields[key]
    field.combo.setCurrentIndex([item[1] for item in NUMBER_MODES].index(mode))
    field.line.setText(first)
    field.line2.setText(second)
    bar._sync_number(field.combo, field.line2)


def _set_date_filter(bar, key: str, mode: str, first, second=None) -> None:
    """驱动时间筛选：选模式、在日期选择控件上挑日期。"""
    from app.ui.components.data_table import DATE_MODES

    field = bar._fields[key]
    field.picker.setDate(first)
    if second is not None:
        field.picker2.setDate(second)
    field.combo.setCurrentIndex([item[1] for item in DATE_MODES].index(mode))
    bar._sync_date(field.combo, field.picker2)


def _row_of_archive(page, archive_id: int) -> int:
    """按存档 id 找当前页里的行号。"""
    for row, archive in enumerate(page._page_items):
        if int(archive.id) == archive_id:
            return row
    return -1


def _select_archive(page, archive_id: int) -> None:
    """按 id 选中存档列表里的行（会自动跳到「存档内条目」页签）。"""
    row = _row_of_archive(page, archive_id)
    assert row >= 0, f"存档列表里没有 id={archive_id} 的行"
    page.archive_list.setCurrentCell(row, 1)


def _archive_pinned(page, archive_id: int) -> bool:
    """从服务层读回某个存档当前的标记状态。"""
    for archive in page.service.history(limit=100):
        if int(archive.id) == archive_id:
            return bool(archive.pinned)
    return False


def _select_suffix(page, suffix: str) -> None:
    """在「查看器」配置页的格式列表里选中指定扩展名。"""
    from PyQt6.QtCore import Qt

    for row in range(page.suffix_list.count()):
        if page.suffix_list.item(row).data(Qt.ItemDataRole.UserRole) == suffix:
            page.suffix_list.setCurrentRow(row)
            return
    raise AssertionError(f"格式列表里没有 {suffix}")


def _select_other_suffix(page, suffix: str) -> None:
    """先选中一个非目标扩展名，逼出下次切回目标格式时的刷新。"""
    from PyQt6.QtCore import Qt

    for row in range(page.suffix_list.count()):
        if page.suffix_list.item(row).data(Qt.ItemDataRole.UserRole) != suffix:
            page.suffix_list.setCurrentRow(row)
            return


def _listed_plugin_ids(page) -> list[str]:
    """当前插件列表里列出的插件 id（按列表顺序）。"""
    from PyQt6.QtCore import Qt

    return [
        page.plugin_list.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(page.plugin_list.count())
    ]


def _broken_page_factory():
    """故意报错的插件页面工厂：验证主窗口退化成提示页。"""
    raise RuntimeError("自检：页面工厂故意报错")


@check("archive_tabs_owner_pin", "pages")
def archive_tabs_owner_pin(case: Case) -> None:
    """存档页：页签联动、按归属可见性、标记存档与列表顺序。"""
    import app.ui.pages.archive_page as archive_module
    from app.ui.pages.archive_page import archive_name_text

    plain_id, pinned_id = _seed_archives(case)
    _fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    confirm_calls: list[str] = []
    original_confirm = archive_module.confirm

    def _refuse(_parent, title, _content=""):
        """自检桩：拒绝一切确认框，验证不确认时不会真删。"""
        confirm_calls.append(str(title))
        return False

    archive_module.confirm = _refuse
    try:
        _expect(problems, page.tab_keys() == ["archives", "entries"], f"存档页应有两个页签，实际 {page.tab_keys()}")
        _expect(problems, page.stack.count() == 2, f"页签容器应有两页，实际 {page.stack.count()}")

        page.switch_tab("entries")
        _expect(
            problems,
            page.current_tab() == "entries" and page.stack.currentIndex() == 1,
            f"切到「存档内条目」失败：{page.current_tab()} / {page.stack.currentIndex()}",
        )
        page.switch_tab("archives")
        _expect(
            problems,
            page.current_tab() == "archives" and page.stack.currentIndex() == 0,
            "手动切回「存档列表」失败",
        )

        _select_archive(page, pinned_id)
        _expect(problems, page.current_tab() == "entries", "选中存档后应自动跳到「存档内条目」")
        page.switch_tab("archives")

        headers = [page.table.horizontalHeaderItem(index).text() for index in range(page.table.columnCount())]
        _expect(problems, "所属用户" in headers, f"条目表应有「所属用户」列，实际 {headers}")
        _expect(problems, page._is_admin, "默认用户应被识别为管理员")
        _expect(problems, "标记存档" in page.caption.text(), "存档页说明应提示「标记存档」的用途")
        page.switch_tab("entries")
        _expect(problems, not page.restore_all_button.isHidden(), "管理员在条目页应看到「整档还原」按钮")
        page.switch_tab("archives")

        archives = page.service.history(limit=100)
        pinned = [archive for archive in archives if archive.pinned]
        _expect(
            problems,
            [int(archive.id) for archive in pinned] == [pinned_id],
            f"应只有一个已标记存档 {pinned_id}，实际 {[int(a.id) for a in pinned]}",
        )
        texts = [page.archive_list.item(row, 1).text() for row in range(page.archive_list.rowCount())]
        expected_texts = [archive_name_text(archive) for archive in page._page_items]
        _expect(problems, texts == expected_texts, f"存档表首列应按服务返回顺序显示，实际 {texts}")
        _expect(problems, any(text.startswith("【已标记】") for text in texts), "已标记的存档行应带「【已标记】」前缀")

        _expect(problems, page.service.delete(pinned[0]) is False, "已标记的存档不应能被服务删除")
        _select_archive(page, pinned_id)
        _expect(problems, page.pin_button.text() == "取消标记", f"已标记存档应显示「取消标记」，实际 {page.pin_button.text()}")
        _expect(problems, "【已标记】" in page.detail_meta.text(), "已标记存档的详情应说明不受自动清理影响")
        before = len(page.service.history(limit=100))
        page._on_delete()
        _expect(problems, len(page.service.history(limit=100)) == before, "删除已标记存档应被拒绝")
        _expect(problems, not confirm_calls, "已标记存档的删除不应弹确认框")

        _select_archive(page, plain_id)
        _expect(problems, page.pin_button.text() == "标记存档", f"未标记存档应显示「标记存档」，实际 {page.pin_button.text()}")
        before = len(page.service.history(limit=100))
        page._on_delete()
        _expect(problems, len(page.service.history(limit=100)) == before, "确认框被拒绝后不应删除存档")
        page.select_all()  # 批量删除要先有勾选，否则页面只提示「未选择存档」而不弹确认框
        page._on_batch_delete()
        _expect(problems, len(page.service.history(limit=100)) == before, "批量删除被拒绝后不应减少存档")
        _expect(problems, len(confirm_calls) >= 2, f"普通删除与批量删除都应先弹确认框，实际 {len(confirm_calls)} 次")

        page._on_toggle_pin()
        _expect(problems, _archive_pinned(page, plain_id), "点「标记存档」后该存档应变成已标记")
        _select_archive(page, plain_id)
        _expect(problems, page.pin_button.text() == "取消标记", "变成已标记后按钮文案应为「取消标记」")
        page._on_toggle_pin()
        _expect(problems, not _archive_pinned(page, plain_id), "再点一次应取消标记")

        page.filter_bar.set_filter("name", "绝不存在的存档")
        _expect(problems, page.archive_list.rowCount() == 0, "筛选无结果时存档表应为空")
        _expect(problems, not page.pin_button.isEnabled(), "没有可见存档时「标记存档」应禁用")
        page.filter_bar.reset()
        _expect(problems, page.archive_list.rowCount() == len(page._page_items), "重置筛选后应恢复存档行")
    finally:
        archive_module.confirm = original_confirm
        dispose_window(window)
    assert not problems, "存档页检查未通过：" + "；".join(problems)


@check("archive_table", "pages")
def archive_table(case: Case) -> None:
    """存档表格：列结构、行文本、筛选空态、全选 / 反选与批量按钮。"""
    from app.ui.pages.archive_page import ARCHIVE_CHECK_COLUMN, ARCHIVE_HEADERS

    _seed_archives(case)
    _fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    try:
        table = page.archive_list
        headers = [table.horizontalHeaderItem(index).text() for index in range(table.columnCount())]
        _expect(
            problems,
            table.columnCount() == len(ARCHIVE_HEADERS),
            f"存档表应有 {len(ARCHIVE_HEADERS)} 列，实际 {table.columnCount()}",
        )
        _expect(problems, headers == list(ARCHIVE_HEADERS), f"存档表表头应为 {list(ARCHIVE_HEADERS)}，实际 {headers}")
        _expect(problems, table.horizontalHeaderItem(ARCHIVE_CHECK_COLUMN) is not None, "存档表缺少勾选列表头")
        _expect(problems, table.horizontalHeader().sectionsMovable(), "存档表头应可拖动调整列")
        _expect(problems, not page.pager.isHidden(), "有存档时翻页控件应可见")

        total = len(page._archives)
        _expect(problems, total >= 2, f"夹具应至少有 2 个存档，实际 {total}")
        _expect(problems, table.rowCount() == len(page._page_items), "表格行数应与当前页条目数一致")
        _expect(problems, f"共 {total} 个存档" in page.archive_stats.text(), f"统计文案应报出 {total} 个存档，实际 {page.archive_stats.text()!r}")

        rows = [
            [table.item(row, column).text() if table.item(row, column) else "" for column in range(table.columnCount())]
            for row in range(table.rowCount())
        ]
        count_column = headers.index("条目数")
        pin_column = headers.index("标记")
        _expect(problems, all(row[count_column].isdigit() for row in rows), f"条目数应为数字，实际 {[row[count_column] for row in rows]}")
        _expect(problems, all(row[pin_column] in ("已标记", "未标记") for row in rows), f"标记列应为已标记/未标记，实际 {[row[pin_column] for row in rows]}")
        _expect(problems, any(row[1].startswith("【已标记】") for row in rows), "已标记的存档行应带「【已标记】」前缀")

        page.filter_bar.set_filter("name", "绝不存在的存档")
        _expect(problems, table.rowCount() == 0, "筛选无结果时表格应为空")
        _expect(problems, not page._visible, "筛选无结果时不应有可见存档")
        _expect(problems, "没有匹配的存档" in page.detail_title.text(), f"空态提示应为「没有匹配的存档」，实际 {page.detail_title.text()!r}")
        _expect(problems, not page.pin_button.isEnabled(), "筛选无结果时标记按钮应禁用")
        page.filter_bar.reset()
        _expect(problems, len(page._visible) == total, "重置筛选后应恢复全部存档")

        page.filter_bar.set_filter("pinned", "已标记")
        _expect(problems, len(page._visible) == 1, f"按「已标记」筛选应只剩 1 个存档，实际 {len(page._visible)}")
        _expect(problems, table.rowCount() == 1, f"按「已标记」筛选后表格应只剩 1 行，实际 {table.rowCount()}")
        _expect(problems, table.item(0, pin_column).text() == "已标记", f"筛选结果的标记列应为「已标记」，实际 {table.item(0, pin_column).text()!r}")
        page.filter_bar.reset()
        _expect(problems, len(page._visible) == total, "重置「已标记」筛选后应恢复全部存档")

        page.select_all()
        _expect(problems, len(page.checked_archives()) == total, f"全选应勾选 {total} 个存档，实际 {len(page.checked_archives())}")
        _expect(problems, len(page._page_ids()) == len(page._page_items), "本页 id 列表应与行数一致")
        _expect(problems, page.selection_label.text() == f"已选 {total} 个存档", f"已选文案不对：{page.selection_label.text()!r}")
        _expect(
            problems,
            page.batch_pin_button.isEnabled() and page.batch_unpin_button.isEnabled() and page.batch_delete_button.isEnabled(),
            "有勾选时三个批量按钮都应可用",
        )
        page.invert_selection()
        _expect(problems, not page.checked_archives(), "反选后勾选应清空")
        _expect(problems, not page.batch_delete_button.isEnabled(), "无勾选时批量删除应禁用")
        _expect(problems, page.selection_label.text() == "未选择存档", f"未选文案不对：{page.selection_label.text()!r}")

        page.select_all_box.click()
        _expect(problems, len(page.checked_archives()) == total, "点「全选本页」应全选")
        page.select_all_box.click()
        _expect(problems, not page.checked_archives(), "再点一次「全选本页」应取消勾选")
        page.select_all()
        page.select_none()
        _expect(problems, not page.checked_archives(), "取消全选后不应有勾选")
        _expect(problems, not page.pager.isHidden(), "翻页控件仍应可见")
    finally:
        dispose_window(window)
    assert not problems, "存档表格检查未通过：" + "；".join(problems)


@check("archive_entry_filters", "pages")
def archive_entry_filters(case: Case) -> None:
    """条目明细：各列筛选、状态随数据变更即时刷新、普通用户的可见与回档范围。"""
    from app.core.signals import signalBus
    from app.db.models import DataItem as Item
    from app.services import ArchiveService, ImportService, ItemService
    from app.ui.framework import PageBase
    from app.ui.pages.archive_page import ENTRY_HEADERS, ENTRY_STATE_LABELS

    fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    identity = (page._is_admin, page._user_id)
    warnings: list[tuple] = []
    restores: list[tuple] = []
    try:
        importer = ImportService(case.session)
        theirs = importer.import_text("自检我的条目", "甲组内容", user_id=fixture.user_id)
        mine = importer.import_text("自检默认条目", "乙组内容")
        assert theirs is not None and mine is not None, "自检条目导入失败"
        case.session.commit()
        archive = ArchiveService(case.session).create(name="自检条目存档", note="筛选")
        assert archive is not None, "创建自检存档失败"
        case.session.commit()

        _expect(
            problems,
            type(page).refresh is not PageBase.refresh,
            "存档页应覆写 refresh()，否则 itemsChanged / archivesChanged 接不到重取数逻辑",
        )
        page.refresh()
        _select_archive(page, int(archive.id))

        headers = [page.table.horizontalHeaderItem(index).text() for index in range(page.table.columnCount())]
        _expect(problems, headers == list(ENTRY_HEADERS), f"条目表表头应为 {list(ENTRY_HEADERS)}，实际 {headers}")
        entries = list(page._all_entries)
        _expect(problems, len(entries) >= 2, f"存档内条目应至少有 2 项，实际 {len(entries)}")
        _expect(problems, page.table.rowCount() == len(entries), "没有筛选时条目表应显示全部条目")
        owners = {entry.user_name for entry in entries}
        _expect(problems, fixture.user_name in owners, f"存档内条目应含 {fixture.user_name} 的条目，实际 {owners}")

        page.toast_warning = lambda *args, **kwargs: warnings.append(args)  # type: ignore[method-assign]
        page._confirm_and_restore = lambda *args, **kwargs: restores.append(args)  # type: ignore[method-assign]
        page.entry_filter_bar.set_filter("name", "绝不存在的条目")
        _expect(problems, page.table.rowCount() == 0, "名称筛选无结果时条目表应为空")
        _expect(
            problems,
            page.restore_all_button.text() != "还原整个存档",
            f"有筛选时按钮应改成只回档筛选结果，实际 {page.restore_all_button.text()!r}",
        )
        page._on_restore_all()
        _expect(problems, len(warnings) == 1 and not restores, "筛选结果为空时应只提示、不发起回档")
        page.entry_filter_bar.reset()
        _expect(problems, page.table.rowCount() == len(entries), "重置筛选后应恢复全部条目")
        _expect(problems, page.restore_all_button.text() == "还原整个存档", "没有筛选时按钮应回到整档文案")

        # 用户报告的核心缺陷：数据管理里删掉一项后，存档条目仍显示「一致（无需还原）」。
        name_column = ENTRY_HEADERS.index("名称")
        state_column = ENTRY_HEADERS.index("状态")

        def row_of(name: str) -> int:
            for index in range(page.table.rowCount()):
                if page.table.item(index, name_column).text() == name:
                    return index
            return -1

        target_row = row_of("自检我的条目")
        _expect(problems, target_row >= 0, "条目表里应能看到自检条目")
        if target_row >= 0:
            before_state = page.table.item(target_row, state_column).text()
            _expect(problems, before_state == ENTRY_STATE_LABELS["same"], f"未改动前状态应为「一致」，实际 {before_state!r}")

        ItemService(case.session).delete([case.session.get(Item, int(theirs.id))])
        case.session.commit()
        signalBus.itemsChanged.emit()
        target_row = row_of("自检我的条目")
        _expect(problems, target_row >= 0, "删除后条目表里仍应有该存档条目")
        if target_row >= 0:
            shown = page.table.item(target_row, state_column).text()
            _expect(
                problems,
                shown == ENTRY_STATE_LABELS["removed"],
                f"数据管理里删除后条目状态应即时变成「已删除」，实际 {shown!r}",
            )
        page.entry_filter_bar.set_filter("state", ENTRY_STATE_LABELS["removed"])
        _expect(problems, page.table.rowCount() == 1, f"「已删除」筛选应命中 1 项，实际 {page.table.rowCount()}")
        page.entry_filter_bar.reset()

        page._is_admin, page._user_id = False, fixture.user_id
        page.refresh()
        _expect(
            problems,
            all(entry.user_id in (None, fixture.user_id) for entry in page._all_entries),
            "普通用户只应看见自己的条目",
        )
        _expect(problems, not page.restore_all_button.isHidden(), "普通用户也应看到「还原整个存档」按钮")
        _expect(problems, page._restore_user_id() == fixture.user_id, "普通用户整档回档的范围应是本人")
        _expect(
            problems,
            "属于我" in page.restore_all_button.toolTip(),
            f"普通用户的整档提示应说明范围，实际 {page.restore_all_button.toolTip()!r}",
        )
        page.entry_filter_bar.set_filter("user", fixture.user_name)
        _expect(
            problems,
            page.table.rowCount() == len(page._all_entries),
            "按所属用户筛选后应只剩该用户的条目",
        )
    finally:
        page._is_admin, page._user_id = identity
        dispose_window(window)
    assert not problems, "存档条目筛选检查未通过：" + "；".join(problems)


@check("archive_filter_modes", "pages")
def archive_filter_modes(case: Case) -> None:
    """存档列表的数值 / 时间筛选模式，以及条目明细的大小、分类分组与标签多选。"""
    from PyQt6.QtCore import QDate

    from app.services import ArchiveService, ImportService
    from app.ui.components.data_table import DATE_MODES, NUMBER_MODES

    fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    try:
        importer = ImportService(case.session)
        tagged = importer.import_text(
            "筛选甲", "甲内容", category_id=fixture.category_child, tags=[fixture.tag, "第二标签"]
        )
        plain = importer.import_text("筛选乙", "乙内容")
        assert tagged is not None and plain is not None, "自检条目导入失败"
        case.session.commit()
        archive = ArchiveService(case.session).create(name="筛选模式存档")
        assert archive is not None, "创建筛选模式存档失败"
        case.session.commit()
        _seed_archives(case)
        page.refresh()

        modes = [item[1] for item in NUMBER_MODES]
        count_field = page.filter_bar._fields["count"]
        _expect(
            problems,
            [count_field.combo.itemData(index) for index in range(count_field.combo.count())] == modes,
            "条目数筛选应提供「不限 / 等于 / 大于 / 小于 / 区间」五种模式",
        )
        _expect(problems, count_field.line is not None and count_field.line2 is not None, "数值筛选应有两个输入框")
        date_modes = [item[1] for item in DATE_MODES]
        created_field = page.filter_bar._fields["created"]
        _expect(
            problems,
            [created_field.combo.itemData(index) for index in range(created_field.combo.count())] == date_modes,
            "创建时间筛选应提供按天筛选的五种模式",
        )
        _expect(
            problems,
            created_field.picker is not None and created_field.picker2 is not None,
            "创建时间筛选应有两个日期选择控件",
        )
        _expect(problems, count_field.line2.isHidden(), "「等于」模式下第二个输入框应隐藏")
        _set_number_filter(page.filter_bar, "count", "between", "1", "5")
        _expect(problems, not count_field.line2.isHidden(), "「区间」模式下第二个输入框应显示")
        page.filter_bar.reset()
        _expect(problems, count_field.line2.isHidden(), "重置后第二个输入框应隐藏")

        counts = {int(a.id): int(row["count"]) for a, row in zip(page._archives, page._archive_numbers)}
        assert counts, "夹具应至少有 1 个存档"
        top = max(counts.values())

        def visible_ids() -> set[int]:
            return {int(archive.id) for archive in page._visible}

        _set_number_filter(page.filter_bar, "count", "eq", str(top))
        _expect(
            problems,
            visible_ids() == {key for key, value in counts.items() if value == top},
            f"「等于 {top}」应命中条目数相同的存档，实际 {sorted(visible_ids())}",
        )
        _set_number_filter(page.filter_bar, "count", "gt", str(top))
        _expect(problems, not visible_ids(), f"「大于 {top}」不应有结果，实际 {sorted(visible_ids())}")
        _set_number_filter(page.filter_bar, "count", "lt", "0")
        _expect(problems, not visible_ids(), "「小于 0」不应有结果")
        _set_number_filter(page.filter_bar, "count", "between", "0", str(top))
        _expect(
            problems,
            visible_ids() == {key for key, value in counts.items() if 0 < value < top},
            f"区间（严格两侧）应只命中条目数在 0 与 {top} 之间的存档，实际 {sorted(visible_ids())}",
        )
        page.filter_bar.reset()
        _expect(problems, len(page._visible) == len(page._archives), "重置数值筛选后应恢复全部存档")

        _set_date_filter(page.filter_bar, "created", "after", QDate.currentDate().addDays(-1))
        _expect(problems, len(page._visible) == len(page._archives), "「在该日之后」选昨天应命中今天创建的存档")
        _set_date_filter(page.filter_bar, "created", "before", QDate.currentDate().addDays(-1))
        _expect(problems, not page._visible, "「在该日之前」选昨天不应命中今天创建的存档")
        _set_date_filter(page.filter_bar, "created", "day", QDate.currentDate())
        _expect(problems, len(page._visible) == len(page._archives), "「在该日」选今天应命中全部存档")
        _set_date_filter(
            page.filter_bar,
            "created",
            "range",
            QDate.currentDate().addDays(-1),
            QDate.currentDate(),
        )
        _expect(problems, len(page._visible) == len(page._archives), "时间区间含首尾两天，应命中全部存档")
        page.filter_bar.reset()

        _select_archive(page, int(archive.id))
        entries = list(page._all_entries)
        _expect(problems, len(entries) >= 2, f"存档内应有至少 2 个条目，实际 {len(entries)}")
        sizes = {int(entry.id): float(page._entry_numbers[index]["size"]) for index, entry in enumerate(entries)}
        biggest = max(sizes.values())
        _set_number_filter(page.entry_filter_bar, "size", "gt", "0")
        _expect(
            problems,
            page.table.rowCount() == sum(1 for value in sizes.values() if value > 0),
            "「大小大于 0」应命中所有非空条目",
        )
        _set_number_filter(page.entry_filter_bar, "size", "eq", "1KB")
        _expect(
            problems,
            page.table.rowCount() == sum(1 for value in sizes.values() if value == 1024),
            "大小输入应支持 1KB 这类带单位写法",
        )
        _set_number_filter(page.entry_filter_bar, "size", "eq", str(int(biggest)))
        _expect(
            problems,
            page.table.rowCount() == sum(1 for value in sizes.values() if value == biggest),
            f"「等于 {int(biggest)}」应只命中大小相同的条目",
        )
        page.entry_filter_bar.reset()
        _expect(problems, page.table.rowCount() == len(entries), "重置大小筛选后应恢复全部条目")

        expected_groups: dict[str, set] = {}
        for entry in entries:
            expected_groups.setdefault(entry.user_name or "未知用户", set()).add(entry.category or "—")
        actual_groups = {title: set(keys) for title, keys in page.entry_category_section._groups}
        _expect(
            problems,
            actual_groups == expected_groups,
            f"分类应按「用户名 / 分类」分组，实际 {actual_groups} 应为 {expected_groups}",
        )
        category = next(
            (entry.category or "—" for entry in entries if int(entry.id) == int(tagged.id)), "—"
        )
        _expect(problems, category in page.entry_category_section._boxes, f"分类分组里应列出 {category}")
        tagged_by_category = sum(1 for entry in entries if (entry.category or "—") == category)
        page.entry_category_section._boxes[category].setChecked(True)
        _expect(
            problems,
            page.table.rowCount() == tagged_by_category and tagged_by_category >= 1,
            f"勾选分类 {category} 应命中 {tagged_by_category} 项，实际 {page.table.rowCount()}",
        )
        _expect(problems, page.restore_all_button.text() != "还原整个存档", "分类勾选后应改成只回档筛选结果")
        page.entry_category_section._boxes[category].setChecked(False)
        _expect(problems, page.table.rowCount() == len(entries), "取消分类勾选后应恢复全部条目")

        tag_boxes = page.entry_tag_section.boxes
        _expect(problems, fixture.tag in tag_boxes, f"标签筛选项应含 {fixture.tag}，实际 {sorted(tag_boxes)}")
        tagged_count = sum(1 for entry in entries if fixture.tag in (entry.tags or []))
        tag_boxes[fixture.tag].setChecked(True)
        _expect(
            problems,
            page.table.rowCount() == tagged_count and tagged_count >= 1,
            f"勾选标签 {fixture.tag} 应命中 {tagged_count} 项，实际 {page.table.rowCount()}",
        )
        page.entry_tag_section.all_box.click()
        with_tag = sum(1 for entry in entries if entry.tags)
        _expect(
            problems,
            page.table.rowCount() == with_tag,
            f"标签全选应命中所有带标签的条目（{with_tag}），实际 {page.table.rowCount()}",
        )
        page.entry_tag_section.all_box.click()
        _expect(problems, page.table.rowCount() == len(entries), "标签全不选后应恢复全部条目")
    finally:
        dispose_window(window)
    assert not problems, "存档筛选模式检查未通过：" + "；".join(problems)


@check("viewer_config_page", "pages")
def viewer_config_page(case: Case) -> None:
    """查看器配置页（由 builtin.lib.viewer 插件提供）：格式清单、模式与查看器联动、保存 / 恢复默认。"""
    from pathlib import Path as _Path

    from app.core.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.extensions import extension_registry
    from app.sdk import viewers as viewer_api
    from app.sdk.viewers import open_api
    from app.services.plugin_service import plugin_service

    # 插件登记配置页要先有 app.ui：按真实程序的顺序，先给接口再载入插件
    ui_api = AppUiApi()
    previous_ui = extension_registry.provider(APP_UI_EXTENSION)
    plugin_service.bootstrap(APP_UI_EXTENSION, ui_api)
    install_builtin_plugins()
    registry = open_api()
    assert registry is not None, "载入内置插件后应提供 viewer.open 扩展接口"
    _fixture, window = build_window(case)
    problems: list[str] = []
    page = window._plugin_pages.get("viewer_config")
    _expect(problems, page is not None, "查看器插件应把配置页注册成插件页面 viewer_config")
    assert page is not None, "查看器配置页检查未通过：" + "；".join(problems)
    api = extension_registry.provider(viewer_api.OPEN_EXTENSION)
    _expect(problems, api is not None, "查看器插件应提供 viewer.open 扩展接口")
    assert api is not None, "查看器配置页检查未通过：" + "；".join(problems)

    from dm_plugin.builtin.lib.viewer import config_page as page_module
    from dm_plugin.builtin.lib.viewer.rules import MODE_BUILTIN, MODE_CUSTOM

    seen: list[tuple[str, str]] = []
    original_success = page_module.toast_success
    original_warning = page_module.toast_warning
    page_module.toast_success = lambda _parent, title, content="": seen.append(("success", str(title)))
    page_module.toast_warning = lambda _parent, title, content="": seen.append(("warning", str(title)))
    try:
        window.switchTo(page)
        page.reload()
        _expect(problems, "md" in registry.extensions(), "载入内置插件后注册表应包含 md 扩展名")
        markdown = registry.viewer_by_id("builtin.markdown")
        _expect(problems, markdown is not None, "内置 markdown 查看器应注册为 builtin.markdown")
        _expect(problems, markdown is not None and markdown.host == "dialog", "内置 markdown 查看器应交由界面工具库托管")
        _expect(problems, extension_registry.provider("dialog") is not None, "界面工具库应提供 dialog 扩展")
        _expect(
            problems,
            bool(registry.viewers()) and all(viewer.host == "dialog" for viewer in registry.viewers()),
            "内置查看器都应声明依赖界面工具库",
        )
        _expect(problems, api.resolve(_Path("示例.md")).is_builtin, "md 应解析到内置查看器")

        _expect(problems, page.suffix_list.count() > 0, "格式列表不应为空")
        _expect(problems, page.count_label.text().endswith("个格式"), f"格式计数文案不对：{page.count_label.text()!r}")
        _select_suffix(page, "md")
        _expect(problems, page.detail_title.text() == ".md", f"详情标题应为 .md，实际 {page.detail_title.text()!r}")
        _expect(problems, "可用查看器" in page.detail_viewers.text(), "详情应列出可用查看器")
        _expect(problems, "builtin.markdown" in page.detail_viewers.text(), "可用查看器里应含 builtin.markdown")

        custom_index = page.mode_box.findData(MODE_CUSTOM)
        builtin_index = page.mode_box.findData(MODE_BUILTIN)
        _expect(problems, builtin_index >= 0 and custom_index >= 0, "模式下拉应同时提供内置与自定义模式")
        _expect(problems, page.viewer_box.count() >= 2 and page.viewer_box.itemData(0) == "", "查看器下拉应以「自动」开头")
        _expect(problems, page.hint_label.text().strip() != "", "应给出当前模式的说明文案")

        page.mode_box.setCurrentIndex(custom_index)
        _expect(problems, page.program_edit.isEnabled(), "自定义模式应启用程序路径输入")
        _expect(problems, page.browse_button.isEnabled(), "自定义模式应启用「浏览」按钮")
        _expect(problems, page.args_edit.isEnabled(), "自定义模式应启用参数输入")
        page.mode_box.setCurrentIndex(builtin_index)
        _expect(problems, not page.program_edit.isEnabled(), "内置模式应禁用程序路径输入")
        _expect(problems, not page.browse_button.isEnabled(), "内置模式应禁用「浏览」按钮")

        viewer_index = page.viewer_box.findData("builtin.markdown")
        _expect(problems, viewer_index >= 0, "查看器下拉应列出 builtin.markdown")
        page.viewer_box.setCurrentIndex(viewer_index)
        page._on_save()
        rule = api.rule_for("md")
        _expect(problems, rule is not None and rule.viewer_id == "builtin.markdown", f"保存后 md 规则应指向选中的查看器，实际 {rule}")
        _expect(problems, [kind for kind, _title in seen if kind == "success"], "保存成功应给出提示")
        _select_other_suffix(page, "md")
        _select_suffix(page, "md")
        _expect(problems, "builtin.markdown" in page.detail_meta.text(), f"保存后状态应显示使用的查看器，实际 {page.detail_meta.text()!r}")

        page._on_reset()
        reset_rule = api.rule_for("md")
        _expect(
            problems,
            reset_rule is None or not reset_rule.viewer_id,
            f"恢复默认后 md 不应再指定查看器，实际 {reset_rule}",
        )

        entries = window.page_entries()
        titles = {entry.title for entry in entries}
        _expect(problems, "查看器" in titles, f"插件配置页应出现在页面清单里，实际 {sorted(titles)}")
    finally:
        page_module.toast_success = original_success
        page_module.toast_warning = original_warning
        plugin_service.bootstrap(APP_UI_EXTENSION, previous_ui if previous_ui is not None else ui_api)
        dispose_window(window)
    assert not problems, "查看器配置页检查未通过：" + "；".join(problems)


@check("plugin_page_detail", "pages")
def plugin_page_detail(case: Case) -> None:
    """插件页：贡献 / 来源筛选、详情字段、启停开关与越权拒绝。"""
    from PyQt6.QtCore import Qt

    from app.sdk import ExtensionPoint
    from app.sdk.viewers import open_api
    from app.services.plugin_service import SOURCE_BUILTIN, plugin_service

    install_builtin_plugins()
    registry = open_api()
    _fixture, window = build_window(case)
    page = window.plugin_page
    problems: list[str] = []
    plugin_ids = _builtin_plugin_ids()
    try:
        with _ToastRecorder(page) as toasts:
            expected_total = len(plugin_ids)
            _expect(
                problems,
                sorted(_listed_plugin_ids(page)) == sorted(plugin_ids),
                f"插件列表应列出全部内置插件，实际 {sorted(_listed_plugin_ids(page))}",
            )
            _expect(
                problems,
                page.count_label.text() == f"共 {expected_total} 个插件",
                f"插件计数文案不对：{page.count_label.text()!r}",
            )
            _expect(problems, page.reset_button.isHidden(), "没有筛选时不该出现「清除筛选」按钮")

            by_point = _point_counts()
            for point, expected in {**by_point, "": expected_total}.items():
                page.apply_contribution(point)
                got = len(_listed_plugin_ids(page))
                _expect(problems, got == expected, f"贡献 {point or '全部'} 应筛出 {expected} 个插件，实际 {got}")
                _expect(
                    problems,
                    page.count_label.text()
                    == (
                        f"已筛选：{expected} / 共 {expected_total} 个插件"
                        if point
                        else f"共 {expected_total} 个插件"
                    ),
                    f"贡献 {point or '全部'} 的计数文案不对：{page.count_label.text()!r}",
                )
                _expect(
                    problems,
                    page.reset_button.isHidden() is not bool(point),
                    f"贡献 {point or '全部'} 的「清除筛选」可见性不对",
                )
            _expect(problems, ExtensionPoint.VIEWER in by_point, f"应有登记 {ExtensionPoint.VIEWER} 贡献的插件")
            page.apply_contribution("app.ui.nonexistent")
            _expect(problems, page.point_box.currentData() in ("", None), "未知贡献应回到「全部贡献」")
            _expect(
                problems,
                page.point_box.count() == 1 + len(ExtensionPoint.values()),
                f"贡献下拉应是「全部贡献」+ {len(ExtensionPoint.values())} 个扩展点，实际 {page.point_box.count()} 项",
            )
            _expect(problems, page.order_box.count() >= 5, "排序下拉应有默认顺序 / 名称 / 类型等方案")
            _expect(problems, page.author_box.count() >= 2, "创建者下拉应有「全部创建者」与至少一个创建者")

            _expect(
                problems,
                bool(page.plugin_list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable),
                "插件列表项应可勾选",
            )
            _expect(
                problems,
                "内置" in page.plugin_list.item(0).text() and "·" in page.plugin_list.item(0).text(),
                f"插件列表项应显示类型与来源，实际 {page.plugin_list.item(0).text()!r}",
            )

            page.source_box.setCurrentIndex(page.source_box.findData(SOURCE_BUILTIN))
            _expect(problems, len(_listed_plugin_ids(page)) == expected_total, "按「内置」筛选应包含全部清单插件")
            _expect(
                problems,
                all("内置" in page.plugin_list.item(row).text() for row in range(page.plugin_list.count())),
                "内置插件的列表项应标注「内置」",
            )
            page.source_box.setCurrentIndex(0)

            _expect(problems, page._select_plugin("builtin.image"), "应能在列表里选中 builtin.image")
            info = plugin_service.get("builtin.image")
            _expect(problems, info is not None and info.has_options, "内置图片插件应声明可配置选项")
            protocol = page.detail_protocol.text()
            for label in ("贡献", "依赖插件", "扩展接口", "提供库", "适配 SDK", "适用管理器版本", "入口文件"):
                _expect(problems, label in protocol, f"协议行缺少「{label}」：{protocol!r}")
            _expect(problems, f"插件选项（{len(info.options)}）" in page.detail_options.text(), f"选项行不对：{page.detail_options.text()!r}")
            _expect(problems, "plugin.json" in page.detail_path.text(), f"应显示清单路径，实际 {page.detail_path.text()!r}")
            _expect(
                problems,
                "data/viewer.json" in page.detail_ext.text(),
                f"应列出清单数据文件，实际 {page.detail_ext.text()!r}",
            )
            _expect(
                problems,
                registry.viewer_by_id("builtin.image") is not None
                and "png" in registry.viewer_by_id("builtin.image").extensions,
                "内置图片插件应在清单数据里声明 png 扩展名",
            )
            _expect(problems, page.detail_meta.text().startswith("builtin.image"), f"副标题应以插件 id 开头，实际 {page.detail_meta.text()!r}")
            _expect(problems, page.options_button.text() == "插件选项", "选项按钮文案应为「插件选项」")
            _expect(problems, not page.delete_button.isEnabled(), "内置插件不应可删除")
            _expect(problems, page.reveal_button.isEnabled(), "内置插件应可打开插件目录")
            _expect(problems, page.toggle_button.text() == "禁用", f"已启用插件的按钮应为「禁用」，实际 {page.toggle_button.text()!r}")

            if info is not None:
                from app.ui.plugin_options_dialog import PluginOptionsDialog

                dialog = PluginOptionsDialog(info, page.service, parent=window)
                try:
                    _expect(
                        problems,
                        len(dialog._editors) == len(info.options),
                        f"选项对话框应生成 {len(info.options)} 个编辑器，实际 {len(dialog._editors)}",
                    )
                    _expect(problems, bool(dialog._checks), "选项对话框应列出该插件支持的扩展名")
                finally:
                    _drop_widget(dialog)

            page.reverse_button.setChecked(True)
            _expect(problems, page.reverse_button.text() == "逆序", "勾选排序开关应显示「逆序」")
            page.reverse_button.setChecked(False)
            _expect(problems, page.reverse_button.text() == "正序", "取消排序开关应显示「正序」")

            _expect(problems, page._select_plugin("builtin.markdown"), "应能选中 builtin.markdown")
            enabled_before = plugin_service.get("builtin.markdown").enabled
            page._on_toggle()
            _expect(
                problems,
                plugin_service.get("builtin.markdown").enabled is not enabled_before,
                "点「禁用」后插件启用状态应翻转",
            )
            _expect(problems, toasts.titles("success"), "启停成功应给出提示")
            page._select_plugin("builtin.markdown")
            page._on_toggle()
            _expect(
                problems,
                plugin_service.get("builtin.markdown").enabled is enabled_before,
                "再点一次应恢复原来的启用状态",
            )

            page.apply_contribution("")
            page.select_all()
            _expect(problems, len(page.checked_infos()) == expected_total, f"全选应勾选 {expected_total} 个插件，实际 {len(page.checked_infos())}")
            _expect(
                problems,
                page.batch_enable_button.isEnabled() and page.batch_disable_button.isEnabled() and page.batch_remove_button.isEnabled(),
                "管理员勾选后三个批量按钮都应可用",
            )

            before = len(_listed_plugin_ids(page))
            page._is_admin = False
            page._apply_permissions()
            _expect(problems, not page.permission_hint.isHidden(), "非管理员应看到权限提示")
            _expect(problems, not page.toggle_button.isEnabled(), "非管理员不应能切换插件状态")
            _expect(problems, not page.delete_button.isEnabled(), "非管理员不应能删除插件")
            _expect(problems, not page.batch_remove_button.isEnabled(), "非管理员批量按钮应禁用")
            page._on_toggle()
            page._on_batch_remove()
            _expect(problems, len(_listed_plugin_ids(page)) == before, "越权操作不应改变插件列表")
            _expect(problems, toasts.titles("warning"), "越权操作应给出「无权操作」提示")
            page._is_admin = True
            page._apply_permissions()
            page.select_none()
            _expect(problems, not page.checked_infos(), "取消全选后不应有勾选")
            _expect(problems, not page.batch_remove_button.isEnabled(), "无勾选时批量按钮应禁用")
            page.select_all_box.click()
            _expect(problems, len(page.checked_infos()) == expected_total, "点三态全选框应勾选全部插件")
            page.select_all_box.click()
            _expect(problems, not page.checked_infos(), "再点一次三态全选框应取消勾选")
    finally:
        dispose_window(window)
    assert not problems, "插件页检查未通过：" + "；".join(problems)


@check("plugin_injected_pages", "pages")
def plugin_injected_pages(case: Case) -> None:
    """插件页面注入：注册 / 卸载导航页，工厂报错时退化为提示页。"""
    from PyQt6.QtWidgets import QWidget

    from app.core.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.extensions import extension_registry
    from app.services.plugin_service import plugin_service

    _fixture, window = build_window(case)
    api = AppUiApi()
    previous = extension_registry.provider(APP_UI_EXTENSION)
    problems: list[str] = []
    try:
        plugin_service.bootstrap(APP_UI_EXTENSION, api)
        api.add_page(
            "selfcheck",
            "自检页面",
            lambda: QWidget(window),
            icon="APPLICATION",
            plugin_id="selfcheck.plugin",
        )
        api.add_page("broken", "报错页面", _broken_page_factory, plugin_id="selfcheck.plugin")
        window._sync_plugin_pages()

        pages = window.plugin_pages()
        _expect(problems, pages == ("selfcheck", "broken"), f"应装配两个插件页面，实际 {pages}")

        page_widget = window._plugin_pages.get("selfcheck")
        broken_widget = window._plugin_pages.get("broken")
        _expect(problems, page_widget is not None, "缺少已注册的插件页面控件")
        _expect(
            problems,
            page_widget is not None and page_widget.objectName() == "plugin.selfcheck",
            f"插件页面控件应带上 plugin.<key> 的 objectName，实际 {getattr(page_widget, 'objectName', lambda: '')()}",
        )
        _expect(problems, page_widget is not None and window.stackedWidget.isAncestorOf(page_widget), "插件页面应装配进主窗口页面栈")
        if page_widget is not None:
            window.switchTo(page_widget)
            _expect(problems, window.stackedWidget.currentWidget() is page_widget, "导航切换应能打开插件页面")

        text = broken_widget.text() if hasattr(broken_widget, "text") else ""
        _expect(
            problems,
            "插件页面无法显示" in text and "自检：页面工厂故意报错" in text,
            f"工厂报错时应退化成提示页，实际 {text!r}",
        )

        api.remove_page("selfcheck")
        api.remove_page("broken")
        window._sync_plugin_pages()
        _expect(problems, not window.plugin_pages(), f"卸载后不应残留插件页面，实际 {window.plugin_pages()}")
        _expect(problems, not api.pages(), "卸载后接口里也不应残留页面登记")
    finally:
        api.clear()
        plugin_service.bootstrap(APP_UI_EXTENSION, previous if previous is not None else api)
        dispose_window(window)
    assert not problems, "插件页面注入检查未通过：" + "；".join(problems)


@check("image_viewer", "pages")
def image_viewer(case: Case) -> None:
    """图片查看器：加载真实图片、缩放 / 适应窗口 / 原尺寸与内置查看器注册。"""
    from PyQt6.QtGui import QPixmap

    from app.sdk.viewers import open_api
    from app.services.plugin_service import plugin_service
    install_builtin_plugins()
    registry = open_api()
    from dm_plugin.builtin.image.image_view import ZOOM_STEP, ImageViewer
    _fixture, window = build_window(case)
    problems: list[str] = []
    viewer = None
    built = None
    try:
        _expect(problems, SAMPLE_IMAGE.is_file(), f"自检图片不存在：{SAMPLE_IMAGE}")
        info = plugin_service.get("builtin.image")
        _expect(problems, info is not None and info.enabled, "内置图片插件应已启用")
        registered = registry.viewer_by_id("builtin.image")
        _expect(problems, registered is not None, "内置图片查看器应注册为 builtin.image")
        _expect(problems, registered is not None and registered.host == "dialog", "内置图片查看器应交由弹窗插件托管")
        _expect(problems, "png" in registry.extensions(), "查看器注册表应包含 png")

        viewer = ImageViewer(SAMPLE_IMAGE, parent=window, fit_on_open=False, smooth=False)
        viewer.resize(400, 300)
        viewer.show()
        ensure_app().processEvents()
        viewer._apply()
        _expect(problems, not viewer._pixmap.isNull(), "图片查看器应成功加载真实图片")
        _expect(problems, viewer._scale == 1.0, f"关闭适应窗口后应按原尺寸显示，实际 {viewer._scale}")
        _expect(problems, viewer._zoom_label.text() == "100%", f"缩放标签应显示 100%，实际 {viewer._zoom_label.text()!r}")
        _expect(problems, not viewer._label.pixmap().isNull(), "标签上应画出图片")

        viewer._zoom(viewer._step_factor)
        _expect(problems, viewer._scale > 1.0, f"放大后比例应大于 1，实际 {viewer._scale}")
        viewer._zoom(1 / viewer._step_factor)
        _expect(problems, abs(viewer._scale - 1.0) < 1e-9, f"缩回来应回到原尺寸，实际 {viewer._scale}")

        width, height = viewer._viewport_size()
        _expect(problems, width > 8 and height > 8, f"查看器视口应当已布局，实际 {width}×{height}")
        viewer._fit_window()
        _expect(problems, 0.0 < viewer._scale < 1.0, f"400×300 里适应窗口应把 512×512 缩到 1 以下，实际 {viewer._scale}")
        viewer._actual_size()
        _expect(problems, viewer._scale == 1.0, f"「原始大小」应回到 1.0，实际 {viewer._scale}")

        small = case.root / "selfcheck_small.png"
        pixmap = QPixmap(40, 20)
        pixmap.fill()
        assert pixmap.save(str(small)), "自检小图写入失败"
        tiny = ImageViewer(small, parent=window)
        try:
            tiny.resize(400, 300)
            tiny.show()
            ensure_app().processEvents()
            tiny._apply()
            _expect(problems, not tiny._pixmap.isNull(), "小图也应能加载显示")
            _expect(problems, tiny._scale > 1.0, f"小图在自适应模式下应放大显示，实际缩放 {tiny._scale:.3f}")
            _expect(problems, tiny._label.width() > 40, "自适应放大后图片控件宽度应跟着变大")
        finally:
            _drop_widget(tiny)

        if registered is not None and callable(registered.factory):
            built = registered.factory(SAMPLE_IMAGE, None)
            _expect(problems, isinstance(built, ImageViewer), "内置工厂应返回图片查看器")
            _expect(problems, built is not None and built._step_factor == ZOOM_STEP, "默认缩放步长应为 1.25")
            _expect(problems, built is not None and built._fit is True, "插件选项默认打开时应适应窗口")
    finally:
        _drop_widget(built)
        _drop_widget(viewer)
        dispose_window(window)
    assert not problems, "图片查看器检查未通过：" + "；".join(problems)


@check("plugin_page_contributions", "pages")
def plugin_page_contributions(case: Case) -> None:
    """插件页按贡献分组：界面上没有类型概念，贡献筛选与查看器注册表一致。"""
    from app.sdk import ExtensionPoint
    from app.sdk.viewers import open_api

    install_builtin_plugins()
    registry = open_api()
    _fixture, window = build_window(case)
    page = window.plugin_page
    problems: list[str] = []
    try:
        _expect(problems, not hasattr(page, "kind_box"), "插件页不应再保留类型筛选（kind_box）")
        _expect(
            problems,
            page.point_box.count() == 1 + len(ExtensionPoint.values()),
            f"贡献下拉应是「全部贡献」+ {len(ExtensionPoint.values())} 个扩展点，实际 {page.point_box.count()} 项",
        )
        page.apply_contribution(ExtensionPoint.VIEWER)
        got = set(_listed_plugin_ids(page))
        expected = set(registry.plugin_ids())
        _expect(problems, got == expected, f"「查看器」筛选应等于查看器注册表的插件：{sorted(got)} != {sorted(expected)}")
        label = ExtensionPoint.label(ExtensionPoint.VIEWER)
        rows = {
            _listed_plugin_ids(page)[index]: page.plugin_list.item(index).text()
            for index in range(page.plugin_list.count())
        }
        row = rows.get("builtin.markdown", "")
        _expect(problems, label in row, f"插件行应显示贡献「{label}」，实际 {row!r}")
        page.apply_contribution("")
        _expect(problems, len(_listed_plugin_ids(page)) == len(_builtin_plugin_ids()), "回到「全部贡献」应列出全部插件")
    finally:
        dispose_window(window)
    assert not problems, "插件贡献检查未通过：" + "；".join(problems)


@check("archive_restore_dialog", "pages")
def archive_restore_dialog(case: Case) -> None:
    """回档变更弹窗：没有变更不弹窗；取消不动数据；「先存档再回档」先留快照再回档。"""
    import app.ui.pages.archive_page as archive_module
    from app.repositories import ItemFilter, ItemRepository
    from app.services import ArchiveService, ImportService, ItemService

    session = case.session
    item = ImportService(session).import_text("回档弹窗笔记", "弹窗里的原始内容")
    assert item is not None, "导入文本失败"
    session.commit()
    service = ArchiveService(session)
    archive = service.create(note="弹窗基线")
    session.commit()

    _fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    dialogs: list[object] = []
    original_dialog = archive_module.RestoreDialog

    class _StubDialog:
        """替身弹窗：记下预览报告、方式与选择。"""

        CANCEL = original_dialog.CANCEL
        RESTORE = original_dialog.RESTORE
        SNAPSHOT = original_dialog.SNAPSHOT
        choice = CANCEL

        def __init__(self, report, parent=None, *, allow_mirror=False, preview=None) -> None:
            self.report = report
            self.allow_mirror = allow_mirror
            self.preview = preview
            self.mode = getattr(report, "mode", "restore")
            self.choice = _StubDialog.choice
            dialogs.append(self)

        def exec(self) -> bool:
            return self.choice != "cancel"

    archive_module.RestoreDialog = _StubDialog
    widget = None
    admin_widget = None
    try:
        with _ToastRecorder(page) as toast:
            # 1) 没有变更：不弹窗，直接提示无需回档
            result = page._confirm_and_restore(archive)
            _expect(problems, result is None, "没有变更时不该回档")
            _expect(problems, not dialogs, "没有变更时不该弹变更清单")
            _expect(
                problems,
                toast.titles("success") == ["无需回档"],
                f"没有变更时的提示不对：{toast.messages}",
            )

            # 2) 有变更但取消：弹窗出现、数据不动
            ItemService(session).update(item, content="弹窗里改过的内容")
            session.commit()
            _StubDialog.choice = "cancel"
            result = page._confirm_and_restore(archive)
            _expect(problems, result is None, "取消后不该回档")
            _expect(problems, len(dialogs) == 1, "有变更时应弹一次变更清单")
            _expect(
                problems,
                dialogs and dialogs[0].report.restored == 1,
                "变更清单的数字应与预览一致",
            )
            session.refresh(item)
            _expect(problems, item.content == "弹窗里改过的内容", "取消后数据不该变")

            # 3) 先存档再回档：快照 + 复原
            _StubDialog.choice = "snapshot"
            before = len(service.history(limit=200))
            result = page._confirm_and_restore(archive)
            _expect(problems, result is not None, "确认后应执行回档")
            if result is not None:
                snapshot = result.snapshot
                _expect(problems, snapshot is not None, "「先存档再回档」应先建快照")
                _expect(
                    problems,
                    snapshot is not None and snapshot.name.startswith("回档前快照"),
                    f"快照名字不对：{snapshot.name if snapshot else ''}",
                )
                _expect(problems, result.restored == 1, f"回档结果不对：{result.summary()}")
            _expect(problems, len(service.history(limit=200)) == before + 1, "快照数量不对")
            contents = sorted(
                row.content or ""
                for row in ItemRepository(session).query(ItemFilter())
                if row.name == "回档弹窗笔记"
            )
            _expect(problems, "弹窗里的原始内容" in contents, f"回档没写回内容：{contents}")
            _expect(
                problems,
                toast.titles("success")[-1:] == ["回档完成"],
                f"回档完成的提示不对：{toast.messages}",
            )

        # 3.5) 管理员整档回档：弹窗应能切换覆盖式并重新预演；条目回档不给切换
        _StubDialog.choice = "cancel"
        page._confirm_and_restore(archive)
        _expect(problems, dialogs and dialogs[-1].allow_mirror, "整档回档应允许覆盖式")
        preview = dialogs[-1].preview
        mirror_view = preview("mirror") if preview else None
        _expect(
            problems,
            mirror_view is not None and mirror_view.mode == "mirror",
            "切换覆盖式时应能重新预演",
        )
        ItemService(session).update(item, content="条目回档前又改了一次")
        session.commit()
        page._confirm_and_restore(service.entries(archive)[:1])
        _expect(problems, dialogs and not dialogs[-1].allow_mirror, "条目回档不该允许覆盖式")

        # 4) 弹窗本体：标题、列、按钮与三个选择
        report = service.preview_restore(archive)
        _expect(problems, not report.is_empty, "这一步应有变更清单可用")
        widget = original_dialog(report, window)
        headers = [widget.tree.headerItem().text(index) for index in range(3)]
        _expect(problems, headers == ["文件", "分类", "归属"], f"弹窗列标题不对：{headers}")
        _expect(
            problems,
            widget.tree.topLevelItemCount() == len(report.grouped()),
            "变更清单的分组行数不对",
        )
        _expect(problems, widget.yesButton.text() == "确认回档", "确认按钮文案不对")
        _expect(problems, widget.cancelButton.text() == "取消", "取消按钮文案不对")
        _expect(problems, widget.snapshotButton.text() == "先存档再回档", "先存档按钮文案不对")
        _expect(problems, widget.choice == original_dialog.CANCEL, "默认选择应是取消")
        widget.snapshotButton.click()
        _expect(problems, widget.choice == original_dialog.SNAPSHOT, "点「先存档再回档」的选择不对")
        _expect(problems, not hasattr(widget, "mirrorRadio"), "条目回档不该出现方式切换")

        # 5) 整档回档：管理员可切换覆盖式，头部与摘要跟着刷新
        admin_widget = original_dialog(
            report,
            window,
            allow_mirror=True,
            preview=lambda mode: service.preview_restore(archive, mode),
        )
        _expect(problems, hasattr(admin_widget, "mirrorRadio"), "整档回档应给出覆盖式切换")
        _expect(
            problems,
            admin_widget.restoreRadio.isChecked() and not admin_widget.mirrorRadio.isChecked(),
            "默认方式应是恢复式",
        )
        admin_widget.mirrorRadio.click()
        _expect(problems, admin_widget.mode == "mirror", "切换后方式应为覆盖式")
        _expect(
            problems,
            "覆盖式" in admin_widget.sourceLabel.text(),
            f"切换后头部应显示覆盖式：{admin_widget.sourceLabel.text()}",
        )
        _expect(
            problems,
            admin_widget.summaryLabel.text() == admin_widget._summary_text(),
            "切换后摘要应跟着刷新",
        )
        admin_widget.restoreRadio.click()
        _expect(problems, admin_widget.mode == "restore", "切回恢复式的方式不对")
    finally:
        archive_module.RestoreDialog = original_dialog
        _drop_widget(widget)
        _drop_widget(admin_widget)

    assert not problems, "；".join(problems)


@check("archive_usage_ui", "pages")
def archive_usage_ui(case: Case) -> None:
    """存档占用口径：表格与统计报真实落盘占用，自动清理文案进提示框，详情标签与回档计划同源。"""
    import app.ui.pages.archive_page as archive_module
    from app.core.signals import signalBus
    from app.services import ArchiveService, ImportService, ItemService
    from app.ui.framework import format_size

    session = case.session
    item = ImportService(session).import_text("占用笔记", "占用口径里的内容" * 60)
    assert item is not None, "导入文本失败"
    session.commit()
    service = ArchiveService(session)
    archive = service.create(note="占用基线")
    session.commit()
    _seed_archives(case)

    _fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    original_dialog = archive_module.RestoreDialog
    original_input = archive_module.TextInputDialog
    dialogs: list[object] = []
    emitted: list[int] = []

    class _StubDialog:
        choice = original_dialog.CANCEL

        def __init__(self, report, parent=None, *, allow_mirror=False, preview=None) -> None:
            self.report = report
            dialogs.append(self)

        def exec(self) -> bool:
            return False

    class _StubInput:
        """替身输入框：直接确认，避免自检弹出真窗口。"""

        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self) -> bool:
            return True

        def value(self) -> str:
            return "自检新建存档"

    archive_module.RestoreDialog = _StubDialog
    archive_module.TextInputDialog = _StubInput
    handler = signalBus.archivesChanged.connect(lambda *_: emitted.append(1))
    try:
        table = page.archive_list
        headers = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
        _expect(problems, "大小" not in headers, f"「大小」列与「实际占用」重复，应该去掉：{headers}")
        actual_column = headers.index("实际占用")
        dedupe_column = headers.index("去重率")

        row = _row_of_archive(page, int(archive.id))
        _expect(problems, row >= 0, "存档表里找不到自检存档")
        actual_text = table.item(row, actual_column).text() if row >= 0 else ""
        _expect(problems, actual_text not in ("0 B", "", "—"), f"实际占用应是真实大小：{actual_text!r}")
        usage = service.archive_usage()
        _expect(
            problems,
            format_size(int(usage["stored_size"])) == actual_text,
            f"实际占用应与服务层一致：{actual_text!r} / {usage}",
        )
        dedupe_text_value = table.item(row, dedupe_column).text() if row >= 0 else ""
        _expect(problems, dedupe_text_value != "100%", f"有压缩收益时去重率不该是 100%：{dedupe_text_value!r}")

        stats = page.archive_stats.text()
        _expect(problems, "总占用" in stats, f"存档统计应带总占用：{stats!r}")
        _expect(
            problems,
            format_size(int(usage["stored_size"])) in stats,
            f"总占用应与服务层一致：{stats!r} / {usage}",
        )

        # ③ 自动清理文案并进「按策略清理」的提示框，标题下不再占一行
        tip = page.prune_button.toolTip()
        _expect(problems, not page.policy_label.isVisible(), "标题下的自动清理文案应隐藏")
        _expect(
            problems,
            service.policy_summary() in tip and "创建存档时自动执行" in tip,
            f"清理策略应进提示框：{tip!r}",
        )

        # 详情标签与回档提示同源：软删一条后应描述回档计划，而不是另一套口径
        ItemService(session).delete([item])
        session.commit()
        _select_archive(page, int(archive.id))
        label = page.diff_label.text()
        _expect(problems, label.startswith("回档将"), f"有变更时详情标签应描述回档计划：{label!r}")
        with _ToastRecorder(page) as toast:
            page._confirm_and_restore(archive)
        _expect(problems, bool(dialogs), "有变更时点回档应弹变更清单")
        _expect(
            problems,
            toast.titles("success") != ["无需回档"],
            f"有变更时不该提示无需回档：{toast.messages}",
        )

        # ②b 存档页自己改动集合时广播 archivesChanged，概览才能立刻更新
        emitted.clear()
        page._on_create()
        _expect(problems, bool(emitted), "新建存档后应广播 archivesChanged")
    finally:
        archive_module.RestoreDialog = original_dialog
        archive_module.TextInputDialog = original_input
        signalBus.archivesChanged.disconnect(handler)
        dispose_window(window)

    assert not problems, "；".join(problems)


@check("archive_missing_changes", "pages")
def archive_missing_changes(case: Case) -> None:
    """变更清单：内容缺失的条目也要有一行；只有内容缺失时不允许确认回档。"""
    from sqlalchemy import text

    import app.ui.pages.archive_page as archive_module
    from app.db.models import DataItem as Item
    from app.services import ArchiveService, ImportService, ItemService

    session = case.session
    importer = ImportService(session)
    lost = importer.import_text("缺失清单甲", "甲的内容")
    dropped = importer.import_text("缺失清单乙", "乙的内容")
    assert lost is not None and dropped is not None, "导入文本失败"
    session.commit()
    service = ArchiveService(session)
    archive = service.create(name="缺失清单存档", note="缺失")
    assert archive is not None, "创建存档失败"
    session.commit()

    entries = {entry.name: entry for entry in service.entries(archive)}
    assert "缺失清单甲" in entries, "存档里没有自检条目"
    # 把仓库里取不到内容的状态造出来：校验和指向不存在的文件，条目就是「内容缺失」。
    session.execute(
        text("update archive_entries set checksum = :checksum where id = :entry_id"),
        {"checksum": "0" * 64, "entry_id": int(entries["缺失清单甲"].id)},
    )
    ItemService(session).delete([session.get(Item, int(dropped.id))])
    session.commit()
    session.expire_all()

    missing_entries = [entry for entry in service.entries(archive) if entry.name == "缺失清单甲"]
    assert missing_entries, "找不到被改成内容缺失的条目"
    missing_entry = missing_entries[0]

    fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    original_dialog = archive_module.RestoreDialog
    stubbed: list[object] = []
    widget = None
    missing_widget = None
    try:
        report = service.preview_restore(archive)
        kinds = [change.kind for change in report.changes]
        _expect(problems, report.missing == 1, f"应有一项内容缺失：{report.summary()}")
        _expect(problems, kinds.count("内容缺失") == 1, f"内容缺失应出现在变更清单里：{kinds}")
        _expect(problems, "撤销删除" in kinds, f"被删掉的条目也应出现在清单里：{kinds}")
        _expect(problems, report.actionable and not report.is_empty, "有可执行变更时应判为可回档")
        _expect(problems, report.total == len(report.changes), "total 应与清单行数一致")

        widget = original_dialog(report, window)
        groups = [widget.tree.topLevelItem(index).text(0) for index in range(widget.tree.topLevelItemCount())]
        _expect(problems, "内容缺失" in groups, f"弹窗清单应有「内容缺失」分组：{groups}")
        _expect(problems, widget.yesButton.isEnabled(), "有可执行变更时应允许确认")
        _expect(problems, "内容缺失" in widget.summaryLabel.text(), f"摘要应点出内容缺失：{widget.summaryLabel.text()!r}")
        _expect(problems, widget.summaryLabel.text() == widget._summary_text(), "摘要应自洽")

        # 只有内容缺失：清单照列，但没有任何可执行变更，不该允许确认。
        only = service.preview_restore([missing_entry])
        _expect(
            problems,
            only.missing == 1 and not only.actionable and only.is_empty,
            f"只有内容缺失时应判为不可回档：{only.summary()}",
        )
        _expect(problems, [change.kind for change in only.changes] == ["内容缺失"], f"清单应列出内容缺失：{only.changes}")
        missing_widget = original_dialog(only, window)
        _expect(problems, not missing_widget.yesButton.isEnabled(), "只有内容缺失时不该允许确认回档")
        _expect(problems, not missing_widget.snapshotButton.isEnabled(), "只有内容缺失时不该允许先存档再回档")
        _expect(
            problems,
            missing_widget.tree.topLevelItemCount() == len(only.grouped()) == 1,
            "只有内容缺失时清单也应有一行",
        )

        _select_archive(page, int(archive.id))
        label = page.diff_label.text()
        _expect(problems, label.startswith("回档将"), f"有可执行变更时详情应描述回档计划：{label!r}")
        _expect(problems, "内容缺失" in label, f"详情应点出内容缺失：{label!r}")
        page_archive = page._current_archive()
        page_missing = [entry for entry in page.service.entries(page_archive) if entry.name == "缺失清单甲"]
        _expect(problems, len(page_missing) == 1, "页面上应能看到内容缺失的条目")
        if page_missing:

            class _RecorderDialog:
                def __init__(self, *args, **kwargs) -> None:
                    stubbed.append(self)

            archive_module.RestoreDialog = _RecorderDialog
            with _ToastRecorder(page) as toast:
                page._confirm_and_restore(page_missing)
            archive_module.RestoreDialog = original_dialog
            _expect(problems, not stubbed, "只有内容缺失时不该弹变更清单")
            _expect(
                problems,
                toast.titles("warning") == ["无法回档"],
                f"只有内容缺失时应提示无法回档：{toast.messages}",
            )
    finally:
        archive_module.RestoreDialog = original_dialog
        _drop_widget(widget)
        _drop_widget(missing_widget)
        dispose_window(window)
    assert not problems, "；".join(problems)


@check("archive_entry_selection", "pages")
def archive_entry_selection(case: Case) -> None:
    """条目勾选：打开存档自动勾选不一致项，全选 / 清空 / 只选不一致 / 跳到下一个不一致。"""
    from PyQt6.QtCore import Qt

    from app.core.signals import signalBus
    from app.db.models import DataItem as Item
    from app.services import ArchiveService, ImportService, ItemService
    from app.ui.framework import accent_color
    from app.ui.pages.archive_page import ENTRY_CHECK_COLUMN, ENTRY_HEADERS, ENTRY_STATE_LABELS

    session = case.session
    importer = ImportService(session)
    kept = importer.import_text("勾选检查甲", "甲的内容")
    gone = importer.import_text("勾选检查乙", "乙的内容")
    assert kept is not None and gone is not None, "导入文本失败"
    session.commit()
    archive = ArchiveService(session).create(name="勾选存档", note="勾选")
    assert archive is not None, "创建存档失败"
    session.commit()
    ItemService(session).delete([session.get(Item, int(gone.id))])
    session.commit()

    _fixture, window = build_window(case)
    page = window.archive_page
    problems: list[str] = []
    restores: list[tuple] = []
    jumped: list[int] = []
    jump_handler = lambda item_id: jumped.append(int(item_id))  # noqa: E731 - 自检里的临时槽
    try:
        _select_archive(page, int(archive.id))
        _expect(problems, ENTRY_HEADERS[ENTRY_CHECK_COLUMN] == "选择", "条目表首列应是「选择」")
        headers = [page.table.horizontalHeaderItem(index).text() for index in range(page.table.columnCount())]
        _expect(problems, headers == list(ENTRY_HEADERS), f"条目表表头应为 {list(ENTRY_HEADERS)}，实际 {headers}")

        name_column = ENTRY_HEADERS.index("名称")
        state_column = ENTRY_HEADERS.index("状态")

        def row_of(name: str) -> int:
            for index in range(page.table.rowCount()):
                if page.table.item(index, name_column).text() == name:
                    return index
            return -1

        same_row = row_of("勾选检查甲")
        bad_row = row_of("勾选检查乙")
        _expect(problems, same_row >= 0 and bad_row >= 0, "条目表里应能看到两条自检条目")
        if same_row >= 0 and bad_row >= 0:
            _expect(
                problems,
                page.table.item(bad_row, state_column).text() == ENTRY_STATE_LABELS["removed"],
                f"被删掉的条目状态应为「已删除」，实际 {page.table.item(bad_row, state_column).text()!r}",
            )
            # 不一致条目要一眼看出来：状态列加粗 + 主题强调色
            _expect(problems, page.table.item(bad_row, state_column).font().bold(), "不一致条目的状态列应加粗")
            _expect(
                problems,
                page.table.item(bad_row, state_column).foreground().color() == accent_color(),
                "不一致条目的状态列应用主题强调色",
            )
            _expect(problems, not page.table.item(same_row, state_column).font().bold(), "一致条目不该被加粗")
            _expect(
                problems,
                bool(page.table.item(bad_row, ENTRY_CHECK_COLUMN).flags() & Qt.ItemFlag.ItemIsUserCheckable),
                "条目表首列应是可勾选的复选框",
            )
            # 打开存档后自动勾选不一致条目
            _expect(
                problems,
                [entry.name for entry in page.checked_entries()] == ["勾选检查乙"],
                f"打开存档应只自动勾选不一致条目：{[entry.name for entry in page.checked_entries()]}",
            )
            _expect(problems, page.entry_selection_label.text() == "已选 1 项", "已选计数不对")
            _expect(
                problems,
                page.entry_select_all_box.checkState() == Qt.CheckState.PartiallyChecked,
                "部分选中时「全选本页」应是横杠",
            )
            _expect(
                problems,
                page.table.item(bad_row, ENTRY_CHECK_COLUMN).checkState() == Qt.CheckState.Checked
                and page.table.item(same_row, ENTRY_CHECK_COLUMN).checkState() == Qt.CheckState.Unchecked,
                "自动勾选应写回勾选框",
            )

            # 双击不一致行 → 请求数据管理页定位
            signalBus.focusItem.connect(jump_handler)
            page._on_entry_double_clicked(bad_row, ENTRY_CHECK_COLUMN)
            signalBus.focusItem.disconnect(jump_handler)
            _expect(problems, jumped == [int(gone.id)], f"双击不一致行应请求定位该数据项：{jumped}")

            # 跳到下一个不一致（只有一项时循环回同一行）
            page._jump_row = -1
            page.entry_jump_button.click()
            _expect(problems, page.table.currentRow() == bad_row, "「下一个不一致」应停在那一行")
            _expect(problems, page._jump_row == bad_row, "「下一个不一致」应记住位置")
            page.entry_jump_button.click()
            _expect(problems, page.table.currentRow() == bad_row, "只有一项不一致时应循环回同一行")

            page.entry_filter_bar.set_filter("state", ENTRY_STATE_LABELS["same"])
            with _ToastRecorder(page) as toast:
                page.jump_to_next_inconsistent()
            _expect(problems, toast.titles("warning") == ["没有不一致的条目"], f"视图里没有不一致条目时应提示：{toast.messages}")
            page.entry_filter_bar.reset()

            # 全选 / 清空 / 只选不一致
            page.select_all_entries()
            _expect(problems, len(page.checked_entries()) == 2, "全选应勾上当前视图全部条目")
            _expect(problems, page.entry_selection_label.text() == "已选 2 项", "全选后的计数不对")
            _expect(
                problems,
                page.entry_select_all_box.checkState() == Qt.CheckState.Checked,
                "全选后「全选本页」应是勾",
            )
            page.select_no_entries()
            _expect(problems, page.checked_entries() == [], "清空选择应取消全部勾选")
            _expect(problems, page.entry_selection_label.text() == "未选择条目", "清空后的计数不对")
            _expect(
                problems,
                page.entry_select_all_box.checkState() == Qt.CheckState.Unchecked,
                "清空后「全选本页」应是空",
            )
            page.entry_inconsistent_button.click()
            _expect(
                problems,
                [entry.name for entry in page.checked_entries()] == ["勾选检查乙"],
                "「只选不一致」应只勾上不一致条目",
            )

            # 手动点勾选框应记入选择集合
            page.table.item(same_row, ENTRY_CHECK_COLUMN).setCheckState(Qt.CheckState.Checked)
            _expect(problems, len(page.checked_entries()) == 2, "手动勾选应记入选择集合")
            page.table.item(bad_row, ENTRY_CHECK_COLUMN).setCheckState(Qt.CheckState.Unchecked)
            _expect(
                problems,
                [entry.name for entry in page.checked_entries()] == ["勾选检查甲"],
                "手动取消勾选应生效",
            )

            # 筛选刷新不该丢勾选
            page.entry_filter_bar.set_filter("name", "勾选检查甲")
            _expect(problems, page.table.rowCount() == 1, "名称筛选应只显示一条")
            page.entry_filter_bar.reset()
            _expect(
                problems,
                [entry.name for entry in page.checked_entries()] == ["勾选检查甲"],
                "筛选刷新后勾选状态不应丢",
            )

            # 「还原选中条目」优先用勾选的条目
            page._confirm_and_restore = lambda *args, **kwargs: restores.append(args)  # type: ignore[method-assign]
            page._on_restore()
            _expect(problems, len(restores) == 1, f"勾选后点还原应发起一次回档：{restores}")
            if restores:
                _expect(
                    problems,
                    [entry.name for entry in restores[0][0]] == ["勾选检查甲"],
                    f"应只还原勾选的条目：{[entry.name for entry in restores[0][0]]}",
                )

            restores.clear()
            page.select_no_entries()
            page.table.clearSelection()
            with _ToastRecorder(page) as toast:
                page._on_restore()
            _expect(problems, not restores, "没勾选也没选行时不该回档")
            _expect(problems, toast.titles("warning") == ["未选择条目"], f"没有选择时应提示：{toast.messages}")
    finally:
        dispose_window(window)
    assert not problems, "；".join(problems)
