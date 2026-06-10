# 数据管理页面筛选器完善计划

## 概要

在 DataTabPage 的右侧创建筛选器区域，将类型筛选器从顶部移到右侧，并新增关键词筛选器和标签筛选器。每个筛选器都包含带补全功能的搜索框 + 复选框列表。

## 当前状态分析

### 当前布局结构
```
DataTabPage (QVBoxLayout)
  ├── 顶部栏 (QHBoxLayout): SegmentedWidget(表格/卡片/条目) + stretch
  ├── TypeFilterPanel (折叠式，水平排列的 CheckBox)
  └── QStackedWidget (视图堆栈，占满剩余空间)
```

### 关键数据
- `Data.keywords`: JSON 字段，存储关键词列表如 `["key1", "key2"]`
- `Data.tag`: JSON 字段，存储标签列表如 `["tag1", "tag2"]`
- `Tag` 模型：独立表，有 `id/name/desc` 字段，但当前未被使用
- `TypeFilterPanel`：当前内嵌在 `data_tab_page.py` 中，折叠式面板 + 水平排列 CheckBox

## 目标布局

```
DataTabPage (QHBoxLayout)
  ├── 左侧 (QVBoxLayout, stretch=1)
  │   ├── 顶部栏: SegmentedWidget(表格/卡片/条目)
  │   └── QStackedWidget (视图堆栈)
  └── 右侧筛选器区域 (QVBoxLayout, 固定宽度 ~240px)
      ├── 类型筛选器 (FilterPanel)
      │   ├── 折叠头部: 标题 + 折叠按钮 + 全选/全不选
      │   └── 折叠内容:
      │       ├── SearchLineEdit (带 QCompleter 补全)
      │       └── CheckBox 列表 (垂直排列，可滚动)
      ├── 关键词筛选器 (FilterPanel)
      │   ├── 折叠头部: 标题 + 折叠按钮 + 全选/全不选
      │   └── 折叠内容:
      │       ├── SearchLineEdit (带 QCompleter 补全)
      │       └── CheckBox 列表 (垂直排列，可滚动)
      └── 标签筛选器 (FilterPanel)
          ├── 折叠头部: 标题 + 折叠按钮 + 全选/全不选
          └── 折叠内容:
              ├── SearchLineEdit (带 QCompleter 补全)
              └── CheckBox 列表 (垂直排列，可滚动)
```

## 具体修改

### 1. 新建 `src/app/components/filter_panel.py` — 通用筛选器面板

提取 `TypeFilterPanel` 为通用组件 `FilterPanel`，支持：
- 折叠/展开（带动画）
- 头部：折叠按钮 + 标题 + 全选/全不选按钮
- 折叠内容区：
  - `SearchLineEdit` + `QCompleter`（搜索框，输入时过滤复选框可见性）
  - `QScrollArea` 内垂直排列的 `CheckBox` 列表
- 对外接口：
  - `set_items(items: dict)` — 设置选项 `{key: display_name}`，动态创建 CheckBox
  - `get_checked() -> set` — 获取当前勾选的 key 集合
  - `connect_changed(callback)` — 连接变更信号
  - `filter_checkboxes(text: str)` — 搜索框过滤复选框可见性

**设计原理**：将 TypeFilterPanel 的折叠逻辑 + 搜索框 + 复选框列表抽象为通用组件，三种筛选器复用同一组件。

### 2. 修改 `src/app/components/data_tab_page.py` — 重构布局和筛选逻辑

#### 2.1 布局变更
- 根布局从 `QVBoxLayout` 改为 `QHBoxLayout`
- 左侧：`QVBoxLayout`（顶部栏 + 视图堆栈）
- 右侧：`QVBoxLayout`（三个 FilterPanel）

#### 2.2 删除 `TypeFilterPanel` 类
- 该类逻辑已移入通用 `FilterPanel`

#### 2.3 新增三个筛选器
```python
# 类型筛选器
self.typeFilter = FilterPanel("类型筛选", self)
self.typeFilter.set_items({dt: name for dt, name, _ in DATA_TYPE_INFO})

# 关键词筛选器
self.keywordFilter = FilterPanel("关键词筛选", self)
# setData 时动态更新

# 标签筛选器
self.tagFilter = FilterPanel("标签筛选", self)
# setData 时从数据库加载 Tag 表
```

