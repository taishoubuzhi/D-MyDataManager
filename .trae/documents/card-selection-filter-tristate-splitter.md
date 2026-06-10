# 数据卡片选中效果 + 筛选器三态复选框 + 可拖拽分割布局

## 概要

三个独立改进：
1. 卡片模式选中效果改为左侧主题色竖条 + 点击空白取消选中
2. 筛选器全选/全不选按钮替换为三态复选框，移入折叠区域
3. 数据区与筛选区之间使用 QSplitter 可拖拽调节

## 当前状态分析

### 卡片选中效果
- `DataCard` 通过 `isSelected` 属性 + QSS `border: 2px solid rgb(0, 120, 212)` 实现选中
- `_normalBackgroundColor()` / `_hoverBackgroundColor()` 返回不同背景色
- 点击卡片切换选中，再次点击取消选中
- **问题**：点击空白区域不会取消选中；选中效果是边框变色，没有左侧竖条

### 筛选器全选/全不选
- `FilterPanel` 头部有两个 `TransparentPushButton`（"全选"、"全不选"）
- 位于折叠头部（header），不在折叠内容区（content）内

### 布局
- `DataTabPage` 使用 `QHBoxLayout`，左侧 stretch=1，右侧 `setFixedWidth(240)`
- **问题**：用户无法拖拽调节左右区域宽度

## 具体修改

### 1. 修改 `data_card.py` — 卡片选中效果

#### 1.1 添加左侧竖条
在 `_build_ui()` 中，在主布局最左侧插入一个 `QFrame` 作为选中指示条：
- 宽度 4px，默认隐藏（`setVisible(False)`）
- 选中时显示，背景色为主题色
- 圆角与卡片左侧对齐

```python
# 选中指示条
self._indicator = QFrame(self)
self._indicator.setObjectName('selectionIndicator')
self._indicator.setFixedWidth(4)
self._indicator.setVisible(False)
self.hBoxLayout.insertWidget(0, self._indicator)
```

#### 1.2 修改 `setSelected()`
选中时显示指示条，取消时隐藏：
```python
def setSelected(self, selected: bool):
    if self._selected == selected:
        return
    self._selected = selected
    self.setProperty('isSelected', selected)
    self._indicator.setVisible(selected)
    self.style().unpolish(self)
    self.style().polish(self)
    self._updateBackgroundColor()
```

#### 1.3 修改 QSS
- 选中时去掉边框高亮（改为左侧竖条），保留背景色变化
- 新增 `#selectionIndicator` 样式

dark:
```css
DataCard[isSelected="true"] {
    border: 1px solid rgb(0, 102, 204);
    background-color: rgba(0, 60, 120, 0.6);
}
DataCard[isSelected="true"]:hover {
    background-color: rgba(0, 70, 140, 0.7);
}
#selectionIndicator {
    background-color: rgb(0, 102, 204);
    border-radius: 2px;
}
```

light:
```css
DataCard[isSelected="true"] {
    border: 1px solid rgb(0, 120, 212);
    background-color: rgba(230, 243, 255, 0.98);
}
DataCard[isSelected="true"]:hover {
    background-color: rgba(220, 238, 255, 0.98);
}
#selectionIndicator {
    background-color: rgb(0, 120, 212);
    border-radius: 2px;
}
```

### 2. 修改 `data_tab_page.py` — 点击空白取消选中 + QSplitter

#### 2.1 点击空白取消选中
在 `_setup_ui()` 中为 `cardContainer` 安装事件过滤器，监听鼠标按下事件：
- 如果点击位置不在任何 DataCard 上，则取消当前选中卡片

```python
self.cardContainer.mousePressEvent = self._onCardContainerClicked

def _onCardContainerClicked(self, event):
    """点击卡片容器空白区域取消选中"""
    if self._selected_card:
        self._selected_card.setSelected(False)
        self._selected_card = None
```

#### 2.2 使用 QSplitter 替代 QHBoxLayout
将根布局从 `QHBoxLayout` 改为 `QSplitter`：
- 左侧：数据展示区（初始占大部分空间）
- 右侧：筛选器区域（初始 240px，最小 180px）
- 设置 `setStretchFactor(0, 1)` 让左侧自动拉伸
- 设置初始大小比例 `setSizes([width-240, 240])`

