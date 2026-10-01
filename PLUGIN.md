# 插件开发指南

本指南面向想给 **D-MyDataManager** 写插件的人：插件目录长什么样、清单有哪些字段、程序怎么载入它、
`register(api)` 里能调用哪些接口、怎么自定义插件类型，以及大型插件如何把自己的页面挂进主界面。

- 相关源码：`src/app/core/plugins.py`（清单与依赖排序）、`src/app/core/plugin_kinds.py`（类型表）、
  `src/app/core/extensions.py`（扩展接口注册表）、`src/app/core/app_ui.py`（主程序界面扩展接口）、
  `src/app/services/plugin_service.py`（发现 / 载入 / 启停 / 导入）。
- 现成样例：`plugins/builtin.dialog/`（提供 `dialog` 扩展接口）、`plugins/builtin.image/`（依赖它显示图片）。

## 1. 插件是什么

一个插件就是**插件目录下的一个子目录**：

```
plugins/
├─ builtin.dialog/          # 内置插件（随仓库分发）
│  ├─ plugin.json           # 清单：元信息 + 依赖 + 对外接口
│  └─ plugin.py             # 入口脚本，必须定义 register(api)
└─ demo.hello/              # 你自己的插件，放进同一个目录即可
   ├─ plugin.json
   └─ plugin.py
```

- 程序启动时扫描 `plugins/*/plugin.json`，校验清单，按依赖排序，然后 `import` 入口脚本并调用 `register(api)`。
- 插件与主程序**同进程**、就是普通 Python 模块：`import app.services.xxx`、`import app.ui.widgets.xxx`
  都可以用。你不需要注册任何东西，把目录放进去就会出现在「插件」页。
- 内置插件和外部插件走**完全相同**的流程（同样的清单、同样的入口、同样的 `register(api)`）；
  区别只是内置插件随程序分发、清单里写了 `"builtin": true`、不能被删除，并且在依赖排序里优先载入。
- 外部插件可以用「插件」页的「导入插件」按钮以**目录**或 **`.zip`** 包的形式安装（导入时会强制
  `builtin=false`），也可以手动把目录拷进插件目录。

## 2. 最小插件

`plugins/demo.hello/plugin.json`：

```json
{
  "id": "demo.hello",
  "name": "演示插件",
  "version": "1.0.0",
  "kind": "page",
  "description": "在导航栏加一个自己的页面。",
  "author": "你的名字",
  "manager_version": "0.1.0",
  "entry": "plugin.py",
  "depends": [],
  "provides": ["hello"]
}
```

`plugins/demo.hello/plugin.py`：

```python
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


def register(api) -> None:          # 入口函数，必须叫 register，参数是 PluginApi
    api.provide("hello", object())  # 对外提供扩展接口（其它插件可以 require）
    ui = api.require("app.ui")      # 主程序提供的界面接口
    ui.add_page("hello", "演示页", _build, icon="HOME", plugin_id=api.plugin_id)


def _build() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.addWidget(QLabel("你好，这是插件页面。"))
    return page
```

改完代码后在「插件」页点一次「刷新」即可整体重载（载入时会重新 `import` 入口脚本）。

## 3. 清单字段（plugin.json）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 插件唯一 id，`^[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$`，通常用 `作者.功能` 形式（如 `demo.hello`） |
| `name` | ✅ | 显示名，出现在「插件」页与界面标题里 |
| `kind` | ✅ | 插件类型，自由字符串，`^[a-z][a-z0-9_.\-]{1,63}$`；内置类型见第 6 节 |
| `version` | 建议 | 插件版本号，仅用于展示 |
| `entry` | ✅ | 相对插件目录的入口脚本，必须存在；里面要有 `register(api)` |
| `description` | 可选 | 一句话介绍 |
| `author` | 可选 | 作者 |
| `manager_version` | 可选 | 需要的最低管理器版本；当前版本见 `src/app/core/version.py` 的 `APP_VERSION`，不满足时插件被标为「异常」 |
| `depends` | 可选 | 依赖的插件 id 列表（字符串或数组），不能依赖自己，必须都已安装 |
| `provides` | 可选 | 对外提供的扩展接口名列表，`^[a-z][a-z0-9_.\-]{1,63}$`，对应 `api.provide(name, provider)` |
| `capabilities` | 可选 | 能力标签（字符串或数组），在插件详情里展示 |
| `extensions` | 类型要求时必填 | 适用的文件扩展名（字符串或数组），如 `[".PNG", "jpg"]`；自动转小写、去掉点、去重 |
| `builtin` | 可选 | `true` 表示随程序分发；导入的插件会被强制为 `false`。**外部插件不要写 `true`** |
| `kind_label` | 可选 | 自定义类型的显示名（如 `"主题"`），写进类型表 |
| `kind_description` | 可选 | 自定义类型的说明 |
| `kind_requires_extensions` | 可选 | 自定义类型是否强制要求声明 `extensions`，默认 `false` |
| `options` | 可选 | 用户可配置的选项声明（列表或对象），插件页据此生成「插件选项」页；插件用 `api.option("键")` 读回，见第 5 节 |

