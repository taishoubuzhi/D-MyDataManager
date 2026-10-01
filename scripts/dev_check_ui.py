"""界面层烟测：离屏构建主窗口、刷新各页面，并回归界面接线问题。

用法：.venv\\Scripts\\python.exe scripts\\dev_check_ui.py
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def _configure_stdout() -> None:
    """GBK 控制台下打印 ✔/✗ 会抛 UnicodeEncodeError，这里统一兜底成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_configure_stdout()

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from app.core import paths  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.db.database import init_db, session_scope  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.services.layout_migration import migrate_uncategorized  # noqa: E402

PAGES = (
    "home_page",
    "import_page",
    "manage_page",
    "tag_page",
    "user_page",
    "archive_page",
    "open_with_page",
    "plugin_page",
    "settings_page",
)


def _check_style_uniformity(window) -> list[str]:
    """全站页面骨架尺寸、面板边距与按钮类型是否统一（对齐插件/打开方式/存档/标签页那一套）。"""
    from qfluentwidgets import CardWidget

    from app.ui.common import DETAIL_MARGINS, PAGE_MARGINS, PAGE_SPACING, PANEL_MARGINS

    problems: list[str] = []
    wanted = tuple(PAGE_MARGINS)
    panel_margins = {tuple(PANEL_MARGINS), tuple(DETAIL_MARGINS)}
    legacy_accents = ("#0a84ff", "#0078d4", "rgba(10, 132, 255", "rgba(0, 120, 212")

    for name in PAGES:
        page = getattr(window, name)
        holder = page.widget() if hasattr(page, "widget") else page
        layout = holder.layout()
        if layout is None:
            problems.append(f"{name} 没有页面布局")
            continue

        box = layout.contentsMargins()
        margins = (box.left(), box.top(), box.right(), box.bottom())
        if margins != wanted or layout.spacing() != PAGE_SPACING:
            problems.append(
                f"{name} 页面边距/间距为 {margins}/{layout.spacing()}，应为 {wanted}/{PAGE_SPACING}"
            )

        for widget in holder.findChildren(QPushButton):
            # Fluent 的 PushButton 也是 QPushButton 的子类，只有 Python 类名仍为 QPushButton 的才是原生按钮。
            if type(widget).__name__ == "QPushButton":
                problems.append(f"{name} 使用了原生 QPushButton：{widget.text()}")

        for widget in holder.findChildren(QWidget):
            stylesheet = widget.styleSheet()
            for legacy in legacy_accents:
                if legacy in stylesheet:
                    problems.append(f"{name} 的 {type(widget).__name__} 仍写死强调色 {legacy}")

        for index in range(layout.count()):
            card = layout.itemAt(index).widget()
            if not isinstance(card, CardWidget) or card.layout() is None:
                continue
            box = card.layout().contentsMargins()
            got = (box.left(), box.top(), box.right(), box.bottom())
            if got not in panel_margins:
                problems.append(f"{name} 的面板卡片边距为 {got}，应为 {PANEL_MARGINS} 或 {DETAIL_MARGINS}")
    return problems


def _check_theme_background(app, window) -> list[str]:
    """切成浅色后不允许还有「按旧调色板实绘深色」的控件（运行时切主题最容易漏）。"""
    from qfluentwidgets import Theme, isDarkTheme, setTheme

    problems: list[str] = []
    restore_theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
    restore_page = window.stackedWidget.currentWidget()
    setTheme(Theme.LIGHT)
    app.processEvents()
    try:
        for name in PAGES:
            page = getattr(window, name)
            window.switchTo(page)
            app.processEvents()

            seen: set[str] = set()
            for widget in page.findChildren(QWidget):
                if not widget.isVisible() or widget.width() * widget.height() < 400:
                    continue
                if not widget.autoFillBackground():
                    continue
                color = widget.palette().color(QPalette.ColorRole.Window)
                if color.lightness() >= 90:
                    continue
                label = f"{type(widget).__name__}({widget.objectName()})"
                if label in seen:
                    continue
                seen.add(label)
                problems.append(f"{name} 的 {label} 仍按旧调色板实绘深色 {color.name()}")
    finally:
        setTheme(restore_theme)
        app.processEvents()
        if restore_page is not None:
            window.switchTo(restore_page)
            app.processEvents()
    return problems


def _check_home_stats(window) -> list[str]:
    """首页在构建时就必须显示统计（此前只在信号触发后刷新，首屏恒为 0）。"""
    from app.services import UserService, overview

    page = window.home_page
    stats = overview(page.session, user_id=UserService(page.session).current_id())
    problems = []
    expected = {"数据项": str(stats["total"]), "分类": str(stats["categories"]), "标签": str(stats["tags"])}
    actual = {
        "数据项": page._total_card._value.text(),
        "分类": page._category_card._value.text(),
        "标签": page._tag_card._value.text(),
    }
    for key, value in expected.items():
        if actual[key] != value:
            problems.append(f"首页 {key} 显示 {actual[key]}，应为 {value}")
    return problems


def _check_category_filter(app, window) -> list[str]:
    """筛选栏里的分类勾选此前被忽略，只有左侧分类树生效。"""
    from app.repositories import ItemFilter

    page = window.manage_page
    user_id = page.user_service.current_id()
    before = page._total

    target = None
    for category_id in page.filter_panel._category_boxes:
        if page.item_repo.count(ItemFilter(category_ids={category_id}, user_ids={user_id})):
            target = category_id
            break
    if target is None:
        return []

    page.filter_panel._category_boxes[target].setChecked(True)
    app.processEvents()
    problems = []
    if page._total == 0 or not all(item.category_id == target for item in page._items):
        problems.append(f"勾选分类 {target} 后筛选无效：total={page._total}")

    page.filter_panel._category_boxes[target].setChecked(False)
    app.processEvents()
    if page._total != before:
        problems.append(f"取消分类勾选后未恢复：total={page._total}，应为 {before}")
    return problems


def _check_uncategorized_fixed(app, window) -> list[str]:
    """「未分类」是固定分类：带标识、排在树的最底下、没有重命名/删除/新建子分类入口。"""
    from PyQt6.QtCore import Qt

    from app.services import is_uncategorized
    from app.ui.widgets.category_tree import FIXED_SUFFIX, category_label, menu_entries

    page = window.manage_page
    _show_page(window, page)
    app.processEvents()
    user_id = page.user_service.current_id()
    uncategorized = page.taxonomy.uncategorized_category(user_id=user_id, create=False)
    if uncategorized is None:
        return []
    problems: list[str] = []
    if not is_uncategorized(uncategorized):
        problems.append("「未分类」没有被判定为固定分类")
    if menu_entries(fixed=True):
        problems.append("「未分类」的右键菜单仍提供修改入口")
    if not menu_entries(fixed=False):
        problems.append("普通分类的右键菜单为空")
    nodes = page.taxonomy.tree(user_id=user_id)
    roots = [node for node in nodes if node.depth == 0]
    if not roots or roots[-1].category.id != uncategorized.id:
        problems.append("「未分类」没有排在分类树最后")
    elif FIXED_SUFFIX not in category_label(roots[-1], fixed=True):
        problems.append("「未分类」的树标签没有固定标识")

    items = list(page.tree._iter_items())
    fixed = [item for item in items if item.data(0, Qt.ItemDataRole.UserRole) == uncategorized.id]
    if not fixed:
        problems.append("分类树里找不到「未分类」节点")
    elif FIXED_SUFFIX not in fixed[0].text(0):
        problems.append(f"「未分类」节点缺少固定标识：{fixed[0].text(0)!r}")
    elif uncategorized.id not in page.tree.fixed_ids():
        problems.append("分类树没有把「未分类」登记为固定分类")
    # 根分类是「全部数据」节点的子项，「未分类」必须是最后一个。
    if page.tree.topLevelItemCount():
        parent = page.tree.topLevelItem(0)
        if parent.childCount() and parent.child(parent.childCount() - 1).data(
            0, Qt.ItemDataRole.UserRole
        ) != uncategorized.id:
            problems.append("分类树里「未分类」不是最后一个根分类")
    return problems


