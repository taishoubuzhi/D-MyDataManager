"""L2 页面检查：数据管理页（分类筛选、多选批量）与导入页（范围、批量）。"""

from __future__ import annotations

import time
from pathlib import Path

from .harness import Case, build_window, check, dispose_window, ensure_app, install_builtin_plugins

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget
from qfluentwidgets import StrongBodyLabel

import app.ui.dialogs as dialogs_module
import app.ui.pages.manage_page as manage_module
from app.core import paths
from app.core.config import resources_root
from app.core.naming import RENAME_MODE_KEYS, RenameRule, build_plan
from app.repositories import CategoryRepository, ItemFilter, TagRepository
from app.services import (
    ImportService,
    ItemService,
    LibraryService,
    TaxonomyService,
    UserService,
    is_uncategorized,
)
from app.ui.components.category_tree import FIXED_SUFFIX, category_label, menu_entries
from app.ui.components.item_card import ItemListRow
from app.ui.framework import tri_state


def _tree_item(page, category_id):
    """按 UserRole 取分类树节点；`category_id=None` 取「全部数据」根节点。"""
    for item in page.tree._iter_items():
        if item.data(0, Qt.ItemDataRole.UserRole) == category_id:
            return item
    return None


def _tree_ids(page) -> set[int]:
    """分类树里所有真实分类 id（不含「全部数据」根节点）。"""
    return {
        value
        for value in (item.data(0, Qt.ItemDataRole.UserRole) for item in page.tree._iter_items())
        if isinstance(value, int)
    }


def _list_rows(page) -> list:
    """当前列表视图里的数据行（跳过末尾的伸缩哨兵）。"""
    rows = []
    for index in range(page.list_layout.count()):
        widget = page.list_layout.itemAt(index).widget()
        if getattr(widget, "item", None) is not None:
            rows.append(widget)
    return rows


def _widget_texts(root) -> list[str]:
    """收集控件树里所有可读文本（旧门禁同名辅助函数）。"""
    texts: list[str] = []
    for widget in root.findChildren(QWidget):
        getter = getattr(widget, "text", None)
        if not callable(getter):
            continue
        try:
            texts.append(str(getter()))
        except Exception:  # noqa: BLE001 - 个别控件的 text() 需要参数
            continue
    return texts


