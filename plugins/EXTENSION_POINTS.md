# 扩展点与事件

这份文档列出程序**对外开放的扩展点**（插件往这里放东西）和**广播的事件**（插件订阅），
以及每个扩展点的值结构、生效位置与稳定级别。

写插件的教程见根目录 [`../PLUGIN.md`](../PLUGIN.md)；清单字段与依赖规则见 [`PLUGIN_PROTOCOL.md`](PLUGIN_PROTOCOL.md)。

```python
from app.sdk import Events, ExtensionPoint

ctx.contribute(ExtensionPoint.HOME_KPI, {...}, key="my.kpi", order=100, description="我的卡片")
ctx.on(Events.ITEM_IMPORTED, self._on_imported)     # 处理函数签名要收 **payload
```

- 扩展点名字都是**字符串常量**（`ExtensionPoint` 的类属性），事件名同理（`Events`）。
- 贡献与订阅都记在插件名下；插件被禁用 / 卸载时程序**自动撤销**，插件自己不用清理（`Plugin.teardown()` 只用于自己的额外资源）。
- 贡献的排序键是 `(order, plugin_id, key)`：`order` 小的在前（默认 `100`），同 `order` 时内置插件按 id 排。

## 1. 扩展点总览

| 扩展点 | 标签 | 值结构 | 生效位置 | 稳定级别 |
| --- | --- | --- | --- | --- |
| `app.viewer` | 打开方式 | 由 `ctx.add_viewer()` 构造 | 「打开方式」页、条目打开流程 | 稳定 |
| `app.ui.page` | 页面 | 由 `ctx.add_page()` 构造 | 主窗口左侧导航 + 堆叠页 | 稳定 |
| `app.ui.manage.toolbar` | 数据管理工具栏 | `{"text", "callback", "icon", "tip"}` | 数据管理页工具栏 | 稳定 |
| `app.ui.manage.item_menu` | 条目菜单 | `{"text", "callback", "icon"}` | 数据行右键菜单 | 稳定 |
| `app.ui.detail.panel` | 详情面板 | `{"title", "lines"}` | 详情弹窗底部 | 稳定 |
| `app.ui.import.filter` | 导入筛选 | `{"name", "accept"}` | 导入页扫描文件时 | 稳定 |
| `app.ui.home.kpi` | 概览卡片 | `{"title", "value", "sub", "icon"}` | 概览页统计卡 | 稳定 |
| `app.ui.settings.card` | 设置卡片 | `{"title", "factory"}` | 设置页「插件」分组 | 稳定 |
| `app.data.import.hook` | 导入钩子 | 待定 | —— | **预留**（程序侧尚未接线） |
| `app.item.open.resolver` | 打开解析器 | 待定 | —— | **预留**（程序侧尚未接线） |

「稳定」= 从 SDK 1.0 起可用，值结构变更会当作不兼容改动；「预留」= 名字已冻结、程序侧还没接线，插件现在贡献它不会有任何效果。

## 2. 界面扩展点

界面扩展点由 `src/app/ui/framework/contributions.py` 统一读取（`items(point)` 取贡献、`title_of` / `value_of` / `icon_of` 取字段、`resolve(callback, *args)` 安全调用回调、`text_of(value)` 把「字符串或回调」统一成文本）：

- 所有 `callback` / `accept` / `lines` / `factory` 都是**可调用对象**，由程序在界面线程调用；
- 回调抛异常只记日志，不会让界面崩（`resolve()` 捕获后返回 `None`）；
- `icon` 是 `FluentIcon` 的成员名（如 `"HEART"`、`"SYNC"`），认不出来时回退 `FluentIcon.APPLICATION`；
- `title` 必填，`description` 是可选说明（显示在插件页的贡献列表里）。

### 2.1 `app.ui.home.kpi`：概览卡片

