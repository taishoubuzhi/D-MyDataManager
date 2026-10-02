"""L2 页面检查：存档页、打开方式页、插件页与图片查看器。

移植自旧门禁 `scripts/dev_check_ui.py` 的 _check_archive_tabs / _check_archive_owner /
_check_archive_pin / _check_archive_table / _check_open_with / _check_plugins /
_check_plugin_pages / _check_image_viewer，只走公开契约（页面属性、页面公开方法、
服务层 API），数据一律在本用例的临时目录与全新数据库上自造。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .fixtures import SAMPLE_IMAGE
from .harness import ROOT, Case, build_window, check, dispose_window, ensure_app

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


def _install_builtin_plugins() -> None:
    """把仓库内置插件复制进隔离插件目录，并让插件服务重新载入。"""
    from app.core import paths
    from app.services.plugin_service import plugin_service

    target = Path(paths.PLUGIN_DIR)
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(BUILTIN_PLUGINS.iterdir()):
        if source.is_dir() and (source / "plugin.json").is_file():
            shutil.copytree(source, target / source.name, dirs_exist_ok=True)
    plugin_service.load_viewers()


def _manifest_kinds() -> dict[str, str]:
    """读仓库内置插件清单，返回 {插件 id: 类型}（用作独立于服务层的期望值）。"""
    kinds: dict[str, str] = {}
    for source in sorted(BUILTIN_PLUGINS.iterdir()):
        manifest = source / "plugin.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        kinds[str(data.get("id"))] = str(data.get("kind") or "")
    return kinds


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
    """在「打开方式」页的格式列表里选中指定扩展名。"""
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
        _expect(problems, all(row[4].isdigit() for row in rows), f"条目数应为数字，实际 {[row[4] for row in rows]}")
        _expect(problems, all(row[6] in ("已标记", "未标记") for row in rows), f"标记列应为已标记/未标记，实际 {[row[6] for row in rows]}")
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
        _expect(problems, table.item(0, 6).text() == "已标记", f"筛选结果的标记列应为「已标记」，实际 {table.item(0, 6).text()!r}")
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


@check("open_with_page", "pages")
def open_with_page(case: Case) -> None:
    """打开方式页：格式清单、模式与查看器联动、保存 / 恢复默认与跳转插件页。"""
    from pathlib import Path as _Path

    from app.core.extensions import extension_registry
    from app.core.plugin_kinds import KIND_VIEWER
    from app.core.viewers import viewer_registry
    from app.services.open_with_service import MODE_BUILTIN, MODE_CUSTOM, open_with_service

    _install_builtin_plugins()
    _fixture, window = build_window(case)
    page = window.open_with_page
    problems: list[str] = []
    try:
        with _ToastRecorder(page) as toasts:
            _expect(problems, "md" in viewer_registry.extensions(), "载入内置插件后注册表应包含 md 扩展名")
            markdown = viewer_registry.by_id("builtin.markdown.1")
            _expect(problems, markdown is not None, "内置 markdown 查看器应注册为 builtin.markdown.1")
            _expect(problems, markdown is not None and markdown.host == "dialog", "内置 markdown 查看器应交由弹窗插件托管")
            _expect(problems, extension_registry.provider("dialog") is not None, "载入内置插件后应注册 dialog 弹窗页面扩展")
            _expect(
                problems,
                bool(viewer_registry.all()) and all(viewer.host == "dialog" for viewer in viewer_registry.all()),
                "内置查看器都应声明依赖 dialog 弹窗页面插件",
            )
            _expect(problems, open_with_service.resolve(_Path("示例.md")).is_builtin, "md 应解析到内置查看器")

            _expect(problems, page.suffix_list.count() > 0, "格式列表不应为空")
            _expect(problems, page.count_label.text().endswith("个格式"), f"格式计数文案不对：{page.count_label.text()!r}")
            _select_suffix(page, "md")
            _expect(problems, page.detail_title.text() == ".md", f"详情标题应为 .md，实际 {page.detail_title.text()!r}")
            _expect(problems, "可用插件" in page.detail_viewers.text(), "详情应列出可用插件")
            _expect(problems, "builtin.markdown" in page.detail_viewers.text(), "可用插件里应含 builtin.markdown")

            custom_index = page.mode_box.findData(MODE_CUSTOM)
            builtin_index = page.mode_box.findData(MODE_BUILTIN)
            _expect(problems, builtin_index >= 0 and custom_index >= 0, "模式下拉应同时提供内置与自定义模式")
            _expect(problems, page.viewer_box.count() >= 2 and page.viewer_box.itemData(0) == "", "查看器下拉应以「自动」开头")
            _expect(problems, page.hint_label.text().strip() != "", "应给出当前模式的说明文案")

            page.mode_box.setCurrentIndex(custom_index)
            _expect(problems, page.program_edit.isEnabled(), "自定义模式应启用程序路径输入")
            _expect(problems, page.browse_button.isEnabled(), "自定义模式应启用「浏览」按钮")
            page.mode_box.setCurrentIndex(builtin_index)
            _expect(problems, not page.program_edit.isEnabled(), "内置模式应禁用程序路径输入")
            _expect(problems, not page.browse_button.isEnabled(), "内置模式应禁用「浏览」按钮")

            viewer_index = page.viewer_box.findData("builtin.markdown.1")
            _expect(problems, viewer_index >= 0, "查看器下拉应列出 builtin.markdown.1")
            page.viewer_box.setCurrentIndex(viewer_index)
            page._on_save()
            rule = open_with_service.rule_for("md")
            _expect(problems, rule is not None and rule.viewer_id == "builtin.markdown.1", f"保存后 md 规则应指向选中的查看器，实际 {rule}")
            _expect(problems, toasts.titles("success"), "保存成功应给出提示")
            _select_other_suffix(page, "md")
            _select_suffix(page, "md")
            _expect(problems, "builtin.markdown" in page.detail_meta.text(), f"保存后状态应显示使用的插件，实际 {page.detail_meta.text()!r}")

            page._on_reset()
            _expect(problems, not open_with_service.rule_for("md").viewer_id, "恢复默认后 md 不应再指定查看器")

            page._on_manage_plugins()
            _expect(problems, window.stackedWidget.currentWidget() is window.plugin_page, "「管理插件」应跳到插件页")
            _expect(problems, window.plugin_page.kind_box.currentData() == KIND_VIEWER, "跳转后插件页应筛成查看器类型")
            window.switchTo(window.open_with_page)
    finally:
        dispose_window(window)
    assert not problems, "打开方式页检查未通过：" + "；".join(problems)


@check("plugin_page_detail", "pages")
def plugin_page_detail(case: Case) -> None:
    """插件页：类型 / 来源筛选、详情字段、启停开关与越权拒绝。"""
    from PyQt6.QtCore import Qt

    from app.core.plugin_kinds import KIND_KIND, KIND_PAGE, KIND_VIEWER
    from app.services.plugin_service import SOURCE_BUILTIN, plugin_service

    _install_builtin_plugins()
    _fixture, window = build_window(case)
    page = window.plugin_page
    problems: list[str] = []
    kinds = _manifest_kinds()
    try:
        with _ToastRecorder(page) as toasts:
            expected_total = len(kinds)
            _expect(
                problems,
                sorted(_listed_plugin_ids(page)) == sorted(kinds),
                f"插件列表应列出全部内置插件，实际 {sorted(_listed_plugin_ids(page))}",
            )
            _expect(
                problems,
                page.count_label.text() == f"{expected_total} / {expected_total} 个插件",
                f"插件计数文案不对：{page.count_label.text()!r}",
            )

            by_kind: dict[str, int] = {}
            for kind in kinds.values():
                by_kind[kind] = by_kind.get(kind, 0) + 1
            by_kind[""] = expected_total
            for kind, expected in by_kind.items():
                page.apply_kind(kind)
                got = len(_listed_plugin_ids(page))
                _expect(problems, got == expected, f"类型 {kind or '全部'} 应筛出 {expected} 个插件，实际 {got}")
            for kind in (KIND_VIEWER, KIND_PAGE, KIND_KIND):
                _expect(problems, kind in by_kind, f"清单里应有 {kind} 类型的插件")
            page.apply_kind("没有的类型")
            _expect(problems, page.kind_box.currentData() in ("", None), "未知类型应回到「全部类型」")
            _expect(
                problems,
                page.kind_box.count() == 1 + len(set(kinds.values())),
                f"类型下拉应是「全部类型」+ {len(set(kinds.values()))} 种插件类型，实际 {page.kind_box.count()} 项",
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
            for label in ("依赖插件", "扩展接口", "功能", "适用管理器版本", "入口文件"):
                _expect(problems, label in protocol, f"协议行缺少「{label}」：{protocol!r}")
            _expect(problems, f"插件选项（{len(info.options)}）" in page.detail_options.text(), f"选项行不对：{page.detail_options.text()!r}")
            _expect(problems, "plugin.json" in page.detail_path.text(), f"应显示清单路径，实际 {page.detail_path.text()!r}")
            _expect(problems, "png" in page.detail_ext.text(), f"应列出扩展名，实际 {page.detail_ext.text()!r}")
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

            page.apply_kind("")
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


def _prune_router_history() -> int:
    """摘掉 qfluentwidgets 全局路由里已被销毁的 StackedWidget。

    库里的 `qrouter` 会把每个窗口的 StackedWidget 一直留在 `stackHistories` 里，
    窗口销毁后再卸载导航页（qrouter.remove → stacked.findChild）就会抛
    `RuntimeError: wrapped C/C++ object of type StackedWidget has been deleted`。
    自检用例反复建销窗口，所以卸载前先清掉失效记录；返回清理条数。
    """
    from PyQt6 import sip
    from qfluentwidgets.common.router import qrouter

    dead = [stacked for stacked in list(qrouter.stackHistories) if sip.isdeleted(stacked)]
    for stacked in dead:
        qrouter.stackHistories.pop(stacked, None)
    return len(dead)


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
        _prune_router_history()
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

    from app.core.viewers import viewer_registry
    from app.services.plugin_service import plugin_service
    from app.ui.viewers.image_view import ZOOM_STEP, ImageViewer

    _install_builtin_plugins()
    _fixture, window = build_window(case)
    problems: list[str] = []
    viewer = None
    built = None
    try:
        _expect(problems, SAMPLE_IMAGE.is_file(), f"自检图片不存在：{SAMPLE_IMAGE}")
        info = plugin_service.get("builtin.image")
        _expect(problems, info is not None and info.enabled, "内置图片插件应已启用")
        registered = viewer_registry.by_id("builtin.image.1")
        _expect(problems, registered is not None, "内置图片查看器应注册为 builtin.image.1")
        _expect(problems, registered is not None and registered.host == "dialog", "内置图片查看器应交由弹窗插件托管")
        _expect(problems, "png" in viewer_registry.extensions(), "查看器注册表应包含 png")

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