def _visible_rows(page) -> list:
    """当前页列表视图里的行控件（末尾是伸缩哨兵，跳过）。"""
    rows = []
    for index in range(page.list_layout.count()):
        widget = page.list_layout.itemAt(index).widget()
        if widget is not None and getattr(widget, "item", None) is not None:
            rows.append(widget)
    return rows


def _check_manage_selection(app, window) -> list[str]:
    """数据管理页：双击才打开、行复选框 + Ctrl/Shift 多选、三态全选框、批量移动入口。"""
    from PyQt6.QtCore import Qt

    from app.ui.pages.manage_page import menu_items, open_with_items, tri_state

    page = window.manage_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()
    problems: list[str] = []
    items = list(page._items)
    if len(items) < 3:
        return ["数据管理页当前页不足 3 项，无法验证多选"]

    rows = _visible_rows(page)
    if not rows:
        return ["列表视图没有行控件"]

    # 单击只选中、不打开；双击才打开（用替身记录打开调用，行控件重新渲染后接上替身）。
    opened: list = []
    original_open = page._on_open
    page._on_open = opened.append
    try:
        page._render()
        rows = _visible_rows(page)
        page._selected.clear()
        page._on_item_activated(items[0], Qt.KeyboardModifier.NoModifier)
        if page._selected != {items[0].id}:
            problems.append(f"单击未单选：{page._selected}")
        if opened:
            problems.append(f"单击不应直接打开：{opened}")
        page._on_item_activated(items[2], Qt.KeyboardModifier.ShiftModifier)
        if page._selected != {item.id for item in items[:3]}:
            problems.append(f"Shift 连选不对：{page._selected}")
        page._on_item_activated(items[2], Qt.KeyboardModifier.ControlModifier)
        if page._selected != {items[0].id, items[1].id}:
            problems.append(f"Ctrl 取消选中不对：{page._selected}")
        rows[0].opened.emit(rows[0].item)
        if opened != [rows[0].item]:
            problems.append(f"双击未触发打开：{opened}")
    finally:
        page._on_open = original_open
        page._render()
    rows = _visible_rows(page)

    # 行复选框与三态全选框保持同步。
    page.clear_selection()
    rows[0].check_box.setChecked(True)
    app.processEvents()
    if page._selected != {rows[0].item.id}:
        problems.append(f"勾选框未生效：{page._selected}")
    if page.select_all_box.checkState() != Qt.CheckState.PartiallyChecked:
        problems.append(f"部分选中时全选框不是横杠：{page.select_all_box.checkState()}")
    page.select_all_box.setChecked(True)
    app.processEvents()
    if page._selected != {item.id for item in items}:
        problems.append(f"全选未覆盖本页：{page._selected}")
    if page.select_all_box.checkState() != Qt.CheckState.Checked:
        problems.append("全选后全选框不是勾")
    if not page.move_button.isEnabled():
        problems.append("有选中项时「移动到分类」应为可用")
    page.clear_selection()
    app.processEvents()
    if page._selected:
        problems.append(f"清空选择后仍有选中项：{page._selected}")
    if page.select_all_box.checkState() != Qt.CheckState.Unchecked:
        problems.append("清空选择后全选框不是空")
    if page.move_button.isEnabled():
        problems.append("没有选中项时「移动到分类」应为禁用")

    # 右键菜单与「打开方式」子菜单的条目。
    labels = dict(menu_items(1))
    for key in ("open", "open_with", "move", "details"):
        if key not in labels:
            problems.append(f"右键菜单缺少 {key}")
    if "移动到分类…（2 项）" not in dict(menu_items(2)).get("move", ""):
        problems.append("多选时「移动到分类」没有数量提示")
    entries = open_with_items(".txt")
    if entries[0][0] != "system" or entries[-1][0] != "ask":
        problems.append(f"「打开方式」条目顺序不对：{[entry[0] for entry in entries]}")
    if tri_state(1, 3) != Qt.CheckState.PartiallyChecked:
        problems.append("三态判定不对")
    page._selected = {items[0].id}
    if page._build_menu(items[0]) is None:
        problems.append("无法构造右键菜单")
    page.clear_selection()
    return problems


def _show_page(window, page) -> None:
    """把堆叠页切到目标页面，确保页面内的控件真正参与布局。"""
    try:
        window.switchTo(page)
    except Exception:  # noqa: BLE001
        window.stackedWidget.setCurrentWidget(page)


def _check_import_categories(window) -> list[str]:
    """导入页的分类下拉只能列出当前用户的分类（含「未分类」），不能串到别的用户。"""
    from app.repositories import CategoryRepository
    from app.services import TaxonomyService, UserService

    page = window.import_page
    _show_page(window, page)
    session = page.session
    user_service = UserService(session)
    user_id = user_service.current_id()
    expected = {node.category.id for node in TaxonomyService(session).tree(user_id=user_id)}
    ids = [page.category_box.itemData(index) for index in range(page.category_box.count())]
    problems = []
    if set(ids) != expected:
        problems.append(f"导入页分类项为 {sorted(set(ids))}，应为当前用户的 {sorted(expected)}")
    others = {
        category.id
        for info in user_service.list_users()
        if info.user.id != user_id
        for category in CategoryRepository(session).roots(user_id=info.user.id)
    }
    if others & set(ids):
        problems.append("导入页分类下拉混入了其它用户的分类")
    uncategorized = CategoryRepository(session).by_name("未分类", None, user_id)
    if uncategorized is None:
        problems.append("当前用户缺少「未分类」分类")
    elif uncategorized.id not in ids:
        problems.append("导入页分类下拉缺少「未分类」")
    if page.category_box.currentData() is None:
        problems.append("导入页分类下拉没有选中项")
    return problems


def _check_import_user_scope(window) -> list[str]:
    """导入页的用户下拉：只有默认用户能替别人导入，其他用户只能导入到自己名下。"""
    from app.core.signals import signalBus
    from app.services import UserService

    page = window.import_page
    _show_page(window, page)
    service = UserService(page.session)
    default_user = service.default()
    if default_user is None:
        return ["缺少默认用户"]
    problems: list[str] = []
    backup = service.current()
    extra = next(
        (info.user for info in service.list_users() if info.user.id != default_user.id),
        None,
    )
    if extra is None:
        extra = service.create("导入范围探针")
        page.session.commit()
    if extra is None:
        return ["无法准备第二个用户"]
    try:
        service.set_current(extra)
        page.session.commit()
        page.refresh()
        ids = [page.user_box.itemData(index) for index in range(page.user_box.count())]
        if ids != [extra.id]:
            problems.append(f"非默认用户的导入页用户下拉为 {ids}，应只有自己 {extra.id}")
        if page.user_box.isEnabled():
            problems.append("非默认用户的导入页用户下拉应禁用")
        if page.target_user_id() != extra.id:
            problems.append(f"非默认用户的导入目标为 {page.target_user_id()}，应为 {extra.id}")

        service.set_current(default_user)
        page.session.commit()
        page.refresh()
        default_ids = [page.user_box.itemData(index) for index in range(page.user_box.count())]
        if extra.id not in default_ids or default_user.id not in default_ids:
            problems.append(f"默认用户的导入页用户下拉为 {default_ids}，应列出全部用户")
        if not page.user_box.isEnabled():
            problems.append("默认用户的导入页用户下拉应可用")
        page.user_box.setCurrentIndex(default_ids.index(extra.id))
        if page.target_user_id() != extra.id:
            problems.append(f"默认用户选择其他用户后导入目标为 {page.target_user_id()}，应为 {extra.id}")
    finally:
        service.set_current(backup)
        page.session.commit()
        page.refresh()
        index = page.user_box.findData(backup.id)
        if index >= 0:
            page.user_box.setCurrentIndex(index)
        signalBus.userChanged.emit()
    return problems


