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
- 清单里的 `provides` 只是**声明与展示**：真正生效的是插件在 `setup()` 里调用的 `ctx.provide("接口名", 对象)`；
  消费方用 `ctx.require("接口名")` 取实例，取不到时应当自己兜底。

## 1. 扩展点总览

| 扩展点 | 标签 | 值结构 | 生效位置 | 稳定级别 |
| --- | --- | --- | --- | --- |
| `app.viewer` | 查看器 | 由 `ctx.add_viewer()` 构造 | 「查看器」页、条目打开流程 | 稳定 |
| `app.editor` | 编辑器 | 由 `ctx.add_editor()` 构造 | 「编辑器」页、条目右键「编辑器 ▸」 | 稳定 |
| `app.ui.page` | 页面 | 由 `ctx.add_page()` 构造 | 主窗口左侧导航 + 堆叠页 | 稳定 |
| `app.ui.manage.toolbar` | 数据管理工具栏 | `{"text", "callback", "icon", "tip"}` | 数据管理页工具栏 | 稳定 |
| `app.ui.manage.item_menu` | 条目菜单 | `{"text", "callback", "icon"}` | 数据行右键菜单 | 稳定 |
| `app.ui.detail.panel` | 详情面板 | `{"title", "lines"}` | 详情弹窗底部 | 稳定 |
| `app.ui.import.filter` | 导入筛选 | `{"name", "accept"}` | 导入页扫描文件时 | 稳定 |
| `app.ui.import.action` | 导入页功能 | `{"text", "callback", "icon", "tip"}` | 导入页「开始导入」旁 | 稳定 |
| `app.ui.home.kpi` | 概览卡片 | `{"title", "value", "sub", "icon", "hint"}` | 概览页统计卡 | 稳定 |
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
    {"title": "示例计数", "value": self._kpi_text, "sub": self._kpi_sub, "icon": "HEART", "hint": "示例插件贡献的概览卡片"},
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
| `hint` | 字符串或 `-> str` 回调 | 可选，鼠标停在卡片上弹出的说明 |

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
| `callback` | `() -> None` 或 `(selection) -> None` | 点击时调用；接受 1 个参数时收到 `app.sdk.items.SelectionContext`（当前选中的条目与刷新回调） |
| `icon` | 字符串 | 可选 |
| `tip` | 字符串 | 可选，悬停提示；缺省时用贡献的 `description` |

按钮加在工具栏已有按钮之后（流式布局，窄窗口自动换行）。回调是否收参数由 `contributions.accepts_argument()` 判断，
因此**老插件的不带参数回调照常工作**；要拿选中条目就写成 `def _on_toolbar(self, selection)`：

```python
def _on_toolbar(self, selection):
    ids = selection.item_ids          # tuple[int, ...]
    if not selection.count:
        return                        # 没选中就自己提示
    app.sdk.items.tag_items(ids, ["待整理"])
    selection.do_refresh()            # 让管理页重新加载
```

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

菜单项一律加在「查看器」「编辑器」两个子菜单之后、内置项之前（紧随其后的分隔符由页面补）。`item` 是域对象，可读 `item.id` / `item.name` / `item.file_path` 等。

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

### 2.6 `app.ui.import.action`：导入页上的功能按钮

