# 插件 SDK（`app.sdk`）

插件**唯一**可见的程序面。插件源码只允许 import `app.sdk`（以及标准库、qfluentwidgets 等三方包）和已声明依赖的 `dm_plugin.<id>`；
`app.core` / `app.services` / `app.repositories` / `app.db` / `app.data` / `app.ui.pages` / `app.ui.framework` 一律禁止（自检 `plugin_imports` 会拦）。

> 读法：本文里凡是带 `app.core.` / `app.services.` / `app.ui.` / `app.db` / `app.repositories` / `app.data` 前缀的名字，以及 `dm_plugin.*` 的名字，都是**程序内部或库插件**的模块，插件用不到也不允许直接 import，写在这里只为说明「东西搬去哪儿了」。其余名字都是 `app.sdk` 的真实接口（能直接搜到源码）。第 11 节「原来是」一列里的 `app.sdk.models` / `app.sdk.viewers` / `app.sdk.editors` 是**已删除的历史名字**，只出现在那张迁移对照表里。

- 插件协议（目录、清单字段、载入阶段）见 [`PLUGIN_PROTOCOL.md`](PLUGIN_PROTOCOL.md)。
- 写插件的上手教程见 [`PLUGIN.md`](PLUGIN.md)；扩展点与事件的**完整参考**就在本文第 5 节。
- 工具库插件（`builtin.lib.ui` / `builtin.lib.viewer` / `builtin.lib.editor` / `lib.model` / `lib.autolabel`）的接口**不在 SDK 里**，见各自的 `plugins/<id>/PLUGIN.md`。

## 1. 最小示例

```python
from app.sdk import ExtensionPoint, Plugin, PluginContext


class HelloPlugin(Plugin):
    """清单 plugin.json 里 class 指向它；entry 固定 plugin.py。"""

    def setup(self, ctx: PluginContext) -> None:
        ctx.log.info("hello from {}", ctx.plugin_id)
        ctx.contribute(ExtensionPoint.HOME_KPI, {"title": "你好", "value": "1"}, key="hello")

    def describe(self) -> list[tuple[str, str]]:
        return [("说明", "插件页展示的额外信息行")]
```

`setup(ctx)` 里做注册；`teardown()` 里撤销（`ctx.contribute` / `ctx.provide` / 页面与查看器都由程序自动撤销，自己申请的资源才需要自己收）。

## 2. 模块目录

| 模块 | 作用 | 关键接口 |
| --- | --- | --- |
| `app.sdk.plugin` | 插件基类与类发现 | `Plugin`、`collect_plugin_classes`、`plugin_class_names` |
| `app.sdk.context` | 插件与程序打交道的门面 | `PluginContext`、`ContextServices` |
| `app.sdk.points` | 扩展点与事件常量 | `ExtensionPoint`、`Events`、`Contribution`、`events` |
| `app.sdk.console` | 播报到程序控制台 | `console_for`、`Console`、`emit`、`stage`、`progress`、`available` |
| `app.sdk.items` | 条目 / 标签 / 关键词 | `ItemRef`、`ItemsApi`、`SelectionContext`、`ImportContext`、`list_items` 等 |
| `app.sdk.data` | 文本、表格、压缩包、图片的解析工具 | `read_text`、`decode_text`、`csv_rows`、`xlsx_sheets`、`archive_members`、`archive_read`、`image_info`、`image_data_url`、`human_size` |
| `app.sdk.storage` | 配置目录读写 | `config_dir`、`config_file`、`data_dir`、`resources_dir`、`read_json`、`write_json`、`loads`、`dumps`（JSON 走 orjson，缺依赖自动退回标准库） |
| `app.sdk.ui` | 界面支持 | `open_default`、`ask_open_with`、`open_with_program`、`reveal`、`open_page`、`notify_items_changed`、`simple_mode`、`clear_scroll_background`、间距常量 |
| `app.sdk.library` | 取别的插件暴露的库 | `library`、`requires` |
| `app.sdk.manifest` | 受程序管理的 JSON 清单 | `MANIFEST_EXTENSION`、`describe`、`load`、`query`、`diff`、`update`、`write`、`reset`、`backup`、`backups`、`raw`、`entries`、`ids`、`items_of`、`record_of`、`register`、`value_of`、`available` |
| `app.sdk.version` | 版本与版本范围 | `SDK_VERSION`、`parse_version`、`parse_range`、`satisfies`、`compare_versions`、`range_text` |
| `app.sdk.errors` | 异常类型 | `PluginError`、`PluginManifestError`、`DependencyError`、`VersionError`、`SdkError` |