def _check_tag_picker(app, window) -> list[str]:
    """导入页的标签需可从已有标签中多选，且选中的标签块必须可见。"""
    from app.repositories import TagRepository
    from app.services import UserService

    page = window.import_page
    _show_page(window, page)
    picker = page.tag_input
    tags = sorted(TagRepository(page.session).names(user_id=UserService(page.session).current_id()))
    problems = []
    if picker.known_tags() != tags:
        problems.append(f"导入页已有标签 {picker.known_tags()}，应为 {tags}")
    if len(tags) < 2:
        return problems

    picker.set_keywords([])
    for tag in tags[:2]:
        picker._toggle(tag, True)
    app.processEvents()
    if picker.keywords() != tags[:2]:
        problems.append(f"多选标签后为 {picker.keywords()}，应为 {tags[:2]}")
    if picker.chips_height() <= 0:
        problems.append("标签块高度为 0，用户看不到已选标签")

    picker._toggle(tags[0], False)
    app.processEvents()
    if picker.keywords() != tags[1:2]:
        problems.append(f"取消勾选后为 {picker.keywords()}，应为 {tags[1:2]}")
    picker.clear()
    app.processEvents()
    return problems


def _check_keyword_filter(app, window) -> list[str]:
    """数据管理页需要能用关键词筛选，关键词来自数据项本身。"""
    from app.repositories import ItemFilter, TagRepository
    from app.services import UserService

    page = window.manage_page
    _show_page(window, page)
    user_id = UserService(page.session).current_id()
    words = TagRepository(page.session).distinct_keywords(user_id=user_id)
    problems = []
    if set(page.filter_panel._keyword_boxes) != set(words):
        problems.append(f"关键词筛选项 {sorted(page.filter_panel._keyword_boxes)}，应为 {words}")
    if not words:
        return problems

    target = words[0]
    before = page._total
    page.filter_panel._keyword_boxes[target].setChecked(True)
    app.processEvents()
    expected = page.item_repo.count(ItemFilter(keywords={target}, user_ids={user_id}))
    if page._total != expected:
        problems.append(f"勾选关键词「{target}」后 total={page._total}，应为 {expected}")
    page.filter_panel._keyword_boxes[target].setChecked(False)
    app.processEvents()
    if page._total != before:
        problems.append(f"取消关键词勾选后未恢复：total={page._total}，应为 {before}")
    return problems


def _check_keyword_display(window) -> list[str]:
    """列表行需要显示数据项的关键词（此前只显示标签）。"""
    from app.ui.widgets.item_card import ItemListRow

    page = window.manage_page
    _show_page(window, page)
    item = next((entry for entry in page._items if entry.keywords), None)
    if item is None:
        return []
    widget = ItemListRow(item)
    text = widget._keywords.text()
    wanted = str(item.keywords[0])
    if wanted not in text:
        return [f"列表行未显示关键词「{wanted}」：{text!r}"]
    return []


def _check_tag_page(app, window) -> list[str]:
    """标签管理页列出可见标签并标明全局 / 个人归属。"""
    from app.repositories import TagRepository
    from app.services import UserService

    page = window.tag_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()
    users = UserService(page.session)
    repo = TagRepository(page.session)
    # 默认用户可以看到全部标签，其他用户只看到全局标签与自己创建的标签。
    tags = repo.all() if users.is_admin() else repo.all(user_id=users.current_id())
    problems = []
    if page.table.rowCount() != len(tags):
        problems.append(f"标签页 {page.table.rowCount()} 行，应为 {len(tags)} 行")
    all_tags = repo.all()
    globals_ = {tag.id: tag.name for tag in all_tags if tag.is_global}
    for info in users.list_users():
        if info.is_default:
            continue
        visible = {tag.id for tag in repo.all(user_id=info.user.id)}
        missing = [name for tag_id, name in globals_.items() if tag_id not in visible]
        if missing:
            problems.append(f"用户「{info.name}」看不到全局标签：{sorted(missing)}")
        others = {
            tag.id: tag.name
            for tag in all_tags
            if not tag.is_global and info.user.id not in (tag.user_id, tag.created_by)
        }
        leaked = sorted(name for tag_id, name in others.items() if tag_id in visible)
        if leaked:
            problems.append(f"用户「{info.name}」看到了他人的个人标签：{leaked}")
    for row, tag in enumerate(tags):
        name_cell = page.table.item(row, 0)
        scope_cell = page.table.item(row, 1)
        if name_cell is None or scope_cell is None:
            problems.append(f"第 {row} 行单元格为空")
            continue
        if name_cell.text() != tag.name:
            problems.append(f"第 {row} 行名称为 {name_cell.text()!r}，应为 {tag.name!r}")
            continue
        wanted = "全局" if tag.is_global else "个人"
        if not scope_cell.text().startswith(wanted):
            problems.append(f"标签「{tag.name}」归属显示为 {scope_cell.text()!r}，应为 {wanted}")
    return problems


SELFCHECK_IMPORT_NAME = "selfcheck_import"


def _cleanup_selfcheck_import(window) -> None:
    """清掉自检真导入产生的分类、数据项与落盘文件，避免污染开发库。"""
    import shutil
    from pathlib import Path

    from app.repositories import CategoryRepository, ItemRepository
    from app.services import ItemService, TaxonomyService

    session = window.import_page.session
    node = CategoryRepository(session).by_name(SELFCHECK_IMPORT_NAME)
    if node is None:
        return
    try:
        items = list(ItemRepository(session).in_category(node.id))
        paths = [ItemService(session).file_path_of(item) for item in items]
        if items:
            ItemService(session).purge(items)
        TaxonomyService(session).delete_category(node, recursive=True)
        session.commit()
    except Exception as exc:  # 自检清理失败不应影响检查结论
        session.rollback()
        print(f"自检清理：导入页自检数据未清干净：{exc}")
        return
    folders: set[Path] = set()
    for path in paths:
        for parent in path.parents if path else ():
            if parent.name == SELFCHECK_IMPORT_NAME:
                folders.add(parent)
                break
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


def _check_import_batch(app, window) -> list[str]:
    """导入页：可切换导入用户、显示完整文件信息，并把文件夹作为新分类整体导入。"""
    try:
        return _run_import_batch_check(app, window)
    finally:
        _cleanup_selfcheck_import(window)


def _run_import_batch_check(app, window) -> list[str]:
    """批量导入自检主体；真导入的数据由 _cleanup_selfcheck_import 清掉。"""
    import time
    from pathlib import Path

    page = window.import_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()
    problems: list[str] = []

    if page.user_box.count() < 1:
        problems.append("导入页没有「导入用户」下拉")
    elif not any("（当前用户）" in page.user_box.itemText(index) for index in range(page.user_box.count())):
        problems.append("导入用户下拉没有标注当前用户")
    if page.details_card.isVisibleTo(page):
        problems.append("尚未选择文件时不应显示「待导入文件信息」")

    folder = Path(__file__).resolve().parents[1] / "tests" / "_tmp" / "selfcheck_import"
    sub = folder / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    (folder / "自检一.txt").write_text("自检内容一", encoding="utf-8")
    (folder / "自检二.txt").write_text("自检内容二", encoding="utf-8")
    (sub / "自检三.txt").write_text("自检内容三", encoding="utf-8")

    page._set_mode("file")
    page._on_directory(str(folder))
    app.processEvents()
    if page.details_table.rowCount() != 3:
        problems.append(f"文件夹展开后应有 3 行文件信息，实际 {page.details_table.rowCount()} 行")
    if "3 个文件" not in page.details_summary.text():
        problems.append(f"文件信息摘要没有统计总数：{page.details_summary.text()!r}")
    subdirs = {page.details_table.item(row, 4).text() for row in range(page.details_table.rowCount())}
    if "sub" not in subdirs:
        problems.append(f"文件信息没有保留子目录：{sorted(subdirs)}")
    if page.category_box.isEnabled():
        problems.append("文件夹导入时分类下拉应禁用（文件夹名即新分类）")
    if folder.name not in page.category_hint.text():
        problems.append(f"分类提示没有说明新分类名：{page.category_hint.text()!r}")

    page._on_files([str(folder / "自检一.txt"), str(folder / "自检二.txt")])
    app.processEvents()
    if page.details_table.rowCount() != 2:
        problems.append(f"选择两个文件后应有 2 行信息，实际 {page.details_table.rowCount()} 行")
    if not page.category_box.isEnabled():
        problems.append("多文件导入时分类下拉应可用")

    page._on_directory(str(folder))
    app.processEvents()
    page.import_now()
    deadline = time.time() + 60
    while page._worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    if page._worker is not None:
        problems.append("批量导入没有在 60 秒内结束")
        return problems
    if page.progress_bar.value() != 100:
        problems.append(f"导入完成后进度条为 {page.progress_bar.value()}，应为 100")
    if page.result_table.rowCount() < 3:
        problems.append(f"导入结果表只有 {page.result_table.rowCount()} 行，应有 3 行")
    if "成功" not in page.progress_label.text():
        problems.append(f"导入摘要没有成功计数：{page.progress_label.text()!r}")
    if page.details_card.isVisibleTo(page):
        problems.append("导入完成后没有清空待导入文件信息")
    return problems