```python
ctx.contribute(
    ExtensionPoint.IMPORT_ACTION,
    {"text": "自动挂标签", "callback": self._on_import_action, "icon": "TAG", "tip": "按规则预填标签"},
    key="demo.import.action",
)

def _on_import_action(self, context):
    context.apply_tags(["待整理"])       # 写回导入页的标签输入框
    context.toast(f"已预填 {context.apply_tags(['待整理'])} 个标签")
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 字符串 | 按钮文字 |
| `callback` | `(ImportContext) -> None` | 点击时调用，收到 `app.sdk.items.ImportContext` |
| `icon` | 字符串 | 可选 |
| `tip` | 字符串 | 可选，悬停提示；缺省时用贡献的 `description` |

按钮加在导入页「开始导入」右侧（`import_page._sync_plugin_actions()`，插件启用/禁用后自动增删）。
`ImportContext` 的字段与方法：

| 成员 | 说明 |
| --- | --- |
| `paths` | `tuple[Path, ...]`，导入页当前待导入的文件 |
| `user_id` / `category_id` | 导入页当前选中的归属用户与分类（可能为 `None`） |
| `apply_tags(names)` / `apply_keywords(words)` | 把建议并进导入页的标签框 / 关键词框，返回**新增**条数 |
| `toast(message)` | 在导入页弹一条提示 |
| `scan()` | 重新扫描待导入文件（返回 `tuple[Path, ...]`） |
| `recent_paths()` | 等价于 `paths` |

插件只负责**预填**，真正的导入动作仍然由用户点「开始导入」——这样插件永远不会在没有用户确认时改动库。

### 2.7 `app.ui.settings.card`：设置页卡片

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

### 3.1 `app.viewer`：查看器

用 `ctx.add_viewer()` 注册，而不是直接 `contribute`：

```python
ctx.add_viewer(
    "图片查看器",
    extensions=["png", "jpg"],          # 认领的扩展名
    factory=self.create_view,           # (path, parent) -> QWidget
    opener=self.open_view,              # 可选：(path, parent) -> (bool, str)，插件自己弹窗（推荐）
    kind="image",                       # 查看器分组：image/video/audio/archive/text/code/markdown/spreadsheet
    description="查看图片",
    capabilities=["缩放", "旋转"],
    host="dialog",                      # 显示在哪个宿主里，内置弹窗外壳为 "dialog"
    viewer_id="my.plugin",              # 缺省即插件 id
    order=100,
)
```

- `kind` 是**查看器元数据**（「查看器」页的分组与图标），不是插件类型；一个插件可以注册多个查看器，`kind` 各不相同。
- 注册后会出现在「查看器」页的插件下拉里，用户可以按扩展名指定用哪个查看器打开；规则存 `.configs/viewers.json`。
- 查看器控件由 `host` 决定挂在哪：内置弹窗外壳（`builtin.lib.ui`）用 `ctx.require("dialog")` 取到，页面会显示在程序本体之外的独立窗口里。
- **推荐做法（也是内置 7 个查看器的做法）**：把 `opener` 一起登记，让插件自己建窗口、自己弹；程序侧的
  `open_viewer()` 会优先调它，界面代码因此完全住在插件里。内置工具箱是 `builtin.lib.viewer` 的 `plugin.py`
  （`ViewerPlugin` 基类 + `ViewerWindow` 窗口外壳 + 注册表 `ViewerRegistry`）—— 继承 `ViewerPlugin` 只实现
  `create_view(path, parent=None)` 即可，登记与弹窗都由基类完成。只给 `factory` 的老式写法仍然可用
  （程序用宿主把控件包一层），但视图控件仍必须来自插件自己的目录。

### 3.2 `app.editor`：编辑器

用 `ctx.add_editor()` 注册，而不是直接 `contribute`：

```python
ctx.add_editor(
    "文本编辑器",
    extensions=["txt", "md"],           # 认领的扩展名
    factory=self.create_editor,         # (path, parent) -> QWidget（可带 save()/is_dirty()）
    opener=self.open_editor,            # 可选：(path, parent) -> (bool, str)，插件自己开窗（推荐）
    kind="internal",                    # internal=程序内编辑控件；external=交给系统默认程序编辑
    description="编辑文本文件",
    capabilities=["保存", "未保存提示"],
    host="dialog",                      # 显示在哪个宿主里，内置弹窗外壳为 "dialog"
    editor_id="my.plugin",              # 缺省即插件 id
    order=100,
)
```

- 编辑器和查看器一样分「工具库 + 具体插件」：内置工具箱是 `builtin.lib.editor` 的 `plugin.py`
  （`EditorPlugin` 基类 + `EditorWindow` 窗口外壳 + 注册表 `EditorRegistry`）—— 继承 `EditorPlugin`
  只实现 `create_editor(path, parent=None)` 即可，登记、规则与弹窗都由基类完成。
- `kind="internal"` 的编辑器控件是可以改内容的 `QWidget`；若它提供 `save()`（返回 `True` 或 `(bool, str)`）
  和 `is_dirty()`，窗口外壳会据此启用「保存」按钮、显示「已修改」并在关闭前询问未保存改动。
- `kind="external"` 表示「不提供程序内编辑控件，交给电脑系统默认程序编辑」（登记 `opener` 即可，
  `factory` 可省略），用户点「编辑器 → 系统默认程序」时直接打开系统默认编辑器。
- 注册后会出现在「编辑器」页的插件下拉里，用户可以按扩展名指定用哪个编辑器；规则存 `.configs/editors.json`。
- 程序侧 `app.sdk.editors` 的 `edit_path()` 拿不到 `editor.open` 接口（没装编辑器插件）时**退回系统默认编辑器**；
  内置编辑器插件保存文件后会通过公开信号刷新对应条目的 checksum / size / 内容。

### 3.3 `app.ui.page`：插件页面

```python
ctx.add_page("hello", "演示页", self._build_page, icon="HOME", bottom=False, order=100)
```

- 页面注册成 `plugin.<key>` 路由，程序把它挂进主窗口堆叠页并按需加左侧导航项。
- `factory` 返回一个 QWidget；插件被禁用 / 卸载后页面与导航项一起消失。
- 左侧导航的顺序是固定的：内置页面按内置顺序（设置恒在最下面），插件页面按载入顺序追加，
  最多显示 7 个，追加不下的只出现在内置的「页面管理」页里（那一页每行点整行即进入该页面）。
- 页面挂了 `session` 之类资源时，在 `Plugin.teardown()` 里释放（主窗口关闭时也会逐个调用）。

### 3.4 扩展接口：`model.open`（模型调度）

`model.open` 不是扩展点，而是**扩展接口**（插件用 `ctx.provide("model.open", ModelOpenApi(...))` 提供、消费方 `ctx.require("model.open")` 取用）：
程序侧门面是 `app.sdk.models`（`list_models()` / `model_by_id()` / `capabilities()` / `loaded()` / `acquire()` / `invoke()`），
由内置库插件 `lib.model` 实现，成员与用法见 [`lib.model/PLUGIN.md`](lib.model/PLUGIN.md)；
完整的消费方写法（按能力取租约 → 调用 → 归还 + 没有模型时的降级）见示例插件 [`example.model_usage/PLUGIN.md`](example.model_usage/PLUGIN.md)。
同类的运行期接口还有查看器的 `viewer.open` 与编辑器的 `editor.open`（分别见 `builtin.lib.viewer/PLUGIN.md` / `builtin.lib.editor/PLUGIN.md`）。

### 3.5 扩展接口：`console.output`（控制台输出）

`console.output` 是扩展接口：**程序本体实现、插件只消费**。插件侧门面是 `app.sdk.console`，
`ctx.console` 就是已经绑好来源（插件 id）的 `Console`：

```python
ctx.console.stage("下载权重", "第 2 片")     # … | INFO | lib.model | page.py:123 | [下载权重] 第 2 片
ctx.console.progress(3, 10, "解压")          # … | INFO | lib.model | page.py:124 | 进度 解压 3/10
ctx.console.warning("模型没登记，走降级分支")
```

控制台与文件日志共用一套格式（`app.core.logging_setup.DEFAULT_FORMAT`）：

    时间 | 级别 | 来源 | 函数:行 | 消息

「来源」列由 `emit()` 保证填对：调用点在插件里就是插件 id，在程序里就是 `app.<模块>`；`函数:行` 用
`logger.opt(depth=…)` 跳过门面与 `console.output` 实现，指到真正发那行日志的位置。正文里**不要**再自己拼
`[来源]` 前缀（那会污染消息），阶段前缀由 `stage` 参数拼成 `[下载权重] 第 2 片`。

| 调用 | 说明 |
| --- | --- |
| `info / success / warning / error / debug(message, *, source="", stage="")` | 按级别各写一行 |
| `write(message, *, level="info", source="", stage="")` | 级别用字符串给（`progress` 会加「进度 」前缀） |
| `stage(name, message="", *, level="info")` | 阶段播报：`stage("下载", "开始")` → `[下载] 开始` |
| `progress(done, total=None, message="")` | 进度播报：`progress(3, 10, "解压")` → `进度 解压 3/10` |

- 程序没有提供实现时（脚本、单测）**直接落 loguru**，插件不需要判断运行环境；
- 插件里**不要**直接 `import loguru`：一律走 `ctx.console` / `console_for("插件.id")`，来源、函数行号、级别才统一
  （自检 `plugin_logging_via_sdk` 会扫插件源码拦这条）；
- 插件侧优先用 `ctx.console`；拿不到 ctx 的地方用 `app.sdk.console.console_for("插件.id")`；
  什么都不传来源也可以（`console_for()`）；
- 程序侧实现是 `app.services.console_service.ConsoleOutput`（在 `src/main.py` 用
  `plugin_service.bootstrap(CONSOLE_EXTENSION, ConsoleOutput())` 挂上）；
  把设置里「日志 → 输出到控制台」关掉后，插件播报只会进文件日志；
- 加载某类数据、进到某个阶段时都建议播报一句（模型工具库的安装 / 卸载 / 设备探测已经这么做），
  方便用户和作者定位「卡在哪一步」。

### 3.6 扩展接口：`items.open`（数据读写）

`items.open` 是扩展接口：**程序本体实现、插件只消费**。插件侧门面是 `app.sdk.items`：

```python
from app.sdk import items