字符串型列表字段（`depends` / `provides` / `capabilities` / `extensions`）既可以写成数组，也可以写成
用逗号、分号或空白分隔的字符串，例如 `"extensions": ".png, jpg jpeg"`。

## 4. 载入流程与生命周期

```
扫描 plugins/*/            →  校验清单  →  套用启用状态（config/plugins.json）
        ↓                                        ↓
依赖拓扑排序（内置优先、依赖在前） → 逐个 import 入口并执行 register(api) → 收集查看器/接口/页面
```

- **顺序**：依赖插件一定先于依赖它的插件载入，所以 `register` 里 `api.require("xxx")` 一定能拿到已载入的接口。
- **程序本体接口**（如 `app.ui`）在每次清空注册表后由主程序重新提供，插件可以直接依赖。
- **失败隔离**：某个插件 `register(api)` 抛错时，它已经 `provide` 的接口会被回收，依赖它的插件会被标成
  「依赖插件未启用：xxx」，其余插件照常载入 —— **插件出错不会影响程序启动**。
- **整体重载**：在「插件」页启用 / 禁用 / 导入 / 删除插件，或点「刷新」，都会整体重新载入；插件登记的
  界面页面也随之增删（见第 7 节）。
- **查看器注册**：重载时查看器注册表会被清空并重建，所以插件里不要长期持有旧的对象引用。

常见的载入错误（会在「插件」页显示为「异常」，原因写在详情里）：

| 报错 | 原因 |
| --- | --- |
| `插件清单缺少 id` / `插件清单缺少 name` | 必填字段没写 |
| `插件 id 不合法：xxx` | id 不满足命名规则 |
| `插件类型不合法：xxx` | `kind` 不满足命名规则 |
| `需要管理器版本 9.9.9，当前 0.1.0` | `manager_version` 高于当前程序版本 |
| `缺少依赖插件：xxx` | `depends` 里写了没安装的插件 |
| `插件依赖存在循环：a → b → a` | 依赖成环 |
| `插件清单缺少 entry（入口文件）` / `入口文件不存在：plugin.py` | 入口脚本缺失 |
| `无法载入插件模块：plugin.py` | 入口脚本导入时报语法错误或缺少依赖库 |
| `插件入口缺少 register(api)：plugin.py` | 入口脚本没定义 `register` |
| `插件选项 kind 不合法：number` | `options` 里的 `kind` 只能是 `bool` / `text` / `choice` |
| `插件选项 zoom_step 是 choice 类型，至少要声明一个选项值：choices` | `choice` 没写 `choices` |
| `插件选项 zoom_step 的默认值不在选项里：2.0` | `choice` 的 `default` 不在 `choices` 里 |
| `插件选项 key 重复：zoom_step` | 同一个 `options` 里写了两个相同的 `key` |
| `插件载入时发生未预期的错误，详见日志` | `register` 内其它异常，日志里有完整堆栈 |
| `依赖插件未启用：xxx` | 依赖被禁用或载入失败 |

## 5. 可用接口：`PluginApi`

`register(api)` 收到的 `api` 就是 `PluginApi`，字段与方法如下。

| 成员 | 说明 |
| --- | --- |
| `api.plugin_id` | 当前插件 id（登记页面 / 查看器归属时用得上） |
| `api.plugin_name` | 当前插件显示名 |
| `api.default_extensions` | 清单里声明的 `extensions`，插件不显式传扩展名时作为默认值 |
| `api.registry` | 扩展接口注册表（`ExtensionRegistry`），一般用不到 |
| `api.add_viewer(name, extensions=(), factory=None, kind="text", description="", capabilities=(), viewer_id="", host="")` | 注册一个查看器，返回注册进注册表的 `Viewer` |
| `api.option(key, default=None)` | 读取用户在「插件选项」页里设置的值（未设置时返回清单默认值），见下文 |
| `api.add(kind, name="", *args, **fields)` | 通用登记入口，按插件类型路由（见第 6 节） |
| `api.entries(kind="")` | 读取本插件登记的自定义类型条目（`PluginContribution`） |
| `api.provide(name, provider)` | 对外提供扩展接口（配合清单 `provides`） |
| `api.require(name)` | 取别的插件 / 主程序提供的接口，取不到会抛错（错误信息提示去「插件」页启用） |
| `api.has(name)` | 接口是否存在，不会抛错 |
| `api.extensions()` | 当前注册表里所有接口名 |

