"""设置卡片：范围配置项同时给滑块和输入框。

qfluentwidgets 的 `RangeSettingCard` 只有滑块，想设「保留 37 个存档」这种具体数值
只能靠拖动对齐。`NumberSettingCard` 在滑块左边补一个输入框，两边双向同步。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import RangeSettingCard, SpinBox

__all__ = ["SPIN_WIDTH", "NumberSettingCard"]

SPIN_WIDTH = 96


class NumberSettingCard(RangeSettingCard):
    """范围配置卡：输入框 + 滑块，改哪个都会写回配置项。"""

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

        self.spin.valueChanged.connect(self.setValue)

    def setValue(self, value) -> None:
        super().setValue(int(value))
        if self.spin.value() != int(value):
            self.spin.setValue(int(value))