```python
from PyQt6.QtWidgets import QSplitter

self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
self._splitter.setChildrenCollapsible(False)
self._splitter.addWidget(left_widget)
self._splitter.addWidget(filter_widget)
self._splitter.setStretchFactor(0, 1)
self._splitter.setStretchFactor(1, 0)
root_layout.addWidget(self._splitter)
```

注意：`filter_widget` 不再使用 `setFixedWidth(240)`，改为 `setMinimumWidth(180)` + `setMaximumWidth(360)`，由 splitter 控制实际宽度。

### 3. 修改 `filter_panel.py` — 三态复选框替换全选/全不选按钮

#### 3.1 移除头部的全选/全不选按钮
从 `_header` 中删除 `_selectAllBtn` 和 `_deselectAllBtn`。

#### 3.2 在折叠内容区顶部添加三态复选框
在搜索框之前插入一个三态 `CheckBox`：
```python
self._triStateCb = CheckBox("全选/全不选", self._content)
self._triStateCb.setTristate(True)
self._triStateCb.stateChanged.connect(self._onTriStateChanged)
content_layout.addWidget(self._triStateCb)
```

#### 3.3 三态逻辑
- **勾选（Checked）**→ 全选所有可见复选框
- **未勾选（Unchecked）**→ 全不选所有可见复选框
- **半选（PartiallyChecked）**→ 不改变其他复选框状态（仅作为显示状态）

当子复选框状态变化时，同步更新三态复选框：
```python
def _syncTriState(self):
    """根据子复选框状态同步三态复选框"""
    visible_cbs = [cb for cb in self._checkboxes.values() if cb.isVisible()]
    if not visible_cbs:
        return
    checked_count = sum(1 for cb in visible_cbs if cb.isChecked())
    if checked_count == 0:
        self._triStateCb.setCheckState(Qt.CheckState.Unchecked)
    elif checked_count == len(visible_cbs):
        self._triStateCb.setCheckState(Qt.CheckState.Checked)
    else:
        self._triStateCb.setCheckState(Qt.CheckState.PartiallyChecked)
```

关键：三态复选框的 `stateChanged` 信号需要区分是用户点击还是程序设置，避免循环触发。使用 `_updating` 标志位：
```python
def _onTriStateChanged(self, state):
    if self._updating:
        return
    self._updating = True
    if state == Qt.CheckState.Checked.value:
        self.selectAll()
    elif state == Qt.CheckState.Unchecked.value:
        self.deselectAll()
    # PartiallyChecked 时不改变子复选框
    self._updating = False
```

#### 3.4 子复选框变化时同步三态
在 `connect_changed` 的回调链中添加 `_syncTriState` 调用。每个子复选框的 `stateChanged` 信号连接到 `_syncTriState`。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/app/components/data_card.py` | 修改 | 添加选中指示条，修改 setSelected |
| `src/app/components/data_tab_page.py` | 修改 | QSplitter 布局 + 点击空白取消选中 |
| `src/app/components/filter_panel.py` | 修改 | 三态复选框替换全选/全不选按钮 |
| `src/app/resource/qss/dark/data_card.qss` | 修改 | 选中效果改为左侧竖条 |
| `src/app/resource/qss/light/data_card.qss` | 修改 | 选中效果改为左侧竖条 |

## 验证步骤

1. 启动应用，进入数据管理页面，切换到卡片模式
2. 点击一张卡片 → 左侧出现主题色竖条，背景色变化
3. 再次点击同一卡片 → 取消选中，竖条消失
4. 点击一张卡片选中后，点击卡片容器空白区域 → 取消选中
5. 展开类型筛选器 → 顶部出现三态复选框（默认全选=勾选状态）
6. 取消一个类型复选框 → 三态复选框变为半选（横线）
7. 点击三态复选框取消全选 → 所有类型复选框取消勾选
8. 点击三态复选框全选 → 所有类型复选框勾选
9. 拖拽数据区与筛选区之间的分割线 → 左右区域宽度跟随变化
10. 筛选区最小宽度 180px，最大宽度 360px