一次 `from app.sdk import …` 也能拿到上面大部分名字（`app.sdk.__all__` 是白名单）。

## 3. `Plugin`（`app.sdk.plugin`）

| 成员 | 说明 |
| --- | --- |
| `id` / `name` / `version` / `path` / `manifest` | 由载入器用清单填好，插件自己只读 |
| `setup(ctx)` / `teardown()` | 生命周期钩子，默认什么都不做 |
| `describe() -> list[tuple[str, str]]` | 插件页「自检信息」的追加行 |
| `log` | 带插件 id 的 loguru 日志器（**插件内仍推荐 `app.sdk.console`**，见下） |
| `data_dir` | 插件数据目录 `<插件目录>/.data` |
| `data_path(key)` | `.data/<key>`（无后缀时补 `.json`），不存在返回 `None` |
| `data(key, default=…)` | 读取并按 `.json` 解析；缺文件且有 `default` 就返回它，否则抛 `SdkError` |

## 4. `PluginContext`（`app.sdk.context`）

`setup(ctx)` 里能用到的全部能力：

| 成员 | 说明 |
| --- | --- |
| `plugin_id` / `plugin_name` / `manifest` | 自身信息 |
| `log` / `console` | 日志器 / 控制台播报口（`console_for(ctx.plugin_id)` 的等价物） |
| `data_path(key)` / `data(key, default=None)` | 读 `.data/` 里的数据文件 |
| `option(key, default)` / `options()` / `set_option(key, value)` | 读 / 写清单 `options` 的当前取值（落 `.configs/plugins.json`） |
| `provide(name, obj)` / `require(name)` / `has(name)` | 暴露 / 取用扩展接口（跨插件契约） |
| `contribute(point, value, key="")` / `contributions(point="")` | 往扩展点留坑里放东西 |
| `on(event, handler)` / `emit(event, **payload)` | 订阅 / 广播事件 |
| `add_page(key, title, factory, **fields)` | 注册一个导航页面 |
| `add_viewer(name, **fields)` / `add_editor(name, **fields)` | 注册查看器 / 编辑器（接口名分别是 `viewer.open` / `editor.open`） |
| `host` | 程序服务句柄：`viewers()` / `editors()` / `refresh_path(path)` 等 |

## 5. 扩展点与事件（`app.sdk.points`）

程序在固定位置「留坑」（扩展点），插件用 `ctx.contribute(ExtensionPoint.XXX, 东西)` 往坑里放；
程序在状态变化时广播事件，插件用 `ctx.on(Events.XXX, 处理函数)` 订阅。

```python
from app.sdk import Events, ExtensionPoint

ctx.contribute(ExtensionPoint.HOME_KPI, {...}, key="my.kpi", order=100, description="我的卡片")
ctx.on(Events.ITEM_IMPORTED, self._on_imported)     # 处理函数签名要收 **payload
```

- 扩展点名与事件名都是**字符串常量**（`ExtensionPoint` / `Events` 的类属性，另有 `values()` / `label()` 取全部名字与中文标签）。
- 贡献与订阅都记在插件名下；插件被禁用 / 卸载时程序**自动撤销**，插件自己不用清理（`Plugin.teardown()` 只用于自己的额外资源）。
- 贡献的排序键是 `(order, plugin_id, key)`：`order` 小的在前（默认 `100`），同 `order` 时按插件 id 排。
- 清单里的 `provides` 只是**声明与展示**；真正生效的是 `setup()` 里调用的 `ctx.provide("接口名", 对象)`，消费方用 `ctx.require("接口名")` 取实例、取不到要自己兜底。

### 5.1 扩展点总览

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