def _check_tag_filters(app, window) -> list[str]:
    """标签页：表头可拖动、逐列筛选（含选项列）、重置筛选与计数文案。"""
    page = window.tag_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()
    problems: list[str] = []
    if not page.table.horizontalHeader().sectionsMovable():
        problems.append("标签页表头不能拖动调整列宽/顺序")
    total = page.table.rowCount()
    if total == 0:
        return problems + ["标签页没有任何标签"]

    def visible_rows() -> list[int]:
        return [row for row in range(total) if not page.table.isRowHidden(row)]

    names = [page.table.item(row, 0).text() for row in range(total)]
    unique = next(
        (
            name
            for name in names
            if sum(1 for other in names if name.strip().lower() in other.strip().lower()) == 1
        ),
        None,
    )
    if unique is None:
        return problems + ["标签页没有名称唯一的标签，无法校验按名称筛选"]
    page.filter_bar.set_filter("name", unique)
    app.processEvents()
    if len(visible_rows()) != 1:
        problems.append(f"按名称「{unique}」筛选后仍有 {len(visible_rows())} 行")
    if page.filter_caption.text() != f"显示 1 / {total} 个标签":
        problems.append(f"筛选计数文案为 {page.filter_caption.text()!r}")

    page.filter_bar.set_filter("name", "绝不存在的标签")
    app.processEvents()
    if visible_rows():
        problems.append("筛选不存在的名称后仍有可见行")
    page._on_reset_filters()
    app.processEvents()
    if len(visible_rows()) != total:
        problems.append("重置筛选后没有恢复全部行")
    if page.table.selectionModel().hasSelection():
        problems.append("筛选后仍保留着已隐藏行的选中状态")

    page.filter_bar.set_filter("scope", "全局")
    app.processEvents()
    for row in visible_rows():
        if page.table.item(row, 1).text() != "全局":
            problems.append(f"按归属「全局」筛选后出现 {page.table.item(row, 1).text()!r} 行")
            break
    page._on_reset_filters()
    app.processEvents()
    return problems


def _check_recent_focus(app, window) -> list[str]:
    """概览页「最近导入」可点击跳转到数据管理页，并选中该项、展开其分类。"""
    from app.core.signals import signalBus
    from app.ui.pages.home_page import _Row

    home = window.home_page
    page = window.manage_page
    _show_page(window, home)
    home.refresh()
    app.processEvents()
    rows = home.findChildren(_Row)
    if not rows:
        return ["概览页没有「最近导入」条目，无法验证跳转"]
    problems: list[str] = []
    item_id = rows[0]._item_id
    captured: list[int] = []
    signalBus.focusItem.connect(captured.append)
    try:
        rows[0].clicked.emit()
        app.processEvents()
    finally:
        signalBus.focusItem.disconnect(captured.append)
    if captured != [item_id]:
        problems.append(f"点击最近导入未请求跳转：{captured}")
    if window.stackedWidget.currentWidget() is not page:
        problems.append("跳转后未切换到数据管理页")
    if page._selected != {item_id}:
        problems.append(f"跳转后未选中该项：{page._selected}")
    item = page.item_repo.get(item_id)
    if item is None:
        return problems + [f"找不到数据项 {item_id}"]
    if page._category_id != item.category_id:
        problems.append(f"跳转后未定位到所属分类：{page._category_id!r} != {item.category_id!r}")
    if item.category_id is not None and page.tree.current_category() != item.category_id:
        problems.append(f"左侧分类未展开并选中该分类：{page.tree.current_category()!r}")
    return problems


def _widget_texts(root) -> list[str]:
    """收集控件树上所有可见文本（不同控件类的文本取自 text()）。"""
    from PyQt6.QtWidgets import QWidget

    texts = []
    for widget in root.findChildren(QWidget):
        getter = getattr(widget, "text", None)
        if not callable(getter):
            continue
        try:
            texts.append(str(getter()))
        except Exception:  # noqa: BLE001
            continue
    return texts


def _check_single_library(window) -> list[str]:
    """库文件夹已取消多库：设置页只有一个库根目录，库内容按「全局 + 各用户」展示。"""
    from PyQt6.QtWidgets import QPushButton
    from qfluentwidgets import StrongBodyLabel

    from app.services import LibraryService, UserService

    page = window.settings_page
    _show_page(window, page)
    page._refresh_libraries()
    problems = []
    buttons = {button.text() for button in page.findChildren(QPushButton)}
    for forbidden in ("新建库", "导入库文件夹", "设为默认", "移除"):
        if forbidden in buttons:
            problems.append(f"设置页仍存在多库入口「{forbidden}」")
    for wanted in ("更改位置", "扫描并登记", "重建目录结构", "打开文件夹"):
        if wanted not in buttons:
            problems.append(f"设置页缺少库操作「{wanted}」")

    service = LibraryService(page.session)
    library = service.ensure_default()
    rows = []
    for index in range(page._library_layout.count()):
        widget = page._library_layout.itemAt(index).widget()
        if widget is None:
            continue
        rows.extend(label.text() for label in widget.findChildren(StrongBodyLabel))
    body = "\n".join(rows)
    if paths.GLOBAL_DIR_NAME not in body:
        problems.append(f"库内容未列出「{paths.GLOBAL_DIR_NAME}」文件夹：{rows}")
    for info in UserService(page.session).list_users():
        if info.name not in body:
            problems.append(f"库内容未列出用户名文件夹「{info.name}」：{rows}")
    if str(library.path) not in _widget_texts(page):
        problems.append(f"设置页未显示库路径：{library.path}")
    return problems


def _check_no_library_picker(window) -> list[str]:
    """导入页与详情面板不再要求选择库：库固定为唯一库文件夹。"""
    from app.ui.pages import manage_page as manage_module

    problems = []
    import_page = window.import_page
    _show_page(window, import_page)
    leftover = [text for text in _widget_texts(import_page) if "目标库" in text]
    if leftover:
        problems.append(f"导入页仍有库选择控件：{leftover}")

    page = window.manage_page
    _show_page(window, page)
    if not page._items:
        return problems
    captured: dict[str, str] = {}

    class _FakeBox:
        def __init__(self, title, text, parent=None) -> None:
            captured["text"] = text

        def exec(self) -> None:
            return None

    original = manage_module.MessageBox
    manage_module.MessageBox = _FakeBox
    try:
        page._on_details(page._items[0])
    finally:
        manage_module.MessageBox = original
    if "所在库" in captured.get("text", ""):
        problems.append(f"详情面板仍有「所在库」：{captured['text']!r}")
    return problems