### 查看器约定（`kind = "viewer"`）

```python
def register(api) -> None:
    api.require("dialog")                      # 声明用弹窗外壳显示内容（清单里要写 depends）
    api.add_viewer(
        "演示查看器",
        extensions=("demo",),
        factory=_factory,                      # factory(path, parent) -> QWidget
        kind="text",                           # 查看器内部类别：image/video/audio/archive/text/code/markdown/spreadsheet
        description="演示用查看器。",
        capabilities=("只读",),
        host="dialog",                         # 由哪个扩展接口承载窗口，内置查看器都是 "dialog"
    )
```

- `factory(path, parent)` 返回一个 `QWidget`；如果返回 `None`，查看器会被视为「没有可用控件」。
  建议在 factory 里**延迟导入** `app.ui.viewers.*`，这样插件清单校验阶段不会加载界面依赖。
- `host` 指定承载窗口的扩展接口名。程序自带 `dialog`（内置弹窗页面插件），它会把你的控件放进一个
  独立弹窗外壳里，并提供 Esc / 关闭按钮退出；插件也**可以自己提供 `host` 接口**，只要对方提供了
  `open_page(title=..., content_factory=..., meta=...)`。
- 扩展名匹配由注册表按后缀完成；同一个扩展名有多个查看器时，先注册的优先。打开方式还可以在
  「打开方式」页按扩展名改用系统默认程序（见 HELP 文档）。

### 插件选项（`options`）与 `api.option`

插件可以在清单里声明**用户可配置的选项**，程序会在插件页给它生成「插件选项」页，改完的值存进
`config/plugins.json`，插件在 `register(api)` 里用 `api.option("键")` 读回来：

```json
{
  "options": [
    {"key": "fit_on_open", "label": "打开时适应窗口", "kind": "bool", "default": true,
     "description": "小图也放大到填满窗口。"},
    {"key": "zoom_step", "label": "缩放步长", "kind": "choice",
     "choices": {"1.25": "1.25×（默认）", "1.5": "1.5×（快速）"}, "default": "1.25"},
    {"key": "watermark", "label": "水印文字", "kind": "text", "default": ""}
  ]
}
```

```python
def register(api) -> None:
    fit = api.option("fit_on_open", True)          # 没设置过时用清单里的默认值
    step = float(api.option("zoom_step", "1.25"))
    api.add_viewer("演示查看器", ("demo",), factory=make_factory(fit, step))
```

- `kind` 支持 `bool`（开关）/ `text`（单行文本）/ `choice`（下拉单选，必须给 `choices`，可以写成对象或
  `[["值", "显示名"], ...]`）。
- `key` 要满足 `^[a-z][a-z0-9_.\-]{0,63}$`；`label` / `description` 可选，`description` 会作为控件提示。
- 取值会被规范化（`"是"` / `"开"` / `"1"` → `True`，无法识别的取值回退默认值），所以 `api.option` 拿到
  的类型是可预期的。
- 插件选项改动后插件会被**整体重新载入**，工厂闭包里的取值随之更新（上面的例子就是这么生效的）。
- 选项列表与当前取值也会显示在插件页详情里（`插件选项（N）：…`）。

### 用 `app.open_with` 帮用户改打开方式

`app.open_with` 是主程序提供的扩展接口（`OpenWithApi`），插件可以据此把某个扩展名指定成
「用本插件打开」，实现「一键把本插件支持的格式都设为默认」之类的功能：

| 方法 | 说明 |
| --- | --- |
| `suffix_of(path)` | 从路径取扩展名 |
| `viewers_for(suffix)` / `viewer_ids_for(suffix)` | 该扩展名可用的查看器 |
| `suffixes_of(viewer_id)` / `suffixes_of_plugin(plugin_id)` | 某个查看器 / 插件覆盖的扩展名 |
| `current_viewer_id(suffix)` | 当前指定的查看器 id（空字符串表示自动） |
| `set_viewer(suffix, viewer_id)` | 指定某个扩展名用哪个查看器 |
| `use_viewer_for_all(viewer_id, suffixes=None)` | 一次性把某查看器覆盖的（或指定的）扩展名都改成用它打开 |
| `reset_viewer(viewer_id, suffixes=None)` | 撤销上述指定，回到自动选择 |

