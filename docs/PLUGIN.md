# 插件开发指南

这份文档教你**怎么写一个插件**：从最小插件开始，再到库插件、依赖与排错。

- 清单字段、版本范围、依赖规则、载入阶段、状态文件等**规范**见 [`PLUGIN_PROTOCOL.md`](PLUGIN_PROTOCOL.md)。
- 程序开放了哪些**扩展点**、会广播哪些**事件**见 [`SDK.md`](SDK.md)。
- 现成的例子：`plugins/example.ui_extension/`（界面扩展点 + 事件）、`plugins/example.model_usage/`（消费 `model.open` 扩展接口）、`plugins/builtin.lib.viewer/`（查看器库插件）、`plugins/builtin.lib.editor/`（编辑器库插件）、`plugins/lib.model/`（模型库插件：下载 / 懒加载 / 运行环境）、`plugins/builtin.viewer.image/`（功能插件样板）。

## 1. 插件是什么

插件就是一个目录，放在 `plugins/` 下，用 `plugin.json` 声明自己，用 `plugin.py` 里的插件类干活：

```
plugins/demo.hello/
  plugin.json     清单：我是谁、依赖谁、有哪些选项
  plugin.py       插件类：注册贡献、订阅事件
  .data/          可选：插件自己的数据文件
  PLUGIN.md       说明：这个插件有什么用
```

程序启动时按依赖顺序把插件载入内存，插件通过 **SDK**（`app.sdk`）拿到程序给的能力。控制台会播报进展：先按「启动 1/8 … 8/8」报告启动阶段，插件部分打印「插件扫描完成：发现 N 个（启用 X、未启用 Y、清单有误 Z）」，载入完成后打印汇总「插件载入：共 N 个（已启用 X、未启用 Y）；库插件 …、功能插件 …」；

- 往**扩展点**放东西（概览卡片、工具栏按钮、右键菜单、详情行、设置卡片、页面、查看器、编辑器……）；
- 订阅程序的**事件**（导入、删除、用户切换、主题变化……）；
- 读**插件选项**与**数据文件**；
- 用 `provide()` / `require()` 在插件之间交换运行期对象；
- 用 `libraries` 把可复用的类暴露给别的插件（库插件）。

协议里**没有「插件类型」这个概念**：一个插件想当查看器，就依赖查看器库并继承它的基类；
想同时是别的什么，就再依赖另一个库。插件之间请用 `depends` + `dm_plugin.<id>` 静态导入显式取库。

## 2. 快速开始：最小插件

### 2.1 建目录与清单

`plugins/demo.hello/plugin.json`：

```json
{
  "id": "demo.hello",
  "name": "打招呼插件",
  "version": "1.0.0",
  "api_version": ">=1.0 <2.0",
  "description": "在概览页加一张卡片，数一数这次会话导入了几条数据。",
  "author": "你的名字",
  "entry": "plugin.py"
}
```

- `id` 要与目录名一致（`demo.hello` → `plugins/demo.hello/`）。
- `id`、`name` 与 `api_version` 是必须的（`api_version` 写清适配的 SDK 版本范围，例如 `">=1.0 <2.0"`，缺了会被拒绝载入）；外部插件还要 `entry`，其余字段见协议文档。

### 2.2 写插件类

`plugins/demo.hello/plugin.py`：

```python
from app.sdk import Events, ExtensionPoint, Plugin, PluginContext


class HelloPlugin(Plugin):
    """最小插件：一张概览卡片 + 一个事件计数。"""

    def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._imported = 0
        ctx.contribute(
            ExtensionPoint.HOME_KPI,
            {"title": "本次导入", "value": self._text, "sub": "打招呼插件", "icon": "HEART"},
            key="demo.hello.kpi",
            description="打招呼插件贡献的概览卡片",
        )
        ctx.on(Events.ITEM_IMPORTED, self._on_imported)

    def describe(self) -> list[tuple[str, str]]:
        """插件页详情里的额外信息行。"""
        return [("导入计数", str(self._imported))]

    def _text(self) -> str:
        return str(self._imported)

    def _on_imported(self, **payload) -> None:
        self._imported += 1
```

要点：