def _check_global_tag_marks(app, window) -> list[str]:
    """全局标签在筛选栏与标签选择器中标注「（全局）」，筛选取值仍是纯名字。"""
    from app.repositories import TagRepository
    from app.services import UserService

    page = window.manage_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()
    session = page.session
    repo = TagRepository(session)
    user_id = UserService(session).current_id()
    global_names = set(repo.global_names())
    visible = set(repo.names(user_id=user_id))

    problems = []
    boxes = page.filter_panel._tag_boxes
    if set(boxes) != visible:
        problems.append(f"筛选栏标签 {sorted(boxes)}，应为 {sorted(visible)}")
    for name, box in boxes.items():
        wanted = f"{name}（全局）" if name in global_names else name
        if box.text() != wanted:
            problems.append(f"筛选栏标签「{name}」显示为 {box.text()!r}，应为 {wanted!r}")

    picker = window.import_page.tag_input
    _show_page(window, window.import_page)
    if set(picker.known_tags()) != visible:
        problems.append(f"标签选择器标签 {picker.known_tags()}，应为 {sorted(visible)}")
    menu = picker._build_menu()
    labels = {action.text() for action in menu.actions()}
    for name in visible:
        wanted = f"{name}（全局）" if name in global_names else name
        if wanted not in labels:
            problems.append(f"标签选择器未按 {wanted!r} 显示标签")
    return problems


def _check_user_page(app, window) -> list[str]:
    """用户管理已独立成页，且用户管理入口不再留在设置页。"""
    from PyQt6.QtWidgets import QPushButton

    page = window.user_page
    previous = window.stackedWidget.currentWidget()
    _show_page(window, page)  # 非当前堆叠页不会参与布局，先切过去量尺寸
    app.processEvents()
    problems: list[str] = []
    if page.objectName() != "userPage":
        problems.append(f"用户页 objectName={page.objectName()!r}，应为 'userPage'")
    texts = _widget_texts(page)
    if not any("当前用户" in text for text in texts):
        problems.append("用户页没有标注「当前用户」")
    buttons = [
        widget
        for widget in page.findChildren(QPushButton)
        if "新建用户" in str(widget.text() or "")
    ]
    if not buttons:
        problems.append("用户页缺少「新建用户」按钮")
    elif not any(button.isVisibleTo(page) for button in buttons):
        problems.append("默认用户看不到「新建用户」按钮")
    cards = page.cards() if hasattr(page, "cards") else []
    if not cards:
        problems.append("用户页没有用户卡片")
    else:
        users = page.service.list_users()
        if len(cards) != len(users):
            problems.append(f"用户卡片数量 {len(cards)} 与用户数 {len(users)} 不一致")
        current = page.current_card()
        if current is None:
            problems.append("用户页找不到当前用户卡片")
        elif not current.is_highlighted():
            problems.append("当前用户卡片没有高亮")
        # 固定宽度卡片：卡片必须完整落在滚动区域内，且宽度恒为 CARD_WIDTH。
        page._layout_cards(force=True)
        app.processEvents()
        viewport = page.scroll.viewport().width()
        from app.ui.pages.user_page import CARD_SPACING, CARD_WIDTH, grid_columns

        for card in cards:
            if card.geometry().right() > viewport:
                problems.append(
                    f"用户卡片越出滚动区域：右边界 {card.geometry().right()} > 视口 {viewport}"
                )
            elif card.width() != CARD_WIDTH:
                problems.append(f"用户卡片宽度应为 {CARD_WIDTH}，实际 {card.width()}")
        # 卡片即对象：每张卡片自带首字头像与本用户的操作按钮，且按钮完整落在卡片内。
        from PyQt6.QtWidgets import QLabel
        from qfluentwidgets import PushButton

        from app.ui.pages.user_page import AVATAR_SIZE

        heights = {card.height() for card in cards}
        if len(heights) != 1:
            problems.append(f"用户卡片高度不一致：{sorted(heights)}")
        for card in cards:
            avatars = [
                label
                for label in card.findChildren(QLabel)
                if label.width() == AVATAR_SIZE and label.height() == AVATAR_SIZE
            ]
            if len(avatars) != 1:
                problems.append(f"用户卡片 {card.user_id} 的首字头像数量应为 1，实际 {len(avatars)}")
            card_buttons = card.findChildren(PushButton)
            if not card_buttons:
                problems.append(f"用户卡片 {card.user_id} 缺少操作按钮")
            for button in card_buttons:
                if (
                    button.geometry().right() > card.width()
                    or button.geometry().bottom() > card.height()
                ):
                    problems.append(
                        f"用户卡片 {card.user_id} 的按钮 {button.text()!r} 被裁出卡片范围"
                    )
        # 卡片列不伸缩，其后有一个占位伸缩列 → 卡片靠左且不被拉宽。
        if page.grid.columnStretch(page._columns) != 1:
            problems.append("用户卡片网格缺少占位伸缩列，卡片不会被左对齐")
        if page.grid.columnStretch(0) != 0:
            problems.append("用户卡片列被设置了拉伸，卡片宽度会随窗口变化")
        # 窄视口（700px）下固定宽度网格必须放得下。
        available = 700 - 24
        columns = min(grid_columns(available, card_width=CARD_WIDTH), len(cards))
        needed = columns * CARD_WIDTH + (columns - 1) * CARD_SPACING
        if needed > available:
            problems.append(f"700px 视口下卡片网格放不下：需要 {needed}px > {available}px")
    settings_texts = _widget_texts(window.settings_page)
    for banned in ("新建用户", "切换用户", "删除用户"):
        if any(banned in text for text in settings_texts):
            problems.append(f"设置页仍残留用户管理入口：{banned}")
    if previous is not None and previous is not page:
        _show_page(window, previous)
        app.processEvents()
    return problems


def _check_archive_owner(app, window) -> list[str]:
    """存档条目要标注归属用户，只有默认用户能整档还原。"""
    page = window.archive_page
    problems: list[str] = []
    headers = [
        page.table.horizontalHeaderItem(column).text()
        for column in range(page.table.columnCount())
    ]
    if "所属用户" not in headers:
        problems.append(f"存档表格缺少「所属用户」列：{headers}")
    if not page._is_admin:
        problems.append("默认用户没有被识别为管理员")
    page.switch_tab("entries")  # 「还原整个存档」位于「存档内条目」Tab
    app.processEvents()
    if page.restore_all_button.isHidden():
        problems.append("管理员看不到「还原整个存档」按钮")
    page.switch_tab("archives")
    app.processEvents()
    return problems


def _check_archive_tabs(app, window) -> list[str]:
    """存档页拆成两个 Tab：选中存档自动切到条目 Tab，也能手动切回。"""
    page = window.archive_page
    problems: list[str] = []
    keys = page.tab_keys()
    if keys != ["archives", "entries"]:
        problems.append(f"存档页 Tab 定义异常：{keys}")
    if page.stack.count() != 2:
        problems.append(f"存档页分页容器应有 2 页，实际 {page.stack.count()}")
    page.switch_tab("archives")
    app.processEvents()
    if page.current_tab() != "archives" or page.stack.currentIndex() != 0:
        problems.append("无法切回「存档列表」Tab")
    if page._archives:
        page.archive_list.setCurrentRow(0)
        app.processEvents()
        if page.current_tab() != "entries" or page.stack.currentIndex() != 1:
            problems.append("选中存档后没有自动切到「存档内条目」Tab")
        page.switch_tab("archives")
        app.processEvents()
        if page.current_tab() != "archives" or page.stack.currentIndex() != 0:
            problems.append("手动切回「存档列表」Tab 失败")
    return problems


def _check_archive_pin(app, window) -> list[str]:
    """存档可标记：按钮随选中存档切换文案，已标记的快照在列表与详情中标出。"""
    page = window.archive_page
    page.switch_tab("archives")  # 「标记存档」按钮位于「存档列表」Tab
    app.processEvents()
    problems: list[str] = []
    if not hasattr(page, "pin_button"):
        return ["存档页缺少「标记存档」按钮"]
    if "标记存档" not in page.caption.text():
        problems.append("存档页说明没有提到「标记存档」")
    archives = page._archives
    if not archives:
        if page.pin_button.isEnabled():
            problems.append("没有存档时「标记存档」按钮仍可用")
        return problems
    pinned = [archive for archive in archives if archive.pinned]
    target = pinned[0] if pinned else archives[0]
    index = archives.index(target)
    page.archive_list.setCurrentRow(index)
    if not page.pin_button.isEnabled():
        problems.append("选中存档后「标记存档」按钮仍禁用")
    expected = "取消标记" if pinned else "标记存档"
    if page.pin_button.text() != expected:
        problems.append(f"按钮文案应为「{expected}」，实际为「{page.pin_button.text()}」")
    if pinned:
        if "【已标记】" not in page.archive_list.item(index).text():
            problems.append("列表项没有标出【已标记】")
        if "【已标记】" not in page.detail_meta.text():
            problems.append("详情区没有标出【已标记】")
    page.switch_tab("archives")
    app.processEvents()
    return problems