@check("manage_category_filter", "pages")
def manage_category_filter(case: Case) -> None:
    """分类树勾选驱动列表、「未分类」固定语义、筛选栏不再有分类分组。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        session = case.session
        user_id = page.user_service.current_id()
        taxonomy = TaxonomyService(session)

        # 造第二个子分类：父分类的三态聚合才可观察
        sibling = taxonomy.create_category("Java", parent_id=fixture.category_root)
        session.commit()
        page.refresh()
        sibling_id = sibling.id if sibling is not None else None
        child_id, root_id = fixture.category_child, fixture.category_root

        panel = page.filter_panel
        if hasattr(panel, "category_section"):
            problems.append("筛选栏仍带分类分组 category_section")
        if len(panel.sections()) != 3:
            problems.append(f"筛选栏分组数 {len(panel.sections())} != 3（类型/标签/关键词）")
        if not hasattr(page, "_checked_categories"):
            problems.append("管理页缺少 _checked_categories 状态")
        if page.category_move_button.isEnabled() or page.category_delete_button.isEnabled():
            problems.append("未勾选任何分类时批量移动/删除按钮仍可用")
        root_item = _tree_item(page, None)
        if root_item is None:
            problems.append("分类树缺少「全部数据」根节点")
        elif not root_item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            problems.append("「全部数据」根节点不可勾选")
        assert not problems, "分类筛选：" + "；".join(problems)

        def set_checked(category_id, state=Qt.CheckState.Checked) -> None:
            item = _tree_item(page, category_id)
            assert item is not None, f"分类树缺少节点 {category_id}"
            item.setCheckState(0, state)
            app.processEvents()

        def clear_checks() -> None:
            for item in page.tree._iter_items():
                item.setCheckState(0, Qt.CheckState.Unchecked)
            app.processEvents()

        def expected_total(checked: set[int]) -> int:
            """与 _load_items 同口径：勾选集为空时退回当前选中分类。"""
            ids = set(checked)
            if not ids and page._category_id is not None:
                ids = {page._category_id}
            return page.item_repo.count(ItemFilter(category_ids=ids, user_ids={user_id}))

        def visible_ok(checked: set[int]) -> list[int]:
            return [item.category_id for item in page._items if item.category_id not in checked]

        def stub_batch(confirm: bool) -> list[tuple[str, int]]:
            """桩掉二次确认与分类增删，记录批量操作真正作用到的分类。"""
            calls: list[tuple[str, int]] = []
            saved_confirm = manage_module.confirm
            saved_delete = page.taxonomy.delete_category
            saved_move = page.taxonomy.move_category
            manage_module.confirm = lambda *args, **kwargs: confirm
            page.taxonomy.delete_category = lambda category, *a, **k: calls.append(("delete", category.id)) or True
            page.taxonomy.move_category = lambda category, parent, *a, **k: calls.append(("move", category.id)) or True
            try:
                page._on_category_batch_delete()
                page._on_category_batch_move()
            finally:
                manage_module.confirm = saved_confirm
                page.taxonomy.delete_category = saved_delete
                page.taxonomy.move_category = saved_move
            return calls

        # 勾选一个子分类：计数与列表跟随勾选集合，父分类半选
        set_checked(child_id)
        checked = set(page._checked_categories)
        if checked != {child_id}:
            problems.append(f"勾选子分类后 _checked_categories={sorted(checked)}")
        if page._total != expected_total(checked):
            problems.append(f"勾选子分类后计数 {page._total} != {expected_total(checked)}")
        stray = visible_ok(checked)
        if stray:
            problems.append(f"列表出现未勾选分类的数据：{stray}")
        if _tree_item(page, root_id).checkState(0) != Qt.CheckState.PartiallyChecked:
            problems.append(
                f"只勾选部分子分类时父分类状态为 {_tree_item(page, root_id).checkState(0).name}"
            )
        if not (page.category_move_button.isEnabled() and page.category_delete_button.isEnabled()):
            problems.append("勾选可操作的子分类后批量按钮未启用")

        # 再勾另一个子分类：子分类全选后父分类也变为勾选，三者都在勾选集合里
        if sibling_id is not None:
            set_checked(sibling_id)
            checked = set(page._checked_categories)
            if checked != {root_id, child_id, sibling_id}:
                problems.append(f"勾选两个子分类后 _checked_categories={sorted(checked)}")
            if _tree_item(page, root_id).checkState(0) != Qt.CheckState.Checked:
                problems.append(
                    f"子分类全选后父分类状态为 {_tree_item(page, root_id).checkState(0).name}"
                )

        # 二次确认被拒绝：不得改动任何分类
        refused = stub_batch(confirm=False)
        if refused:
            problems.append(f"二次确认被拒绝后仍执行了批量操作：{refused}")

        # 取消勾选：计数回到全量，根节点恢复未勾选
        clear_checks()
        checked = set(page._checked_categories)
        if checked:
            problems.append(f"取消勾选后 _checked_categories={sorted(checked)}")
        if page._total != expected_total(checked):
            problems.append(f"取消勾选后计数 {page._total} != {expected_total(checked)}")
        tree_root = _tree_item(page, None)
        if tree_root.checkState(0) != Qt.CheckState.Unchecked:
            problems.append(f"取消勾选后根节点状态为 {tree_root.checkState(0).name}")
        if page.category_move_button.isEnabled() or page.category_delete_button.isEnabled():
            problems.append("取消勾选后批量按钮仍可用")

        # 只勾选无子分类的根分类：不可批量操作
        nodes = taxonomy.tree(user_id=user_id)
        child_parents = {node.category.parent_id for node in nodes if node.depth > 0}
        uncategorized = taxonomy.uncategorized_category(user_id=user_id)
        plain_roots = [
            node.category.id
            for node in nodes
            if node.depth == 0
            and node.category.id not in child_parents
            and node.category.id != root_id
            and not is_uncategorized(node.category)
        ]
        if not plain_roots:
            problems.append("没有可用于验证的「无子分类根分类」")
        else:
            set_checked(plain_roots[0])
            if page.category_move_button.isEnabled() or page.category_delete_button.isEnabled():
                problems.append("只勾选无子分类的根分类时批量按钮仍可用")
            clear_checks()

        # 勾选父分类：后代全部勾选，批量删除只动勾选范围内的子分类
        set_checked(root_id)
        checked = set(page._checked_categories)
        descendants = {child_id} | ({sibling_id} if sibling_id is not None else set())
        if not descendants <= checked:
            problems.append(f"勾选父分类后后代未全部进入勾选集合：{sorted(checked)}")
        if any(
            item.checkState(0) != Qt.CheckState.Checked
            for item in page.tree._iter_items()
            if item.data(0, Qt.ItemDataRole.UserRole) in descendants
        ):
            problems.append("勾选父分类后后代节点没有全部勾选")
        deleted = [category_id for action, category_id in stub_batch(confirm=True) if action == "delete"]
        top_ids = {node.category.id for node in nodes if node.category.parent_id is None}
        if not deleted:
            problems.append("勾选父分类后批量删除没有作用到任何分类")
        if set(deleted) & top_ids:
            problems.append(f"批量删除动了顶层分类：{sorted(set(deleted) & top_ids)}")
        if not set(deleted) <= checked:
            problems.append(f"批量删除越出勾选范围：{sorted(set(deleted) - checked)}")
        clear_checks()

        # 勾选「全部数据」：全选但不可批量操作，且 hint 提示改为子分类
        all_item = _tree_item(page, None)
        all_item.setCheckState(0, Qt.CheckState.Checked)
        app.processEvents()
        checked = set(page._checked_categories)
        if checked != _tree_ids(page):
            problems.append(f"勾选「全部数据」后 _checked_categories={sorted(checked)}")
        unchecked = [
            item.text(0)
            for item in page.tree._iter_items()
            if item.checkState(0) != Qt.CheckState.Checked
        ]
        if unchecked:
            problems.append(f"勾选「全部数据」后仍有节点未勾选：{unchecked}")
        per_category = sum(
            page.item_repo.count(ItemFilter(category_ids={category_id}, user_ids={user_id}))
            for category_id in sorted(checked)
        )
        if page._total != per_category:
            problems.append(f"全选后计数 {page._total} != 各分类之和 {per_category}")
        if page.category_move_button.isEnabled() or page.category_delete_button.isEnabled():
            problems.append("全选「全部数据」后批量按钮仍可用")
        if page.category_hint.text() == "勾选分类可批量移动或删除":
            problems.append("全选「全部数据」后提示语没有改成只处理子分类")
        blocked = stub_batch(confirm=True)
        if blocked:
            problems.append(f"全选「全部数据」时批量操作仍然动手：{blocked}")
        if set(page._checked_categories) != checked:
            problems.append("全选「全部数据」时批量操作改写了勾选集合")
        tree_root = _tree_item(page, None)
        tree_root.setCheckState(0, Qt.CheckState.Unchecked)
        app.processEvents()
        if set(page._checked_categories):
            problems.append(f"取消「全部数据」后 _checked_categories={sorted(page._checked_categories)}")

        # 「未分类」是固定分类：无菜单、树里带固定后缀、不可批量处理
        if uncategorized is None or not is_uncategorized(uncategorized):
            problems.append("默认用户没有固定的「未分类」分类")
        else:
            if menu_entries(fixed=True):
                problems.append(f"固定分类仍有右键菜单：{menu_entries(fixed=True)}")
            if not menu_entries(fixed=False):
                problems.append("普通分类没有右键菜单")
            roots = [node for node in nodes if node.depth == 0]
            if not roots or roots[-1].category.id != uncategorized.id:
                problems.append("「未分类」不是分类树里最后一个根分类")
            if roots and FIXED_SUFFIX not in category_label(roots[-1], fixed=True):
                problems.append("「未分类」的标签缺少固定后缀")
            if not any(FIXED_SUFFIX in item.text(0) for item in page.tree._iter_items()):
                problems.append("分类树里没有带固定后缀的节点")
            if uncategorized.id not in page.tree.fixed_ids():
                problems.append("分类树 fixed_ids() 里没有「未分类」")
            tree_root = _tree_item(page, None)
            if tree_root.childCount() and FIXED_SUFFIX not in tree_root.child(
                tree_root.childCount() - 1
            ).text(0):
                problems.append("「全部数据」的最后一个子项不是「未分类」")

        assert not problems, "分类筛选：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("manage_selection", "pages")
def manage_selection(case: Case) -> None:
    """多选（Ctrl / Ctrl+Shift / 三态全选）、双击打开与批量按钮启用状态。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        ImportService(case.session).import_text(
            "自检第三项", "甲\n乙", category_id=fixture.category_child, keywords=["自检"]
        )
        case.session.commit()
        page.refresh()
        app.processEvents()

        if len(page._items) < 3:
            problems.append(f"可见数据不足 3 项：{len(page._items)}")
            assert not problems, "多选：" + "；".join(problems)
        items = list(page._items)

        opened: list = []
        page._selected.clear()
        page._on_open = opened.append
        page._render()
        rows = _list_rows(page)
        if len(rows) != len(items):
            problems.append(f"列表行数 {len(rows)} != 数据项数 {len(items)}")

        page._on_item_activated(items[0], Qt.KeyboardModifier.NoModifier)
        if set(page._selected) != {items[0].id}:
            problems.append(f"普通点击后选中 {sorted(page._selected)} != {items[0].id}")
        if opened:
            problems.append("单击就触发了打开动作")

        page._on_item_activated(items[2], Qt.KeyboardModifier.ShiftModifier)
        expect = {items[0].id, items[1].id, items[2].id}
        if set(page._selected) != expect:
            problems.append(f"Shift 区间选择 {sorted(page._selected)} != {sorted(expect)}")

        page._on_item_activated(items[2], Qt.KeyboardModifier.ControlModifier)
        expect = {items[0].id, items[1].id}
        if set(page._selected) != expect:
            problems.append(f"Ctrl 点选 {sorted(page._selected)} != {sorted(expect)}")

        rows[0].opened.emit(rows[0].item)
        if [item.id for item in opened] != [rows[0].item.id]:
            problems.append(f"双击打开没有把该数据交出去：{[item.id for item in opened]}")

        page.clear_selection()
        rows[0].check_box.setChecked(True)
        app.processEvents()
        if set(page._selected) != {rows[0].item.id}:
            problems.append(f"勾选单行的复选框后选中 {sorted(page._selected)}")
        if page.select_all_box.checkState() != Qt.CheckState.PartiallyChecked:
            problems.append(f"部分选中时全选框状态为 {page.select_all_box.checkState().name}")

        page.select_all_box.setChecked(True)
        app.processEvents()
        if set(page._selected) != {item.id for item in items}:
            problems.append(f"全选后选中 {sorted(page._selected)} != 全部可见项")
        if page.select_all_box.checkState() != Qt.CheckState.Checked:
            problems.append(f"全选后全选框状态为 {page.select_all_box.checkState().name}")
        if not page.move_button.isEnabled():
            problems.append("有选中项时批量移动按钮不可用")

        page.clear_selection()
        app.processEvents()
        if page._selected:
            problems.append(f"清空选择后仍有选中项：{sorted(page._selected)}")
        if page.select_all_box.checkState() != Qt.CheckState.Unchecked:
            problems.append(f"清空选择后全选框状态为 {page.select_all_box.checkState().name}")
        if page.move_button.isEnabled():
            problems.append("清空选择后批量移动按钮仍可用")

        single = dict(manage_module.menu_items(1))
        for key in ("open", "open_with", "move", "details"):
            if key not in single:
                problems.append(f"单项右键菜单缺少 {key}")
        multi = dict(manage_module.menu_items(2))
        if "2 项" not in multi.get("move", ""):
            problems.append(f"多选右键菜单没有标注数量：{multi.get('move')!r}")
        entries = manage_module.open_with_items(".txt")
        if not entries or entries[0][0] != "system" or entries[-1][0] != "ask":
            problems.append(f"查看器菜单不是「系统默认程序…交给系统选择」：{[key for key, *_ in entries]}")
        if tri_state(1, 3) != Qt.CheckState.PartiallyChecked:
            problems.append("三态计算：部分选中不是半选")
        page._selected = {rows[0].item.id}
        if page._build_menu(rows[0].item) is None:
            problems.append("单项右键菜单构建失败")

        assert not problems, "多选：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("import_page_scope", "pages")
