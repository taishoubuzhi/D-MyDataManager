"""通用对话框：文本输入、数据项编辑。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QListWidgetItem, QTreeWidgetItem
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    ListWidget,
    MessageBoxBase,
    PushButton,
    RadioButton,
    SingleDirectionScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TextEdit,
    TreeWidget,
)

from ..core.runtime.naming import NUMBER_STYLES, RENAME_MODES, RenameRule, build_plan, split_suffix
from ..db.models import DataItem
from ..db.seed import UNCATEGORIZED_NAME
from .framework import IconTextButton, clear_scroll_background, format_size
from .components.category_tree import KIND_CATEGORY, KIND_ROLE, CategoryTree
from .components.keyword_input import KeywordInput
from .components.tag_picker import TagPicker
from .components.tri_state_list import TriStateList


class TextInputDialog(MessageBoxBase):
    """单行或多行文本输入。"""

    def __init__(
        self,
        title: str,
        placeholder: str = "",
        text: str = "",
        parent: QWidget | None = None,
        multiline: bool = False,
        hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)
        if hint:
            self.viewLayout.addWidget(BodyLabel(hint, self))

        if multiline:
            self.edit: QWidget = TextEdit(self)
            self.edit.setPlainText(text)
            self.edit.setMinimumHeight(140)
        else:
            self.edit = LineEdit(self)
            self.edit.setText(text)
        self.edit.setPlaceholderText(placeholder)
        self.viewLayout.addWidget(self.edit)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def value(self) -> str:
        if isinstance(self.edit, TextEdit):
            return self.edit.toPlainText().strip()
        return self.edit.text().strip()


class CategoryConflictDialog(MessageBoxBase):
    """删除分类时子分类上移会与同级重名：可自动加 -1、-2，也可逐个改名。"""

    def __init__(self, rows: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edits: list[LineEdit] = []
        self._auto = False
        self.titleLabel = SubtitleLabel("处理重名子分类", self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(
            BodyLabel("以下子分类上移后会与同级分类重名，可自动加 -1、-2 后缀，也可以逐个改名：", self)
        )
        for name, suggested in rows:
            row = QWidget(self)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(BodyLabel(f"{name} →", row))
            edit = LineEdit(row)
            edit.setText(suggested)
            layout.addWidget(edit, 1)
            self.viewLayout.addWidget(row)
            self._edits.append(edit)
        self.auto_button = IconTextButton(FluentIcon.LABEL, "自动编号（-1、-2）", self)
        self.auto_button.clicked.connect(self._on_auto)
        self.buttonLayout.insertWidget(0, self.auto_button)
        self.yesButton.setText("重命名并删除")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(480)

    def _on_auto(self, *_args) -> None:
        self._auto = True
        self.accept()

    def auto(self) -> bool:
        """用户选择了自动编号。"""
        return self._auto

    def renames(self) -> list[str]:
        """逐行输入的新名称，顺序与传入 rows 一致。"""
        return [edit.text().strip() for edit in self._edits]


class CategoryDeleteDialog(MessageBoxBase):
    """删除分类时选下级分类怎么处理（用户 m01073 第 2 条）。

    「保留下级分类」= 原来的行为：下级分类上移一层（与同级重名的再单独改名），本分类里的
    数据变成未分类；「下级分类一起删除」= 逐层删掉下级分类，各级分类里的数据全部变成未分类
    （也就是只保留文件，不保留分类）。
    """

    def __init__(self, name: str, child_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel("删除分类", self))
        self.viewLayout.addWidget(
            BodyLabel(
                f"确定删除分类「{name}」吗？它下面还有 {child_count} 个分类，请选择怎么处理：",
                self,
            )
        )
        # RadioButton 继承 QRadioButton：同一个父控件下自动互斥
        self.keep_radio = RadioButton("保留下级分类（下级分类上移一层）", self)
        self.keep_radio.setChecked(True)
        self.remove_radio = RadioButton("下级分类一起删除（只保留分类里的文件）", self)
        self.viewLayout.addWidget(self.keep_radio)
        self.viewLayout.addWidget(self.remove_radio)

        self.yesButton.setText("删除")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(460)

    def recursive(self) -> bool:
        """True = 连下级分类一起删除。"""
        return bool(self.remove_radio.isChecked())


def _category_paths(nodes) -> dict[int, str]:
    """分类 id → 从根目录开始的完整路径（`一级 / 二级 / 三级`），用来区分同名分类。"""
    name_by_id: dict[int, str] = {}
    parent_by_id: dict[int, int | None] = {}
    for node in nodes or ():
        name_by_id[node.category.id] = node.category.name
        parent_by_id[node.category.id] = node.category.parent_id
    paths: dict[int, str] = {}
    for category_id in name_by_id:
        parts: list[str] = []
        cursor: int | None = category_id
        guard = 0
        while cursor is not None and cursor in name_by_id and guard <= len(name_by_id):
            parts.append(name_by_id[cursor])
            cursor = parent_by_id.get(cursor)
            guard += 1
        paths[category_id] = " / ".join(reversed(parts))
    return paths


class CategoryPickerDialog(MessageBoxBase):
    """选择目标分类：用弹窗呈现分类栏那样的层级结构，而不是一个扁平的同名列表。

    从根目录开始按一级 / 二级 / 三级……逐层缩进，同名分类靠层级天然区分开（用户 m00117
    第 4 条）。单选：只有被直接点中的分类算选中，「下级全选」不会连带把上级算进来；
    分类可以折叠，默认全部展开；内容超出弹窗范围时上下左右都能滚动。
    """

    def __init__(
        self,
        nodes,
        parent: QWidget | None = None,
        count: int = 1,
        *,
        title: str = "移动到分类",
        tip: str | None = None,
        current: int | None = None,
        allow_root: bool = False,
    ) -> None:
        super().__init__(parent)
        self._allow_root = bool(allow_root)
        self._path_by_id = _category_paths(nodes)
        self._selected: int | None = None

        self.viewLayout.addWidget(SubtitleLabel(title, self))
        self.viewLayout.addWidget(BodyLabel(tip or f"把选中的 {count} 项数据移动到：", self))

        # 这里不需要复选框：勾选是「中间列表显示哪些数据」的语义，与「选哪个分类」无关
        self.tree = CategoryTree(self)
        # 不建「全部数据」根行：弹窗只要分类本身；若先建再隐藏根行，顶级分类会跟着一起消失
        self.tree.set_nodes(nodes, expand=True, checkable=False, show_root=False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMinimumSize(380, 300)
        self.tree.categorySelected.connect(self._on_category_selected)
        self.viewLayout.addWidget(self.tree)

        self.target_hint = CaptionLabel(self)
        self.viewLayout.addWidget(self.target_hint)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(460)

        if current is not None and current in self._path_by_id:
            # 预选当前分类：`select_category()` 会走到 `_on_category_selected()`，不用重复设置
            self.tree.select_category(current)
        self._show_target(None)

    def _on_category_selected(self, category_id) -> None:
        self._show_target(None if category_id is None else int(category_id))

    def _show_target(self, category_id: int | None) -> None:
        """更新提示与「确定」按钮：`allow_root` 时「没选分类」就等于放到顶层。"""
        if category_id is not None:
            self._selected = category_id
            self.target_hint.setText(f"目标：{self._path_by_id.get(category_id, '')}")
            self.yesButton.setEnabled(True)
            return
        self._selected = None
        if self._allow_root:
            self.target_hint.setText("目标：顶层（不放入任何分类）")
            self.yesButton.setEnabled(True)
        else:
            self.target_hint.setText("尚未选择分类")
            self.yesButton.setEnabled(False)

    def category_id(self) -> int | None:
        """选中的分类 id；`allow_root=True` 且没有选分类时返回 None（顶层）。"""
        return self._selected


class CategoryPickerComboBox(ComboBox):
    """分类下拉：不弹默认的扁平同名列表，改为打开层级分类选择弹窗。

    保留 `ComboBox` 的 items 接口（`itemData` / `currentData` / `currentText` / `count` /
    `setCurrentIndex`），调用方与自检都不用改取值方式；只有「怎么挑」换成了层级弹窗
    （用户 m00117 第 4 条）。
    """

    def __init__(self, parent: QWidget | None = None, *, title: str = "选择分类") -> None:
        super().__init__(parent)
        self._picker_nodes: list = []
        self._picker_title = title

    def set_picker_nodes(self, nodes, *, title: str | None = None) -> None:
        """把分类树节点喂给弹窗（就是 `TaxonomyService.tree()` 的返回值）。"""
        self._picker_nodes = list(nodes or ())
        if title:
            self._picker_title = title

    def _showComboMenu(self) -> None:
        if not self._picker_nodes or not self.isEnabled():
            return
        dialog = CategoryPickerDialog(
            self._picker_nodes,
            self.window(),
            title=self._picker_title,
            tip="选择一个分类：",
            current=self.currentData(),
        )
        if not dialog.exec():
            return
        wanted = dialog.category_id()
        for index in range(self.count()):
            if self.itemData(index) == wanted:
                self.setCurrentIndex(index)
                return


class CategorySelectDialog(MessageBoxBase):
    """批量分类操作的第一步：点一个加一个，攒成一张待处理清单。

    批量移动 / 批量删除 / 批量重命名都要先挑出「对哪些分类下手」，所以把这段收在这里
    （用户 m01929、m01932 第 1 条）。清单里的复选框只用于中间列表显示哪些数据，挑分类
    靠点击，因此这里 `checkable=False`：点左侧分类树里的分类就把它加进右边清单，
    加入的瞬间校验层级关系，确认前可以随时移出某一项，也可以继续新增。

    `allow_nested=False`（批量移动）时，新分类与清单里已有分类互为上级 / 下级就拒绝加入
    ——它们一起移动必然冲突；`allow_nested=True`（批量删除 / 重命名）时父子一起操作是
    合法的（父级连下级一起删 / 父级和子级都改名），照常加入。
    """

    def __init__(
        self,
        nodes,
        parent: QWidget | None = None,
        *,
        title: str,
        tip: str,
        list_title: str,
        accept_text: str = "确定",
        allow_nested: bool = False,
    ) -> None:
        super().__init__(parent)
        nodes = list(nodes or ())
        self._parent_by_id: dict[int, int | None] = {
            node.category.id: node.category.parent_id for node in nodes
        }
        self._path_by_id = _category_paths(nodes)
        self._list_title = list_title
        self._allow_nested = bool(allow_nested)
        self._order: list[int] = []

        self.viewLayout.addWidget(SubtitleLabel(title, self))
        self.viewLayout.addWidget(BodyLabel(tip, self))

        row = QHBoxLayout()
        row.setSpacing(12)

        self.tree = CategoryTree(self)
        # 不建「全部数据」根行：弹窗只要分类本身；若先建再隐藏根行，顶级分类会跟着一起消失
        self.tree.set_nodes(nodes, expand=True, checkable=False, show_root=False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMinimumSize(320, 300)
        self.tree.categorySelected.connect(self._on_tree_selected)
        # 单选模式下重复点同一行不会再有「选中变化」，而移出清单之后想重新点回来正需要它
        self.tree.itemClicked.connect(self._on_tree_clicked)
        row.addWidget(self.tree, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(StrongBodyLabel(list_title, self))
        self.move_list = ListWidget(self)
        self.move_list.setMinimumSize(280, 260)
        side.addWidget(self.move_list, 1)
        self.remove_button = PushButton("移出选中项", self)
        self.remove_button.clicked.connect(self._remove_selected)
        side.addWidget(self.remove_button)
        row.addLayout(side, 1)
        self.viewLayout.addLayout(row)

        self.message = CaptionLabel("", self)
        self.viewLayout.addWidget(self.message)

        self.yesButton.setText(accept_text)
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(760)
        self._sync_buttons()

    # ------------------------------------------------------------------ 待处理清单
    def category_ids(self) -> list[int]:
        """确认要处理的分类 id（按加入顺序）。"""
        return list(self._order)

    def _on_tree_selected(self, category_id) -> None:
        if category_id is None:
            return
        self._add(int(category_id))

    def _on_tree_clicked(self, item, _column: int) -> None:
        """单击兜底：分类被移出清单之后，重复点同一行仍要能再加回来。

        `categorySelected` 只在「选中项变化」时发信号，单选模式下点已经选中的那一行不会
        再发；所以这里补一个点击回调。已经在清单里的直接跳过，提示交给 `_on_tree_selected`。
        """
        if item.data(0, KIND_ROLE) != KIND_CATEGORY:
            return
        category_id = item.data(0, Qt.ItemDataRole.UserRole)
        if category_id is None or int(category_id) in self._order:
            return
        self._add(int(category_id))

    def _add(self, category_id: int) -> None:
        if category_id in self._order:
            self.message.setText(
                f"「{self._path_by_id.get(category_id, '')}」已经在{self._list_title}里了。"
            )
            return
        if not self._allow_nested:
            for other in self._order:
                if not self._related(category_id, other):
                    continue
                relation = "上级" if self._is_ancestor(category_id, other) else "下级"
                self.message.setText(
                    f"无法加入：「{self._path_by_id.get(category_id, '')}」是{self._list_title}里"
                    f"「{self._path_by_id.get(other, '')}」的{relation}分类，它们不能一起批量操作。"
                )
                return
        self._order.append(category_id)
        self.message.setText(f"已加入：「{self._path_by_id.get(category_id, '')}」")
        self._rebuild_list()

    def _related(self, left: int, right: int) -> bool:
        return self._is_ancestor(left, right) or self._is_ancestor(right, left)

    def _is_ancestor(self, ancestor: int, node_id: int) -> bool:
        """`ancestor` 是不是 `node_id` 的上级（含它自己）。"""
        cursor: int | None = node_id
        guard = 0
        while cursor is not None and guard <= len(self._parent_by_id):
            if cursor == ancestor:
                return True
            cursor = self._parent_by_id.get(cursor)
            guard += 1
        return False

    def _remove_selected(self) -> None:
        picked = {
            item.data(Qt.ItemDataRole.UserRole) for item in self.move_list.selectedItems()
        }
        if not picked:
            self.message.setText("先在移动表里选中要移出的项。")
            return
        self._order = [category_id for category_id in self._order if category_id not in picked]
        self.message.setText(f"已移出 {len(picked)} 项。")
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        self.move_list.clear()
        for category_id in self._order:
            path = self._path_by_id.get(category_id, "")
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, category_id)
            item.setToolTip(path)
            self.move_list.addItem(item)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.yesButton.setEnabled(bool(self._order))


class CategoryMoveDialog(CategorySelectDialog):
    """批量移动分类的第一步：攒一张「分类移动表」（保留原名，批量移动流程与自检都依赖它）。"""

    def __init__(
        self, nodes, parent: QWidget | None = None, *, title: str = "批量移动分类"
    ) -> None:
        super().__init__(
            nodes,
            parent,
            title=title,
            tip="点左侧分类把它加入右边的移动表，加入时会检查层级关系；"
            "确认后统一移动到同一个目标分类。",
            list_title="分类移动表",
            accept_text="下一步",
            allow_nested=False,
        )


class ItemEditDialog(MessageBoxBase):
    """编辑单个数据项：上方是只读的数据信息，下方可改名称、分类、标签、关键词与隐藏状态。"""

    def __init__(
        self,
        item: DataItem,
        categories: list[tuple[int, str]],
        known_tags: list[str],
        global_tags: set[str] | None = None,
        info: list[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
        nodes: list | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item

        self.viewLayout.addWidget(SubtitleLabel("编辑数据项", self))

        if info:
            self.viewLayout.addWidget(StrongBodyLabel("数据信息", self))
            info_grid = QGridLayout()
            info_grid.setHorizontalSpacing(10)
            info_grid.setVerticalSpacing(4)
            for row, (label, value) in enumerate(info):
                name_label = CaptionLabel(label, self)
                value_label = CaptionLabel(value, self)
                value_label.setToolTip(value)
                value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                info_grid.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)
                info_grid.addWidget(value_label, row, 1)
            info_grid.setColumnStretch(1, 1)
            self.viewLayout.addLayout(info_grid)

        self.viewLayout.addWidget(StrongBodyLabel("可修改的信息", self))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.name_edit = LineEdit(self)
        self.name_edit.setText(item.name)

        self.category_box = CategoryPickerComboBox(self)
        for category_id, label in categories:
            self.category_box.addItem(label, userData=category_id)
        if nodes:
            # 有层级节点时，点下拉不再弹同名扁平列表，而是弹层级分类选择弹窗
            self.category_box.set_picker_nodes(nodes)
        if UNCATEGORIZED_NAME not in [label.lstrip("　") for _, label in categories]:
            # 兜底：用户还没有真实的「未分类」分类时，保留一个空占位项。
            self.category_box.addItem(UNCATEGORIZED_NAME)
        selected = 0
        for index in range(self.category_box.count()):
            if self.category_box.itemData(index) == item.category_id:
                selected = index
                break
        else:
            if item.category_id is None:
                # 没有分类的数据默认落到真实的「未分类」分类，保存后会归入 <用户名>/未分类/。
                for index in range(self.category_box.count()):
                    if self.category_box.itemText(index).lstrip("　") == UNCATEGORIZED_NAME:
                        selected = index
                        break
        self.category_box.setCurrentIndex(selected)

        self.tag_input = TagPicker(
            known_tags, "输入标签后回车，或点右侧按钮选择已有标签", self, global_tags=global_tags
        )
        self.tag_input.set_keywords(list(item.tag_names))
        self.keyword_input = KeywordInput("输入关键词后回车", self)
        self.keyword_input.set_keywords(list(item.keywords or []))

        self.hidden_switch = SwitchButton(self)
        self.hidden_switch.setChecked(bool(item.is_hidden))

        grid.addWidget(BodyLabel("名称", self), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(BodyLabel("分类", self), 1, 0)
        grid.addWidget(self.category_box, 1, 1)
        grid.addWidget(BodyLabel("标签", self), 2, 0)
        grid.addWidget(self.tag_input, 2, 1)
        grid.addWidget(BodyLabel("关键词", self), 3, 0)
        grid.addWidget(self.keyword_input, 3, 1)
        grid.addWidget(BodyLabel("隐藏项", self), 4, 0)
        grid.addWidget(self.hidden_switch, 4, 1)
        self.viewLayout.addLayout(grid)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(480)

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or self._item.name,
            "category_id": self.category_box.currentData(),
            "tags": self.tag_input.keywords(),
            "keywords": self.keyword_input.keywords(),
            "is_hidden": self.hidden_switch.isChecked(),
        }



class DuplicateDialog(MessageBoxBase):
    """重复内容清理：按校验和分组，勾选要删除的冗余项。"""

    def __init__(self, groups: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.groups = groups

        self.viewLayout.addWidget(SubtitleLabel("重复内容清理", self))
        total = sum(len(items) for items in groups.values())
        self.viewLayout.addWidget(
            BodyLabel(f"共 {len(groups)} 组、{total} 项内容重复；每组默认保留最新导入的一项。", self)
        )

        self.tree = TreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["名称", "库内路径", "导入时间"])
        self.tree.setMinimumSize(620, 320)
        for index, (checksum, items) in enumerate(groups.items(), start=1):
            ordered = sorted(items, key=lambda item: item.created_at or 0, reverse=True)
            size = ordered[0].size or 0
            top = QTreeWidgetItem([f"第 {index} 组 · {len(ordered)} 项 · {format_size(size)} · {checksum[:10]}", "", ""])
            top.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(top)
            for position, item in enumerate(ordered):
                child = QTreeWidgetItem(
                    [
                        item.name,
                        item.file_path or item.source_path or "",
                        item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, item.id)
                child.setCheckState(0, Qt.CheckState.Unchecked if position == 0 else Qt.CheckState.Checked)
                top.addChild(child)
            top.setExpanded(True)
        self.viewLayout.addWidget(self.tree)

        actions = QHBoxLayout()
        keep_latest = IconTextButton(FluentIcon.HISTORY, "每组只保留最新", self)
        keep_latest.clicked.connect(self._check_all_but_first)
        clear_button = IconTextButton(FluentIcon.CLEAR_SELECTION, "全部取消勾选", self)
        clear_button.clicked.connect(self._uncheck_all)
        actions.addWidget(keep_latest)
        actions.addWidget(clear_button)
        actions.addStretch(1)
        self.viewLayout.addLayout(actions)

        self.yesButton.setText("删除勾选项")
        self.cancelButton.setText("关闭")
        self.widget.setMinimumWidth(680)

    def _check_all_but_first(self) -> None:
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for position in range(top.childCount()):
                child = top.child(position)
                state = Qt.CheckState.Unchecked if position == 0 else Qt.CheckState.Checked
                child.setCheckState(0, state)

    def _uncheck_all(self) -> None:
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for position in range(top.childCount()):
                top.child(position).setCheckState(0, Qt.CheckState.Unchecked)

    def checked_ids(self) -> list[int]:
        ids: list[int] = []
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for position in range(top.childCount()):
                child = top.child(position)
                if child.checkState(0) == Qt.CheckState.Checked:
                    ids.append(child.data(0, Qt.ItemDataRole.UserRole))
        return ids


class BatchRenameDialog(MessageBoxBase):
    """批量重命名：选一种改名方式，再在变更清单里勾选要改的行。"""

    def __init__(
        self,
        entries: list[tuple[int, str]],
        parent: QWidget | None = None,
        reserved: set[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries = list(entries)
        self._reserved = set(reserved or ())
        self._rows: list[tuple[int, str, CheckBox, BodyLabel]] = []

        self.viewLayout.addWidget(SubtitleLabel("批量重命名", self))
        self.viewLayout.addWidget(
            BodyLabel("先选一种方式，下面的清单就是每个文件的最终名称；只勾选要改的行。", self)
        )

        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel("改名方式", self))
        self.mode_box = ComboBox(self)
        for key, text in RENAME_MODES:
            self.mode_box.addItem(text, userData=key)
        self.mode_box.setMinimumWidth(320)
        mode_row.addWidget(self.mode_box, 1)
        self.viewLayout.addLayout(mode_row)

        self._params = QGridLayout()
        self._params.setContentsMargins(0, 0, 0, 0)
        self._params.setHorizontalSpacing(8)
        self._params.setVerticalSpacing(6)
        self.viewLayout.addLayout(self._params)

        self.find_edit = LineEdit(self)
        self.find_edit.setPlaceholderText("要替换掉的文字")
        self.replace_edit = LineEdit(self)
        self.replace_edit.setPlaceholderText("替换成什么（留空 = 删掉这段文字）")
        self.base_edit = LineEdit(self)
        self.base_edit.setPlaceholderText("新名称（扩展名保持不动）")
        self.numbered_box = CheckBox("自动编号", self)
        self.numbered_box.setChecked(True)
        self.start_edit = SpinBox(self)
        self.start_edit.setRange(1, 99999)
        self.width_edit = SpinBox(self)
        self.width_edit.setRange(0, 8)
        self.width_edit.setToolTip("数字编号补零位数，0 表示不补零")
        self.style_box = ComboBox(self)
        for key, text in NUMBER_STYLES:
            self.style_box.addItem(text, userData=key)
        self.text_edit = LineEdit(self)
        self.text_edit.setPlaceholderText("要插入的文字")
        self.position_edit = SpinBox(self)
        self.position_edit.setRange(1, 9999)
        self.position_edit.setToolTip("从第几个字开始（第一个字算第 1 个）")
        self.length_edit = SpinBox(self)
        self.length_edit.setRange(1, 9999)

        self._add_param("查找", self.find_edit, {"replace"})
        self._add_param("替换为", self.replace_edit, {"replace"})
        self._add_param("新名称", self.base_edit, {"overwrite"})
        self._add_param("编号设置", self.numbered_box, {"overwrite"})
        self._add_param("起始编号", self.start_edit, {"overwrite"})
        self._add_param("补零位数", self.width_edit, {"overwrite"})
        self._add_param("编号样式", self.style_box, {"overwrite"})
        self._add_param("插入文字", self.text_edit, {"insert"})
        self._add_param("插入位置", self.position_edit, {"insert", "delete"})
        self._add_param("删除字数", self.length_edit, {"delete"})

        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel("变更清单", self))
        header.addStretch(1)
        self._summary = CaptionLabel("", self)
        header.addWidget(self._summary)
        self.viewLayout.addLayout(header)

        self._host = QWidget(self)
        self._list = QVBoxLayout(self._host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        self._list.addStretch(1)
        self._scroll = SingleDirectionScrollArea(self)
        self._scroll.setWidget(self._host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(220)
        clear_scroll_background(self._scroll)
        self.viewLayout.addWidget(self._scroll)
        self._build_rows()

        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        for edit in (self.find_edit, self.replace_edit, self.base_edit, self.text_edit):
            edit.textChanged.connect(self._refresh)
        for box in (self.start_edit, self.width_edit, self.position_edit, self.length_edit):
            box.valueChanged.connect(self._refresh)
        self.style_box.currentIndexChanged.connect(self._refresh)
        self.numbered_box.stateChanged.connect(self._refresh)
        self._on_mode_changed()

        self.yesButton.setText("应用改名")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(620)

    # ------------------------------------------------------------------ 内部
    def _add_param(self, label: str, widget: QWidget, modes: set[str]) -> None:
        row = self._params.rowCount()
        caption = BodyLabel(label, self)
        for column, part in enumerate((caption, widget)):
            part.setProperty("renameModes", ",".join(sorted(modes)))
            self._params.addWidget(part, row, column)
        widget.setMinimumWidth(260)

    def _build_rows(self) -> None:
        for item_id, name in self._entries:
            row = QWidget(self._host)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            box = CheckBox(row)
            box.setChecked(True)
            box.setToolTip("取消勾选就不改这一项")
            layout.addWidget(box)
            old_label = BodyLabel(name, row)
            old_label.setToolTip(name)
            layout.addWidget(old_label, 3)
            arrow = BodyLabel("→", row)
            layout.addWidget(arrow)
            new_label = BodyLabel(name, row)
            layout.addWidget(new_label, 4)
            box.stateChanged.connect(self._refresh)
            self._list.insertWidget(self._list.count() - 1, row)
            self._rows.append((item_id, name, box, new_label))

    def _rule(self) -> RenameRule:
        return RenameRule(
            mode=self.mode_box.currentData() or "replace",
            find=self.find_edit.text(),
            replace=self.replace_edit.text(),
            base=self.base_edit.text().strip(),
            numbered=self.numbered_box.isChecked(),
            start=self.start_edit.value(),
            width=self.width_edit.value(),
            style=self.style_box.currentData() or "number",
            text=self.text_edit.text(),
            position=self.position_edit.value(),
            length=self.length_edit.value(),
        )

    def _on_mode_changed(self, *_args) -> None:
        mode = self.mode_box.currentData() or "replace"
        for index in range(self._params.count()):
            widget = self._params.itemAt(index).widget()
            if widget is None:
                continue
            modes = (widget.property("renameModes") or "").split(",")
            widget.setVisible(mode in modes)
        self._refresh()

    # ------------------------------------------------------------------ 结果
    def plan_names(self) -> list[str]:
        """每行的最终名称，顺序与传入的 entries 一致。"""
        names = [name for _item_id, name in self._entries]
        selected = [row[2].isChecked() for row in self._rows]
        return build_plan(names, self._rule(), selected, self._reserved)

    def renames(self) -> list[tuple[int, str]]:
        """(数据 id, 新的显示名称主干)：只含勾选了且确实变了的行。"""
        plan = self.plan_names()
        result: list[tuple[int, str]] = []
        for (item_id, name, box, _label), new_name in zip(self._rows, plan):
            if box.isChecked() and new_name != name:
                result.append((item_id, split_suffix(new_name)[0]))
        return result

    def _refresh(self, *_args) -> None:
        plan = self.plan_names()
        changes = 0
        for (item_id, name, box, label), new_name in zip(self._rows, plan):
            label.setText(new_name)
            label.setToolTip(new_name)
            if box.isChecked() and new_name != name:
                changes += 1
        self._summary.setText(
            f"本次将改名 {changes} 项" if changes else "还没有改动：先填好上面需要的参数"
        )
        self.yesButton.setEnabled(changes > 0)


class TriStateManagerDialog(MessageBoxBase):
    """标签 / 关键词快捷管理：三态复选框批量增减，输入框直接新建。"""

    #: 条目在界面上的叫法（「标签」「关键词」），子类覆盖。
    kind = "条目"

    def __init__(
        self,
        entries: list[tuple[str, Qt.CheckState]],
        parent: QWidget | None = None,
        count: int = 1,
        suffixes: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = {name: state for name, state in entries}
        self._suffixes = dict(suffixes or {})

        self.viewLayout.addWidget(SubtitleLabel(f"{self.kind}管理", self))
        self.viewLayout.addWidget(
            BodyLabel(
                f"勾选 = 所选 {count} 项数据全部拥有该{self.kind}，横杠 = 只有部分拥有，空 = 全都没有。"
                f"确认后按勾选状态批量应用；在下面输入新的{self.kind}可直接新建。",
                self,
            )
        )

        self.list = TriStateList(self)
        for name, state in entries:
            self.list.add_name(name, state, label=self._display(name))
        self.viewLayout.addWidget(self.list)

        add_row = QHBoxLayout()
        self.input = LineEdit(self)
        self.input.setPlaceholderText(f"输入新的{self.kind}后回车添加")
        self.input.returnPressed.connect(self._on_add)
        add_row.addWidget(self.input, 1)
        self.add_button = IconTextButton(FluentIcon.ADD, "添加", self)
        self.add_button.clicked.connect(self._on_add)
        add_row.addWidget(self.add_button)
        self.viewLayout.addLayout(add_row)

        self._hint = CaptionLabel("", self)
        self.viewLayout.addWidget(self._hint)
        self.list.changed.connect(lambda: self._hint.setText(""))

        self.yesButton.setText("应用")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(520)

    # ------------------------------------------------------------------ 内部
    def _display(self, name: str) -> str:
        return f"{name}{self._suffixes.get(name, '')}"

    def _on_add(self) -> None:
        name = self.input.text().strip()
        if not name:
            return
        self.input.clear()
        if self.list.has(name):
            if self.list.state_of(name) == Qt.CheckState.Checked:
                self._hint.setText(f"「{name}」已经是全选状态，无需重复添加")
                return
            self.list.set_state(name, Qt.CheckState.Checked)
            self._hint.setText(f"「{name}」已存在，已改成对所选数据全部添加")
            return
        self.list.add_name(name, Qt.CheckState.Checked)
        self._hint.setText(f"已新建「{name}」：确认后对所选数据全部添加")

    # ------------------------------------------------------------------ 结果
    def additions(self) -> list[str]:
        """需要新增到全部所选数据的条目。"""
        return [
            name
            for name, state in self.list.entries()
            if state == Qt.CheckState.Checked and self._initial.get(name) != Qt.CheckState.Checked
        ]

    def removals(self) -> list[str]:
        """需要从全部所选数据里移走的条目。"""
        return [
            name
            for name, state in self.list.entries()
            if state == Qt.CheckState.Unchecked
            and self._initial.get(name, Qt.CheckState.Unchecked) != Qt.CheckState.Unchecked
        ]


class TagManagerDialog(TriStateManagerDialog):
    """标签快捷管理：现有标签（含全局标签）与新建标签一起管。"""

    kind = "标签"


class KeywordManagerDialog(TriStateManagerDialog):
    """关键词快捷管理：所选数据现有词汇总，可批量加减或新建。"""

    kind = "关键词"


__all__ = [
    "BatchRenameDialog",
    "CategoryConflictDialog",
    "CategoryDeleteDialog",
    "CategoryMoveDialog",
    "CategoryPickerComboBox",
    "CategoryPickerDialog",
    "CategorySelectDialog",
    "DuplicateDialog",
    "ItemEditDialog",
    "KeywordManagerDialog",
    "TagManagerDialog",
    "TextInputDialog",
    "TriStateManagerDialog",
]