```python
def register(api) -> None:
    open_with = api.require("app.open_with")
    viewer = api.add_viewer("演示查看器", ("demo",))
    open_with.use_viewer_for_all(viewer.id)       # 本插件声明的格式都用它打开
```

「插件选项」页在本插件注册了查看器时，也会自动列出这些格式的勾选框，勾选等同于调用 `set_viewer`。
内置的 `builtin.image` 插件就是完整示例：3 个选项 + 图片格式的批量勾选。

### 内置的扩展接口

| 接口名 | 提供者 | 能力 |
| --- | --- | --- |
| `dialog` | 内置插件 `builtin.dialog` | `open_page(title, content_factory, meta="", buttons=(), width=980, height=700)` → 独立弹窗；`windows()`、`close_all()` |
| `app.ui` | 主程序本体 | `add_page(key, title, factory, icon="", bottom=False, plugin_id="")` 等，见第 7 节 |
| `app.open_with` | 主程序本体 | `set_viewer` / `use_viewer_for_all` / `reset_viewer` 等，见上 |

大插件可以像内置插件一样 `api.provide("自己的接口", 对象)`，让别的插件依赖你 —— 依赖关系写在
清单的 `provides` / `depends` 里，程序会保证载入顺序。

## 6. 插件类型（kind）与自定义类型

- `kind` **不是枚举**：它是自由字符串，内置两个类型：
  - `viewer`（显示名「打开方式」）：声明了至少一个 `extensions`，用 `api.add_viewer(...)` 注册查看器；
  - `page`（显示名「弹窗页面」）：界面类插件，用 `api.provide(...)` 提供扩展接口。
- 类型表是**累积**的：类型信息来自写清单的插件（`kind_label` / `kind_description` /
  `kind_requires_extensions`）。两个插件声明同一个 `kind` **不会冲突**，只会合并显示信息；
  你也可以完全自定义一个新类型：

```json
{
  "id": "demo.panorama",
  "name": "全景插件",
  "kind": "panorama",
  "kind_label": "全景",
  "kind_description": "球面全景浏览。",
  "kind_requires_extensions": true,
  "entry": "plugin.py",
  "extensions": [".jpg", ".png"],
  "depends": [],
  "provides": ["panorama"]
}
```

```python
def register(api) -> None:
    api.add("panorama", "球面全景", fov=110)   # 登记一条自定义类型条目
```

自定义条目会被记录成 `PluginContribution(kind, plugin_id, name, args, fields)`，其它插件和界面可以读：

```python
from app.services.plugin_service import plugin_service

for entry in plugin_service.contributions("panorama"):
    print(entry.plugin_id, entry.name, entry.fields)
```

- 如果某个类型的 `contributor` 指向主程序内部的登记方法（例如 `viewer` → `add_viewer`），
  `api.add("viewer", ...)` 会直接路由到那个方法；**`contributor` 只能由主程序设置**，清单里不能写。
- 「插件」页的类型下拉框里的类型，就是当前已发现插件累积出来的类型表，所以新类型装上插件后立刻可选。

## 7. 大型插件：挂页面、复用主程序能力

主程序通过 `app.ui` 扩展接口（`AppUiApi`，源码 `src/app/core/app_ui.py`）把「加导航页」的能力开放给插件：

```python
def register(api) -> None:
    ui = api.require("app.ui")
    ui.add_page(
        "demo.page",        # 页面 key：小写字母开头，可含数字、下划线、点和连字符，同一插件内唯一
        "我的工具",          # 导航项标题
        _build,             # 无参工厂，返回 QWidget；在窗口装配时才被调用
        icon="HOME",        # FluentIcon 的名字（如 HOME / TILES / DEVELOPER_TOOLS），未知名字回退通用图标
        bottom=True,        # True 放到侧边导航底部（设置上方），默认顶部
        plugin_id=api.plugin_id,
    )
```

- **生命周期**：页面在插件载入时登记，主程序装配导航时创建控件；插件被禁用 / 删除后页面会自动消失，
  重新启用又会出现。同一个插件重复登记同一个 key 只会更新，不会出现重复导航项。