- 插件类必须继承 `app.sdk.Plugin`，程序在载入时构造它并调用 `setup(ctx)`；入口里只有一个 `Plugin` 子类时可以省略清单的 `class` 字段。
- `ctx.contribute(...)` 注册贡献，`ctx.on(...)` 订阅事件；插件被禁用 / 卸载时程序自动撤销这些，你不用手动清理。
- 事件处理函数**必须能接住关键字参数**（`def _on_x(self, **payload)`），因为程序是 `handler(**payload)` 调用的。
- 需要跟随数量变化重新渲染的东西，把 `value` 写成一个回调（如上面的 `self._text`），界面刷新时会重新取值。

### 2.3 写说明

`plugins/demo.hello/PLUGIN.md`：一句话说清这个插件有什么用，再列出它用到的扩展点与事件。
可以照抄 `plugins/example.ui_extension/PLUGIN.md` 的结构。

### 2.4 跑起来

1. 把目录放进 `plugins/`，重启程序（或在「插件」页点「重新载入」）；
2. 打开「插件」页确认它已载入且为「已启用」；
3. 去概览页看那张卡片，导入一个文件试试计数。

## 3. SDK 速览

### 3.1 插件父类与上下文（速览）

`Plugin` 只有四件事是插件自己要写的：`setup(ctx)`（注册贡献、订阅事件、读配置）、`teardown()`（释放自己的额外资源）、
`describe()`（插件页详情里的 `[(标题, 内容), ...]`）、以及用 `self.log` 记日志。其余成员（`id` / `name` / `path` / `manifest` /
`data_dir` / `data_path` / `data`）由载入器填好，供插件读取自己的身份与 `.data/` 数据文件。

`PluginContext`（`setup` 的入参）是插件与程序打交道的唯一门面，常用成员：

| 想干什么 | 用什么 |
| --- | --- |
| 读自己的身份 / 清单 | `ctx.plugin_id` / `ctx.plugin_name` / `ctx.manifest` |
| 记日志 / 播报到控制台 | `ctx.log` / `ctx.console` |
| 读插件数据 | `ctx.data("name")` / `ctx.data_path("name")`（文件放 `.data/`） |
| 读 / 写自己的选项 | `ctx.option(key, default)` / `ctx.options()` / `ctx.set_option(key, value)` |
| 往扩展点放东西 | `ctx.contribute(ExtensionPoint.XXX, value, key="…")` / `ctx.contributions()` |
| 订阅 / 广播事件 | `ctx.on(Events.XXX, handler)` / `ctx.emit(Events.XXX, **payload)` |
| 注册页面 / 查看器 / 编辑器 | `ctx.add_page(...)` / `ctx.add_viewer(...)` / `ctx.add_editor(...)` |
| 插件之间交换对象 | `ctx.provide(name, obj)` / `ctx.require(name)` / `ctx.has(name)` |
| 宿主服务（高级） | `ctx.host` |

扩展点名用 `ExtensionPoint` 常量、事件名用 `Events` 常量，别手写字符串（写错了贡献会静默失效）。
**完整的成员表、参数与形状见 [`SDK.md`](SDK.md)**——本文只留上手要用到的那几行。

### 3.3 允许导入什么

```python
from app.sdk import Plugin, PluginContext, Events, ExtensionPoint   # ✅
from dm_plugin.<依赖的插件 id>.plugin import SomeClass               # ✅ 要在 depends 里声明（只能取对方 libraries 声明的模块）
from app.services.item_service import ItemService                    # ❌ 自检会报错
```

插件只能依赖 `app.sdk`、Python 标准库、第三方包，以及**声明过的** `dm_plugin.<id>`。
程序内部模块（`app.core` / `app.services` / `app.repositories` / `app.db` / `app.data` / `app.ui.*`）
对插件不开放：需要什么能力就通过扩展点、事件或插件库来拿（自检 `plugin_imports` 只放行 `app.sdk`）。

跨插件引用只能落在对方 `libraries` 里声明过的模块上：`plugin.py` 是 `dm_plugin.<id>.plugin`，`.plugin/api.py` 是
`dm_plugin.<id>.api`、`.plugin/ui/controls.py` 是 `dm_plugin.<id>.ui.controls`（目录用点分、不带 `.plugin`）。
没在 `libraries` 里声明的模块属于实现细节，不要去 import。