def import_page_scope(case: Case) -> None:
    """导入页分类/用户范围、单一默认库、无库选择器、文件夹批量入队。"""
    fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        app = ensure_app()
        app.processEvents()
        session = case.session
        page = window.import_page
        users = UserService(session)
        user_id = users.current_id()

        ids = {page.category_box.itemData(index) for index in range(page.category_box.count())}
        tree_ids = {node.category.id for node in TaxonomyService(session).tree(user_id=user_id)}
        if ids != tree_ids:
            problems.append(f"导入页分类下拉 {sorted(ids)} != 目标用户分类树 {sorted(tree_ids)}")
        uncategorized = CategoryRepository(session).by_name("未分类", None, user_id)
        if uncategorized is None or uncategorized.id not in ids:
            problems.append("导入页分类下拉里没有真实「未分类」分类")
        if page.category_box.currentData() is None:
            problems.append("导入页分类下拉没有默认选中的分类")

        def assert_default_category(tag: str, target_id: int) -> None:
            """默认分类必须是该目标用户的「未分类」，不能落到第一个根分类。"""
            category = TaxonomyService(session).uncategorized_category(
                user_id=target_id, create=False
            )
            data = page.category_box.currentData()
            text = page.category_box.currentText()
            if category is None:
                problems.append(f"{tag}：目标用户 {target_id} 没有「未分类」分类")
            elif data != category.id:
                problems.append(f"{tag}：默认分类 {data!r}（{text}）!= 该用户「未分类」{category.id}")
            elif text != "未分类":
                problems.append(f"{tag}：默认分类文本 {text!r} != 「未分类」")

        assert_default_category("初始默认分类", user_id)

        # 归属用户：默认用户可替别人导入，普通用户被锁在自己名下
        backup = users.current()
        guest = users.by_id(fixture.user_id)
        if guest is None:
            problems.append("夹具用户缺失")
            assert not problems, "导入范围：" + "；".join(problems)
        labels = [page.user_box.itemText(index) for index in range(page.user_box.count())]
        if not page.user_box.isEnabled():
            problems.append("默认用户无法切换导入目标用户")
        if not any("（当前用户）" in text for text in labels):
            problems.append(f"导入目标用户下拉未标注当前用户：{labels}")
        try:
            users.set_current(guest)
            page.refresh()
            app.processEvents()
            scope = [
                (page.user_box.itemData(index), page.user_box.itemText(index))
                for index in range(page.user_box.count())
            ]
            if [data for data, _ in scope] != [fixture.user_id]:
                problems.append(f"普通用户的目标用户下拉里出现其它用户：{scope}")
            if page.user_box.isEnabled():
                problems.append("普通用户的目标用户下拉仍可编辑")
            if page.target_user_id() != fixture.user_id:
                problems.append(f"普通用户导入目标 {page.target_user_id()} != {fixture.user_id}")
            # 目标用户切换后默认分类必须换成新用户的「未分类」（回归：曾落到第一个根分类）
            guest_nodes = TaxonomyService(session).tree(user_id=fixture.user_id)
            guest_first_root = next(
                (node.category.id for node in guest_nodes if node.depth == 0), None
            )
            if page.category_box.currentData() == guest_first_root:
                problems.append("切到普通用户后默认分类落到了第一个根分类")
            assert_default_category("切到普通用户后默认分类", fixture.user_id)
        finally:
            users.set_current(backup)
            page.refresh()
            app.processEvents()
        assert_default_category("恢复默认用户身份后默认分类", page.target_user_id())
        if not page.user_box.isEnabled():
            problems.append("切回默认用户后目标用户下拉仍被禁用")
        index = page.user_box.findData(backup.id)
        if index < 0:
            problems.append("切回默认用户后下拉里没有默认用户")
        else:
            page.user_box.setCurrentIndex(index)
            app.processEvents()
        if page.target_user_id() != backup.id:
            problems.append(f"切回默认用户后 target_user_id={page.target_user_id()} != {backup.id}")
        assert_default_category("切回默认用户后默认分类", backup.id)
        if {page.category_box.itemData(i) for i in range(page.category_box.count())} != tree_ids:
            problems.append("切回默认用户后分类下拉没有回到该用户的分类树")
        foreign = {
            node.category.id for node in TaxonomyService(session).tree(user_id=fixture.user_id)
        } - tree_ids
        leaked = foreign & {page.category_box.itemData(index) for index in range(page.category_box.count())}
        if leaked:
            problems.append(f"导入页分类下拉里出现了其它用户的分类：{sorted(leaked)}")

        # 只有一个默认库、没有库选择器
        settings = window.settings_page
        settings._refresh_libraries()
        app.processEvents()
        buttons = {button.text() for button in settings.findChildren(QPushButton)}
        forbidden = [text for text in ("新建库", "导入库文件夹", "设为默认", "移除") if text in buttons]
        if forbidden:
            problems.append(f"设置页仍有多库按钮：{forbidden}")
        missing = [
            text
            for text in ("更改位置", "扫描并登记", "重建目录结构", "打开文件夹")
            if text not in buttons
        ]
        if missing:
            problems.append(f"设置页缺少库操作按钮：{missing}")
        if LibraryService(session).ensure_default() is None:
            problems.append("没有默认库")
        rows: list[str] = []
        for index in range(settings._library_layout.count()):
            widget = settings._library_layout.itemAt(index).widget()
            if widget is not None:
                rows.extend(label.text() for label in widget.findChildren(StrongBodyLabel))
        body = " ".join(rows)
        if paths.GLOBAL_DIR_NAME not in body:
            problems.append(f"库目录清单缺少全局目录 {paths.GLOBAL_DIR_NAME}：{rows}")
        for info in UserService(session).list_users():
            if info.name not in body:
                problems.append(f"库目录清单缺少用户 {info.name}")
        if str(resources_root()) not in _widget_texts(settings):
            problems.append("设置页没有显示库根目录")
        if [text for text in _widget_texts(page) if "目标库" in text]:
            problems.append("导入页仍显示库选择器")

        # 数据详情里不再提「所在库」
        captured: list[str] = []

        class _FakeMessageBox:
            def __init__(self, title, text, parent=None):
                captured.append(str(text))

            def exec(self):
                return None

        manage = window.manage_page
        saved_box = manage_module.MessageBox
        manage_module.MessageBox = _FakeMessageBox
        try:
            if manage._items:
                manage._on_details(manage._items[0])
        finally:
            manage_module.MessageBox = saved_box
        if any("所在库" in text for text in captured):
            problems.append("数据详情里仍然显示「所在库」")

        # 文件夹整体导入：明细、子目录与入队结果
        sources = Path(case.root) / "import_src"
        (sources / "sub").mkdir(parents=True, exist_ok=True)
        (sources / "a.txt").write_text("甲", encoding="utf-8")
        (sources / "b.txt").write_text("乙", encoding="utf-8")
        (sources / "sub" / "c.txt").write_text("丙", encoding="utf-8")
        if page.details_card.isVisibleTo(page):
            problems.append("尚未选择文件时已显示文件明细")
        page._set_mode("file")
        page._on_directory(str(sources))
        app.processEvents()
        if page.details_table.rowCount() != 3:
            problems.append(f"文件夹明细行数 {page.details_table.rowCount()} != 3")
        summary = page.details_summary.text()
        if "3 个文件" not in summary:
            problems.append(f"文件夹明细摘要没有文件数：{summary}")
        subdirs = {
            cell.text() if (cell := page.details_table.item(row, 4)) else ""
            for row in range(page.details_table.rowCount())
        }
        if "sub" not in subdirs:
            problems.append(f"文件夹明细没有保留子目录：{sorted(subdirs)}")
        if page.category_box.isEnabled():
            problems.append("文件夹导入时分类下拉仍可用（应以文件夹名作为新分类）")
        if sources.name not in page.category_hint.text():
            problems.append(f"文件夹导入提示未说明新分类：{page.category_hint.text()}")

        page._on_files([str(sources / "a.txt"), str(sources / "b.txt")])
        app.processEvents()
        if page.details_table.rowCount() != 2:
            problems.append(f"文件模式明细行数 {page.details_table.rowCount()} != 2")
        if not page.category_box.isEnabled():
            problems.append("文件导入时分类下拉不可用")

        page._on_directory(str(sources))
        app.processEvents()
        page.import_now()
        deadline = time.time() + 90.0
        while page._worker is not None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
        app.processEvents()
        if page._worker is not None:
            problems.append("批量导入 90 秒内没有结束")
        else:
            if page.progress_bar.value() != 100:
                problems.append(f"批量导入进度 {page.progress_bar.value()} != 100")
            if page.result_table.rowCount() < 3:
                problems.append(f"批量导入结果行数 {page.result_table.rowCount()} < 3")
            label = page.progress_label.text()
            if "成功" not in label:
                problems.append(f"批量导入结果没有报告成功：{label}")
            if page.details_card.isVisibleTo(page):
                problems.append("批量导入完成后仍显示文件明细")

        assert not problems, "导入范围：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("tag_picker_keywords", "pages")