#### 2.4 筛选逻辑变更
`_applyFilter()` 改为三重筛选的交集：
```python
def _applyFilter(self):
    result = self._all_data

    # 类型筛选
    checked_types = self.typeFilter.get_checked()
    if 0 < len(checked_types) < len(DATA_TYPE_INFO):
        result = [d for d in result if _normalize_type(d.type) in checked_types]

    # 关键词筛选
    checked_keywords = self.keywordFilter.get_checked()
    if checked_keywords:
        result = [d for d in result
                  if set(_parse_json_list(d.keywords)) & checked_keywords]

    # 标签筛选
    checked_tags = self.tagFilter.get_checked()
    if checked_tags:
        result = [d for d in result
                  if set(_parse_json_list(d.tag)) & checked_tags]

    self._filtered_data = result
    self._refreshView()
```

#### 2.5 setData 时更新筛选器选项
```python
def setData(self, data_list):
    self._all_data = data_list
    self._updateFilterOptions()
    self._applyFilter()

def _updateFilterOptions(self):
    # 关键词：统计当前数据中所有关键词
    keyword_set = {}
    for d in self._all_data:
        for kw in _parse_json_list(d.keywords):
            if kw not in keyword_set:
                keyword_set[kw] = kw
    self.keywordFilter.set_items(keyword_set)

    # 标签：从数据库加载
    session = get_session()()
    try:
        tags = session.query(Tag).all()
        self.tagFilter.set_items({t.name: t.name for t in tags})
    finally:
        session.close()
```

### 3. 修改 `src/app/components/__init__.py` — 导出新组件

添加 `FilterPanel` 的导出。

### 4. 新增 QSS 文件 — 筛选器面板样式

新增 `filter_panel.qss`（dark + light 两份），注册到 `resource.qrc` 和 `StyleSheet` 枚举。

### 5. 修改 `src/app/common/style_sheet.py`

添加 `FILTER_PANEL = "filter_panel"` 枚举值。

### 6. 修改 `src/app/resource/resource.qrc`

添加 dark/light 的 `filter_panel.qss` 引用。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/app/components/filter_panel.py` | 新建 | 通用筛选器面板组件 |
| `src/app/components/data_tab_page.py` | 修改 | 重构布局、删除 TypeFilterPanel、新增三筛选器 |
| `src/app/components/__init__.py` | 修改 | 导出 FilterPanel |
| `src/app/common/style_sheet.py` | 修改 | 添加 FILTER_PANEL 枚举 |
| `src/app/resource/qss/dark/filter_panel.qss` | 新建 | 暗色主题样式 |
| `src/app/resource/qss/light/filter_panel.qss` | 新建 | 亮色主题样式 |
| `src/app/resource/resource.qrc` | 修改 | 注册新 QSS 文件 |

## 假设与决策

1. **标签筛选器数据来源**：从 `Tag` 数据库表获取所有标签（而非从 Data.tag JSON 字段统计），因为 Tag 表是标签的权威来源。如果 Tag 表为空，则回退到从 Data.tag 统计。
2. **关键词筛选器数据来源**：从当前展示数据的 `keywords` JSON 字段统计，因为关键词没有独立表。
3. **筛选逻辑**：三个筛选器取交集（AND 逻辑），每个筛选器内部复选框取并集（OR 逻辑）。
4. **搜索框行为**：搜索框仅过滤复选框的可见性，不影响筛选结果。输入文字时，不匹配的复选框隐藏，匹配的显示。
5. **筛选器宽度**：右侧筛选器区域固定宽度 240px，内容区使用 QScrollArea 支持滚动。
6. **FilterPanel 不使用 ExpandSettingCard**：因为 ExpandSettingCard 是为设置页面设计的，过于重量级。自定义的折叠面板更轻量，更符合筛选器场景。

## 验证步骤

1. 启动应用，进入数据管理页面
2. 验证右侧出现三个筛选器面板
3. 点击折叠按钮，验证展开/折叠动画
4. 展开类型筛选器，验证复选框与之前行为一致
5. 展开关键词筛选器，验证复选框列表来自数据关键词
6. 展开标签筛选器，验证复选框列表来自 Tag 表
7. 在搜索框输入文字，验证复选框过滤功能
8. 勾选/取消复选框，验证数据筛选结果正确
9. 多个筛选器组合使用，验证 AND 逻辑
10. 切换 Tab 页，验证每个 Tab 独立筛选
