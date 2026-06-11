# 修复 data_interface 及相关组件的主题适配问题

## 问题概述

在亮色主题下，筛选器面板区域仍然显示为黑色/深色，存在明显的主题不适配现象。需要修复所有与 `data_interface` 相关的组件，使其在亮色/暗色两套主题下都能正确显示。

## 当前状态分析

### 问题 1：`#nameSearchPanel` 无 QSS 样式定义

`NameSearchPanel` 设置了 `objectName='nameSearchPanel'`，但 `filter_panel.qss`（light/dark）中**没有任何 `#nameSearchPanel` 选择器的样式**。NameSearchPanel 完全依赖框架默认样式，在亮色主题下背景色不正确。

### 问题 2：`#filterArea` 无 QSS 样式定义

`data_tab_page.py` 中筛选器容器设置了 `objectName='filterArea'`，但 `data_tab_page.qss` 中**没有 `#filterArea` 选择器**。该容器没有显式背景色，可能继承到不正确的颜色。

### 问题 3：`DataCard.setSelected()` 使用 `setStyleSheet("")` 覆盖了 styleSheetManager 管理的 QSS

`data_card.py` 第 127-133 行：
- 选中时：`self.setStyleSheet(f'DataCard[isSelected="true"]...')` — 覆盖了 styleSheetManager 注入的 QSS
- 取消选中时：`self.setStyleSheet("")` — 清空了所有样式，导致 styleSheetManager 管理的主题 QSS 也被清除

这意味着：如果卡片被选中后取消选中，再切换主题，该卡片不会跟随主题变化。

### 问题 4：`#cardContainer` 无样式定义

卡片视图容器 `cardContainer` 没有在 QSS 中定义背景色，可能继承到不正确的颜色。

## 修复方案

### 修改 1：为 `#nameSearchPanel` 添加 QSS 样式

**文件**：`src/app/resource/qss/light/filter_panel.qss` 和 `src/app/resource/qss/dark/filter_panel.qss`

在 light 版本中添加：
```css
#nameSearchPanel {
    background-color: rgba(255, 255, 255, 200);
    border: 1px solid rgb(220, 220, 220);
    border-radius: 6px;
}
```

在 dark 版本中添加：
```css
#nameSearchPanel {
    background-color: rgba(255, 255, 255, 13);
    border: 1px solid rgb(46, 46, 46);
    border-radius: 6px;
}
```

### 修改 2：为 `#filterArea` 添加 QSS 样式

**文件**：`src/app/resource/qss/light/data_tab_page.qss` 和 `src/app/resource/qss/dark/data_tab_page.qss`

在 light 版本中添加：
```css
#filterArea {
    background-color: transparent;
}
```

在 dark 版本中添加：
```css
#filterArea {
    background-color: transparent;
}
```

### 修改 3：为 `#cardContainer` 添加 QSS 样式

**文件**：`src/app/resource/qss/light/data_tab_page.qss` 和 `src/app/resource/qss/dark/data_tab_page.qss`

在 light 版本中添加：
```css
#cardContainer {
    background-color: transparent;
}
```

在 dark 版本中添加：
```css
#cardContainer {
    background-color: transparent;
}
```

### 修改 4：修复 `DataCard.setSelected()` 的样式覆盖问题

**文件**：`src/app/components/data_card.py`

将 `setStyleSheet` 替换为使用 `setProperty` + `style().unpolish/polish` 的方式，避免覆盖 styleSheetManager 管理的 QSS。具体做法：

1. 在 `data_card.qss` 中添加 `DataCard[isSelected="true"]` 的样式规则
2. 在 `setSelected()` 中移除 `self.setStyleSheet(...)` 调用，仅保留 `setProperty` + `unpolish/polish`

**light/data_card.qss** 添加：
```css
DataCard[isSelected="true"] {
    border: 1px solid rgb(0, 120, 212);
}
DataCard[isSelected="true"]:hover {
    border: 1px solid rgb(0, 120, 212);
}
```

**dark/data_card.qss** 添加：
```css
DataCard[isSelected="true"] {
    border: 1px solid rgb(0, 102, 204);
}
DataCard[isSelected="true"]:hover {
    border: 1px solid rgb(0, 102, 204);
}
```

**data_card.py** 修改 `setSelected()` 方法：
```python
def setSelected(self, selected: bool):
    if self._selected == selected:
        return
    self._selected = selected
    self.setProperty('isSelected', selected)
    self._indicator.setVisible(selected)
    if selected:
        color = themeColor()
        self._indicator.setStyleSheet(
            f"background-color: {color.name()}; border-radius: 2px;"
        )
    else:
        self._indicator.setStyleSheet("")
    self.style().unpolish(self)
    self.style().polish(self)
    self._updateBackgroundColor()
```

移除了 `self.setStyleSheet(...)` 和 `self.setStyleSheet("")` 两行，改为依赖 QSS 中的属性选择器。

## 涉及文件清单

| 文件 | 修改内容 |
|------|----------|
| `src/app/resource/qss/light/filter_panel.qss` | 添加 `#nameSearchPanel` 样式 |
| `src/app/resource/qss/dark/filter_panel.qss` | 添加 `#nameSearchPanel` 样式 |
| `src/app/resource/qss/light/data_tab_page.qss` | 添加 `#filterArea`、`#cardContainer` 样式 |
| `src/app/resource/qss/dark/data_tab_page.qss` | 添加 `#filterArea`、`#cardContainer` 样式 |
| `src/app/resource/qss/light/data_card.qss` | 添加 `DataCard[isSelected="true"]` 样式 |
| `src/app/resource/qss/dark/data_card.qss` | 添加 `DataCard[isSelected="true"]` 样式 |
| `src/app/components/data_card.py` | 修复 `setSelected()` 方法，移除 `setStyleSheet` 覆盖 |

## 验证步骤

1. 启动应用，切换到亮色主题，检查筛选器面板（名称搜索、类型/关键词/标签筛选）背景色是否为浅色
2. 切换到暗色主题，检查筛选器面板背景色是否为深色
3. 在卡片模式下选中一个卡片，切换主题，检查卡片边框是否跟随主题变化
4. 取消卡片选中，切换主题，检查卡片样式是否正确恢复
5. 检查卡片容器和筛选器区域在两种主题下的背景色是否正确