def tag_picker_keywords(case: Case) -> None:
    """标签选择器已知标签、关键词筛选与列表关键词展示。"""
    fixture, window = build_window(case, show=True)
    problems: list[str] = []
    try:
        app = ensure_app()
        session = case.session
        page = window.import_page
        window.switchTo(page)
        app.processEvents()
        taxonomy = TaxonomyService(session)
        user_id = page.target_user_id()
        if len(TagRepository(session).names(user_id=user_id)) < 2:
            taxonomy.create_tag("自检备用标签")
            session.commit()
            page.refresh()
            user_id = page.target_user_id()
        tags = sorted(TagRepository(session).names(user_id=user_id))
        picker = page.tag_input
        if sorted(picker.known_tags()) != tags:
            problems.append(f"标签选择器已知标签 {sorted(picker.known_tags())} != {tags}")

        if len(tags) >= 2:
            picker.set_keywords([])
            picker._toggle(tags[0], True)
            picker._toggle(tags[1], True)
            app.processEvents()
            if picker.keywords() != tags[:2]:
                problems.append(f"连续勾选两个标签后 keywords()={picker.keywords()}")
            if picker.chips_height() <= 0:
                problems.append("标签块区域高度为 0（标签块不可见）")
            picker._toggle(tags[0], False)
            app.processEvents()
            if picker.keywords() != tags[1:2]:
                problems.append(f"取消一个标签后 keywords()={picker.keywords()}")
            picker.clear()
            app.processEvents()
            if picker.keywords() or picker.chips_height() != 0:
                problems.append(f"清空后残留 {picker.keywords()} / 高度 {picker.chips_height()}")
        else:
            problems.append(f"已知标签不足两个，无法验证标签块：{tags}")

        manage = window.manage_page
        manage_user = manage.user_service.current_id()
        manage.refresh()
        app.processEvents()
        words = sorted(TagRepository(session).distinct_keywords(user_id=manage_user))
        if set(manage.filter_panel._keyword_boxes) != set(words):
            problems.append(f"关键词筛选项 {sorted(manage.filter_panel._keyword_boxes)} != 库中关键词 {words}")
        if not words:
            problems.append("夹具里没有可用于筛选的关键词")
        else:
            before = manage._total
            target = words[0]
            manage.filter_panel._keyword_boxes[target].setChecked(True)
            app.processEvents()
            expected = manage.item_repo.count(
                ItemFilter(keywords={target}, user_ids={manage_user})
            )
            if manage._total != expected:
                problems.append(f"勾选关键词「{target}」后计数 {manage._total} != {expected}")
            manage.filter_panel._keyword_boxes[target].setChecked(False)
            app.processEvents()
            if manage._total != before:
                problems.append(f"取消关键词筛选后计数 {manage._total} != {before}")

        keyword_items = [item for item in manage._items if item.keywords]
        if not keyword_items:
            problems.append("列表里没有带关键词的数据")
        else:
            item = keyword_items[0]
            row = ItemListRow(item)
            text = row._keywords.text()
            if str(item.keywords[0]) not in text:
                problems.append(f"列表行没有显示关键词「{item.keywords[0]}」：{text!r}")

        assert not problems, "标签与关键词：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("manage_edit_dialog", "pages")