def _check_archive_table(app, window) -> list[str]:
    """存档表格：表头可拖动、有分页控件、支持逐列筛选。"""
    page = window.archive_page
    page.switch_tab("archives")  # 分页与筛选栏位于「存档列表」Tab
    app.processEvents()
    problems: list[str] = []
    if not page.archive_list.horizontalHeader().sectionsMovable():
        problems.append("存档表头不能拖动调整列")
    if not hasattr(page, "pager"):
        problems.append("存档页缺少分页控件")
    elif page.pager.isHidden():
        problems.append("存档页分页控件被隐藏")
    if not hasattr(page, "filter_bar"):
        problems.append("存档页缺少逐列筛选栏")
    else:
        before = len(page._visible)
        page.filter_bar.set_filter("name", "绝不存在的存档")
        app.processEvents()
        if page._visible:
            problems.append("存档按名称筛选不存在后仍有可见行")
        page.filter_bar.reset()
        app.processEvents()
        if len(page._visible) != before:
            problems.append(f"存档重置筛选后可见行由 {before} 变为 {len(page._visible)}")
    return problems


def _check_open_with(window) -> list[str]:
    """打开方式页：列出格式、选中后能显示模式，并默认使用内置查看器。"""
    from pathlib import Path

    from app.core.extensions import extension_registry
    from app.core.viewers import viewer_registry
    from app.services.open_with_service import open_with_service
    from app.services.plugin_service import plugin_service

    page = window.open_with_page
    problems: list[str] = []
    plugin_service.load_viewers()
    if "md" not in viewer_registry.extensions():
        problems.append("载入内置插件后注册表里没有 md 查看器")
    if extension_registry.provider("dialog") is None:
        problems.append("载入内置插件后没有注册 dialog 弹窗页面扩展")
    if not all(viewer.host == "dialog" for viewer in viewer_registry.all()):
        problems.append("内置查看器没有声明依赖 dialog 弹窗页面插件")
    decision = open_with_service.resolve(Path("示例.md"))
    if not decision.is_builtin:
        problems.append(f"md 文件默认应使用内置查看器，实际为 {decision.mode}")
    if page.suffix_list.count() == 0:
        return problems + ["打开方式页没有列出任何格式"]
    page.suffix_list.setCurrentRow(0)
    if not page.detail_title.text():
        problems.append("选中格式后没有显示格式说明")
    if page.mode_box.count() == 0:
        problems.append("选中格式后「打开方式」下拉框没有选项")
    page._on_manage_plugins()
    if window.stackedWidget.currentWidget() is not window.plugin_page:
        problems.append("「管理打开方式插件」没有跳到插件页")
    elif window.plugin_page.kind_box.currentData() != "viewer":
        problems.append("跳到插件页后没有按「打开方式」类型筛选")
    window.switchTo(window.open_with_page)

    # 每个格式都能挑一个具体插件打开
    for row in range(page.suffix_list.count()):
        suffix = page.suffix_list.item(row).data(Qt.ItemDataRole.UserRole)
        if suffix == "md":
            page.suffix_list.setCurrentRow(row)
            break
    if page.viewer_box.count() < 2:
        problems.append("格式有插件可用时，「使用插件」下拉框应列出「自动」与各插件")
    elif page.viewer_box.itemData(0) != "":
        problems.append("「使用插件」下拉框的第一项应是「自动」")
    if "可用插件" not in page.detail_viewers.text():
        problems.append("选中格式后没有列出可用插件")
    from app.services.open_with_service import MODE_BUILTIN, MODE_CUSTOM, open_with_service

    custom = page.mode_box.findData(MODE_CUSTOM)
    if custom < 0:
        problems.append("「打开方式」下拉框里没有「自定义程序」")
    else:
        page.mode_box.setCurrentIndex(custom)
        if not page.program_edit.isEnabled():
            problems.append("切到「自定义程序」后程序输入框应可用")
        page.mode_box.setCurrentIndex(max(0, page.mode_box.findData(MODE_BUILTIN)))
        if page.program_edit.isEnabled():
            problems.append("「使用插件打开」时程序输入框应禁用")
    viewer_index = page.viewer_box.findData("builtin.markdown.1")
    if viewer_index >= 0:
        page.viewer_box.setCurrentIndex(viewer_index)
        page._on_save()
        if open_with_service.rule_for("md").viewer_id != "builtin.markdown.1":
            problems.append("保存后没有记下指定的查看器插件")
        page._on_reset()
        if open_with_service.rule_for("md").viewer_id:
            problems.append("「恢复默认」后应清掉指定查看器")
    return problems


def _check_plugins(window) -> list[str]:
    """插件页：内置插件齐全、按类型筛选可用、内置插件不能删除。"""
    page = window.plugin_page
    problems: list[str] = []
    page.apply_kind("viewer")
    if page.plugin_list.count() != 7:
        problems.append(f"查看器插件应为 7 个，实际 {page.plugin_list.count()} 个")
    if page.kind_box.currentData() != "viewer":
        problems.append(f"按查看器类型筛选后下拉框应为 viewer，实际为 {page.kind_box.currentData()!r}")
    page.apply_kind("page")
    if page.plugin_list.count() != 1:
        problems.append(f"弹窗页面插件应为 1 个，实际 {page.plugin_list.count()} 个")
    page.apply_kind("kind")
    if page.plugin_list.count() != 3:
        problems.append(f"插件类型插件应为 3 个，实际 {page.plugin_list.count()} 个")
    page.apply_kind("")
    if page.plugin_list.count() != 11:
        problems.append(f"内置插件应为 11 个，实际 {page.plugin_list.count()} 个")
    page.apply_kind("theme")
    if page.kind_box.currentData() not in ("", None):
        problems.append("未知类型筛选后下拉框应回到「全部类型」")
    page.apply_kind("viewer")
    page.source_box.setCurrentIndex(1)
    for row in range(page.plugin_list.count()):
        if "内置" not in page.plugin_list.item(row).text():
            problems.append("按「内置」来源筛选后仍列出了外部插件")
            break
    page.source_box.setCurrentIndex(0)
    page.plugin_list.setCurrentRow(0)
    if not page.detail_title.text():
        problems.append("选中插件后没有显示插件详情")
    if page.delete_button.isEnabled():
        problems.append("内置插件的「删除」按钮应禁用")
    if page.toggle_button.text() not in ("启用", "禁用"):
        problems.append(f"启用按钮文案异常：{page.toggle_button.text()}")
    if not page.reveal_button.isEnabled():
        problems.append("内置插件也应能打开插件目录")
    protocol = page.detail_protocol.text()
    for token in ("依赖插件", "扩展接口", "功能", "适用管理器版本", "入口文件"):
        if token not in protocol:
            problems.append(f"插件详情没有展示协议字段：{token}")

    # 筛选与排序：类型 / 来源 / 创建者 / 状态 + 排序方向
    page.apply_kind("")
    if page.author_box.count() < 2:
        problems.append("插件页没有列出创建者筛选项")
    if page.order_box.count() < 5:
        problems.append("插件页的排序方式太少")
    if page.reverse_button.text() != "正序":
        problems.append("排序方向按钮初始文案应为「正序」")
    page.reverse_button.setChecked(True)
    if page.reverse_button.text() != "逆序":
        problems.append("勾选排序方向后按钮文案应变成「逆序」")
    page.reverse_button.setChecked(False)
    if "个插件" not in page.count_label.text():
        problems.append("插件页没有显示插件总数")
    # 默认（全部类型）下所有插件都要在列表里，列表项显示类型与来源
    if page.plugin_list.count() != 11:
        problems.append(f"「全部类型」下应列出 11 个插件，实际 {page.plugin_list.count()} 个")
    first = page.plugin_list.item(0).text()
    if "内置" not in first or "·" not in first:
        problems.append(f"插件列表项应显示类型与来源，实际为 {first!r}")
    if page.kind_box.count() != 4:
        problems.append(f"类型下拉应有「全部类型」+3 种插件类型，实际 {page.kind_box.count()} 项")

    # 插件选项：详情里显示选项摘要与清单路径，并能打开选项对话框
    page._select_plugin("builtin.image")
    if "插件选项（3）" not in page.detail_options.text():
        problems.append(f"图片插件详情没有列出 3 个选项：{page.detail_options.text()!r}")
    if "plugin.json" not in page.detail_path.text():
        problems.append("插件详情没有显示清单路径")
    if page.options_button.text() != "插件选项":
        problems.append("插件详情缺少「插件选项」按钮")
    from app.ui.plugin_options_dialog import PluginOptionsDialog

    from app.services.plugin_service import plugin_service

    dialog = PluginOptionsDialog(plugin_service.get("builtin.image"), page.service, parent=window)
    try:
        if len(dialog._editors) != 3:
            problems.append(f"插件选项对话框应生成 3 个编辑器，实际 {len(dialog._editors)} 个")
        if not dialog._checks:
            problems.append("插件选项对话框没有列出该插件支持的扩展名")
    finally:
        dialog.deleteLater()
    return problems