```python
ctx.contribute(
    ExtensionPoint.HOME_KPI,
    {"title": "示例计数", "value": self._kpi_text, "sub": self._kpi_sub, "icon": "HEART"},
    key="example.kpi",
    description="示例插件贡献的概览卡片",
)
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | 字符串 | 卡片标题 |
| `value` | 字符串或 `-> str` 回调 | 卡片主数值，每次刷新时重新取值 |
| `sub` | 字符串或 `-> str` 回调 | 卡片副标题（小字） |
| `icon` | 字符串 | `FluentIcon` 成员名 |

程序在概览页 `refresh()` 时重建这些卡片（`home_page._rebuild_plugin_cards()`），并在 `itemsChanged` / `pluginsChanged` 后自动刷新。

### 2.2 `app.ui.manage.toolbar`：数据管理工具栏按钮

```python
ctx.contribute(
    ExtensionPoint.MANAGE_TOOLBAR,
    {"text": "示例动作", "callback": self._on_toolbar, "icon": "HEART", "tip": "示例插件贡献的工具栏按钮"},
    key="example.toolbar",
)
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 字符串 | 按钮文字 |
| `callback` | `() -> None` | 点击时调用，不带参数 |
| `icon` | 字符串 | 可选 |
| `tip` | 字符串 | 可选，悬停提示；缺省时用贡献的 `description` |

按钮加在工具栏已有按钮之后（流式布局，窄窗口自动换行）。

### 2.3 `app.ui.manage.item_menu`：条目右键菜单项

```python
ctx.contribute(
    ExtensionPoint.MANAGE_ITEM_MENU,
    {"text": "示例菜单项", "callback": self._on_menu, "icon": "HEART"},
    key="example.menu",
)
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 字符串 | 菜单项文字 |
| `callback` | `(item) -> None` | 调用时收到该行对应的数据项对象 |
| `icon` | 字符串 | 可选 |

菜单项加在条目菜单末尾（前面自动补分隔符）。`item` 是域对象，可读 `item.id` / `item.name` / `item.file_path` 等。

### 2.4 `app.ui.detail.panel`：详情弹窗里的一段文本

```python
ctx.contribute(
    ExtensionPoint.DETAIL_PANEL,
    {"title": "示例信息", "lines": self._detail_lines},
    key="example.detail",
)

def _detail_lines(self, item):
    return [f"示例信息：{item.name}"]
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | 字符串 | 段落标题（每行以前缀形式出现） |
| `lines` | `(item) -> Iterable[str]` | 返回若干行文本；返回字符串时整段作为一行 |

### 2.5 `app.ui.import.filter`：导入筛选器

```python
ctx.contribute(
    ExtensionPoint.IMPORT_FILTER,
    {"name": "跳过 .tmp 文件", "accept": _accept},
    key="example.tmp",
)

def _accept(path):
    return path.suffix.lower() != ".tmp"
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | 字符串 | 筛选器名称（插件页展示） |
| `accept` | `(Path) -> bool` | 返回 `False` 的文件不会进入导入队列 |

- 程序在导入页扫描文件时逐个询问（`import_page._collect_sources()`），**所有**筛选器都通过才会导入。
- 多个筛选器之间是「与」；回调抛异常时按「通过」处理，避免插件把导入卡死。

### 2.6 `app.ui.settings.card`：设置页卡片

```python
ctx.contribute(
    ExtensionPoint.SETTINGS_CARD,
    {"title": "示例设置", "factory": self._settings_card},
    key="example.settings",
)

def _settings_card(self, parent):
    return SettingCard(FluentIcon.HEART, "示例设置", "说明文字", parent)
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | 字符串 | 卡片标题 |
| `factory` | `(parent) -> QWidget` | 返回要放进「插件」分组里的控件 |

- 卡片放在设置页最后一组（标题「插件」）里；返回 `None` 或非控件时该贡献被跳过；一个贡献都没生效时这一组不显示。
- 想要用户可配置项，优先用清单的 `options`（第 2.6 节协议），它会自动出现在「插件选项」对话框里。

## 3. 非界面扩展点

### 3.1 `app.viewer`：打开方式（查看器）

用 `ctx.add_viewer()` 注册，而不是直接 `contribute`：

```python
ctx.add_viewer(
    "图片查看器",
    extensions=["png", "jpg"],          # 认领的扩展名
    factory=self.create_view,           # (path, parent) -> QWidget
    opener=self.open_view,              # 可选：(path, parent) -> (bool, str)，插件自己弹窗（推荐）
    kind="image",                       # 打开方式分组：image/video/audio/archive/text/code/markdown/spreadsheet
    description="查看图片",
    capabilities=["缩放", "旋转"],
    host="dialog",                      # 显示在哪个宿主里，内置弹窗外壳为 "dialog"
    viewer_id="my.plugin",              # 缺省即插件 id
    order=100,
)
```