def manage_edit_dialog(case: Case) -> None:
    """数据信息编辑：右键菜单有入口、弹窗带只读信息区，改名后库内文件一起改名。"""
    from app.services import ItemService
    from app.ui.dialogs import ItemEditDialog

    fixture, window = build_window(case)
    problems: list[str] = []
    app = None
    dialog = None
    try:
        app = ensure_app()
        session = case.session
        page = window.manage_page
        item = ImportService(session).import_text(
            "改名前", "改名前的内容", category_id=fixture.category_child
        )
        session.commit()
        page.refresh()
        app.processEvents()

        if "edit" not in dict(manage_module.menu_items(1)):
            problems.append("单项右键菜单没有「编辑信息」入口")

        info = dict(page._item_info(item))
        for key in ("类型", "大小", "归属用户", "所在库", "库内路径", "磁盘位置", "创建时间", "内容指纹"):
            if not info.get(key) or info[key] == "—":
                problems.append(f"编辑弹窗缺少只读信息：{key}")

        dialog = ItemEditDialog(
            item,
            categories=[
                (node.category.id, node.category.name)
                for node in page.taxonomy.tree(user_id=item.user_id)
            ],
            known_tags=[],
            info=page._item_info(item),
            parent=window,
        )
        texts = _widget_texts(dialog)
        for key in ("数据信息", "可修改的信息", "库内路径", "磁盘位置"):
            if not any(key in text for text in texts):
                problems.append(f"编辑弹窗没有渲染「{key}」")
        if dialog.values()["name"] != item.name:
            problems.append(f"编辑弹窗的名称初值 {dialog.values()['name']!r} != {item.name!r}")

        dialog.name_edit.setText("改名之后")
        ItemService(session).update(item, name=dialog.values()["name"])
        session.commit()
        path = LibraryService(session).abs_path(item)
        if path is None or path.name != "改名之后.txt":
            problems.append(f"改名后库内文件没有跟着改：{path}")
        elif not path.is_file():
            problems.append("改名后库内文件不存在")

        if getattr(page, "session", None) is not None:
            # 页面用的是另一个会话，改完要让它的身份映射重新读库。
            page.session.expire_all()
        page.refresh()
        app.processEvents()
        if not any(row.item is not None and row.item.name == "改名之后" for row in _list_rows(page)):
            problems.append(
                "改名后列表没有出现新名称："
                f"行={[row.item.name for row in _list_rows(page) if row.item is not None]} / "
                f"可见项={[entry.name for entry in page._items]}"
            )

        assert not problems, "数据信息编辑：" + "；".join(problems[:12])
    finally:
        if dialog is not None:
            from PyQt6 import sip

            dialog.close()
            dialog.setParent(None)
            sip.delete(dialog)
        if app is not None:
            app.processEvents()
        dispose_window(window)


