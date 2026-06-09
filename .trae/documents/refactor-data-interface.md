# 数据管理页面重构计划

## 概述

将 `DataInterface` 从 ComboBox 模式切换重构为 **TabBar + SegmentedWidget** 架构：
- **TabBar**：用户可动态新增/删除数据视图Tab，每个Tab独立筛选
- **SegmentedWidget**：替代 ComboBox，在每个Tab内切换表格/卡片/条目模式

## 当前状态分析

### 现有架构
```
DataInterface (ScrollArea)
├── topBar (QHBoxLayout)
│   ├── TitleLabel "数据管理" (已删除)
│   └── ComboBox (模式切换：表格/卡片/条目)
├── TableWidget (表格视图)
├── CardContainer + FlowLayout (卡片视图)
└── ListWidget (条目视图)
```

### 问题
- ComboBox 不符合 Fluent Design 规范，交互不够直观
- 只有一个固定视图，无法同时查看不同筛选条件的数据
- 无法保存/管理多个数据视图

## 目标架构

```
DataInterface (ScrollArea)
├── TabBar (可新增/关闭Tab)
├── QStackedWidget
│   ├── DataTabPage 1
│   │   ├── SegmentedWidget (表格/卡片/条目)
│   │   ├── FilterBar (类型筛选等)
│   │   └── QStackedWidget (三种视图)
│   ├── DataTabPage 2
│   │   ├── SegmentedWidget
│   │   ├── FilterBar
│   │   └── QStackedWidget
│   └── ...
```

## 文件变更清单

### 1. 新建 `src/app/components/data_tab_page.py`

**DataTabPage** — 单个数据视图Tab页，包含：
- `SegmentedWidget`：切换表格/卡片/条目三种模式
- `FilterBar`：类型筛选（ComboBox，可选 ALL/IMAGE/VIDEO/AUDIO/DOC/TEXT 等）
- `QStackedWidget`：管理三种视图组件
- `TableWidget`：表格视图（复用现有 `_COLUMNS` 逻辑）
- `CardContainer + FlowLayout`：卡片视图
- `ListWidget`：条目视图

关键属性：
- `self._all_data`：全量数据（引用，不拷贝）
- `self._filtered_data`：筛选后数据
- `self.currentMode`：当前显示模式
- `self.filterType`：当前筛选类型（None=全部）

关键方法：
- `setData(data_list)`：设置全量数据并触发筛选
- `applyFilter(filter_type)`：按类型筛选数据
- `refreshView()`：刷新当前视图
- `currentMode` 属性：获取/设置当前模式

### 2. 修改 `src/app/view/data_interface.py`

**DataInterface** 重构为 TabBar 管理器：
- 移除 `ComboBox`、`topBar`、三个视图组件
- 新增 `TabBar` + `QStackedWidget`
- 默认创建一个"全部数据"Tab
- TabBar 设置 `setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ON_HOVER)`
- TabBar 设置 `tabAddRequested` 信号 → 新增Tab
- TabBar 设置 `tabCloseRequested` 信号 → 关闭Tab
- `__loadData()` 后将数据分发给所有 Tab

关键逻辑：
```python
class DataInterface(ScrollArea):
    def __init__(self):
        self.tabBar = TabBar(self)
        self.stackedWidget = QStackedWidget(self)
        self.tabPages = {}  # routeKey -> DataTabPage
        self._tabCount = 0
        
    def _addTab(self, title="数据视图", filter_type=None):
        """新增Tab页"""
        self._tabCount += 1
        routeKey = f"tab_{self._tabCount}"
        page = DataTabPage(self)
        page.setData(self._data_list)
        page.applyFilter(filter_type)
        
        self.tabPages[routeKey] = page
        self.stackedWidget.addWidget(page)
        self.tabBar.addTab(routeKey, title, onClick=lambda: self.stackedWidget.setCurrentWidget(page))
        self.stackedWidget.setCurrentWidget(page)
        self.tabBar.setCurrentTab(routeKey)
    
    def _removeTab(self, index):
        """关闭Tab页"""
        item = self.tabBar.tabItem(index)
        routeKey = item.routeKey()
        page = self.tabPages.pop(routeKey)
        self.stackedWidget.removeWidget(page)
        self.tabBar.removeTab(index)
        page.deleteLater()
```

### 3. 修改 `src/app/components/data_card.py`

无需修改，DataCard/DataListCard/TagChip/TagContainer 保持不变。

### 4. 修改 `src/app/common/style_sheet.py`

新增枚举项：
- `DATA_TAB_PAGE = "data_tab_page"` — Tab页样式

### 5. 新建 `src/app/resource/qss/dark/data_tab_page.qss` 和 `src/app/resource/qss/light/data_tab_page.qss`

Tab页内部样式（FilterBar等）。

### 6. 修改 `src/app/resource/resource.qrc`

添加 `qss/dark/data_tab_page.qss` 和 `qss/light/data_tab_page.qss`。

### 7. 修改 `src/app/resource/qss/dark/data_interface.qss` 和 `src/app/resource/qss/light/data_interface.qss`

可能需要微调以适配 TabBar 布局。

## 实施步骤

1. 新建 `data_tab_page.py`，实现 DataTabPage 组件
2. 新建 QSS 样式文件
3. 更新 `style_sheet.py` 枚举
4. 更新 `resource.qrc`
5. 重写 `data_interface.py`，集成 TabBar + QStackedWidget
6. 验证运行

## 设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 模式切换组件 | SegmentedWidget | 比 Pivot 更紧凑，适合Tab内的子导航 |
| Tab管理组件 | TabBar（非 TabWidget） | 需要自定义Tab内容（DataTabPage），TabWidget 封装过重 |
| 筛选实现 | 每个Tab持有全量数据引用+独立筛选状态 | 避免数据拷贝，筛选轻量 |
| 默认Tab | 创建1个"全部数据"Tab | 用户首次进入即可看到数据 |
| Tab关闭 | 至少保留1个Tab | 避免无Tab的空状态 |
| Tab新增 | 弹出简单输入框让用户命名 | 参考Gallery示例的addTab模式 |

## 验证步骤

1. 启动程序，确认默认有1个"全部数据"Tab
2. 点击TabBar的"+"按钮，新增Tab，确认新Tab显示数据
3. 在不同Tab中切换 SegmentedWidget 模式，确认视图正确切换
4. 在不同Tab中设置不同筛选条件，确认数据独立筛选
5. 关闭Tab，确认Tab和对应页面正确移除
6. 关闭到只剩1个Tab时，确认无法继续关闭
7. 切换主题，确认样式正确