- **错误隔离**：页面工厂抛异常时只会显示一个「插件页面无法显示：…」的提示页，不会影响主界面。
- **不要做重活**：`add_page` 本身只记一条登记；真正耗时的加载放在页面控件的 `showEvent` 或后台线程里。
- **复用主程序能力**：插件是普通 Python 模块，可以直接使用主程序的模块，例如
  `from app.services.item_service import item_service`、`from app.core.signals import signalBus`、
  `from app.ui.widgets.xxx import ...`、`from app.core.config import config`。注意这些内部接口**没有稳定
  版本承诺**，跨版本升级可能变化；用清单的 `manager_version` 声明你需要的版本。
- **弹出窗口**：需要独立窗口时优先依赖内置的 `dialog` 接口（`api.require("dialog").open_page(...)`），
  它已经处理好窗口装饰、Esc 退出与生命周期。

## 8. 排错

1. 「插件」页 → 选中插件 → 详情区会显示类型、来源、状态、依赖、扩展接口、入口文件与错误原因。
2. 载入失败的完整堆栈在日志里（`logs/app-*.log`，设置页可切换日志模式），关键字「插件载入失败」「插件载入异常」。
3. 启用状态保存在 `config/plugins.json`（`{"version": 1, "plugins": {"<id>": {"enabled": true, ...}}}`）；
   删掉对应条目等于恢复默认（启用）。
4. 改了插件代码后点「插件」页的「刷新」；载入使用模块名 `dm_plugin_<id>`，不会与主程序模块冲突，
   但同一进程内不会重复执行 `register`，所以不要依赖模块级全局状态的持久性。
5. 「打开插件目录」按钮可以定位当前插件的 `plugin.json`，方便就地编辑。

## 9. 完整示例：一个带页面、接口和自定义类型的插件

```
plugins/demo.hello/
├─ plugin.json
└─ plugin.py
```

```json
{
  "id": "demo.hello",
  "name": "演示插件",
  "version": "0.1.0",
  "kind": "page",
  "description": "演示页面、扩展接口与自定义类型。",
  "author": "你的名字",
  "manager_version": "0.1.0",
  "entry": "plugin.py",
  "depends": ["builtin.dialog"],
  "provides": ["hello"],
  "capabilities": ["演示页面", "对外接口", "自定义类型"]
}
```

```python
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class HelloApi:
    def greet(self, name: str) -> str:
        return f"你好，{name}！"


def _build() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.addWidget(QLabel("这是插件页面。"))
    button = QPushButton("弹出窗口")
    button.clicked.connect(_popup)
    layout.addWidget(button)
    return page


def _popup() -> None:
    from app.core.extensions import extension_registry

    dialog = extension_registry.provider("dialog")
    if dialog is not None:
        dialog.open_page("演示弹窗", lambda parent: QLabel("来自插件的内容"), meta="demo.hello")


def register(api) -> None:
    api.require("dialog")           # 清单里 depends 声明了 builtin.dialog
    api.provide("hello", HelloApi())
    api.add("panorama", "球面全景", fov=110)   # 自定义类型的条目
    api.require("app.ui").add_page("hello", "演示页", _build, icon="HOME", plugin_id=api.plugin_id)
```

其它插件装了 `demo.hello` 后，只要在清单里写 `"depends": ["demo.hello"]`，就能：

```python
hello = api.require("hello")
print(hello.greet("世界"))
```

## 10. 速查

| 我想…… | 怎么做 |
| --- | --- |
| 让某种文件用我的界面打开 | `kind: "viewer"` + `extensions` + `api.add_viewer(..., factory, host="dialog")` |
| 在程序里加一个页面 | 依赖主程序的 `app.ui`：`api.require("app.ui").add_page(...)`（第 7 节） |
| 弹一个独立窗口 | `api.require("dialog").open_page(title, content_factory)` |
| 给别的插件提供能力 | 清单 `provides` + `api.provide("名字", 对象)` |
| 用别的插件的能力 | 清单 `depends` + `api.require("名字")` |
| 造一个新插件类型 | 清单 `kind` 写新名字，可选 `kind_label` / `kind_description` / `kind_requires_extensions`，用 `api.add(kind, ...)` 登记条目 |
| 让用户配置插件参数 | 清单写 `options`（bool / text / choice），`register` 里用 `api.option("键")` 读回（第 5 节） |
| 让某种格式固定用我的查看器打开 | `api.require("app.open_with").set_viewer("png", viewer.id)`，或 `use_viewer_for_all(viewer.id)` |
| 声明最低程序版本 | 清单 `manager_version` |
| 用主程序的内部能力 | 直接 `import app.*`（无稳定承诺，注意 `manager_version`） |