@check("manage_editor_menu", "pages")
def manage_editor_menu(case: Case) -> None:
    """右键「编辑器」子菜单是程序本体内置的：有系统默认程序 / 各编辑器 / 交给系统选择，禁用编辑器库也还在。"""
    from app.sdk import editors

    install_builtin_plugins()
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        session = case.session
        page = window.manage_page
        item = ImportService(session).import_text(
            "编辑目标", "原始内容", category_id=fixture.category_child
        )
        session.commit()
        page.session.expire_all()
        page.refresh()
        app.processEvents()
        target = next(
            (row.item for row in _list_rows(page) if row.item is not None and row.item.id == item.id),
            None,
        )
        assert target is not None, "列表里找不到刚导入的数据项"

        keys = [key for key, _text in manage_module.menu_items(1)]
        if "editor" not in keys or "open_with" not in keys:
            problems.append(f"右键菜单应有「查看器」「编辑器」，实际 {keys}")
        elif keys.index("editor") != keys.index("open_with") + 1:
            problems.append(f"「编辑器」应紧跟「查看器」，实际 {keys}")

        menu_texts = [action.text() for action in page._build_menu(target).actions()]
        if not menu_texts or menu_texts[0] != "直接打开" or "编辑器" in menu_texts:
            problems.append(f"「编辑器」应是子菜单、不进 actions()，实际 {menu_texts}")

        inner = [action.text() for action in page._build_editor_menu(target).actions()]
        if not inner or inner[0] != "系统默认程序" or inner[-1] != "交给系统选择…":
            problems.append(f"编辑器子菜单首尾应是系统默认程序 / 交给系统选择…，实际 {inner}")

        # 编辑器库被禁用时（这里直接让 editors_for 返回空）子菜单仍要在，只是只剩系统项
        original_for = manage_module.editors_for
        manage_module.editors_for = lambda suffix: ()
        try:
            fallback = [action.text() for action in page._build_editor_menu(target).actions()]
        finally:
            manage_module.editors_for = original_for
        if fallback[:1] != ["系统默认程序"] or fallback[-1:] != ["交给系统选择…"]:
            problems.append(f"没有编辑器插件时子菜单应只剩系统项，实际 {fallback}")

        seen: list[str] = []
        original_edit = manage_module.edit_with
        fake_edit = lambda path, editor, parent=None: (seen.append(str(path)), (True, "自检"))[1]
        manage_module.edit_with = fake_edit
        editors.edit_with = fake_edit
        try:
            action = next(
                (
                    action
                    for action in page._build_editor_menu(target).actions()
                    if action.text() not in ("系统默认程序", "交给系统选择…")
                ),
                None,
            )
            if action is None:
                problems.append("没有可点名的编辑器插件，无法验证回调路径")
            else:
                action.trigger()
        finally:
            manage_module.edit_with = original_edit
            editors.edit_with = original_edit
        expected = LibraryService(session).abs_path(item)
        if not seen or expected is None or Path(seen[-1]).resolve() != expected.resolve():
            problems.append(f"「编辑器 → 点名插件」应打开该项的库内文件 {expected}，实际 {seen}")

        assert not problems, "编辑器菜单：" + "；".join(problems[:12])
    finally:
        dispose_window(window)

@check("manage_double_click_action", "pages")
def manage_double_click_action(case: Case) -> None:
    """设置页新增「左键双击」配置：默认打开查看器，可改成打开编辑器。"""
    from app.core.config import DOUBLE_CLICK_EDITOR, DOUBLE_CLICK_VIEWER, config
    from app.sdk import editors
    from app.ui.pages.settings_page import ComboSettingCard

    install_builtin_plugins()
    fixture, window = build_window(case)
    problems: list[str] = []
    original = config.doubleClickAction.value
    original_open = manage_module.open_path
    original_edit = editors.edit_path
    try:
        app = ensure_app()
        session = case.session
        page = window.manage_page
        item = ImportService(session).import_text(
            "双击目标", "双击内容", category_id=fixture.category_child
        )
        session.commit()
        page.session.expire_all()
        page.refresh()
        app.processEvents()
        row = next(
            (r for r in _list_rows(page) if r.item is not None and r.item.id == item.id), None
        )
        assert row is not None, "列表里找不到刚导入的数据项"

        cards = [
            card
            for card in window.settings_page.findChildren(ComboSettingCard)
            if card.titleLabel.text() == "左键双击"
        ]
        if len(cards) != 1:
            problems.append(f"设置页应有 1 张「左键双击」卡，实际 {len(cards)}")
        else:
            labels = [cards[0].combo.itemText(i) for i in range(cards[0].combo.count())]
            if labels != ["打开查看器", "打开编辑器"]:
                problems.append(f"「左键双击」下拉的选项是 {labels}")
        if config.doubleClickAction.value != DOUBLE_CLICK_VIEWER:
            problems.append(f"默认动作应是打开查看器，实际 {config.doubleClickAction.value!r}")

        viewer_calls: list[str] = []
        editor_calls: list[str] = []
        manage_module.open_path = lambda path, parent=None: (
            viewer_calls.append(str(path)),
            (True, "自检"),
        )[1]
        fake_edit_path = lambda path, parent=None: (
            editor_calls.append(str(path)),
            (True, "自检"),
        )[1]
        editors.edit_path = fake_edit_path
        manage_module.edit_path_with = fake_edit_path

        row.opened.emit(row.item)  # 双击
        if len(viewer_calls) != 1 or editor_calls:
            problems.append(f"默认双击应交给查看器：viewer={viewer_calls} editor={editor_calls}")

        config.set(config.doubleClickAction, DOUBLE_CLICK_EDITOR)
        row.opened.emit(row.item)
        if len(editor_calls) != 1 or len(viewer_calls) != 1:
            problems.append(f"切成编辑器后双击应交给编辑器：viewer={viewer_calls} editor={editor_calls}")
        expected = LibraryService(session).abs_path(item)
        if not editor_calls or expected is None or Path(editor_calls[-1]).resolve() != expected.resolve():
            problems.append(f"双击编辑器应打开该项的库内文件 {expected}，实际 {editor_calls}")

        assert not problems, "左键双击配置：" + "；".join(problems[:12])
    finally:
        manage_module.open_path = original_open
        manage_module.edit_path_with = original_edit
        editors.edit_path = original_edit
        config.set(config.doubleClickAction, original)
        dispose_window(window)