def _broken_page() -> QWidget:
    raise RuntimeError("自检：页面工厂故意报错")


def _check_plugin_pages(window) -> list[str]:
    """插件界面扩展接口（app.ui）：插件登记的导航页面能被装配、切换、移除。"""
    from app.core.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.services.plugin_service import plugin_service

    problems: list[str] = []
    api = AppUiApi()
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    plugin_service.load_viewers()

    api.add_page("selfcheck", "自检页面", lambda: QWidget(window), icon="HOME", plugin_id="selfcheck.plugin")
    window._sync_plugin_pages()
    if "selfcheck" not in window.plugin_pages():
        problems.append("插件登记的页面没有装进导航")
        return problems
    widget = window._plugin_pages["selfcheck"]
    if widget.objectName() != "plugin.selfcheck":
        problems.append(f"插件页面路由应为 plugin.selfcheck，实际 {widget.objectName()!r}")
    if window.stackedWidget.indexOf(widget) < 0:
        problems.append("插件页面没有加入页面栈")
    window.switchTo(widget)
    if window.stackedWidget.currentWidget() is not widget:
        problems.append("无法切换到插件页面")

    api.add_page("broken", "坏页面", _broken_page, plugin_id="selfcheck.plugin")
    window._sync_plugin_pages()
    if "broken" not in window.plugin_pages():
        problems.append("页面工厂报错时应退化成提示页，而不是整页缺失")

    api.remove_page("selfcheck")
    api.remove_page("broken")
    window._sync_plugin_pages()
    if window.plugin_pages():
        problems.append(f"插件页面注销后仍留在导航：{window.plugin_pages()}")
    return problems


def _check_image_viewer(window) -> list[str]:
    """图片查看器：小图要自适应放大，「原始大小」与插件选项都要生效。"""
    import shutil

    from PyQt6.QtGui import QPixmap

    from app.core.viewers import viewer_registry
    from app.services.plugin_service import plugin_service
    from app.ui.viewers.image_view import ImageViewer

    problems: list[str] = []
    root = Path(__file__).resolve().parents[1] / "tests" / "_tmp" / "selfcheck_image"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    try:
        small = root / "small.png"
        pixmap = QPixmap(40, 20)
        pixmap.fill()
        pixmap.save(str(small))

        viewer = ImageViewer(small, parent=window)
        viewer.resize(400, 300)
        viewer.show()
        QApplication.processEvents()
        viewer._apply()
        if viewer._scale <= 1.0:
            problems.append(f"小图在自适应模式下应放大显示，实际缩放 {viewer._scale:.3f}")
        if viewer._label.width() <= 40:
            problems.append("自适应放大后图片控件宽度没有跟着变大")
        viewer._actual_size()
        if abs(viewer._scale - 1.0) > 1e-6:
            problems.append(f"「原始大小」应回到 100%，实际 {viewer._scale:.3f}")
        viewer._zoom(1 / viewer._step_factor)
        if viewer._scale >= 1.0:
            problems.append("缩小按钮没有生效")
        viewer._fit_window()
        if viewer._scale <= 1.0:
            problems.append("「适应窗口」没有重新放大")
        viewer.close()

        plain = ImageViewer(small, fit_on_open=False)
        if abs(plain._scale - 1.0) > 1e-6:
            problems.append("插件选项关闭「打开时适应窗口」后应按原始大小显示")

        # 插件选项要真的传到查看器工厂里
        plugin_service.load_viewers()
        info = plugin_service.get("builtin.image")
        if not info.has_options:
            problems.append("内置图片插件没有声明插件选项")
        factory_viewer = viewer_registry.by_id("builtin.image.1")
        if factory_viewer is None or factory_viewer.factory is None:
            problems.append("内置图片查看器没有注册工厂")
        else:
            built = factory_viewer.factory(small, None)
            if abs(getattr(built, "_step_factor", 0) - 1.25) > 1e-6:
                problems.append("查看器工厂没有用插件选项里的缩放步长")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return problems


def _check_settings_extras(window) -> list[str]:
    """数据仓库配置已删除；日志模式与四张保留卡存在且随模式联动。"""
    from app.core import logging_setup
    from app.core.config import config
    from app.ui.common import restart_application

    page = window.settings_page
    problems: list[str] = []
    texts = _widget_texts(page)
    if any("数据仓库" in text for text in texts):
        problems.append("设置页仍有「数据仓库」配置项")
    if not any("日志文件模式" in text for text in texts):
        problems.append("设置页缺少「日志文件模式」选项")
    for name in (
        "_log_keep_files_card",
        "_log_max_file_card",
        "_log_keep_days_card",
        "_log_total_card",
    ):
        if not hasattr(page, name):
            problems.append(f"设置页缺少日志卡片：{name}")
    if not callable(restart_application):
        problems.append("ui.common.restart_application 不可调用")
    original = str(config.logMode.value)
    try:
        page._on_log_mode_changed(logging_setup.MODE_DAILY)
        if not page._log_keep_days_card.isEnabled():
            problems.append("日切模式没有启用「日志保留天数」")
        if page._log_max_file_card.isEnabled():
            problems.append("日切模式仍启用「单个日志文件大小上限」")
        page._on_log_mode_changed(logging_setup.MODE_SIZE)
        if not page._log_max_file_card.isEnabled():
            problems.append("按大小切分没有启用「单个日志文件大小上限」")
    finally:
        page._on_log_mode_changed(original)
    return problems


def _check_user_password_clear(app, window) -> list[str]:
    """清除口令：默认用户可以清除他人的口令，普通用户只能清除自己的。"""
    from PyQt6.QtWidgets import QPushButton

    from app.ui.pages import user_page

    page = window.user_page
    _show_page(window, page)
    app.processEvents()

    problems: list[str] = []
    service = page.service
    member = next((info for info in service.list_users() if not info.is_default), None)
    default = next((info for info in service.list_users() if info.is_default), None)
    if member is None or default is None:
        return ["缺少可验证的默认用户 / 非默认用户"]

    member_id = member.user.id

    def info_of(user_id: int):
        return next(info for info in service.list_users() if info.user.id == user_id)

    service.set_password(member.user, "临时口令")
    page.session.commit()
    page.refresh()
    app.processEvents()

    labels = {str(widget.text() or "") for widget in page.findChildren(QPushButton)}
    if "清除口令" not in labels:
        problems.append("已设口令的用户没有「清除口令」按钮")

    state = (page._is_admin, page._user_id)
    original_confirm = user_page.confirm
    try:
        page._is_admin, page._user_id = False, default.user.id
        page._clear_password(info_of(member_id))
        if not info_of(member_id).protected:
            problems.append("普通用户清除了其他用户的口令")

        page._is_admin, page._user_id = state
        user_page.confirm = lambda *args, **kwargs: True
        page._clear_password(info_of(member_id))
    finally:
        user_page.confirm = original_confirm
        page._is_admin, page._user_id = state

    if info_of(member_id).protected:
        problems.append("默认用户没有清除他人口令")
    page.refresh()
    app.processEvents()
    return problems