### 3.4 让 IDE 认识 `dm_plugin`

`dm_plugin.<id>` 是载入期间在内存里合成的包，磁盘上并不存在，所以编辑器会把 `from dm_plugin...`
标成找不到模块。仓库里备了一份生成好的桩：

    python scripts/plugin_stubs.py     # 按插件清单刷新 stubs/dm_plugin/**

把 `stubs` 目录标记为源码根（PyCharm：右键 → Mark Directory as → Sources Root；仓库自带的
`.idea/D-MyDataManager.iml` 已经配好），导入就不再标红。改了清单或库的 `__all__` 之后重跑一次脚本即可，
自检 `plugin_stubs_current` 会检查桩与清单一不一致。

### 3.5 界面按钮的写法

插件里的按钮和程序里一样按「有没有图标」分两种：**只有文字**的直接用 qfluentwidgets 的 `PushButton`；
**图标 + 文字**的请用 SDK 暴露的 `ui.IconTextButton`（等同 `PushButton`，多记了一份完整文字）：

```python
from qfluentwidgets import FluentIcon     # 插件可以直接 import qfluentwidgets
from app.sdk import ui

button = ui.IconTextButton(FluentIcon.HEART, "示例动作", self)   # 图标 + 文字：用这个
```

用户在「设置 → 外观 → 简化显示」里选到「默认」或「完全简化」后，`ui.IconTextButton` 会自己变成只有图标、把文字挪进提示条，
插件不用写任何适配代码；没有图标的按钮不受影响。往 `app.ui.manage.toolbar` 这类扩展点贡献的按钮由宿主生成，
同样是 `IconTextButton`，插件只管给 `text` 与 `icon`。自检 `icon_text_buttons` 会扫 `plugins/`，
发现「图标 + 文本」却用裸 `PushButton` 的写法会直接报错。

另外，插件界面上不要把说明文字成排铺出来（`CaptionLabel` 一个个挂着）：某个控件的用途写成 `widget.setToolTip("…")`，
鼠标停住（默认 2 秒，用户在「设置 → 外观 → 悬停提示延迟」里改）才会弹出；写在容器（页面分区卡片、列表行）上的提示还会被
里面的子控件继承，所以一句话写在容器上就够，不必每个标签都设一遍。

## 4. 库插件怎么写

「库插件」= 把可复用的类 / 函数做成模块，给别的插件继承或调用。内置的查看器库就是典型例子：

库插件通常只声明 `libraries`（给的是类，消费方要继承 / 实例化）；如果还要给人「程序里当前那一个实例」，
再声明 `provides` 把它登记成扩展接口 —— 内置弹窗工具库两者都用（类走 `libraries`，宿主实例走 `provides: dialog`），
分工见 [协议文档 2.7](PLUGIN_PROTOCOL.md)。声明了 `provides` 却忘了在 `setup()` 里 `ctx.provide()` 时，程序只在该插件的
备注里提醒一句（不算载入失败），补上注册后自动清掉。

`plugins/builtin.lib.viewer/plugin.json`：

```json
{
  "id": "builtin.lib.viewer",
  "name": "查看器库",
  "version": "1.0.0",
  "api_version": ">=1.0 <2.0",
  "entry": "plugin.py",
  "libraries": [
    {"name": "viewer", "module": "plugin.py", "description": "查看器插件基类 ViewerPlugin、窗口外壳 ViewerWindow、注册表 ViewerRegistry"}
  ]
}
```

`plugins/builtin.lib.viewer/plugin.py`（库模块就是入口文件）里定义基类（节选）：

```python
from app.sdk import Plugin


class ViewerPlugin(Plugin):
    """查看器插件基类：子类只需要实现 create_view()。"""

    default_kind = "text"
    default_host = "dialog"
    default_order = 100

    def setup(self, ctx):
        self._ctx = ctx
        data = ctx.data("viewer")          # .data/viewer.json：名称、扩展名、能力……
        ...
        ctx.require(self.default_host)     # 先确认弹窗外壳可用
        ctx.add_viewer(name, extensions=..., factory=self.create_view,
                       opener=self.open_view, kind=..., host=...)

    def create_view(self, path, parent=None):
        raise NotImplementedError      # 子类在这里造自己的视图控件

    def open_view(self, path, parent=None):
        """在自己的目录里造好视图，再让弹窗工具库弹出来。"""
        host = self._ctx.require(self._host)
        host.open_page(title=..., content_factory=lambda container: ViewerWindow(
            path, lambda holder: self.create_view(path, holder), self.view_name, container))
        return True, self.view_name
```