### 5.2 界面扩展点

界面扩展点由 `src/app/ui/framework/contributions.py` 统一读取：`items(point)` 取贡献、`title_of` / `value_of` / `icon_of` 取字段、`resolve(callback, *args)` 安全调用回调、`text_of(value)` 把「字符串或回调」统一成文本、`accepts_argument(callback)` 判断回调收不收参数。

- 所有 `callback` / `accept` / `lines` / `factory` 都是**可调用对象**，由程序在界面线程调用；
- 回调抛异常只记日志，不会让界面崩（`resolve()` 捕获后返回 `None`）；
- `icon` 是 `FluentIcon` 的成员名（如 `"HEART"`、`"SYNC"`），认不出来时回退 `FluentIcon.APPLICATION`；
- `title` 必填；`description` 是可选说明，显示在插件页的贡献列表里。

#### 5.2.1 `app.ui.home.kpi`：概览卡片

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

程序在概览页刷新时重建这些卡片（`app/ui/pages/home_page.py` 的 `_rebuild_plugin_cards()`），并在条目 / 插件变化后自动刷新。

#### 5.2.2 `app.ui.manage.toolbar`：数据管理工具栏按钮

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
| `callback` | `() -> None` 或 `(selection) -> None` | 点击时调用；接受 1 个参数时收到 `app.sdk.items.SelectionContext` |
| `icon` | 字符串 | 可选 |
| `tip` | 字符串 | 可选，悬停提示；缺省时用贡献的 `description` |

按钮加在工具栏已有按钮之后（流式布局，窄窗口自动换行）。回调是否收参数由 `contributions.accepts_argument()` 判断，
因此**不带参数的回调照常工作**；要拿选中条目就写成 `def _on_toolbar(self, selection)`：

```python
def _on_toolbar(self, selection):
    ids = selection.item_ids          # tuple[int, ...]（属性）
    if not selection.count:           # 属性
        return                        # 没选中就自己提示
    from app.sdk import items
    items.tag_items(ids, ["待整理"])
    selection.do_refresh()            # 让管理页重新加载
```

`SelectionContext`（`src/app/sdk/items.py`）的成员：`items`（`tuple[ItemRef, ...]`）、`user_id`、`refresh`，属性 `item_ids` / `count`，方法 `do_refresh()`。

#### 5.2.3 `app.ui.manage.item_menu`：条目右键菜单项

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 字符串 | 菜单项文字 |
| `callback` | `(item) -> None` | 调用时收到该行对应的数据项对象 |
| `icon` | 字符串 | 可选 |

菜单项加在「查看器」「编辑器」两个子菜单之后、内置项之前（分隔符由页面补）。`item` 是域对象，可读 `item.id` / `item.name` / `item.file_path` 等。

#### 5.2.4 `app.ui.detail.panel`：详情弹窗里的一段文本

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | 字符串 | 段落标题（每行以前缀形式出现） |
| `lines` | `(item) -> Iterable[str]` | 返回若干行文本；返回字符串时整段作为一行 |

#### 5.2.5 `app.ui.import.filter`：导入筛选器

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | 字符串 | 筛选器名称（插件页展示） |
| `accept` | `(Path) -> bool` | 返回 `False` 的文件不会进入导入队列 |

- 程序在导入页扫描文件时逐个询问（`app/ui/pages/import_page.py` 的 `_collect_sources()`），**所有**筛选器都通过才会导入（多个筛选器之间是「与」）。
- 回调抛异常时按「通过」处理（`contributions.path_filters()` 的 guarded 包装），避免插件把导入卡死。

#### 5.2.6 `app.ui.import.action`：导入页上的功能按钮

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 字符串 | 按钮文字 |
| `callback` | `(ImportContext) -> None` | 点击时调用，收到 `app.sdk.items.ImportContext` |
| `icon` | 字符串 | 可选 |
| `tip` | 字符串 | 可选，悬停提示；缺省时用贡献的 `description` |

按钮加在导入页「开始导入」右侧（`import_page._sync_plugin_actions()`，插件启用 / 禁用后自动增删；上下文由 `import_page._plugin_action_context()` 构造）。
`ImportContext` 的成员：