def _check_filter_sections(app, window) -> list[str]:
    """筛选分组支持折叠、搜索与三态全选（双向同步），工具栏为可滚动的流式布局。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QPushButton
    from qfluentwidgets import FlowLayout

    from app.ui.pages import manage_page as manage_module

    page = window.manage_page
    _show_page(window, page)
    page.refresh()
    app.processEvents()

    problems: list[str] = []
    panel = page.filter_panel
    for section in panel.sections():
        if not section.all_box.isTristate():
            problems.append("筛选分组的三态全选框不支持三态")
        if not section.all_box.toolTip():
            problems.append("筛选分组的三态全选框没有提示文本")
        if not section.search.placeholderText():
            problems.append("筛选分组的搜索框没有占位提示")

    section = panel.type_section
    keys = list(section.boxes)
    if len(keys) < 2:
        return problems + ["类型分组没有足够的选项用于验证"]

    section.clear()
    section.boxes[keys[0]].setChecked(True)
    app.processEvents()
    if section.all_box.checkState() != Qt.CheckState.PartiallyChecked:
        problems.append("部分选中时三态框没有显示为横杠")
    if not section.all_box.isVisibleTo(section):
        problems.append("分组标题栏没有显示三态全选框")
    if panel.sections() and not panel.type_section.search.isVisibleTo(panel.type_section):
        problems.append("分组标题栏没有显示搜索框")

    section.all_box.click()
    app.processEvents()
    if section.all_box.checkState() != Qt.CheckState.Checked:
        problems.append("三态框全选后自身不是勾选状态")
    if len(section.checked_keys()) != len(keys):
        problems.append("三态框全选没有同步到分组内所有选项")
    section.all_box.click()
    app.processEvents()
    if section.checked_keys():
        problems.append("三态框取消全选没有清空分组内的勾选")

    section.search.setText(str(section.boxes[keys[0]].text()))
    app.processEvents()
    if not section.boxes[keys[0]].isVisibleTo(section) or section.boxes[keys[1]].isVisibleTo(section):
        problems.append("分组搜索框没有过滤选项")
    section.search.clear()
    app.processEvents()
    if not all(box.isVisibleTo(section) for box in section.boxes.values()):
        problems.append("清空分组搜索后选项没有恢复")
    section.clear()

    was_visible = section.scroll.isVisibleTo(section)
    section.toggle_button.click()
    app.processEvents()
    if section.scroll.isVisibleTo(section) == was_visible:
        problems.append("筛选分组不能折叠")
    section.toggle_button.click()
    app.processEvents()
    if section.scroll.isVisibleTo(section) != was_visible:
        problems.append("筛选分组不能重新展开")

    flow = page.toolbar_layout
    view = page.toolbar_view
    if not isinstance(flow, FlowLayout):
        problems.append("工具栏不是流式布局")
    if view.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
        problems.append("工具栏不应出现横向滚动条")
    if view.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAsNeeded:
        problems.append("工具栏放不下时不能纵向滚动")
    host = view.widget()
    missing = [
        key
        for key, button in page._buttons.items()
        if not isinstance(button, QPushButton) or button.parent() is not host
    ]
    if missing:
        problems.append(f"工具栏按钮不在流式容器内：{missing}")
    single = manage_module.TOOLBAR_BUTTON_HEIGHT + manage_module.TOOLBAR_ROW_SPACING
    limit = single * manage_module.TOOLBAR_MAX_ROWS + manage_module.TOOLBAR_SCROLLBAR_HEIGHT
    window.resize(900, 700)
    app.processEvents()
    page._fit_toolbar()
    app.processEvents()
    if view.height() < single:
        problems.append(f"工具栏高度 {view.height()} 小于一行按钮高度 {single}")
    if view.height() > limit:
        problems.append(f"工具栏高度 {view.height()} 超过两行上限 {limit}")
    window.resize(1200, 780)
    app.processEvents()
    page._fit_toolbar()
    app.processEvents()
    return problems


def main() -> int:
    paths.ensure_dirs()
    setup_logging()
    init_db()
    with session_scope() as session:
        seed(session)
        # 与 main.py 一致：老库启动时补齐「未分类」分类并归置无分类数据
        migrate_uncategorized(session)

    app = QApplication(sys.argv)

    from app.ui.main_window import MainWindow

    from app.services.plugin_service import plugin_service

    # 与 main.py 一致：先载入内置插件，插件类型表与查看器注册表才完整
    plugin_service.load_viewers()

    window = MainWindow()
    window.resize(1200, 780)
    print(f"window: {window.windowTitle()!r} pages={window.stackedWidget.count()}")

    failures = 0
    try:
        window.show()
        app.processEvents()
        print("show: ok")
    except Exception:  # noqa: BLE001
        failures += 1
        traceback.print_exc()

    for name in PAGES:
        page = getattr(window, name)
        try:
            if hasattr(page, "refresh"):
                page.refresh()
            print(f"  {name}: objectName={page.objectName()!r} refresh=ok")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  {name}: FAILED")
            traceback.print_exc()

    for label, check in (
        ("home_stats", lambda: _check_home_stats(window)),
        ("category_filter", lambda: _check_category_filter(app, window)),
        ("uncategorized_fixed", lambda: _check_uncategorized_fixed(app, window)),
        ("manage_selection", lambda: _check_manage_selection(app, window)),
        ("import_categories", lambda: _check_import_categories(window)),
        ("import_user_scope", lambda: _check_import_user_scope(window)),
        ("tag_picker", lambda: _check_tag_picker(app, window)),
        ("keyword_filter", lambda: _check_keyword_filter(app, window)),
        ("keyword_display", lambda: _check_keyword_display(window)),
        ("tag_page", lambda: _check_tag_page(app, window)),
        ("tag_filters", lambda: _check_tag_filters(app, window)),
        ("import_batch", lambda: _check_import_batch(app, window)),
        ("single_library", lambda: _check_single_library(window)),
        ("no_library_picker", lambda: _check_no_library_picker(window)),
        ("global_tag_marks", lambda: _check_global_tag_marks(app, window)),
        ("user_page", lambda: _check_user_page(app, window)),
        ("user_password_clear", lambda: _check_user_password_clear(app, window)),
        ("archive_tabs", lambda: _check_archive_tabs(app, window)),
        ("archive_owner", lambda: _check_archive_owner(app, window)),
        ("archive_pin", lambda: _check_archive_pin(app, window)),
        ("archive_table", lambda: _check_archive_table(app, window)),
        ("open_with", lambda: _check_open_with(window)),
        ("plugins", lambda: _check_plugins(window)),
        ("recent_focus", lambda: _check_recent_focus(app, window)),
        ("settings_extras", lambda: _check_settings_extras(window)),
        ("filter_sections", lambda: _check_filter_sections(app, window)),
        ("style_uniformity", lambda: _check_style_uniformity(window)),
        ("plugin_pages", lambda: _check_plugin_pages(window)),
        ("image_viewer", lambda: _check_image_viewer(window)),
        ("theme_background", lambda: _check_theme_background(app, window)),
    ):
        try:
            problems = check()
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  {label}: FAILED")
            traceback.print_exc()
            continue
        for problem in problems:
            failures += 1
            print(f"  {label}: {problem}")
        if not problems:
            print(f"  {label}: ok")

    print(f"RESULT failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