- `kind` 是**查看器元数据**（「打开方式」页的分组与图标），不是插件类型；一个插件可以注册多个查看器，`kind` 各不相同。
- 注册后会出现在「打开方式」页的插件下拉里，用户可以按扩展名指定用哪个查看器打开；规则存 `config/open_with.json`。
- 查看器控件由 `host` 决定挂在哪：内置弹窗外壳（`builtin.lib.dialog`）用 `ctx.require("dialog")` 取到，页面会显示在程序本体之外的独立窗口里。
- **推荐做法（也是内置 7 个查看器的做法）**：把 `opener` 一起登记，让插件自己建窗口、自己弹；程序侧的
  `open_viewer()` 会优先调它，界面代码因此完全住在插件里。内置工具箱是 `builtin.lib.viewer` 的 `plugin.py`
  （`ViewerPlugin` 基类 + `ViewerWindow` 窗口外壳 + `MediaViewer` 播放页）—— 继承 `ViewerPlugin` 只实现
  `create_view(path, parent=None)` 即可，登记与弹窗都由基类完成。只给 `factory` 的老式写法仍然可用
  （程序用宿主把控件包一层），但视图控件仍必须来自插件自己的目录。

### 3.2 `app.ui.page`：插件页面

```python
ctx.add_page("hello", "演示页", self._build_page, icon="HOME", bottom=False, order=100)
```

- 页面注册成 `plugin.<key>` 路由，程序把它挂进主窗口堆叠页并按需加左侧导航项。
- `factory` 返回一个 QWidget；插件被禁用 / 卸载后页面与导航项一起消失。
- 左侧导航的顺序是固定的：内置页面按内置顺序（设置恒在最下面），插件页面按载入顺序追加；
  追加不下的插件页面只出现在内置的「页面管理」页里（在那一页里仍可打开），这一页只读、不改布局。
- 页面挂了 `session` 之类资源时，在 `Plugin.teardown()` 里释放（主窗口关闭时也会逐个调用）。

## 4. 事件

程序在状态变化后广播事件；插件用 `ctx.on(event, handler)` 订阅，处理函数必须能接住关键字参数：

```python
def _on_imported(self, **payload) -> None:
    self._imported += 1
    self.log.info("收到导入事件：{}", payload.get("name"))
```

| 事件 | 标签 | 载荷 | 广播时机（程序侧） |
| --- | --- | --- | --- |
| `item.imported` | 条目导入 | `item_id`, `name` | `import_service._announce_import()`：粘贴文本导入、文件导入、库扫描登记成功后 |
| `item.deleted` | 条目删除 | `item_id`, `name` | `item_service.delete()` 逐项广播 |
| `user.changed` | 用户切换 | `user_id`, `name` | `user_service.set_current()` |
| `library.changed` | 库目录变化 | `path` | `library_service.set_path()`（迁移库文件夹后） |
| `theme.changed` | 主题变化 | `theme`（`light` / `dark` / `auto`） | 设置页切换主题时 |
| `plugin.enabled` | 插件启用 | `plugin_id` | `plugin_service.set_enabled(..., True)` |
| `plugin.disabled` | 插件禁用 | `plugin_id` | `plugin_service.set_enabled(..., False)` |

- 事件是**通知**，不是事务钩子：载荷只保证包含上表字段，别假设里面有数据库会话或事务能回滚。
- 处理器抛异常不会影响程序（只记日志），也不要在这里做长耗时工作。
- 插件也可以自己 `ctx.emit(event, **payload)` 广播自定义事件，其他插件用同一个名字订阅即可（自定义事件名请加命名空间前缀，例如 `my.plugin.ready`）。

## 5. 查看自己贡献了什么

- 插件页的「贡献」筛选：可以按扩展点过滤插件列表，详情区会列出每个插件的贡献（扩展点、键、说明）与订阅的事件。
- 代码里：

  ```python
  ctx.contributions()                        # 本插件的全部贡献
  ctx.contributions(ExtensionPoint.HOME_KPI)  # 只看某个扩展点
  plugin_service.point_items(ExtensionPoint.HOME_KPI)   # 某个扩展点上的全部贡献（按 order 排序）
  ```