| 成员 | 说明 |
| --- | --- |
| `paths` | `tuple[Path, ...]`，导入页当前待导入的文件 |
| `user_id` / `category_id` | 导入页当前选中的归属用户与分类（可能为 `None`） |
| `apply_tags(names)` / `apply_keywords(words)` | 把建议并进导入页的标签框 / 关键词框，返回**新增**条数 |
| `toast(message)` | 在导入页弹一条提示 |
| `recent_paths()` | 重新扫描并返回当前的待导入文件 |
| `scan` / `add_tags` / `add_keywords` / `notify` | 页面上挂进来的回调，插件一般直接用上面的封装方法 |

插件只负责**预填**，真正的导入动作仍然由用户点「开始导入」——这样插件永远不会在没有用户确认时改动库。

#### 5.2.7 `app.ui.settings.card`：设置页卡片

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | 字符串 | 卡片标题 |
| `factory` | `(parent) -> QWidget` | 返回要放进「插件」分组里的控件 |

- 卡片放在设置页最后一组（标题「插件」）里；返回 `None` 或非控件时该贡献被跳过；一个贡献都没生效时这一组不显示。
- 想要用户可配置项，优先用清单的 `options`（见 [`PLUGIN_PROTOCOL.md`](PLUGIN_PROTOCOL.md)），它会自动出现在「插件选项」对话框里。

### 5.3 注册式扩展点：查看器 / 编辑器 / 页面

这三类不直接 `contribute`，而用 `ctx.add_viewer()` / `ctx.add_editor()` / `ctx.add_page()` 注册（程序按对应扩展点登记，字段含义由工具库解释）。

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
ctx.add_editor("文本编辑器", extensions=["txt", "md"], factory=self.create_editor, kind="internal", host="dialog")
ctx.add_page("hello", "演示页", self._build_page, icon="HOME", bottom=False, order=100)
```

- `kind` 是**元数据**（「查看器」/「编辑器」页的分组与图标），不是插件类型；一个插件可以注册多个查看器 / 编辑器。
- 注册后会出现在对应配置页的插件下拉里，用户可以按扩展名指定用哪个；规则分别存 `.configs/viewers.json` / `.configs/editors.json`。
- **推荐做法**（也是内置 7 个查看器与 2 个编辑器的做法）：登记 `opener` 让插件自己建窗口、自己弹，界面代码完全住在插件里；内置工具箱是 `builtin.lib.viewer` 的 `plugin.py`（`ViewerPlugin` 基类 + `ViewerWindow` + 注册表）与 `builtin.lib.editor` 的 `plugin.py`（`EditorPlugin` 基类 + `EditorWindow` + 注册表）——继承基类只实现 `create_view(path, parent=None)` / `create_editor(path, parent=None)`，登记与弹窗都由基类完成。只给 `factory` 的老式写法仍可用（程序用宿主把控件包一层）。
- `kind="internal"` 的编辑器控件若提供 `save()`（`True` 或 `(bool, str)`）与 `is_dirty()`，窗口外壳会据此启用「保存」、显示「已修改」并在关闭前询问未保存改动；`kind="external"` 表示不提供程序内控件，交给系统默认程序编辑（登记 `opener` 即可）。
- 程序侧调度在 `app.services.viewer_service` / `app.services.editor_service`（插件看不到）；插件侧写 `ctx.require("viewer.open")` / `ctx.require("editor.open")` 或直接继承工具库基类。
- 页面注册成 `plugin.<key>` 路由，程序把它挂进主窗口堆叠页并按需加左侧导航项；左侧导航内置页面顺序固定（设置恒在最下面），插件页面按载入顺序追加、最多显示 7 个，超出的只出现在「页面管理」页里。

### 5.4 三个扩展接口（程序本体实现、插件消费）

这三个不是扩展点，而是**扩展接口**：`setup()` 里 `ctx.provide("名字", 对象)` 提供、消费方 `ctx.require("名字")` 取用。

| 接口 | 谁提供 | 插件侧门面 | 详细成员 |
| --- | --- | --- | --- |
| `model.open` | 库插件 `lib.model` | `dm_plugin.lib.model.api`（依赖 `lib.model`） | `plugins/lib.model/PLUGIN.md` |
| `console.output` | 程序本体（`src/main.py` 挂 `ConsoleOutput`） | `app.sdk.console`（`ctx.console`） | 本文第 6 节 |
| `items.open` | 程序本体（`app.services.item_api`） | `app.sdk.items` | 本文第 7 节 |

`viewer.open` / `editor.open` 由查看器 / 编辑器工具库提供，见 5.3。

### 5.5 事件

程序在状态变化后广播事件；插件用 `ctx.on(event, handler)` 订阅，处理函数必须能接住关键字参数：

```python
def _on_imported(self, **payload) -> None:
    self._imported += 1
    self.log.info("收到导入事件：{}", payload.get("name"))
