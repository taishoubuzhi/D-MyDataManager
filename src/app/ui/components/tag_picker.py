"""标签选择控件：关键词输入 + 从已有标签中多选。"""

from __future__ import annotations

from functools import partial

from PyQt6.QtWidgets import QWidget
from qfluentwidgets import Action, CheckableMenu, FluentIcon, PushButton

from .keyword_input import KeywordInput
from ..framework import IconTextButton


class TagPicker(KeywordInput):
    """可手动输入，也可从已有标签中勾选（支持多选）的标签控件。"""

    def __init__(
        self,
        known_tags: list[str] | None = None,
        placeholder: str = "输入标签后回车，或点右侧按钮选择已有标签",
        parent: QWidget | None = None,
        global_tags: set[str] | None = None,
    ) -> None:
        super().__init__(placeholder, parent)
        self._known: list[str] = []
        self._global: set[str] = set()
        self._button = IconTextButton(FluentIcon.TAG, "选择已有标签", self)
        self._button.clicked.connect(self._show_menu)
        self.add_trailing_widget(self._button)
        self.set_known_tags(known_tags or [], global_tags=global_tags)

    # ------------------------------------------------------------------ API
    def known_tags(self) -> list[str]:
        return list(self._known)

    def set_known_tags(self, tags, global_tags: set[str] | None = None) -> None:
        self._known = sorted({str(tag) for tag in tags or [] if str(tag).strip()})
        self._global = {str(tag) for tag in (global_tags or ())}
        self._button.setText(f"选择已有标签（{len(self._known)}）")
        self._button.setEnabled(bool(self._known))
        self._button.setToolTip("可多选，再次点击取消选择" if self._known else "标签库为空，可直接输入新标签")

    # --------------------------------------------------------------- 内部
    def _build_menu(self) -> CheckableMenu:
        menu = CheckableMenu(parent=self)
        for tag in self._known:
            label = f"{tag}（全局）" if tag in self._global else tag
            action = Action(label, menu)
            action.setCheckable(True)
            action.setChecked(tag in self._keywords)
            action.triggered.connect(partial(self._toggle, tag))
            menu.addAction(action)
        if self._known:
            menu.addSeparator()
            clear = Action("清空已选标签", menu)
            clear.triggered.connect(self._clear_selected)
            menu.addAction(clear)
        return menu

    def _show_menu(self) -> None:
        menu = self._build_menu()
        menu.exec(self._button.mapToGlobal(self._button.rect().bottomLeft()))

    def _toggle(self, tag: str, checked: bool = True) -> None:
        if checked:
            if tag in self._keywords:
                return
            self._append(tag)
            self.changed.emit()
        elif tag in self._keywords:
            self._remove(tag)

    def _clear_selected(self) -> None:
        remaining = [word for word in self._keywords if word not in self._known]
        if len(remaining) == len(self._keywords):
            return
        self._keywords = remaining
        self._rebuild()
        self.changed.emit()


__all__ = ["TagPicker"]