@check("manage_batch_rename", "pages")
def manage_batch_rename(case: Case) -> None:
    """批量重命名：四种方式的清单、重名 -1/-2、勾选范围、参数显隐与真实落盘改名。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        imports = ImportService(case.session)
        user_id = UserService(case.session).current_id()
        items = [
            imports.import_text(name, f"{name} 的正文", category_id=fixture.category_child, user_id=user_id)
            for name in ("abc", "bdh", "kjc")
        ]
        case.session.commit()
        page.refresh()
        app.processEvents()

        entries = [(item.id, page._item_display_name(item)) for item in items]
        shown = [name for _id, name in entries]
        if shown != ["abc.txt", "bdh.txt", "kjc.txt"]:
            problems.append(f"变更清单里应是带后缀的文件名，实际 {shown}")

        def dialog(mode: str):
            built = dialogs_module.BatchRenameDialog(entries, parent=window)
            built.mode_box.setCurrentIndex(RENAME_MODE_KEYS.index(mode))
            return built

        replace = dialog("replace")
        replace.find_edit.setText("b")
        replace.replace_edit.setText("j")
        if replace.plan_names() != ["ajc.txt", "jdh.txt", "kjc.txt"]:
            problems.append(f"替换 b→j 后是 {replace.plan_names()}")
        if replace.renames() != [(items[0].id, "ajc"), (items[1].id, "jdh")]:
            problems.append(f"只该交回真正变了的行：{replace.renames()}")

        overwrite = dialog("overwrite")
        overwrite.base_edit.setText("照片")
        overwrite.width_edit.setValue(2)
        overwrite._rows[2][2].setChecked(False)
        if overwrite.plan_names() != ["照片01.txt", "照片02.txt", "kjc.txt"]:
            problems.append(f"覆盖并编号、第三行取消勾选后是 {overwrite.plan_names()}")
        if overwrite.renames() != [(items[0].id, "照片01"), (items[1].id, "照片02")]:
            problems.append(f"取消勾选的行不应改：{overwrite.renames()}")

        same = dialog("overwrite")
        same.base_edit.setText("同名")
        same.numbered_box.setChecked(False)
        same._rows[2][2].setChecked(False)
        if same.plan_names() != ["同名.txt", "同名-1.txt", "kjc.txt"]:
            problems.append(f"重名应追加 -1、-2：{same.plan_names()}")
        reserved = dialogs_module.BatchRenameDialog(entries, parent=window, reserved={"同名.txt"})
        reserved.mode_box.setCurrentIndex(RENAME_MODE_KEYS.index("overwrite"))
        reserved.base_edit.setText("同名")
        reserved.numbered_box.setChecked(False)
        if reserved.plan_names()[0] != "同名-1.txt":
            problems.append(f"已被占用的名称应让位加 -1：{reserved.plan_names()}")

        insert = dialog("insert")
        if insert.text_edit.isHidden() or not insert.base_edit.isHidden() or not insert.find_edit.isHidden():
            problems.append("切到「添加」后参数表没有换成插入用的控件")
        insert.text_edit.setText("新-")
        insert.position_edit.setValue(1)
        insert._rows[2][2].setChecked(False)
        if insert.plan_names() != ["新-abc.txt", "新-bdh.txt", "kjc.txt"]:
            problems.append(f"开头插入后是 {insert.plan_names()}")

        delete = dialog("delete")
        delete.position_edit.setValue(2)
        delete.length_edit.setValue(1)
        delete._rows[2][2].setChecked(False)
        if delete.plan_names() != ["ac.txt", "bh.txt", "kjc.txt"]:
            problems.append(f"删掉第 2 个字后是 {delete.plan_names()}")

        plan = build_plan(["a.txt"], RenameRule(mode="delete", position=99, length=1), [True], set())
        if plan != ["a.txt"]:
            problems.append(f"位置超出长度应跳过：{plan}")

        class _FakeRenameDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def renames(self):
                return [(items[0].id, "改名甲"), (items[1].id, "改名乙")]

        original = manage_module.BatchRenameDialog
        manage_module.BatchRenameDialog = _FakeRenameDialog
        try:
            page._selected = {item.id for item in items}
            page._on_batch_rename()
        finally:
            manage_module.BatchRenameDialog = original
        case.session.expire_all()
        libraries = LibraryService(case.session)
        if [item.name for item in items][:2] != ["改名甲", "改名乙"]:
            problems.append(f"确认后应改掉显示名：{[item.name for item in items]}")
        if [libraries.abs_path(item).name for item in items][:2] != ["改名甲.txt", "改名乙.txt"]:
            problems.append(f"确认后应同时改掉库内文件名：{[libraries.abs_path(item).name for item in items]}")
        if items[2].name != "kjc":
            problems.append(f"没在清单里的数据不该被改：{items[2].name}")

        assert not problems, "批量重命名：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("manage_tri_state_managers", "pages")
def manage_tri_state_managers(case: Case) -> None:
    """标签 / 关键词管理：三态清单、同名追加、页面按钮与批量应用。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        imports = ImportService(case.session)
        user_id = UserService(case.session).current_id()
        first = imports.import_text(
            "三态甲",
            "甲",
            category_id=fixture.category_child,
            user_id=user_id,
            keywords=["共同词", "只甲"],
            tags=["共同标签", "只甲标签"],
        )
        second = imports.import_text(
            "三态乙",
            "乙",
            category_id=fixture.category_child,
            user_id=user_id,
            keywords=["共同词", "只乙"],
            tags=["共同标签", "只乙标签"],
        )
        case.session.commit()
        page.refresh()
        app.processEvents()

        for name, button in (
            ("标签管理", getattr(page, "tag_button", None)),
            ("关键词管理", getattr(page, "keyword_button", None)),
            ("批量重命名", getattr(page, "rename_button", None)),
        ):
            if button is None:
                problems.append(f"选择条缺少「{name}」按钮")
        keys = dict(manage_module.menu_items(1))
        for key in ("tag", "keyword", "rename"):
            if key not in keys:
                problems.append(f"右键菜单缺少 {key}")
        many = dict(manage_module.menu_items(2))
        if "2 项" not in many.get("rename", ""):
            problems.append(f"多选时批量重命名没有标注数量：{many.get('rename')!r}")
        page.clear_selection()
        app.processEvents()
        if getattr(page, "rename_button", None) is not None and page.rename_button.isEnabled():
            problems.append("清空选择后批量重命名按钮仍可用")
        page._selected = {first.id, second.id}
        page._sync_selection()
        app.processEvents()
        if not page.rename_button.isEnabled() or not page.keyword_button.isEnabled():
            problems.append("有选中项时选择条的批量按钮应可用")

        dialog = dialogs_module.TagManagerDialog(
            [("共同标签", tri_state(2, 2)), ("只甲标签", tri_state(1, 2)), ("没有的标签", tri_state(0, 2))],
            parent=window,
            count=2,
            suffixes={"共同标签": "（全局）"},
        )
        if dialog.additions() or dialog.removals():
            problems.append("刚打开时不该有改动")
        if dialog.list.state_of("只甲标签") != Qt.CheckState.PartiallyChecked:
            problems.append(f"只有一项带的标签应是横杠：{dialog.list.state_of('只甲标签').name}")
        dialog.list.set_state("只甲标签", Qt.CheckState.Checked)
        if dialog.additions() != ["只甲标签"]:
            problems.append(f"横杠点成全选后应计入新增：{dialog.additions()}")
        dialog.list.set_state("共同标签", Qt.CheckState.Unchecked)
        if dialog.removals() != ["共同标签"]:
            problems.append(f"全选点成空后应计入移除：{dialog.removals()}")
        dialog.list.set_state("共同标签", Qt.CheckState.Checked)
        if dialog.removals():
            problems.append(f"改回全选后不该再移除：{dialog.removals()}")

        dialog.input.setText("没有的标签")
        dialog._on_add()
        if dialog.list.state_of("没有的标签") != Qt.CheckState.Checked:
            problems.append("已存在但没选中的条目应改成全选")
        if "没有的标签" not in dialog.additions():
            problems.append(f"全选后的已有条目应计入新增：{dialog.additions()}")
        dialog.input.setText("没有的标签")
        dialog._on_add()
        if "无需" not in dialog._hint.text():
            problems.append(f"已全选的条目再次添加应被拒绝：{dialog._hint.text()!r}")
        dialog.input.setText("全新标签")
        dialog._on_add()
        if "全新标签" not in dialog.additions():
            problems.append(f"新建的标签应计入新增：{dialog.additions()}")
        if dialog.kind != "标签":
            problems.append(f"标签弹窗的 kind 是 {dialog.kind!r}")

        class _FakeTagDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def additions(self):
                return ["新标签"]

            def removals(self):
                return ["只甲标签"]

        class _FakeKeywordDialog:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def additions(self):
                return ["新词"]

            def removals(self):
                return ["只乙"]

        original_tag = manage_module.TagManagerDialog
        original_keyword = manage_module.KeywordManagerDialog
        manage_module.TagManagerDialog = _FakeTagDialog
        manage_module.KeywordManagerDialog = _FakeKeywordDialog
        try:
            page._on_manage_tags()
            page._on_manage_keywords()
        finally:
            manage_module.TagManagerDialog = original_tag
            manage_module.KeywordManagerDialog = original_keyword
        case.session.expire_all()
        names = set(first.tag_names) | set(second.tag_names)
        if names != {"共同标签", "只乙标签", "新标签"}:
            problems.append(f"标签批量应用后是 {sorted(names)}")
        words = set(first.keywords or []) | set(second.keywords or [])
        if words != {"共同词", "只甲", "新词"}:
            problems.append(f"关键词批量应用后是 {sorted(words)}")

        assert not problems, "标签/关键词管理：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("manage_refresh_sees_plugin_writes", "pages")