```

| 事件 | 标签 | 载荷 | 广播时机（程序侧） |
| --- | --- | --- | --- |
| `item.imported` | 条目导入 | `item_id`, `name` | `app/services/import_service.py` 的 `_announce_import()`：粘贴文本导入、文件导入、库扫描登记成功后 |
| `item.deleted` | 条目删除 | `item_id`, `name` | `ItemService.delete()` 逐项广播 |
| `item.changed` | 条目变化 | 无 | `app/services/plugin_service.py` 广播 `Events.ITEM_CHANGED`：界面广播条目变化时（标签 / 关键词 / 分类 / 隐藏 / 删除等任何改动之后） |
| `user.changed` | 用户切换 | `user_id`, `name` | `UserService.set_current()` |
| `library.changed` | 库目录变化 | `path` | `LibraryService.set_path()`（迁移库文件夹后） |
| `theme.changed` | 主题变化 | `theme`（`light` / `dark` / `auto`） | 设置页切换主题时 |
| `plugin.enabled` | 插件启用 | `plugin_id` | `PluginService.set_enabled(..., True)` |
| `plugin.disabled` | 插件禁用 | `plugin_id` | `PluginService.set_enabled(..., False)` |

- 事件是**通知**，不是事务钩子：载荷只保证包含上表字段，别假设里面有数据库会话或事务能回滚。
- 处理器抛异常不会影响程序（只记日志），也不要在这里做长耗时工作。
- 插件也可以自己 `ctx.emit(event, **payload)` 广播自定义事件，其他插件用同一个名字订阅即可（自定义事件名请加命名空间前缀，例如 `my.plugin.ready`）。

### 5.6 查看自己贡献了什么

- 插件页的「贡献」筛选：可以按扩展点过滤插件列表，详情区会列出每个插件的贡献（扩展点、键、说明）与订阅的事件。
- 代码里：

  ```python
  ctx.contributions()                         # 本插件的全部贡献
  ctx.contributions(ExtensionPoint.HOME_KPI)  # 只看某个扩展点
  ```

  某个扩展点上的**全部**插件贡献（按 `order` 排序）由程序的 `app.services.plugin_service` 的 `point_items(point)` 提供，
  插件页的「贡献」筛选就是用它；插件侧只用自己的 `ctx.contributions()`。

## 6. 控制台（`app.sdk.console`）

插件**不要**直接 `import loguru`：用 SDK 控制台，程序才能把消息挂到插件名下、折叠到「运行日志」里。

```python
from app.sdk.console import console_for

