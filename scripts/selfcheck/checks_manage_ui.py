"""L2 页面检查：数据管理页（分类筛选、多选批量）与导入页（范围、批量）。"""

from __future__ import annotations

import time
from pathlib import Path

from .harness import Case, build_window, check, dispose_window, ensure_app

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget
from qfluentwidgets import StrongBodyLabel

import app.ui.pages.manage_page as manage_module
from app.core import paths
from app.core.config import resources_root
from app.repositories import CategoryRepository, ItemFilter, TagRepository
from app.services import (
    ImportService,
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
            problems.append(f"打开方式菜单不是「系统默认程序…交给系统选择」：{[key for key, *_ in entries]}")
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