def manage_refresh_sees_plugin_writes(case: Case) -> None:
    """插件用自己的会话写库后，管理页刷新必须能看到新标签。

    插件走 `app.sdk.items`（每次调用自建会话并提交），宿主页面持有的是另一个会话：
    不 `expire_all()` 的话，identity map 里的关系集合还是旧值，`refresh()` 重新查库也读不到，
    用户就会看到「提示成功但列表里没有标签」。
    """
    from app.core.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.extensions import extension_registry
    from app.core.signals import signalBus
    from app.sdk import items as items_sdk
    from app.services.item_api import ItemsApi
    from app.services.plugin_service import plugin_service

    previous_ui = extension_registry.provider(APP_UI_EXTENSION)
    previous_items = extension_registry.provider(items_sdk.ITEMS_EXTENSION)
    api = AppUiApi()
    plugin_service.bootstrap(APP_UI_EXTENSION, api)
    extension_registry.provide(items_sdk.ITEMS_EXTENSION, ItemsApi(), "selfcheck-items")
    window = None
    problems: list[str] = []
    try:
        fixture, window = build_window(case)
        page = window.manage_page
        target = ImportService(case.session).import_text(
            "插件写入可见性", "正文", category_id=fixture.category_child
        )
        case.session.commit()
        page.refresh()
        ensure_app().processEvents()

        item_id = int(target.id)
        written = items_sdk.tag_items([item_id], ["自检标签"])
        if written != 1:
            problems.append(f"插件写标签应改动 1 条，实际 {written}")
        signalBus.itemsChanged.emit()  # 插件的 notify_changed() 发的就是这个信号
        ensure_app().processEvents()

        row = next((item for item in page._items if int(item.id) == item_id), None)
        if row is None:
            problems.append("刷新后列表里找不到刚写入标签的那条数据")
        else:
            tags = {str(tag.name) for tag in row.tags}
            if "自检标签" not in tags:
                problems.append(f"管理页刷新后仍看不到插件写的标签，实际 {sorted(tags)}")

        assert not problems, "插件写入后的界面刷新：" + "；".join(problems[:8])
    finally:
        if previous_items is not None:
            extension_registry.provide(items_sdk.ITEMS_EXTENSION, previous_items, "selfcheck-restore")
        plugin_service.bootstrap(APP_UI_EXTENSION, previous_ui if previous_ui is not None else api)
        if window is not None:
            dispose_window(window)