_console = console_for("my.plugin")          # 模块级建一次
_console.info("开始处理 {} 个条目", 12)       # 位置参数不会格式化！用 f-string
_console.stage("scan", "扫描目录")            # 阶段标题
_console.progress(done, total, "正在处理")    # 进度
_console.exception("失败：{}", "原因")        # ❌ 错：Console 只接一个位置参数
_console.exception(f"失败：{reason}")         # ✅ 对
```

`Console` 的方法都只接一个消息参数（其余是关键字参数 `stage=`）：`debug / info / success / warning / error / exception / write / stage / progress`；模块级同名函数（`info`、`warning` 等）是「自动认调用方模块名」的简写。

控制台与文件日志共用一套格式（`src/app/core/runtime/runtime.json` 的 `log_format`）：`时间 | 级别 | 来源 | 函数:行 | 消息`。
「来源」列由 SDK 保证填对：调用点在插件里就是插件 id，在程序里就是 `app.<模块>`；正文里**不要**自己再拼 `[来源]` 前缀。
程序侧实现是 `app.services.console_service.ConsoleOutput`（在 `src/main.py` 用 `plugin_service.bootstrap(CONSOLE_EXTENSION, ConsoleOutput())` 挂上）。

## 7. 条目、标签与关键词（`app.sdk.items`）

| 接口 | 说明 |
| --- | --- |
| `current_user_id()` | 当前用户 id（没登录返回 `None`） |
| `list_items(user_id=…, type=…, suffix=…, …)` | 取条目为 `ItemRef` |
| `get_item(item_id)` | 单个条目或 `None` |
| `suffixes_in_use(user_id=…)` | 后缀 → 条目数 |
| `tag_names` / `ensure_tags` / `tag_items` / `untag_items` | 标签的读 / 建 / 挂 / 摘 |
| `add_keywords` / `remove_keywords` | 关键词 |
| `read_text(item_id, limit=4096)` | `(正文, 编码, 是否截断)` |
| `notify_changed()` | 改完数据后通知界面刷新 |
| `notify_tags_changed()` | 建过 / 改过标签后通知界面刷新；批处理请**整批写完再调一次**，别每条都调 |
| `available()` | 有没有 items 扩展接口（没有时列表类接口返回空、写接口抛 `SdkError`） |

`ensure_tags()` / `tag_items()` 自己**不发**标签变更信号：批处理会连续写很多次，每条都发会让标签页重建很多遍；
调用方在整批写完之后调一次 `notify_tags_changed()` 即可（`auto_tag` / `auto_tag.rule` 就是这么做的）。

`ItemRef` 有 `id` / `name` / `type` / `suffix` / `size` / `keywords` / `tags` / `file_path` / `abs_path` / `category_id` / `user_id` / `preview`；
`SelectionContext` / `ImportContext` 的成员见 5.2.2 与 5.2.6。
批量写走仓储层批量接口，**先 `list_items()` 筛出目标再整批写**，不要在循环里逐条调。

## 8. 清单机制（`app.sdk.manifest`）

程序登记了一批「外部可替换」的 JSON 清单（`app.manifest` 扩展接口背后是 `app.core.manifest.ManifestKit`）：

```python
from app.sdk import manifest

manifest.ids()                                   # 全部清单 id
manifest.describe("core.runtime")                # 元信息（路径 / 大小 / 条数 / 错误）
data = manifest.load("core.runtime")             # ManifestData：value() / list() / filters()
rows = manifest.query("core.plugins", kind="…")  # 按字段筛项
manifest.diff("core.runtime", {"app_name": "X"}) # 与候选内容对照
manifest.update("core.runtime", [{"key": "app_name", "value": "X"}])
manifest.backups("core.runtime"); manifest.reset("core.runtime")

