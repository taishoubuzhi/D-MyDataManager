"""用户管理页：默认用户（管理员）管理其他用户，普通用户只能修改自己。"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
)

from ...core.signals import signalBus
from ...db import database
from ...services import LibraryService, UserService
from ..common import confirm, toast_success, toast_warning
from ..dialogs import TextInputDialog


class UserPage(QWidget):
    """用户管理：默认用户可以管理其他用户，其他用户只能修改自己。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("userPage")
        self.session = database.new_session()
        self.service = UserService(self.session)
        self._user_id = 0
        self._is_admin = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("用户", self))
        header.addStretch(1)
        self.create_button = PrimaryPushButton(FluentIcon.ADD, "新建用户", self)
        self.create_button.clicked.connect(self._create_user)
        header.addWidget(self.create_button)
        root.addLayout(header)

        self.caption = CaptionLabel("", self)
        root.addWidget(self.caption)

        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)
        card_layout.addWidget(StrongBodyLabel("用户列表", card))
        self.row_layout = QVBoxLayout()
        self.row_layout.setContentsMargins(0, 0, 0, 0)
        self.row_layout.setSpacing(6)
        card_layout.addLayout(self.row_layout)
        root.addWidget(card)
        root.addStretch(1)

        signalBus.userChanged.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ 列表
    def refresh(self) -> None:
        self._user_id = self.service.current_id()
        self._is_admin = self.service.is_admin()
        self.create_button.setVisible(self._is_admin)
        self.caption.setText(
            "默认用户可以新建、重命名、删除任意用户，并为任意用户设置或清除口令；"
            "被删除用户的数据与标签会并入默认用户。"
            if self._is_admin
            else "当前用户可以修改自己的用户名与口令（口令用于解锁隐藏数据），"
            "也可以清除自己的口令或删除自己——数据与标签会并入默认用户。"
        )
        while self.row_layout.count():
            widget = self.row_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for info in self.service.list_users():
            self.row_layout.addWidget(self._user_row(info))

    def _user_row(self, info) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        mine = info.user.id == self._user_id
        marks = []
        if mine:
            marks.append("当前用户")
        if info.is_default:
            marks.append("默认用户")
        if info.protected:
            marks.append("已设口令")
        title = info.name + (f"（{'、'.join(marks)}）" if marks else "")
        layout.addWidget(
            StrongBodyLabel(
                f"{title} · {info.item_count} 项数据 · {info.category_count} 个分类", row
            ),
            1,
        )

        if not mine:
            switch_button = PushButton("切换", row)
            switch_button.clicked.connect(lambda _=False, item=info: self._switch_user(item))
            layout.addWidget(switch_button)
        if self._is_admin or mine:
            rename_button = PushButton(FluentIcon.EDIT, "重命名", row)
            rename_button.clicked.connect(lambda _=False, item=info: self._rename_user(item))
            layout.addWidget(rename_button)
            password_button = PushButton(FluentIcon.FINGERPRINT, "口令", row)
            password_button.clicked.connect(lambda _=False, item=info: self._set_password(item))
            layout.addWidget(password_button)
            if info.protected:
                clear_password_button = PushButton(FluentIcon.BROOM, "清除口令", row)
                clear_password_button.clicked.connect(
                    lambda _=False, item=info: self._clear_password(item)
                )
                layout.addWidget(clear_password_button)
        if (self._is_admin or mine) and not info.is_default:
            delete_button = PushButton(FluentIcon.DELETE, "删除", row)
            delete_button.clicked.connect(lambda _=False, item=info: self._delete_user(item))
            layout.addWidget(delete_button)
        return row

    # ------------------------------------------------------------------ 操作
    def _ask(self, title: str, placeholder: str = "", hint: str = "", text: str = "") -> str | None:
        dialog = TextInputDialog(title, placeholder, text=text, parent=self.window(), hint=hint)
        return dialog.value() if dialog.exec() else None

    def _create_user(self) -> None:
        name = self._ask("新建用户", "用户名", "每个用户拥有独立的数据项与分类，互不可见")
        if name is None:
            return
        if not name:
            toast_warning(self, "名称不能为空", "")
            return
        user = self.service.create(name)
        if user is None:
            toast_warning(self, "无法创建", "已存在同名用户")
            return
        self.session.commit()
        signalBus.userChanged.emit()
        signalBus.librariesChanged.emit()
        toast_success(self, "已创建用户", name)

    def _switch_user(self, info) -> None:
        if info.protected:
            password = self._ask("切换用户", f"请输入 {info.name} 的口令", "留空以取消")
            if password is None or not self.service.verify(info.user, password):
                toast_warning(self, "口令错误", f"无法切换到 {info.name}")
                return
        self.service.set_current(info.user)
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已切换用户", info.name)

    def _rename_user(self, info) -> None:
        mine = info.user.id == self._user_id
        if not mine and not self._is_admin:
            toast_warning(self, "没有权限", "只有默认用户可以重命名其他用户")
            return
        name = self._ask("重命名用户", "新的用户名", text=info.name)
        if name is None:
            return
        old_name = info.name
        if not self.service.rename(info.user, name):
            toast_warning(self, "无法重命名", "名称为空或已存在同名用户")
            return
        LibraryService(self.session).rename_user_dir(info.user, old_name)
        self.session.commit()
        signalBus.userChanged.emit()
        signalBus.librariesChanged.emit()
        toast_success(self, "已重命名用户", f"{old_name} -> {info.user.name}")

    def _set_password(self, info) -> None:
        mine = info.user.id == self._user_id
        if not mine and not self._is_admin:
            toast_warning(self, "没有权限", "只有默认用户可以修改其他用户的口令")
            return
        if mine and info.protected:
            password = self._ask("验证当前口令", f"请输入 {info.name} 当前的口令", "留空以取消")
            if password is None or not self.service.verify(info.user, password):
                toast_warning(self, "口令错误", "未能修改口令")
                return
        value = self._ask("设置用户口令", f"{info.name} 的口令（留空以清除）", "切换用户时需要输入")
        if value is None:
            return
        self.service.set_password(info.user, value)
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已更新口令", info.name if value else f"已清除 {info.name} 的口令")

    def _clear_password(self, info) -> None:
        """清除口令：默认用户可以清除任何已设口令的用户，其他用户只能清除自己的。"""
        mine = info.user.id == self._user_id
        if not (self._is_admin or mine):
            toast_warning(self, "没有权限", "只有默认用户可以清除其他用户的口令")
            return
        if not info.protected:
            toast_warning(self, "无需清除", "该用户没有设置口令")
            return
        if not confirm(
            self,
            "清除口令",
            f"确定清除用户「{info.name}」的口令吗？\n"
            "清除后切换用户与解锁隐藏数据都不再需要口令。",
        ):
            return
        self.service.set_password(info.user, "")
        self.session.commit()
        signalBus.userChanged.emit()
        toast_success(self, "已清除口令", info.name)

    def _delete_user(self, info) -> None:
        if not (self._is_admin or info.user.id == self._user_id):
            toast_warning(self, "没有权限", "只能删除当前用户自己的账号")
            return
        if info.is_default:
            toast_warning(self, "无法删除", "默认用户不可删除")
            return
        if not confirm(
            self,
            "删除用户",
            f"确定删除用户「{info.name}」吗？\n"
            f"其分类会以「{info.name}」为一级分类并入默认用户，数据文件与标签一并转移。",
        ):
            return
        if not self.service.delete(info.user):
            toast_warning(self, "无法删除", "默认用户不可删除，或找不到可接收数据的目标用户")
            return
        self.session.commit()
        for signal in (
            signalBus.userChanged,
            signalBus.librariesChanged,
            signalBus.itemsChanged,
            signalBus.categoriesChanged,
            signalBus.archivesChanged,
        ):
            signal.emit()
        toast_success(self, "已删除用户", f"{info.name} 的数据已并入默认用户")


__all__ = ["UserPage"]