refs = items.list_items(type="image", suffix="png", limit=50)   # 只读，拿不到实现时返回 ()
items.tag_items([ref.id for ref in refs], ["待整理"])            # 写，返回真的改了几条
items.notify_changed()                                          # 改完通知界面刷新
```

| 函数 | 说明 |
| --- | --- |
| `current_user_id()` | 当前用户 id（拿不到返回 `0`） |
| `list_items(ids=None, user_id=None, suffix="", type="", include_hidden=False, include_deleted=False, exclude_tags=(), preview_bytes=0, limit=0)` | 按条件查条目，返回 `tuple[ItemRef, ...]`；`type` 用 `image` / `video` / `audio` / `document` / `spreadsheet` / `presentation` / `archive` / `code` / `text` / `other`（小写） |
| `get_item(item_id, preview_bytes=0)` | 取单个 `ItemRef`，不存在返回 `None` |
| `suffixes_in_use(user_id=None)` | `{"后缀": 条数}`，即「当前库里有哪些数据格式」，按后缀调规则时用它 |
| `tag_names(user_id=None)` | 当前可用标签名 |
| `ensure_tags(names, user_id=None)` | 只创建标签、不挂载，返回创建/命中的标签名 |
| `tag_items(ids, names, user_id=None)` / `untag_items(ids, names, user_id=None)` | 批量挂 / 摘标签，返回**真实改动条数** |
| `add_keywords(ids, words, user_id=None)` / `remove_keywords(ids, words, user_id=None)` | 批量加 / 删关键词，返回真实改动条数 |
| `read_text(item_id, limit=4096)` | 读正文，返回 `(text, encoding, truncated)`（二进制条目返回空串） |
| `notify_changed()` | 广播 `itemsChanged`，界面自动刷新 |

- `ItemRef` 是只读快照：`id` / `name` / `type`（小写）/ `suffix`（小写、无点）/ `size` / `keywords` / `tags` / `file_path` / `category_id` / `user_id` / `preview`；
- 写函数在程序没提供实现时抛 `app.sdk.errors.SdkError`；只读函数返回空值（插件不用判断运行环境）；
- 批量写走的是仓储层的批量接口（一次查询 + 一次提交），因此**先 `list_items()` 筛出目标再整批写**，不要在循环里逐条调；
- 插件**不要**直接开数据库会话或 `import` 程序内部模块（自检 `plugin_imports` 会拦）；同理，改动完成后调 `notify_changed()`（或 `app.sdk.ui.notify_items_changed()`），不要自己去碰界面。

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
| `item.changed` | 条目变化 | 无 | `plugin_service._on_items_changed()`：界面广播 `itemsChanged` 时（标签 / 关键词 / 分类 / 隐藏 / 删除等任何改动之后） |
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
