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
from app.core.runtime import paths
from app.core.config import config, resources_root
from app.core.runtime.naming import RENAME_MODE_KEYS, RenameRule, build_plan
from app.repositories import CategoryRepository, ItemFilter, TagRepository
from app.services import (
    ImportService,
    ItemService,
    LibraryService,
    TaxonomyService,
    UserService,
    is_uncategorized,
)
from app.ui.components.category_tree import (
    FIXED_SUFFIX,
    ITEM_ROLE,
    KIND_ALL,
    KIND_FILE,
    KIND_ROLE,
    category_label,
    menu_entries,
)
from app.ui.components.item_card import (
    LIST_COLUMNS,
    VIEW_SIZE_KEYS,
    ItemListRow,
    view_size_key,
    view_size_preset,
)
from app.ui.framework import tri_state


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
    """分类树：复选框只管中间列表显示哪些数据，批量操作改在弹窗里现挑分类。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        session = case.session
        user_id = page.user_service.current_id()
        taxonomy = TaxonomyService(session)

        # 造一棵三层分类 root > sub > leaf，另给 root 添一个无关子分类 sibling
        sibling = taxonomy.create_category("Java", parent_id=fixture.category_root)
        sub = taxonomy.create_category("框架", parent_id=fixture.category_root)
        leaf = taxonomy.create_category(
            "前端", parent_id=sub.id if sub is not None else fixture.category_root
        )
        session.commit()
        page.refresh()
        sibling_id = sibling.id if sibling is not None else None
        sub_id = sub.id if sub is not None else None
        leaf_id = leaf.id if leaf is not None else None
        child_id, root_id = fixture.category_child, fixture.category_root
        assert sub_id is not None and leaf_id is not None, "没能造出三层分类，检查无法继续"

        panel = page.filter_panel
        if hasattr(panel, "category_section"):
            problems.append("筛选栏仍带分类分组 category_section")
        if len(panel.sections()) != 3:
            problems.append(f"筛选栏分组数 {len(panel.sections())} != 3（类型/标签/关键词）")
        if not hasattr(page, "_checked_categories"):
            problems.append("管理页缺少 _checked_categories 状态")
        if not (
            page.category_move_button.isEnabled()
            and page.category_delete_button.isEnabled()
            and page.category_rename_button.isEnabled()
        ):
            problems.append("批量重命名 / 移动 / 删除按钮应当始终可用（挑分类改在弹窗里）")
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

        def select(category_ids) -> None:
            """模拟点分类名 / Ctrl 多选：设置的是「选中状态」，与复选框无关。"""
            page.tree.clearSelection()
            items = []
            for category_id in category_ids:
                item = _tree_item(page, category_id)
                assert item is not None, f"分类树缺少节点 {category_id}"
                items.append(item)
            for item in items:
                page.tree.setCurrentItem(item)
                item.setSelected(True)
            app.processEvents()

        def reset_selection() -> None:
            """把选中状态清空（只留「全部数据」根节点为 current）。"""
            select([None])

        def expected_total(checked: set[int]) -> int:
            """与 _load_items 同口径：勾选集为空时退回当前选中分类，并含全部子孙分类。"""
            ids = set(checked)
            if not ids and page._category_id is not None:
                ids = {page._category_id}
            if ids:
                ids |= page._descendant_category_ids(ids)
            return page.item_repo.count(
                ItemFilter(
                    category_ids=ids,
                    item_ids=set(page._checked_items),
                    user_ids={user_id},
                )
            )

        def visible_ok(checked: set[int]) -> list[int]:
            return [item.category_id for item in page._items if item.category_id not in checked]

        # 复选框只影响中间列表显示哪些数据：不改选中状态，也与批量操作互不相干
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
        if page._selected:
            problems.append(f"勾选复选框后中间列表被改动了选中项：{sorted(page._selected)}")

        # 继续勾选：root 的子分类全部勾上后父分类变为全选
        set_checked(sibling_id)
        set_checked(sub_id)  # 勾父会级联勾中 leaf
        checked = set(page._checked_categories)
        if not {root_id, child_id, sibling_id, sub_id, leaf_id} <= checked:
            problems.append(f"勾选 root 下全部子分类后 _checked_categories={sorted(checked)}")
        if _tree_item(page, root_id).checkState(0) != Qt.CheckState.Checked:
            problems.append(
                f"子分类全选后父分类状态为 {_tree_item(page, root_id).checkState(0).name}"
            )

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

        # 批量操作不看选中状态、也不看复选框：挑分类一律在弹窗里现挑（用户 m01929、m01932）
        if hasattr(page, "strict_select_box"):
            problems.append("「严格选择」开关应当已经移除")
        if hasattr(page, "_sync_category_buttons") or hasattr(page, "_selected_category_ids"):
            problems.append("页面上仍留着「按选中状态更新批量按钮」的旧方法")

        # 传给谁就只处理谁：点中下级不会再把「全选状态」的上级牵连进来
        reset_selection()
        select([leaf_id])
        if page._eligible_category_ids([leaf_id]) != [leaf_id]:
            problems.append(
                f"只挑下级时可移动 / 删除的分类={page._eligible_category_ids([leaf_id])}，应只有 leaf"
            )
        if page._renameable_category_ids([leaf_id]) != [leaf_id]:
            problems.append("只挑下级时可重命名的分类被牵连了上级")
        if page._eligible_category_ids([sub_id, leaf_id]) != [sub_id, leaf_id]:
            problems.append("父子一起挑时两个都该保留（批量删除 / 重命名允许父子同选）")
        if page._eligible_category_ids([root_id]):
            problems.append("根分类不该算作可移动 / 删除对象")
        if page._renameable_category_ids([root_id]) != [root_id]:
            problems.append("根分类应当可以改名")

        # 「未分类」是固定分类：连重命名也不允许
        uncategorized = taxonomy.uncategorized_category(user_id=user_id)
        if uncategorized is None or not is_uncategorized(uncategorized):
            problems.append("默认用户没有固定的「未分类」分类")
            raise AssertionError("；".join(problems))
        if page._eligible_category_ids([uncategorized.id]):
            problems.append("「未分类」不能移动 / 删除")
        if page._renameable_category_ids([uncategorized.id]):
            problems.append("「未分类」不能重命名")
        reset_selection()

        # 批量删除 / 重命名：分类从弹窗里现挑，只有挑中的那些被处理
        def stub_batch(confirm_result: bool, picked: list[int]) -> list[tuple[str, int]]:
            """桩掉挑分类的弹窗、二次确认、重命名弹窗与分类增删改，记录批量操作作用到的分类。"""
            calls: list[tuple[str, int]] = []
            saved_confirm = manage_module.confirm
            saved_select_dialog = manage_module.CategorySelectDialog
            saved_rename_dialog = manage_module.BatchRenameDialog
            saved_delete = page.taxonomy.delete_category
            saved_rename = page.taxonomy.rename_category
            manage_module.confirm = lambda *args, **kwargs: confirm_result

            class _FakeSelectDialog:
                """假的选择弹窗：直接给出「用户挑中的分类」。"""

                def __init__(self, nodes, parent=None, **kwargs):
                    self._picked = list(picked)

                def exec(self):
                    return True

                def category_ids(self):
                    return list(self._picked)

            class _FakeRenameDialog:
                def __init__(self, entries, parent=None, reserved=None):
                    self._entries = list(entries)

                def exec(self):
                    # 重命名的「确认」就是弹窗自身的确定键，跟着同一个开关走
                    return confirm_result

                def renames(self):
                    return [(category_id, f"{name}-改名") for category_id, name in self._entries]

            manage_module.CategorySelectDialog = _FakeSelectDialog
            manage_module.BatchRenameDialog = _FakeRenameDialog
            page.taxonomy.delete_category = (
                lambda category, *a, **k: calls.append(("delete", category.id)) or True
            )
            page.taxonomy.rename_category = (
                lambda category, name, *a, **k: calls.append(("rename", category.id)) or True
            )
            try:
                page._on_category_batch_delete()
                page._on_category_batch_rename()
            finally:
                manage_module.confirm = saved_confirm
                manage_module.CategorySelectDialog = saved_select_dialog
                manage_module.BatchRenameDialog = saved_rename_dialog
                page.taxonomy.delete_category = saved_delete
                page.taxonomy.rename_category = saved_rename
            return calls

        refused = stub_batch(confirm_result=False, picked=[leaf_id])
        if refused:
            problems.append(f"二次确认被拒绝后仍执行了批量操作：{refused}")

        calls = stub_batch(confirm_result=True, picked=[leaf_id])
        deleted = {category_id for action, category_id in calls if action == "delete"}
        renamed = {category_id for action, category_id in calls if action == "rename"}
        if deleted != {leaf_id}:
            problems.append(f"只挑了下级时批量删除作用到的分类={sorted(deleted)}，应只有 leaf")
        if renamed != {leaf_id}:
            problems.append(f"只挑了下级时批量重命名作用到的分类={sorted(renamed)}，应只有 leaf")

        calls = stub_batch(confirm_result=True, picked=[sub_id, leaf_id])
        deleted = {category_id for action, category_id in calls if action == "delete"}
        if deleted != {sub_id, leaf_id}:
            problems.append(f"父子一起挑时批量删除作用到的分类={sorted(deleted)}")
        calls = stub_batch(confirm_result=True, picked=[])
        if calls:
            problems.append(f"弹窗里什么都没挑时批量操作仍然动手：{calls}")
        reset_selection()

        # 勾选「全部数据」：中间列表显示全量数据即可；复选框与批量操作互不相干（用户 m01932）
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
        blocked = stub_batch(confirm_result=True, picked=[])
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
        if menu_entries(fixed=True):
            problems.append(f"固定分类仍有右键菜单：{menu_entries(fixed=True)}")
        if not menu_entries(fixed=False):
            problems.append("普通分类没有右键菜单")
        if ("move", "移动到另一个分类下") not in menu_entries(fixed=False):
            problems.append(f"普通分类菜单缺少「移动到另一个分类下」：{menu_entries(fixed=False)}")
        if True:
            nodes = taxonomy.tree(user_id=user_id)
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


@check("manage_category_move", "pages")
def manage_category_move(case: Case) -> None:
    """分类移动：右键单个移动的两步流程、批量移动表在加入时就拒绝上下级关系。"""
    from app.ui.dialogs import CategoryMoveDialog

    fixture, window = build_window(case)
    problems: list[str] = []
    dialog = None
    try:
        app = ensure_app()
        page = window.manage_page
        session = case.session
        user_id = page.user_service.current_id()
        taxonomy = TaxonomyService(session)

        sub = taxonomy.create_category("框架", parent_id=fixture.category_root, user_id=user_id)
        leaf = taxonomy.create_category(
            "前端",
            parent_id=sub.id if sub is not None else fixture.category_root,
            user_id=user_id,
        )
        # 顶层分类必须带归属，否则 tree(user_id=...) 不会把它列出来
        other = taxonomy.create_category("数据库", user_id=user_id)
        session.commit()
        page.refresh()
        sub_id = sub.id if sub is not None else None
        leaf_id = leaf.id if leaf is not None else None
        other_id = other.id if other is not None else None
        root_id = fixture.category_root
        assert sub_id is not None and leaf_id is not None and other_id is not None

        nodes = taxonomy.tree(user_id=user_id)
        dialog = CategoryMoveDialog(nodes, parent=window)

        # 弹窗里的分类树必须真的显示分类：早先是先建好「全部数据」根行再 setHidden(True)，
        # 而 Qt 隐藏父项会连整棵子树一起藏掉，弹窗因此整片空白（用户 m01073 第 1 条）
        visible_ids = {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in dialog.tree._iter_items()
            if not item.isHidden()
        }
        if not {root_id, sub_id, leaf_id, other_id} <= visible_ids:
            problems.append(
                "移动弹窗的分类树没显示全部分类：可见="
                f"{sorted(value for value in visible_ids if isinstance(value, int))}"
            )
        if dialog.tree.topLevelItem(0).data(0, KIND_ROLE) == KIND_ALL:
            problems.append("移动弹窗的分类树不该再有「全部数据」根行")
        if dialog.tree.currentItem() is not None:
            problems.append("移动弹窗刚打开时不该预选任何分类")

        # 加入表：与表内分类互为祖先 / 后代都要拒绝，无关分类才能共存
        dialog._add(sub_id)
        if dialog.category_ids() != [sub_id]:
            problems.append(f"加入分类后移动表={dialog.category_ids()}")
        dialog._add(leaf_id)
        if dialog.category_ids() != [sub_id]:
            problems.append(f"加入表内分类的子级后移动表={dialog.category_ids()}（应当拒绝）")
        dialog._add(root_id)
        if dialog.category_ids() != [sub_id]:
            problems.append(f"加入表内分类的父级后移动表={dialog.category_ids()}（应当拒绝）")
        dialog._add(other_id)
        if set(dialog.category_ids()) != {sub_id, other_id}:
            problems.append(f"加入无关分类后移动表={dialog.category_ids()}")
        dialog._add(sub_id)
        if set(dialog.category_ids()) != {sub_id, other_id}:
            problems.append("重复加入同一个分类没有被忽略")

        # 移出：点在列表上再移出
        dialog.move_list.setCurrentRow(0)
        dialog._remove_selected()
        if len(dialog.category_ids()) != 1:
            problems.append(f"移出选中项后移动表={dialog.category_ids()}")

        # 移出全部：一次清空整张移动表（用户 m02499 第 1 条）
        if not hasattr(dialog, "clear_button"):
            problems.append("弹窗右侧缺少「移出全部」按钮")
        else:
            dialog._add(sub_id)
            dialog._add(other_id)
            dialog.clear_button.click()
            if dialog.category_ids():
                problems.append(f"点「移出全部」后表应为空：{dialog.category_ids()}")
            if dialog.yesButton.isEnabled():
                problems.append("表空时确认键应禁用")
            if dialog.clear_button.isEnabled():
                problems.append("表空时「移出全部」应禁用")
            if "已移出全部 2 项" not in dialog.message.text():
                problems.append(f"点「移出全部」没有给出提示：{dialog.message.text()}")

        # 页面级两步流程：移动表 → 目标弹窗 → 确认 → 真正移动
        picked = [leaf_id]
        target = other_id
        seen_candidates: list[list[int]] = []
        recorded: list[tuple[int, int | None]] = []
        saved_move_dialog = manage_module.CategoryMoveDialog
        saved_picker_dialog = manage_module.CategoryPickerDialog
        saved_confirm = manage_module.confirm
        saved_move = page.taxonomy.move_category

        class _FakeMoveDialog:
            def __init__(self, nodes, parent=None, **kwargs):
                self._nodes = nodes

            def exec(self):
                return True

            def category_ids(self):
                return list(picked)

        class _FakePickerDialog:
            def __init__(self, candidates, parent=None, **kwargs):
                seen_candidates.append([node.category.id for node in candidates])

            def exec(self):
                return True

            def category_id(self):
                return target

        manage_module.CategoryMoveDialog = _FakeMoveDialog
        manage_module.CategoryPickerDialog = _FakePickerDialog
        manage_module.confirm = lambda *args, **kwargs: True
        page.taxonomy.move_category = (
            lambda category, parent_id, *a, **k: recorded.append((category.id, parent_id)) or True
        )
        try:
            # 先把 sub 选中，验证「被移动分类自己与它的子孙」会从目标候选里剔掉
            picked = [sub_id]
            page.tree.clearSelection()
            item = _tree_item(page, sub_id)
            page.tree.setCurrentItem(item)
            item.setSelected(True)
            app.processEvents()
            page._on_category_batch_move()
            if not seen_candidates:
                problems.append("批量移动没有打开目标选择弹窗")
            else:
                forbidden = {sub_id, leaf_id}
                if forbidden & set(seen_candidates[-1]):
                    problems.append(
                        f"目标候选中出现了被移动的分类或其子孙：{sorted(forbidden & set(seen_candidates[-1]))}"
                    )
                if other_id not in seen_candidates[-1]:
                    problems.append(
                        f"目标候选中缺少可用的无关分类：候选={sorted(seen_candidates[-1])}"
                    )
            if recorded != [(sub_id, other_id)]:
                problems.append(f"批量移动实际移动={recorded}，应为 [({sub_id}, {other_id})]")

            # 右键单个移动：只动点中的那一个分类
            recorded.clear()
            picked = [other_id]
            target = root_id
            page._move_single_category(other_id)
            if recorded != [(other_id, root_id)]:
                problems.append(f"右键移动实际移动={recorded}，应为 [({other_id}, {root_id})]")
        finally:
            manage_module.CategoryMoveDialog = saved_move_dialog
            manage_module.CategoryPickerDialog = saved_picker_dialog
            manage_module.confirm = saved_confirm
            page.taxonomy.move_category = saved_move

        assert not problems, "分类移动：" + "；".join(problems[:12])
    finally:
        if dialog is not None:
            _drop_widget(dialog)
        dispose_window(window)


@check("manage_category_delete", "pages")
def manage_category_delete(case: Case) -> None:
    """右键删除分类：有下级分类时先问「保留 / 一起删除」，选一起删除就走递归删除。"""
    from app.ui.dialogs import CategoryDeleteDialog

    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        ensure_app()
        page = window.manage_page
        session = case.session
        user_id = page.user_service.current_id()
        taxonomy = TaxonomyService(session)

        sub = taxonomy.create_category("待删分类", parent_id=fixture.category_root, user_id=user_id)
        assert sub is not None
        leaf = taxonomy.create_category("待删子级", parent_id=sub.id, user_id=user_id)
        assert leaf is not None
        solo = taxonomy.create_category("没有下级的分类", user_id=user_id)
        assert solo is not None
        session.commit()
        page.refresh()

        # 弹窗本身：默认「保留下级分类」，只有勾右边才是递归删除
        real = CategoryDeleteDialog("演示分类", 2, parent=window)
        try:
            if real.recursive():
                problems.append("删除分类弹窗默认应是「保留下级分类」（非递归）")
            real.remove_radio.setChecked(True)
            if not real.recursive():
                problems.append("勾上「下级分类一起删除」后 recursive() 应变 True")
        finally:
            _drop_widget(real)

        asked: list[tuple[str, int]] = []
        confirmed: list[tuple] = []
        deleted: list[tuple[int, bool, dict]] = []
        mode = {"recursive": False}

        class _FakeDeleteDialog:
            def __init__(self, name, child_count, parent=None):
                asked.append((name, child_count))

            def exec(self):
                return True

            def recursive(self):
                return mode["recursive"]

        saved_dialog = manage_module.CategoryDeleteDialog
        saved_confirm = manage_module.confirm
        saved_delete = page.taxonomy.delete_category
        manage_module.CategoryDeleteDialog = _FakeDeleteDialog
        manage_module.confirm = lambda *args, **kwargs: confirmed.append(args) or True
        page.taxonomy.delete_category = (
            lambda category, *a, **k: deleted.append(
                (category.id, bool(k.get("recursive")), dict(k))
            )
            or 3
        )
        try:
            deleted.clear()
            asked.clear()
            page._on_tree_action("delete", sub.id)
            if asked != [("待删分类", 1)]:
                problems.append(f"有 1 个下级分类时应弹二选一弹窗并带上数量，实际 {asked}")
            if [row[0] for row in deleted] != [sub.id] or deleted[0][1]:
                problems.append(f"选「保留下级分类」时应非递归删除，实际 {deleted}")

            deleted.clear()
            mode["recursive"] = True
            page._on_tree_action("delete", sub.id)
            if len(deleted) != 1 or not deleted[0][1]:
                problems.append(f"选「一起删除」时应递归删除，实际 {deleted}")
            if not deleted[0][2].get("recursive"):
                problems.append(f"递归删除应以关键字 recursive=True 传给服务，实际 {deleted[0][2]}")

            # 没有下级分类时不弹二选一，走原来的简易确认
            deleted.clear()
            asked.clear()
            confirmed.clear()
            page._on_tree_action("delete", solo.id)
            if asked:
                problems.append(f"没有下级分类时不该弹二选一弹窗，实际 {asked}")
            if len(confirmed) != 1:
                problems.append(f"没有下级分类时应走一次简易确认，实际确认 {len(confirmed)} 次")
            if [row[0] for row in deleted] != [solo.id]:
                problems.append(f"没有下级分类时也应删除该分类，实际 {deleted}")
        finally:
            manage_module.CategoryDeleteDialog = saved_dialog
            manage_module.confirm = saved_confirm
            page.taxonomy.delete_category = saved_delete

        assert not problems, "删除分类：" + "；".join(problems[:12])
    finally:
        dispose_window(window)


@check("manage_category_files", "pages")
def manage_category_files(case: Case) -> None:
    """「仅显示分类」：关掉后分类栏列出文件、未分类的文件挂到「全部数据」下、勾选驱动显示范围。"""
    from app.core.config import config

    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        app = ensure_app()
        page = window.manage_page
        user_id = page.user_service.current_id()
        taxonomy = TaxonomyService(case.session)
        uncategorized = taxonomy.uncategorized_category(user_id=user_id)
        box = page.only_categories_box

        def root_row():
            # 每次重建分类栏后旧的 QTreeWidgetItem 已被销毁，必须重新取
            return page.tree.topLevelItem(0)

        def file_rows(item_id: int | None = None) -> list:
            rows = [
                item for item in page.tree._iter_items() if item.data(0, KIND_ROLE) == KIND_FILE
            ]
            if item_id is None:
                return rows
            return [row for row in rows if row.data(0, ITEM_ROLE) == item_id]

        def clear_checks() -> None:
            for item in page.tree._iter_items():
                item.setCheckState(0, Qt.CheckState.Unchecked)
            app.processEvents()

        if not box.isChecked() or not config.onlyShowCategories.value:
            problems.append("「仅显示分类」默认没有勾选")
        if file_rows():
            problems.append(f"默认勾选时分类栏仍有文件行：{[row.text(0) for row in file_rows()]}")

        box.setChecked(False)
        app.processEvents()
        if config.onlyShowCategories.value:
            problems.append("取消「仅显示分类」后没有写进配置")
        if not file_rows():
            problems.append("取消勾选后分类栏没有列出任何文件")
        if uncategorized is not None and _tree_item(page, uncategorized.id) is not None:
            problems.append("取消勾选后「未分类」仍作为分类节点显示")
        if fixture.file_item is not None:
            under_root = [
                row for row in file_rows(fixture.file_item) if row.parent() is root_row()
            ]
            if not under_root:
                problems.append("没有分类的文件没有列在「全部数据」下面")
        child_item = _tree_item(page, fixture.category_child)
        own_rows = file_rows(fixture.text_item)
        if child_item is None:
            problems.append("分类树里找不到子分类节点")
        elif not own_rows or own_rows[0].parent() is not child_item:
            problems.append("分类里的文件没有列在它自己的分类下面")
        for row in file_rows():
            if row.childCount():
                problems.append(f"文件行 {row.text(0)} 下面还有子节点")
            if not row.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                problems.append(f"文件行 {row.text(0)} 没有复选框")

        # 勾选一个文件：中间列表显示它，但不改动中间列表的选中项
        if own_rows:
            own_rows[0].setCheckState(0, Qt.CheckState.Checked)
            app.processEvents()
            if page._checked_items != {fixture.text_item}:
                problems.append(f"勾选文件后 _checked_items={sorted(page._checked_items)}")
            if page._selected:
                problems.append(f"勾选文件后中间列表被改动了选中项：{sorted(page._selected)}")
            if not any(item.id == fixture.text_item for item in page._items):
                problems.append("勾选文件后中间列表没有显示它")

        # 部分勾选时父节点半选：文件与它同级的分类一起参与三态
        if fixture.file_item is not None:
            stray = file_rows(fixture.file_item)
            if stray:
                stray[0].setCheckState(0, Qt.CheckState.Checked)
                app.processEvents()
                if root_row().checkState(0) != Qt.CheckState.PartiallyChecked:
                    problems.append(
                        f"只勾选部分文件时根节点状态为 {root_row().checkState(0).name}"
                    )
        clear_checks()

        # 勾选分类：它下面的文件一起被勾上，中间列表跟着显示这些数据
        if child_item is not None:
            child_item.setCheckState(0, Qt.CheckState.Checked)
            app.processEvents()
            if fixture.category_child not in page._checked_categories:
                problems.append("勾选分类后勾选集合里没有它")
            if not all(
                row.checkState(0) == Qt.CheckState.Checked
                for row in file_rows(fixture.text_item)
            ):
                problems.append("勾选分类后它下面的文件没有一起勾上")
            clear_checks()

        # 勾选父分类：子孙分类里的数据一起显示
        parent_item = _tree_item(page, fixture.category_root)
        if parent_item is not None:
            parent_item.setCheckState(0, Qt.CheckState.Checked)
            app.processEvents()
            expect = page.item_repo.count(
                ItemFilter(
                    category_ids={fixture.category_root, fixture.category_child},
                    user_ids={user_id},
                )
            )
            if page._total != expect:
                problems.append(f"勾选父分类后计数 {page._total} != 含子分类的 {expect}")
            clear_checks()

        # 点分类栏里的文件行：中间列表跳到并选中它
        rows_now = file_rows(fixture.text_item)
        if rows_now:
            page.tree.setCurrentItem(rows_now[0])
            app.processEvents()
            if page._selected != {fixture.text_item}:
                problems.append(f"点文件行后中间列表选中 {sorted(page._selected)}")

        box.setChecked(True)
        app.processEvents()
        if file_rows():
            problems.append("重新勾选「仅显示分类」后分类栏仍有文件行")
        if not config.onlyShowCategories.value:
            problems.append("重新勾选「仅显示分类」后没有写回配置")
        if uncategorized is not None and _tree_item(page, uncategorized.id) is None:
            problems.append("重新勾选「仅显示分类」后「未分类」节点没有回来")

        assert not problems, "分类栏文件列表：" + "；".join(problems[:12])
    finally:
        config.set(config.onlyShowCategories, True)
        dispose_window(window)


@check("manage_open_export_dir", "pages")
def manage_open_export_dir(case: Case) -> None:
    """标题区的「打开导出文件夹」：按当前导出目录调 open_path，不能炸在 config 上。"""
    from app.core.config import export_dir

    _, window = build_window(case)
    page = window.manage_page
    opened: list[str] = []
    original = manage_module.open_folder
    manage_module.open_folder = lambda path: (opened.append(str(path)), True)[1]
    try:
        assert page.export_dir_button.isEnabled(), "「打开导出文件夹」默认应当可用"
        page.export_dir_button.click()
    finally:
        manage_module.open_folder = original
        dispose_window(window)

    expected = str(export_dir())
    assert opened, "点「打开导出文件夹」没有调用 open_path"
    assert opened[0] == expected, f"打开的应当是 {expected}，实际 {opened[0]}"


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

        # 选择封面（用户 m02499 第 2 条）：挑一张图，整批数据都用它当封面
        from PIL import Image

        cover_source = Path(case.root) / "封面.png"
        Image.new("RGB", (32, 32), (200, 60, 60)).save(cover_source)
        if "第一帧" not in page.cover_hint.text():
            problems.append(f"导入页没说明默认封面规则：{page.cover_hint.text()}")
        page._set_cover_source(str(cover_source))
        app.processEvents()
        if page._cover_source != str(cover_source):
            problems.append(f"导入页没有记下挑的封面：{page._cover_source!r}")
        if cover_source.name not in page.cover_name_label.text():
            problems.append(f"导入页没有显示封面文件名：{page.cover_name_label.text()}")
        if not page.cover_clear_button.isEnabled():
            problems.append("挑了封面后「用默认封面」按钮应可用")
        page._clear_cover()
        app.processEvents()
        if page._cover_source:
            problems.append(f"点「用默认封面」后封面源该清空：{page._cover_source!r}")
        if cover_source.name in page.cover_name_label.text():
            problems.append(f"清空封面后不该还显示文件名：{page.cover_name_label.text()}")
        page._set_cover_source(str(cover_source))
        app.processEvents()

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
            # 这一批数据都该带上挑的那张封面（自定义封面名是「校验和-图片摘要.png」）
            from app.repositories import ItemRepository

            custom = {
                str(row.cover_path)
                for row in ItemRepository(session).query(ItemFilter())
                if row.cover_path and "-" in Path(str(row.cover_path)).name
            }
            if len(custom) < 3:
                problems.append(f"批量导入没给 3 条数据带上自定义封面：{sorted(custom)}")

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
        if not any("封面" in text for text in texts):
            problems.append("编辑弹窗没有渲染「封面」")
        if dialog.values()["name"] != item.name:
            problems.append(f"编辑弹窗的名称初值 {dialog.values()['name']!r} != {item.name!r}")

        # 封面行（用户 m02499 第 2 条）：没动封面时 values() 给 None，挑图 / 恢复默认才给值
        from PIL import Image

        from app.core.config import cover_dir
        from app.db.models import DataItem
        from app.services import cover_service

        cover_source = Path(case.root) / "编辑用封面.png"
        Image.new("RGB", (24, 24), (30, 200, 120)).save(cover_source)
        if dialog.values()["cover"] is not None:
            problems.append(f"没动封面时 values()['cover'] 应为 None：{dialog.values()['cover']!r}")
        dialog._set_cover_choice(str(cover_source))
        if dialog.values()["cover"] != str(cover_source):
            problems.append(f"挑了封面后 values()['cover'] 不对：{dialog.values()['cover']!r}")
        dialog._use_default_cover()
        if dialog.values()["cover"] != "":
            problems.append(f"点「恢复默认封面」后 values()['cover'] 该是空串：{dialog.values()['cover']!r}")

        # 页面入口：挑了封面就走 cover_service 落盘（用页面自己的会话取对象）
        page.session.expire_all()
        target = page.session.get(DataItem, item.id)
        if target is None:
            problems.append("页面会话里找不到刚导入的数据")

        class _FakeEditDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return True

            def values(self):
                return {
                    "name": target.name,
                    "category_id": target.category_id,
                    "tags": [],
                    "keywords": [],
                    "is_hidden": False,
                    "cover": str(cover_source),
                }

        if target is not None:
            saved_dialog_class = manage_module.ItemEditDialog
            saved_require = page._require_selection
            manage_module.ItemEditDialog = _FakeEditDialog
            page._require_selection = lambda: [target]
            try:
                page._on_edit()
            finally:
                manage_module.ItemEditDialog = saved_dialog_class
                page._require_selection = saved_require
            page.session.expire_all()
            session.expire_all()
            chosen = str(target.cover_path or "")
            if not chosen or Path(chosen).parent != Path(cover_dir()):
                problems.append(f"编辑弹窗挑了封面却没落盘：{target.cover_path!r}")
            elif not Path(chosen).is_file():
                problems.append(f"落盘的封面文件不存在：{chosen}")

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


@check("manage_view_size", "pages")
def manage_view_size(case: Case) -> None:
    """显示大小档位、列表表头与横向滑动、卡片按内容长高（用户 m04164）。"""
    fixture, window = build_window(case, show=True)
    original = config.viewSize.value
    problems: list[str] = []
    try:
        app = ensure_app()
        session = case.session
        page = window.manage_page
        # 必须真的切到这一页：布局没跑过时量到的几何尺寸全是控件的默认值（用户看不到的重叠也会被误判）。
        window.resize(1280, 900)
        window.switchTo(page)
        app.processEvents()

        # 超长名称 + 满屏标签/关键词：列表要靠横向滑动看全，卡片里标签行必须换行。
        long_name = "超长名称的数据条目" * 6
        tags = [f"标签{index}号" for index in range(8)]
        keywords = [f"关键词{index}" for index in range(8)]
        ImportService(session).import_text(
            long_name,
            "正文",
            category_id=fixture.category_child,
            tags=tags,
            keywords=keywords,
        )
        ImportService(session).import_text(
            "短条目", "正文", category_id=fixture.category_child, tags=["单个标签"]
        )
        session.commit()
        page.refresh()
        app.processEvents()

        start = view_size_key(original)
        if page.size_box.count() != len(VIEW_SIZE_KEYS):
            problems.append(f"显示大小应有 {len(VIEW_SIZE_KEYS)} 档，实际 {page.size_box.count()}")
        if page.size_box.currentData() != start:
            problems.append(
                f"显示大小下拉的初值应与配置一致：{page.size_box.currentData()!r} != {start!r}"
            )

        # ---- 列表模式：表头 + 列宽一致 + 内容不省略 + 能左右滑动
        if page.list_header.isHidden():
            problems.append("列表视图里应当显示表头")
        rows = _list_rows(page)
        if len(rows) != len(page._items):
            problems.append(f"列表行数应与本页数据一致：{len(rows)} != {len(page._items)}")
        header_widths = {key: label.width() for key, label in page.list_header._labels.items()}
        header_texts = [label.text() for label in page.list_header._labels.values()]
        if header_texts != [title for _key, title, _minimum in LIST_COLUMNS]:
            problems.append(f"表头文字不对：{header_texts}")
        for row in rows:
            widths = {key: label.width() for key, label in row._labels.items()}
            if widths != header_widths:
                problems.append(f"列表行的列宽应与表头一致：{widths} != {header_widths}")
                break
        long_row = next((row for row in rows if row.item is not None and row.item.name == long_name), None)
        if long_row is None:
            problems.append("列表里找不到超长名称的那条数据")
        else:
            if long_row._labels["name"].text() != long_name:
                problems.append("列表把名称省略了，用户看不到完整内容")
            if long_row._labels["tags"].text().count("#") != len(tags):
                problems.append("列表没有把标签写全")
            if long_row._labels["keywords"].text().count("、") != len(keywords) - 1:
                problems.append("列表没有把关键词写全")
        scroll = page.list_view.horizontalScrollBar()
        if scroll.maximum() <= 0:
            problems.append("超长内容没有把列表撑宽，用户无法左右滑动查看")
        else:
            value = min(30, scroll.maximum())
            scroll.setValue(value)
            app.processEvents()
            if page.list_header._offset != value:
                problems.append(
                    f"横向滚动应当同步表头，偏移 {page.list_header._offset} != {value}"
                )

        # ---- 滚轮：默认上下滑，按住 Shift 左右滑（用户要求）
        # 只有两条数据，竖向本来滚不动；先把滚动区压矮逼出竖向范围，才验证得了「默认是上下滑」。
        saved_max_height = page.list_view.maximumHeight()
        page.list_view.setFixedHeight(24)
        app.processEvents()
        vbar = page.list_view.verticalScrollBar()
        vbar.setValue(vbar.minimum())
        scroll.setValue(scroll.minimum())
        app.processEvents()
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtWidgets import QApplication

        def wheel(angle_x: int, angle_y: int, modifiers) -> None:
            """向滚动区投一次滚轮事件：鼠标滚一格是 angleDelta 120。"""
            QApplication.sendEvent(
                page.list_view.viewport(),
                QWheelEvent(
                    QPointF(20.0, 20.0),
                    QPointF(20.0, 20.0),
                    QPoint(0, 0),
                    QPoint(angle_x, angle_y),
                    Qt.MouseButton.NoButton,
                    modifiers,
                    Qt.ScrollPhase.NoScrollPhase,
                    False,
                ),
            )
            app.processEvents()

        if vbar.maximum() <= vbar.minimum():
            problems.append("列表内容超出可视区时应当出现竖向滚动条")
        else:
            wheel(0, -120, Qt.KeyboardModifier.NoModifier)
            if vbar.value() <= vbar.minimum():
                problems.append("不按 Shift 的滚轮应当上下滑动列表")
        if scroll.maximum() > scroll.minimum():
            wheel(0, -120, Qt.KeyboardModifier.ShiftModifier)
            expected = min(scroll.minimum() + 120, scroll.maximum())
            if scroll.value() != expected:
                problems.append(f"Shift+滚轮 应当左右滑动：{scroll.value()} != {expected}")
            shifted = scroll.value()
            wheel(0, -120, Qt.KeyboardModifier.NoModifier)
            if scroll.value() != shifted:
                problems.append("不按 Shift 的滚轮不该改变横向位置")
            if vbar.maximum() > vbar.minimum() and vbar.value() <= vbar.minimum():
                problems.append("不按 Shift 的滚轮应当上下滑动列表")
        page.list_view.setMinimumHeight(0)
        page.list_view.setMaximumHeight(saved_max_height)
        app.processEvents()

        # ---- 卡片模式：表头隐藏、标签写全、卡片高度够、同屏卡片不重叠
        page._set_mode("card")
        app.processEvents()
        page._fit_cards()
        app.processEvents()
        if not page.list_header.isHidden():
            problems.append("卡片视图里不该显示列表表头")
        cards = [widget for widget in page._rows["card"] if getattr(widget, "item", None) is not None]
        if len(cards) != len(page._items):
            problems.append(f"卡片数应与本页数据一致：{len(cards)} != {len(page._items)}")
        long_card = next((card for card in cards if card.item.name == long_name), None)
        if long_card is None:
            problems.append("卡片视图里找不到超长名称的那条数据")
        else:
            chips = [chip for chip in long_card._tags._chips if not chip.isHidden()]
            if len(chips) != len(tags):
                problems.append(f"卡片只显示了 {len(chips)} 个标签，应当显示全部 {len(tags)} 个")
            if long_card._tags.height() <= chips[0].height():
                problems.append("卡片里的标签没有换行显示，超出部分会被裁掉")
            needed = long_card.layout().minimumSize().height()
            if long_card.height() < needed:
                problems.append(
                    f"卡片高度 {long_card.height()} 装不下内容 {needed}，标签/关键词显示不全"
                )
        for index, card in enumerate(cards):
            for other in cards[index + 1 :]:
                if card.geometry().intersects(other.geometry()):
                    problems.append("卡片互相重叠，说明高度没有随内容自适应")
                    break
            else:
                continue
            break

        # ---- 换档：整批重建控件，封面大小跟着变
        other = next(key for key in VIEW_SIZE_KEYS if key != start)
        page.size_box.setCurrentIndex(VIEW_SIZE_KEYS.index(other))
        app.processEvents()
        preset = view_size_preset(other)
        if config.viewSize.value != other:
            problems.append(f"换档没有写进配置：{config.viewSize.value!r} != {other!r}")
        if page.card_layout.widgetMinimumWidth() != preset.card_min_width:
            problems.append("换档后卡片的最小宽度没有跟着变")
        card = next(
            (widget for widget in page._rows["card"] if getattr(widget, "item", None) is not None),
            None,
        )
        if card is None:
            problems.append("换档后卡片控件没有重建")
        elif card._cover._size != preset.card_cover:
            problems.append(f"换档后卡片封面大小没变：{card._cover._size} != {preset.card_cover}")
        if page.list_header._cover_cell.width() != preset.list_cover:
            problems.append("换档后表头的封面格没有跟着变")
        page._set_mode("list")
        app.processEvents()
        row = next(iter(_list_rows(page)), None)
        if row is None:
            problems.append("换档后列表行控件没有重建")
        elif row._cover._size != preset.list_cover:
            problems.append(f"换档后列表封面大小没变：{row._cover._size} != {preset.list_cover}")

        assert not problems, "显示大小与列表表头：" + "；".join(problems[:12])
    finally:
        config.set(config.viewSize, original)
        if ensure_app() is not None:
            ensure_app().processEvents()
        dispose_window(window)


@check("manage_editor_menu", "pages")
def manage_editor_menu(case: Case) -> None:
    """右键「编辑器」子菜单是程序本体内置的：有系统默认程序 / 各编辑器 / 交给系统选择，禁用编辑器库也还在。"""
    from app.services import editor_service as editors

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
    from app.services import editor_service as editors
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
    from app.core.plugins.app_ui import APP_UI_EXTENSION, AppUiApi
    from app.core.plugins.extensions import extension_registry
    from app.core.runtime.signals import signalBus
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


@check("category_sort", "pages")
def category_sort(case: Case) -> None:
    """分类栏排序：默认顺序保持不动，按名称 / 数据量 / 最新导入各自可正序逆序，「未分类」始终最后。"""
    import datetime as dt
    import types
    from unittest import mock

    from PyQt6 import sip

    from app.ui.components.category_tree import CategoryTree

    app = ensure_app()
    problems: list[str] = []
    moment_old = dt.datetime(2026, 1, 1, 8, 0, 0)
    moment_new = dt.datetime(2026, 9, 9, 18, 0, 0)

    def node(category_id: int, name: str, total: int, latest):
        category = types.SimpleNamespace(id=category_id, name=name, parent_id=None)
        return types.SimpleNamespace(
            category=category, depth=0, item_count=total, total_count=total, latest_at=latest
        )

    # 用假节点直接驱动排序：真分类会走磁盘，这里只验排序算法本身
    with mock.patch("app.ui.components.category_tree.is_uncategorized", return_value=False):
        tree = CategoryTree()
        tree.set_nodes(
            [
                node(1, "Beta", 5, moment_new),
                node(2, "alpha", 20, moment_old),
                node(3, "Gamma", 1, None),
            ],
            total=26,
        )
        tree.apply_sort()
        root = tree.topLevelItem(0)
        labels = lambda: [root.child(i).text(0) for i in range(root.childCount())]

        if tree.sort_mode() != "default":
            problems.append(f"默认排序模式应为 default，实际 {tree.sort_mode()}")
        if labels() != ["Beta (5)", "alpha (20)", "Gamma (1)"]:
            problems.append(f"默认顺序不该改动分类次序，实际 {labels()}")

        tree.set_sort("name", False)
        tree.apply_sort()
        if labels() != ["alpha (20)", "Beta (5)", "Gamma (1)"]:
            problems.append(f"按名称正序不对：{labels()}")

        tree.set_sort("name", True)
        tree.apply_sort()
        if labels() != ["Gamma (1)", "Beta (5)", "alpha (20)"]:
            problems.append(f"按名称逆序不对：{labels()}")

        tree.set_sort("count", False)
        tree.apply_sort()
        if labels() != ["Gamma (1)", "Beta (5)", "alpha (20)"]:
            problems.append(f"按数据量正序不对：{labels()}")

        tree.set_sort("count", True)
        tree.apply_sort()
        if labels() != ["alpha (20)", "Beta (5)", "Gamma (1)"]:
            problems.append(f"按数据量逆序不对：{labels()}")

        tree.set_sort("latest", False)
        tree.apply_sort()
        # 没有数据的分类算「最旧」，排在最前
        if labels() != ["Gamma (1)", "alpha (20)", "Beta (5)"]:
            problems.append(f"按最新导入正序不对：{labels()}")

        tree.set_sort("latest", True)
        tree.apply_sort()
        if labels() != ["Beta (5)", "alpha (20)", "Gamma (1)"]:
            problems.append(f"按最新导入逆序不对：{labels()}")

        # 选择类弹窗用 show_root=False：不建「全部数据」根行，顶级分类直接当 QTreeWidget
        # 顶层项。早先是先建根行再 setHidden(True)，而 Qt 隐藏父项会连整棵子树一起藏掉，
        # 弹窗就整片空白了（用户 m01073 第 1 条）。
        bare = CategoryTree()
        bare.set_nodes([node(1, "Beta", 5, None), node(2, "alpha", 20, None)], show_root=False)
        bare.apply_sort()
        top_labels = [bare.topLevelItem(i).text(0) for i in range(bare.topLevelItemCount())]
        if sorted(top_labels) != ["Beta (5)", "alpha (20)"]:
            problems.append(f"show_root=False 时顶级分类应直接成为顶层项，实际 {top_labels}")
        if any(
            bare.topLevelItem(i).data(0, KIND_ROLE) == KIND_ALL
            for i in range(bare.topLevelItemCount())
        ):
            problems.append("show_root=False 时不该再建「全部数据」根行")
        bare.set_sort("name", False)
        bare.apply_sort()
        bare_order = [bare.topLevelItem(i).text(0) for i in range(bare.topLevelItemCount())]
        if bare_order != ["alpha (20)", "Beta (5)"]:
            problems.append(f"没有根行时排序应重排 QTreeWidget 顶层项，实际 {bare_order}")
        # 找不到分类时不能顺手选中第一项：上层会把「当前项」当成用户的真实选择
        bare.select_category(999)
        if bare.currentItem() is not None:
            problems.append(f"没有根行且找不到分类时应清空当前项，实际 {bare.currentItem().text(0)}")
        sip.delete(bare)

        if not tree.wordWrap() or tree.textElideMode() != Qt.TextElideMode.ElideNone:
            problems.append("分类名太长时应在分类栏里换行显示，而不是被省略号截断")

        # 折行是自己在文本里做的：有些 Qt 平台不理会 QTreeView 的 setWordWrap
        from app.ui.components.category_tree import _wrap_label

        wrapped = _wrap_label("非常长的分类名称" * 4)
        if "\n" not in wrapped:
            problems.append(f"过长的分类名应折成多行，实际 {wrapped!r}")
        if wrapped.replace("\n", "") != "非常长的分类名称" * 4:
            problems.append(f"折行不该丢字：{wrapped!r}")
        if _wrap_label("短名") != "短名":
            problems.append("短名不该被折行")

        # 固定分类（「未分类」）无论怎么排都压在最后
        with mock.patch(
            "app.ui.components.category_tree.is_uncategorized",
            side_effect=lambda category: category is not None and category.name == "未分类",
        ):
            fixed_tree = CategoryTree()
            fixed_tree.set_nodes(
                [node(1, "Beta", 5, None), node(9, "未分类", 0, None), node(2, "alpha", 20, None)],
                total=25,
            )
            fixed_tree.set_sort("name", True)
            fixed_tree.apply_sort()
            fixed_root = fixed_tree.topLevelItem(0)
            order = [fixed_root.child(i).text(0) for i in range(fixed_root.childCount())]
            if not order or "未分类" not in order[-1]:
                problems.append(f"「未分类」应始终排在最后，实际 {order}")

        # 检查过程中不用建窗口，直接回收这两个临时控件
        sip.delete(fixed_tree)
        sip.delete(tree)
        app.processEvents()

    assert not problems, "分类栏排序：" + "；".join(problems[:8])


@check("manage_category_branch_click", "pages")
def manage_category_branch_click(case: Case) -> None:
    """点展开箭头只切换展开、不选中分类；点分类名才选中（用户 m01549 第 1 条）。"""
    import types
    from unittest import mock

    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest

    from app.ui.components.category_tree import CategoryTree

    app = ensure_app()
    problems: list[str] = []

    def node(category_id: int, name: str, parent_id: int | None = None, depth: int = 0):
        category = types.SimpleNamespace(id=category_id, name=name, parent_id=parent_id)
        return types.SimpleNamespace(
            category=category, depth=depth, item_count=0, total_count=0, latest_at=None
        )

    def item_of(tree, category_id: int):
        return next(
            item
            for item in tree._iter_items()
            if item.data(0, Qt.ItemDataRole.UserRole) == category_id
        )

    with mock.patch("app.ui.components.category_tree.is_uncategorized", return_value=False):
        tree = CategoryTree()
        tree.resize(420, 240)
        # 父级下面挂两个子级，箭头才有得展开
        tree.set_nodes(
            [
                node(1, "父级"),
                node(2, "子级甲", parent_id=1, depth=1),
                node(3, "子级乙", parent_id=1, depth=1),
            ],
            total=0,
        )
        tree.show()
        app.processEvents()
        try:
            parent_item = item_of(tree, 1)
            if parent_item.childCount() != 2:
                problems.append(f"前置条件：父级应有 2 个子级，实际 {parent_item.childCount()}")
            # 根行默认是收起的（config.expandCategories 默认 False），不展开它下面的分类
            # 就没有布局，visualItemRect 会是空矩形，点击位置算不出来
            tree.topLevelItem(0).setExpanded(True)
            tree.collapseItem(parent_item)
            app.processEvents()
            row = tree.visualItemRect(parent_item)
            if row.isEmpty():
                problems.append("前置条件：分类行没有布局出矩形，无法模拟点击")
            # 展开箭头的判定区与 qfluentwidgets 的公式一致：x 在
            # (level * indentation + 20, +10) 之间，`CategoryTree` 的 indentation 是 14
            level = 0
            cursor = parent_item
            while cursor.parent() is not None:
                cursor = cursor.parent()
                level += 1
            arrow_x = level * tree.indentation() + 24

            QTest.mouseClick(
                tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                QPoint(arrow_x, row.center().y()),
            )
            app.processEvents()
            if not parent_item.isExpanded():
                problems.append("点展开箭头应把分类展开")
            if parent_item.isSelected() or tree.selected_categories():
                problems.append(
                    f"点展开箭头不该选中分类，实际选中 {tree.selected_categories()}"
                )
            if tree.currentItem() is parent_item:
                problems.append("点展开箭头不该把分类设成当前项（上层会当成用户选中了它）")

            # 对照：点分类名（箭头右侧）才选中它
            QTest.mouseClick(
                tree.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                QPoint(arrow_x + 80, row.center().y()),
            )
            app.processEvents()
            if not parent_item.isSelected():
                problems.append("点分类名应选中该分类")
            if tree.selected_categories() != [1]:
                problems.append(
                    f"点分类名后选中的分类应为 [1]，实际 {tree.selected_categories()}"
                )
        finally:
            tree.hide()
            _drop_widget(tree)
    assert not problems, "分类栏点展开箭头：" + "；".join(problems[:8])


@check("manage_category_expand_keep", "pages")
def manage_category_expand_keep(case: Case) -> None:
    """刷新时重排分类不该把用户展开的分类收起来（用户 m01604）。"""
    import types
    from unittest import mock

    from app.ui.components.category_tree import CategoryTree

    app = ensure_app()
    problems: list[str] = []

    def node(category_id: int, name: str, parent_id: int | None = None, depth: int = 0):
        category = types.SimpleNamespace(id=category_id, name=name, parent_id=parent_id)
        return types.SimpleNamespace(
            category=category, depth=depth, item_count=0, total_count=0, latest_at=None
        )

    def item_of(tree, category_id: int):
        return next(
            item
            for item in tree._iter_items()
            if item.data(0, Qt.ItemDataRole.UserRole) == category_id
        )

    nodes = [
        node(1, "父级"),
        node(2, "子级甲", parent_id=1, depth=1),
        node(3, "子级乙", parent_id=1, depth=1),
        node(4, "孙级甲", parent_id=2, depth=2),
        node(5, "孙级乙", parent_id=2, depth=2),
    ]

    with mock.patch("app.ui.components.category_tree.is_uncategorized", return_value=False):
        tree = CategoryTree()
        tree.set_nodes(nodes, total=0)
        tree.apply_sort()
        # 用户手动展开父级与中间那一层
        item_of(tree, 1).setExpanded(True)
        item_of(tree, 2).setExpanded(True)
        app.processEvents()
        if not item_of(tree, 1).isExpanded() or not item_of(tree, 2).isExpanded():
            problems.append("前置条件：程序展开后应处于展开状态")

        # 刷新时排队跑的那一次重排：摘行再插回，展开状态必须还在
        tree._sort_dirty = True
        tree.apply_sort()
        if not item_of(tree, 1).isExpanded():
            problems.append("重排后用户展开的分类被收起了（刷新看起来就像「自动折叠」）")
        if not item_of(tree, 2).isExpanded():
            problems.append("重排后中间那一层的展开状态也丢了")

        # 完整刷新：整棵树重建 + 再重排一次
        tree.set_nodes(nodes, total=0)
        tree.apply_sort()
        if not item_of(tree, 1).isExpanded() or not item_of(tree, 2).isExpanded():
            problems.append("刷新（重建 + 重排）后应保留用户展开过的分类")

        # 用户收起过的分类不该被强行展开
        item_of(tree, 1).setExpanded(False)
        app.processEvents()
        tree._sort_dirty = True
        tree.apply_sort()
        if item_of(tree, 1).isExpanded():
            problems.append("用户收起过的分类不该在重排后被展开")
        _drop_widget(tree)
    assert not problems, "分类栏刷新保留展开状态：" + "；".join(problems[:8])


@check("manage_category_batch_delete", "pages")
def manage_category_batch_delete(case: Case) -> None:
    """批量删除分类：有下级分类时先问「保留 / 一起删除」，没有下级才走简易确认。"""
    fixture, window = build_window(case)
    problems: list[str] = []
    try:
        ensure_app()
        page = window.manage_page
        taxonomy = TaxonomyService(case.session)
        user_id = page.user_service.current_id()

        parent = taxonomy.create_category(
            "批量父级", parent_id=fixture.category_root, user_id=user_id
        )
        child = taxonomy.create_category("批量子级", parent_id=parent.id, user_id=user_id)
        # 注意：只有非根分类才可删，所以「没有下级的分类」也要挂在根分类下面
        solo = taxonomy.create_category(
            "批量无下级", parent_id=fixture.category_root, user_id=user_id
        )
        assert parent is not None and child is not None and solo is not None
        case.session.commit()
        page.refresh()

        asked: list[tuple[str, int]] = []
        confirmed: list[tuple] = []
        deleted: list[tuple[int, bool]] = []
        toasts: list[tuple[str, str]] = []
        state = {"ids": [parent.id], "recursive": False, "accept": True}

        class _FakeDeleteDialog:
            def __init__(self, name, child_count, parent=None):
                asked.append((name, child_count))

            def exec(self):
                return state["accept"]

            def recursive(self):
                return state["recursive"]

        saved = (
            manage_module.CategoryDeleteDialog,
            manage_module.confirm,
            page.taxonomy.delete_category,
            page._pick_category_ids,
            page.toast_success,
            page.toast_warning,
        )
        manage_module.CategoryDeleteDialog = _FakeDeleteDialog
        manage_module.confirm = lambda *args, **kwargs: confirmed.append(args) or True
        page.taxonomy.delete_category = (
            lambda category, *a, **k: deleted.append(
                (category.id, bool(k.get("recursive")))
            )
            or 2
        )
        # 桩掉「挑分类」的弹窗：直接把它当成用户挑中了 state["ids"] 里的分类
        page._pick_category_ids = lambda **kwargs: list(state["ids"])
        page.toast_success = lambda title, message: toasts.append((title, message))
        page.toast_warning = lambda title, message: toasts.append((title, message))
        try:
            deleted.clear()
            asked.clear()
            confirmed.clear()
            toasts.clear()
            page._on_category_batch_delete()
            if len(asked) != 1 or asked[0][1] != 1:
                problems.append(f"有下级分类时应弹二选一并报出下级数量，实际 {asked}")
            if confirmed:
                problems.append(f"有下级分类时不该再走简易确认，实际 {confirmed}")
            if deleted != [(parent.id, False)]:
                problems.append(f"选「保留下级分类」时应非递归删除，实际 {deleted}")

            deleted.clear()
            asked.clear()
            toasts.clear()
            state["recursive"] = True
            page._on_category_batch_delete()
            if deleted != [(parent.id, True)]:
                problems.append(f"选「下级一起删除」时应递归删除，实际 {deleted}")
            if toasts and "下级分类已一并删除" not in toasts[-1][1]:
                problems.append(f"递归删除后的提示应说明下级一并删除，实际 {toasts[-1][1]!r}")

            # 弹窗被取消：什么都不做
            deleted.clear()
            asked.clear()
            confirmed.clear()
            state["recursive"] = False
            state["accept"] = False
            page._on_category_batch_delete()
            if deleted or confirmed:
                problems.append("二选一弹窗被取消后不该删除任何分类")

            # 没有下级分类：不弹二选一，走简易确认
            deleted.clear()
            asked.clear()
            confirmed.clear()
            state["accept"] = True
            state["ids"] = [solo.id]
            page._on_category_batch_delete()
            if asked:
                problems.append(f"没有下级分类时不该弹二选一，实际 {asked}")
            if len(confirmed) != 1:
                problems.append(f"没有下级分类时应走一次简易确认，实际 {len(confirmed)} 次")
            if deleted != [(solo.id, False)]:
                problems.append(f"没有下级分类时应非递归删除，实际 {deleted}")

            # 弹出「挑分类」的弹窗后直接取消：什么都不做
            deleted.clear()
            asked.clear()
            confirmed.clear()
            toasts.clear()
            state["ids"] = []
            page._on_category_batch_delete()
            if deleted or asked or confirmed or toasts:
                problems.append("取消「挑分类」弹窗后不该做任何事")

            # 只挑中「未分类」：过滤后没有可删的分类，只给提示
            uncategorized = taxonomy.uncategorized_category(user_id=user_id)
            assert uncategorized is not None
            deleted.clear()
            asked.clear()
            confirmed.clear()
            toasts.clear()
            state["ids"] = [uncategorized.id]
            page._on_category_batch_delete()
            if deleted or asked or confirmed:
                problems.append("挑中的分类都不可删时不该删除任何分类")
            if not toasts:
                problems.append("挑中的分类都不可删时应给出提示")
        finally:
            (
                manage_module.CategoryDeleteDialog,
                manage_module.confirm,
                page.taxonomy.delete_category,
                page._pick_category_ids,
                page.toast_success,
                page.toast_warning,
            ) = saved
        assert not problems, "批量删除分类：" + "；".join(problems[:12])
    finally:
        dispose_window(window)