payload = ctx.data("viewer")                      # 插件自己的 .data/*.json 也是清单格式
manifest.record_of(payload, ctx.plugin_id)        # 单条记录（查看器 / 编辑器：key = 插件 id）
manifest.items_of(payload)                        # 记录列表（模型模板 / 运行环境）
manifest.value_of(payload, "about", "")            # 模块数据的某个值
```

`items_of` / `record_of` / `value_of` 是纯函数，作用于**已经读进内存的清单字典**，不需要 provider，也不需要 Qt。

协议与边界（必须项、`kind` 枚举、items 必须有 `key`、备份策略、legacy 只读）见 [`MANIFEST_PROTOCOL.md`](MANIFEST_PROTOCOL.md)。
没注册 provider（例如纯单测）时 `available()` 为假、读写类接口抛 `SdkError`，`describe/ids/entries` 返回空。

## 9. 界面（`app.sdk.ui` 与 UI 工具库）

- `app.sdk.ui` 只提供**不依赖插件**的少量界面支持：`open_default` / `open_with_program` / `ask_open_with` / `reveal` / `open_page(route)` / `notify_items_changed()` / `simple_mode()` / `simple_display()` / `clear_scroll_background()` 与间距常量（`PAGE_MARGINS`、`PAGE_SPACING`、`PANEL_MARGINS`、`DETAIL_MARGINS`、`COMPACT_MARGINS`、`CARD_SPACING`、`ROW_SPACING`、`SCROLL_GUTTER`；`ClickCard` / `IconTextButton` / `IconTextPrimaryButton` 由 UI 工具库提供后在这里转发）。
- **页面骨架、卡片、工具条、弹窗外壳、控件工厂都从 `builtin.lib.ui` 取**（插件清单里 `depends: builtin.lib.ui`，然后 `from dm_plugin.builtin.lib.ui.plugin import …`），这样插件界面才和主程序一致；自检 `autolabel_ui_via_tool_library` 会强制这条规矩。
- 数据管理页的右键项不要自己画菜单：用 `ExtensionPoint.MANAGE_ITEM_MENU` 贡献 `{"text", "callback", "icon"}`（见 5.2.3）。

## 10. 版本与异常

```python
from app.sdk import SDK_VERSION, PluginError, PluginManifestError, SdkError, satisfies

satisfies("1.2", ">=1.0 <2.0")   # True
```

- `PluginError`：插件可以预期的错误基类；`PluginManifestError`（plugin.json 不合法）、`DependencyError`（依赖缺失 / 版本不符 / 循环）、`VersionError`（版本号写法错）、`SdkError`（用错 SDK：缺接口、依赖没声明、数据文件读取失败）。
- 清单机制的异常是另一套：`app.core.manifest.errors.ManifestError` 及其子类（`ManifestNotFoundError` / `ManifestFormatError` / `ManifestValidationError` / `ManifestBackupError`），SDK 侧只需捕 `ManifestError` 的场合请改用 `Exception` 或 `app.sdk` 暴露的 `SdkError`。

## 11. 插件专属接口去哪儿了

协议 v2 之后 SDK 只放**程序本体衍生的通用**接口。原来在 `app.sdk` 里的三份「插件专属门面」已经搬走：

| 原来是 | 现在 |
| --- | --- |
| `app.sdk.models`（`model.open`） | 库模块 `dm_plugin.lib.model.api`（依赖 `lib.model`）；错误类型 `dm_plugin.lib.model.errors.ModelError` / `ModelBusyError` |
| `app.sdk.viewers`（`viewer.open`） | 插件侧写 `ctx.require("viewer.open")` 取调度接口；程序侧内部用 `app.services.viewer_service`（插件看不到） |
| `app.sdk.editors`（`editor.open`） | 同上，程序侧是 `app.services.editor_service` |

从别的插件接模型就是：

```python
from dm_plugin.lib.model.api import BatchRequest, run_batch   # 清单里 depends: lib.model

results = run_batch([BatchRequest(task="chat", payload={"input": path}, key=str(item.id))])
```

## 12. 排查清单

| 症状 | 先看 |
| --- | --- |
| 插件载入失败 | 插件页的「异常」行 / 控制台「插件载入」汇总：`manifest`（plugin.json）、`dependency`（缺依赖、版本、循环）、`import`（导入期异常）、`construct`、`setup` 五个阶段之一 |
| `ModuleNotFoundError: dm_plugin.xxx` | 依赖没写进 `depends`，或依赖插件被禁用（禁用后它的命名空间不挂） |
| 扩展接口取不到 | `ctx.has(name)` 先判断，或 `ctx.require` 并捕 `PluginError`；库插件没启用时消费方要提示而不是崩 |
| 贡献的东西没出现 | 扩展点名写错（用 `ExtensionPoint` 常量）；`ExtensionPoint.values()` 可列出全部合法名字 |
| 界面和主程序不一致 | 用了裸 Qt 控件而不是 `builtin.lib.ui` 的控件工厂 |
| 日志没进「运行日志」 | 直接用了 `loguru` / `logger.*`，应改成 `console_for(...)` |
| 数据文件读不到 | 数据放 `.data/`，用 `ctx.data("name")`；`.json` 自动解析 |