使用方（内置 7 个查看器插件）只需要：

```python
from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin


class ImageViewerPlugin(ViewerPlugin):
    """按扩展名打开图片。"""

    def create_view(self, path, parent=None):
        return ImageView(path, parent)
```

配合清单：

```json
{
  "id": "builtin.viewer.image",
  "depends": [{"id": "builtin.lib.viewer"}, {"id": "builtin.lib.ui"}]
}
```

写库插件的注意点：

- 用 **`libraries`** 声明要暴露的 `.py` 模块（文件必须存在且非空）；使用者用 `dm_plugin.<你的 id>.<模块>` 导入，或者 `library("<你的 id>", "<模块>")`（兜底写法）。
- 库插件自己也可以有 `entry` 与 `setup()`（可以做初始化、`provide()` 运行期对象）；纯数据 / 纯代码的库插件可以不要 `entry`（内置插件允许省略）。
- 别人用你的库之前必须 `depends` 你；你也可以在库里调 `requires("...")` 断言使用者确实声明过依赖。
- 只改库模块的代码不会「热更新」：载入时程序会清理上一轮的 `dm_plugin.*` 模块，重新导入。

## 5. 多依赖与依赖顺序

一个插件可以同时依赖多个插件（一个插件也可以依赖多个库）：

```json
{
  "depends": [
    {"id": "builtin.lib.viewer"},
    {"id": "builtin.lib.ui"},
    {"id": "other.storage", "version": ">=1.2 <2", "optional": true}
  ]
}
```

- 程序按依赖拓扑排序载入：被依赖的先载入；同层内「内置优先、其次按 id」。
- `version` 是版本范围（`>=1.0 <2`、`^1.2`、`~1.2.3`、`1.2.*`、`A || B` 见协议文档第 3 节）；不满足会让插件标为异常并禁用。
- `optional: true`：目标不存在也能载入。
- `conflicts` 用来声明冲突插件：两边都启用时按载入顺序靠前者胜出，另一个不能启用（不是载入失败）；载入顺序由 `depends` 决定，旧的 `load_after` / `incompatible` 字段已取消（写了当场报错并指明新写法）。
- 循环依赖会被指出来：`插件依赖存在循环：a → b → a`。

## 6. 目录规范与「别重复声明」

```
plugins/<id>/
  plugin.json     参数与配置（名称、版本、依赖、选项）
  plugin.py       入口：插件类、入口函数（`entry` 固定为它）
  .plugin/        实现模块（libraries 声明的模块；载入时会被挂进包搜索路径）
  .data/          具体数据（扩展名表、能力表、文案、模板……）
  PLUGIN.md       说明文档
```

- **清单只放参数与配置，具体数据放 `.data/`**：例如查看器的扩展名清单、能力列表都在 `.data/viewer.json`，不要在清单里重复一遍（协议已经删掉了 `extensions` / `capabilities` 字段，写了会直接报错）。
- `plugin.py` 里不要写死「名称 / 说明 / 扩展名」这类常量：从 `ctx.manifest` 或 `ctx.data(...)` 读，改数据不用改代码。
- 每个插件都要有 `PLUGIN.md`，写清用途、依赖、贡献、`.data/` 里各文件是什么。

## 7. 常见错误排查

