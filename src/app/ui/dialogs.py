"""通用对话框：文本输入、数据项编辑。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
    SwitchButton,
    TextEdit,
    TreeWidget,
)

from ..db.models import DataItem
from .common import format_size
from .widgets.keyword_input import KeywordInput
from .widgets.tag_picker import TagPicker


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


class ItemEditDialog(MessageBoxBase):
    """编辑单个数据项的名称、分类、标签、关键词与隐藏状态。"""

    def __init__(
        self,
        item: DataItem,
        categories: list[tuple[int, str]],
        known_tags: list[str],
        global_tags: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item

        self.viewLayout.addWidget(SubtitleLabel("编辑数据项", self))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.name_edit = LineEdit(self)
        self.name_edit.setText(item.name)

        self.category_box = ComboBox(self)
        self.category_box.addItem("未分类")
        for category_id, label in categories:
            self.category_box.addItem(label, userData=category_id)
        for index in range(self.category_box.count()):
            if self.category_box.itemData(index) == item.category_id:
                self.category_box.setCurrentIndex(index)
                break

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
        keep_latest = PushButton("每组只保留最新", self)
        keep_latest.clicked.connect(self._check_all_but_first)
        clear_button = PushButton("全部取消勾选", self)
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


__all__ = ["DuplicateDialog", "ItemEditDialog", "TextInputDialog"]
