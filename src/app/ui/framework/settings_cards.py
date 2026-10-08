"""设置卡片：范围配置项同时给滑块和输入框。

qfluentwidgets 的 `RangeSettingCard` 只有滑块，想设「保留 37 个存档」这种具体数值
只能靠拖动对齐。`NumberSettingCard` 在滑块左边补一个输入框，两边双向同步；
当前值不等于该配置项的推荐值（`configItem.defaultValue`）时，还会露出「恢复推荐值」按钮。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import PushButton, PushSettingCard, RangeSettingCard, SpinBox

from .feedback import release_widget

__all__ = ["ActionCard", "SPIN_WIDTH", "NumberSettingCard"]

SPIN_WIDTH = 96


class ActionCard(PushSettingCard):
    """设置卡上的操作按钮：换成 Fluent PushButton，避免全站混入原生 QPushButton。

    `PushSettingCard` 内部建的是原生 `QPushButton`（自检 `style_uniformity` 会拦），
    所以这里把它换掉；按钮文字与点击信号仍按上游的用法走。
    """

    def __init__(self, text, icon, title: str, content: str, parent=None) -> None:
        super().__init__(text, icon, title, content, parent)
        index = self.hBoxLayout.indexOf(self.button)
        release_widget(self.button)
        self.button = PushButton(text, self)
        self.hBoxLayout.insertWidget(index, self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.button.clicked.connect(self.clicked)


class NumberSettingCard(RangeSettingCard):
    """范围配置卡：输入框 + 滑块 + 「恢复推荐值」，改哪个都会写回配置项。"""

    def __init__(self, configItem, icon, title, content=None, parent=None) -> None:
        super().__init__(configItem, icon, title, content, parent)

        self.spin = SpinBox(self)
        self.spin.setRange(*configItem.range)
        self.spin.setValue(int(configItem.value))
        self.spin.setMinimumWidth(SPIN_WIDTH)

        # 数值改由输入框承担，原来那个只读数字标签就多余了
        index = self.hBoxLayout.indexOf(self.slider)
        self.hBoxLayout.insertWidget(index, self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.insertSpacing(index + 1, 6)
        self.valueLabel.setVisible(False)

        self.reset_button = PushButton("恢复推荐值", self)
        self.reset_button.setToolTip("回到该配置项的推荐值")
        self.reset_button.clicked.connect(self.reset_to_default)
        self.hBoxLayout.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(6)

        self.spin.valueChanged.connect(self.setValue)
        self.configItem.valueChanged.connect(self._sync_reset_button)
        self._sync_reset_button()

    def setValue(self, value) -> None:
        super().setValue(int(value))
        if self.spin.value() != int(value):
            self.spin.setValue(int(value))

    def reset_to_default(self) -> None:
        """回到该配置项的推荐值（`ConfigItem.defaultValue`）。"""
        default = self.configItem.defaultValue
        if default is not None:
            self.setValue(int(default))

    def _sync_reset_button(self, *_args) -> None:
        """当前值 == 推荐值时按钮没有意义，直接藏起来。"""
        default = self.configItem.defaultValue
        visible = default is not None and int(self.configItem.value) != int(default)
        self.reset_button.setVisible(visible)