| 现象 | 原因与处理 |
| --- | --- |
| 插件页里根本看不到插件 | 目录不是 `plugins/` 的直接子目录，或目录里没有 `plugin.json`（以 `.` / `_` 开头的目录会被跳过） |
| 插件标为「异常」，错误是 `插件清单出现未知字段：kind` | 旧协议残留（`kind` / `kinds` / `extensions` / `capabilities` / `kind_label` 等）；删掉这些字段，数据改放 `.data/` |
| `缺少 plugin.json：<目录>` / `插件清单不是合法的 JSON：…` | 清单缺失或 JSON 语法错（多用编辑器校验一次） |
| `入口文件不存在：plugin.py` | 插件目录里缺 `plugin.py`（协议 v2 的 `entry` 固定为它） |
| `插件 <id> 的查看器数据缺少记录：.data/viewer.json 的 items 里要有 key = <插件 id>` | 数据文件没建好，或统一清单 `items` 里缺少 `key` 等于插件 id 的记录 |
| `libraries 的库 viewer 的 module 必须是 .py 文件` / `声明的模块是空文件` | 库模块必须是存在的非空 `.py` |
| 载入失败，日志里是 `插件入口导入失败，详见日志` | 入口脚本导入期抛异常（语法错 / 导入不存在的模块 / 顶层代码出错），看日志里的原始异常 |
| `ModuleNotFoundError: No module named 'dm_plugin.xxx'` | 忘了在 `depends` 里声明该插件，或模块路径写错 |
| `取不到插件库：…` / `插件未在 depends 里声明依赖：…` | 取库前没有声明依赖；补 `depends` 或改用静态导入 |
| `插件初始化失败，详见日志` | `setup(ctx)` 里抛异常（常见：`require()` 的接口不存在、读数据文件时字段缺失） |
| 贡献的东西没出现在界面上 | 扩展点名写错（用 `ExtensionPoint` 常量）、插件被禁用、或界面没刷新（重新打开该页试试）；回调抛异常只记日志，看日志确认 |
| 改了插件选项没有生效 | 选项改动会让插件重新载入，重新打开界面即可；如果 `setup()` 里没读 `ctx.option()` 就不会生效 |
| 想让插件默认不启用 | 「插件」页把它设为「禁用」（状态存在 `.configs/plugins.json`） |

排查时的几个顺手动作：

```powershell
.venv\Scripts\python.exe -m compileall plugins      # 语法检查
# 看日志：程序日志里插件相关的行都带插件名
```

## 8. 完整示例

想一次看全「界面扩展点 + 事件 + 数据文件 + 选项」的写法，读这两个现成插件：

- `plugins/example.ui_extension/`：往概览卡片、工具栏、右键菜单、详情、导入筛选、设置页各贡献一样东西，并订阅 5 个事件；它的 `PLUGIN.md` 是各插件说明文档的模板。
- `plugins/example.model_usage/`：**消费别的插件提供的扩展接口**的样板——按能力向 `dm_plugin.lib.model.api` 取模型租约（`acquire` → `invoke` → `close`），没有模型可用时优雅降级；库插件一侧的契约见 [`../plugins/lib.model/PLUGIN.md`](../plugins/lib.model/PLUGIN.md)。
- `plugins/builtin.viewer.image/`：功能插件的样板——`plugin.py` 只留一个 `create_view()`，扩展名 / 能力 / 宿主都在 `.data/viewer.json`，用户可配置项在清单的 `options` 里。

## 9. 速查

**最小清单**

```json
{
  "id": "demo.hello",
  "name": "打招呼插件",
  "version": "1.0.0",
  "api_version": ">=1.0 <2.0",
  "entry": "plugin.py"
}
```

**最小插件类**

```python
from app.sdk import Plugin


class HelloPlugin(Plugin):
    def setup(self, ctx):
        ...
```

**常用一行**

```python
ctx.contribute(ExtensionPoint.HOME_KPI, {"title": "标题", "value": "值"})   # 概览卡片
ctx.on(Events.ITEM_IMPORTED, self._on_imported)                            # 订阅事件
data = ctx.data("info")                                                     # 读 .data/ 里的文件
value = ctx.option("zoom_step", 1.25)                                       # 读插件选项
ctx.add_page("hello", "演示页", self._build, icon="HOME")                    # 加一个页面
ctx.add_viewer("我的查看器", extensions=["abc"], factory=self._view)         # 注册查看器
ctx.add_editor("我的编辑器", extensions=["abc"], factory=self._edit)         # 注册编辑器
ctx.require("dialog")                                                       # 用别的插件提供的接口
```

**相关文档**

- [`PLUGIN_PROTOCOL.md`](PLUGIN_PROTOCOL.md)：清单字段、依赖与版本、载入阶段、状态文件
- [`SDK.md`](SDK.md)：扩展点与事件清单
- 各插件目录下的 `PLUGIN.md`：每个内置插件的用途与贡献
