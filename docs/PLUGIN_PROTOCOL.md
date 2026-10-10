# 插件协议（plugin.json）

这份文档是**规范**：清单字段、依赖与版本、目录约定、载入阶段、状态文件。

- 想学「怎么写一个插件」，看根目录的 [`PLUGIN.md`](PLUGIN.md)（教程）。
- 想查「程序开放了哪些扩展点、会广播哪些事件」，看 [`SDK.md`](SDK.md)。

版本：SDK 协议 `1.1`（`src/app/sdk/version.py` 的 `SDK_VERSION`；1.1 只**新增**了 `app.sdk.media` 与 `media.open` 扩展接口，`">=1.0 <2.0"` 这类旧范围继续满足）。插件清单只声明 `api_version`（适配的 SDK 版本范围）；程序自身的协议版本记在 `src/app/core/runtime/runtime.json` 的 `manager_version`（由 `src/app/core/runtime/version.py` 读成 `MANAGER_VERSION`），它已经不是清单字段。

## 1. 目录规范

每个插件一个目录，目录名就是插件 `id`：

```
plugins/
  <id>/
    plugin.json      必需：清单（entry 固定 plugin.py，不再有 data 字段）
    plugin.py        必需：唯一入口（libraries 里最常声明的那一个模块）
    .plugin/         其余代码（运行期按 dm_plugin.<id>.<模块名> 导入）
    .data/           数据文件（ctx.data("name") 读 name.json）
    PLUGIN.md        必需：用一句话说清这个插件有什么用、贡献了什么
```

- `plugin.json` 缺字段、写错类型、出现未知字段都会让插件**载入失败**（见第 6 节），不会影响程序启动。
- 插件根目录**必须且只能**有上面这几项：多出来的文件会被自检 `plugin_layout_is_v2` 报错（代码进 `.plugin/`、数据进 `.data/`）。
- 插件目录必须是 `plugins/` 的直接子目录，目录名以 `.` 或 `_` 开头会被跳过。
- 目录名与清单 `id` 不一致时，以清单 `id` 为准（但不一致会让「页面路由 / 依赖声明」看起来对不上，不建议）。

## 2. 清单字段

### 2.1 完整字段表

| 字段 | 必需 | 类型 | 说明 |
| --- | --- | --- | --- |
| `id` | 是 | 字符串 | 插件唯一标识，匹配 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`（小写字母开头，点分段） |
| `name` | 是 | 字符串 | 显示名（插件页列表、详情头部） |
| `version` | 否 | 字符串 | 插件自己的版本号，用于别人 `depends` 里的版本范围比较 |
| `description` | 否 | 字符串 | 一句话说明（插件页详情） |
| `author` | 否 | 字符串 | 作者（插件页可按创建者筛选） |
| `entry` | 内置可省 | 字符串 | 入口脚本，协议 v2 固定 `"plugin.py"`；写别的值报 `协议 v2 的入口固定为 plugin.py`；内置插件省略时按 `plugin.py` 处理 |
| `class` | 否 | 字符串 | 插件类名；入口里只有一个 `Plugin` 子类时可以省略 |
| `api_version` | 是 | 范围字符串 | 适配的 SDK 版本范围，例如 `>=1.0 <2.0`；缺字段、范围写法非法、当前 SDK 不满足都会拒绝载入 |
| `depends` | 否 | 数组 | 依赖的插件，见 2.2 |
| `conflicts` | 否 | 字符串数组 | 冲突的插件 id：不能**同时启用**（对方没启用就不算冲突；两个都启用时靠前者胜出、后者保持禁用并提示与哪个插件冲突）。载入不受影响，见 2.3 |
| `provides` | 否 | 字符串数组 | 声明对外暴露的**扩展接口名**（`ctx.provide()` / `ctx.require()`），见 2.7 |
| `libraries` | 否 | 数组 | 对外暴露的**库模块**，见 2.4，与 `provides` 的分工见 2.7 |
| `options` | 否 | 数组 | 用户可配置项，见 2.6 |
| `builtin` | 否 | 布尔 | 只由内置插件声明；导入的插件会被强制改回 `false` |
| `enabled` | 否 | 布尔 | 默认启用状态（默认 `true`）；内置插件可用它默认禁用自己，用户在「插件」页改动后以状态文件为准 |

### 2.2 依赖声明

`depends` 支持简写与完整写法混用：

```json
{
  "depends": [
    "builtin.lib.ui",
    {"id": "builtin.lib.viewer"},
    {"id": "other.plugin", "version": ">=1.2 <2", "optional": true}
  ]
}
```

- `id` 必须是合法的插件 id，且不能是自己；同一个 id 不能声明两次。
- `version` 是版本范围（第 3 节），比较对象是被依赖插件的 `version`；被依赖插件没写 `version` 时跳过版本比较。
- `optional: true` 表示「有就用，没有也能跑」：目标不存在时不会让插件失败。
- 依赖的插件被禁用时，依赖方也不会载入（程序按「已启用」的插件集合解析依赖）。

### 2.3 冲突与顺序

- `conflicts`：**只影响启用，不影响载入**。列表里任意一个插件同时启用时，本插件不能启用：已经在启用集合里的那个胜出（两个都启用时按实际载入顺序靠前者胜出），另一个保持禁用，插件页显示「与插件冲突」，控制台打印 `插件启用失败：<id>（与插件冲突：<对方>）；先禁用插件 <对方> 再启用本插件`。把胜出的那个禁用后，另一个即可启用。声明是对称的：任意一方写了就算双方冲突。
- 载入顺序不用单独声明：同层里谁先谁后由依赖决定（想排在被依赖者之后，就写进 `depends`）。已取消的 `load_after` / `incompatible` 字段会在 `manifest` 阶段报 `插件清单已取消字段 …：请改用 …`。
- 实际顺序：先按依赖拓扑排序（被依赖的先载入），同层内按 `(内置优先, id 字典序)` 排。
- 循环依赖报错文本：`插件依赖存在循环：a → b → a`。
- 载入失败（阶段 `manifest` / `dependency` / `import` / `construct` / `setup`）才是插件不可用：清单字段与版本范围不适配、缺依赖 / 依赖未启用、入口导入失败、构造或初始化抛异常。冲突不属于载入失败。

### 2.4 库模块（libraries）

库插件把「可复用的类 / 函数」作为模块暴露给其他插件：

```json
{
  "libraries": [
    {"name": "viewer", "module": "plugin.py", "description": "查看器插件基类 ViewerPlugin"}
  ]
}
```

- `name` 是给使用者看的名字（也用于插件页展示），同一个插件里不能重复。
- `module` 是**相对插件根目录**的 `.py` 路径、必须存在且非空；入口固定 `plugin.py`，其余模块写在 `.plugin/` 下，例如 `{"name": "rules", "module": ".plugin/rules.py"}`。
- 清单里声明出来的模块（包括 `.plugin/...`）都按 `dm_plugin.<id>.<模块名>` 取用；没声明进 `libraries` 的模块都是实现细节。
- 使用者有两种取法：
  - **推荐（方案 A，静态导入）**：先 `depends` 该插件，再直接

    ```python
    from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin
    ```

    程序在载入每个插件前会把它注册成 `dm_plugin.<id>` 包，所以只要声明过依赖，导入必然成功，而且可被静态检查。
  - **兜底（方案 B，运行时取）**：`library("builtin.lib.viewer", "plugin")`（`app.sdk.library`）；目标不存在时抛 `SdkError`。
- `requires("<id>")` 用来断言「我确实在 `depends` 里声明过它」，避免隐式耦合：没声明就抛 `SdkError`。
- 注意区分：`libraries` 是**代码模块**，`provides` 是**运行期对象**（`ctx.provide()` 登记的扩展接口）。查看器基类走 `libraries`，弹窗外壳走 `provides`。

### 2.5 数据文件（`.data/`）

协议 v2 的清单**没有 `data` 字段**：数据文件固定放在插件根目录的 `.data/` 里，按文件名读取：

```
plugins/<id>/
  .data/
    viewer.json
```

插件里读（`.json` 自动解析成 dict/list，其余按文本读）：

```python
data = ctx.data("viewer")            # → .data/viewer.json
path = ctx.data_path("viewer")       # 想自己处理时拿路径
raw = ctx.data_path("viewer.json")   # 也可以写全名
```

- 文件不存在时 `ctx.data("viewer")` 抛 `插件 … 缺少数据文件：viewer`；给了 `default` 就返回它。
- 约定：**清单只放「参数与配置」，具体数据放 `.data/`**。查看器的扩展名、能力、弹窗外壳之类都放 `.data/`——清单里已经没有 `data` / `extensions` / `capabilities` 字段了。

### 2.6 插件选项（options）

```json
{
  "options": [
    {"key": "fit_on_open", "label": "打开时适应窗口", "kind": "bool", "default": true,
     "description": "打开图片时自动缩放到刚好填满窗口。"},
    {"key": "zoom_step", "label": "缩放步长", "kind": "choice", "default": "1.25",
     "choices": {"1.1": "1.1×（细腻）", "1.25": "1.25×（默认）", "1.5": "1.5×（快速）"}},
    {"key": "max_tags", "label": "最多挂几个标签", "kind": "int", "default": 5,
     "minimum": 1, "maximum": 50, "step": 1}
  ]
}
```

- `kind` 有四种：`bool` / `int` / `text` / `choice`。
- `key` 匹配 `^[a-z][a-z0-9_.\-]{0,63}$`，同一个插件里不能重复。
- `choice` 必须有非空 `choices`，且 `default` 必须是其中一个取值。
- `int` 用数字微调框展示：`minimum` / `maximum` 限定范围（可省，默认 `-999999 ~ 999999`），`step` 是步长（默认 1）；
  `default`、`minimum`、`maximum` 都得是数字，`minimum > maximum` 会在载入时直接报错，超范围的值会被钳到边界。
- 插件里读：`ctx.option("zoom_step", 1.25)`；选项值由程序规范化，认不出的值回退默认值。
- 用户在「插件」页改选项会写进 `.configs/plugins.json`，并**重新载入该插件**，所以 `setup()` 里读到的就是最新值。

### 2.7 `provides` 与 `libraries` 的区别

两者都是「本插件给别人用」的声明，但给的东西完全不同：

| | `provides` | `libraries` |
| --- | --- | --- |
| 给的是什么 | 运行期的**对象实例**（扩展接口） | 可 `import` 的**代码模块**（类 / 函数 / 常量） |
| 生产方怎么写 | `setup()` 里 `ctx.provide("dialog", DialogApi())` | 清单声明 `.py` 模块，消费方直接 import |
| 消费方怎么取 | `ctx.require("dialog")` → 实例 | `from dm_plugin.<id>.plugin import DialogApi`（兜底 `library("<id>", "plugin")`） |
| 谁在管 | `extension_registry`（登记时带上提供者插件 id） | Python 导入系统 + 载入期注册的 `dm_plugin.<id>` 命名空间包 |
| 生命周期 | 跟着插件走：禁用 / 卸载即收回（`drop_plugin`），消费方要自己兜底 | 静态：插件没载入时 import 直接失败，载入后就是普通模块 |
| 清单字段的作用 | **只用于声明与展示**（插件页「扩展接口：」、插件列表搜索），不参与匹配与校验 | **强校验**：模块必须存在、是 `.py` 且非空（见 2.4） |
| 能不能继承 / 实例化 | 不能（拿到的是对象） | 能（拿到的是类） |
| 适合什么 | 宿主、服务：程序里当前就一个、可被替换、缺失时能降级提示 | 基类、工具函数、窗口外壳：要复用代码、要 IDE 补全与类型标注 |

判断标准：

- 消费方要**继承 / 实例化 / 静态检查** → 用 `libraries` 暴露代码。
- 消费方要的是**「程序里当前那一个实例」**，并且希望它随插件启停而出现或消失 → 用 `provides` 暴露接口。
- 两者可以同时用：`builtin.lib.ui` 用 `libraries` 暴露 `DialogApi` / `PopupWindow` 两个**类**，同时用 `provides: dialog`
  暴露当前那一个**宿主实例** —— 查看器库 `ctx.require("dialog")` 取的是后者，取不到时提示「缺少弹窗工具库（dialog）」。

注意：清单里的 `provides` 不会校验「是否真的 provide 了」，也不会因此让插件载入失败：写了却没 `ctx.provide()`（或反过来）时，
程序只在插件备注里记一条「清单声明的扩展接口没有注册：`<名字>`」（插件页可见，存在 `.configs/plugins.json` 的对应条目里），
补上注册后重新载入会自动清掉。它对外是承诺、对程序是展示，所以要与代码保持一致。

### 2.8 贡献（`contribute`）与事件的协议约定

清单只声明「依赖谁、提供什么库 / 接口」；**插件往界面里放东西走扩展点，不写进清单**：

- 扩展点名与事件名都是 SDK 常量（`app.sdk.points` 的 `ExtensionPoint` / `Events`），插件用类属性引用，别手写字符串；
  合法名字用 `ExtensionPoint.values()` / `Events.values()` 列出，中文标签用 `label(name)`。
- `ctx.contribute(point, value, key="", order=100, description="")` 登记一条贡献：`key` 在**同一插件的同一扩展点**内唯一
  （重复登记会覆盖），`order` 小者在前、同 `order` 时按插件 id 排，`description` 显示在插件页的贡献列表里。
- 每条贡献都记在提供它的插件名下；插件禁用 / 卸载时程序自动撤销，插件不必自己清理。
- 事件用 `ctx.on(Events.XXX, handler)` 订阅，`handler` 必须能收 `**payload`（程序是 `handler(**payload)` 调用的）；
  插件也可以用 `ctx.emit("my.plugin.ready", **payload)` 广播自己的事件（自定义名字请加命名空间前缀）。
- 扩展点的值结构、各回调签名与事件载荷表见 [`SDK.md`](SDK.md) 第 5 节。
## 3. 版本与版本范围

版本号按点分段、逐段取前导数字比较（`1.2.3-beta` 视为 `(1, 2, 3)`）。

范围表达式里，条件之间用空格或逗号分隔表示「且」，`||` 表示「或」：

| 写法 | 含义 |
| --- | --- |
| `>=1.0 <2` | 区间 |
| `1.2` | 缺省部分版本，等价于 `>=1.2 <1.3` |
| `1.2.*` | 前缀，等价于 `>=1.2 <1.3` |
| `1.2.3` | 三段以上且不带运算符 = 精确版本 |
| `^1.2` | 第一位非零段不变：`>=1.2 <2.0`（`^0.2` = `>=0.2 <0.3`） |
| `~1.2.3` | 只允许末段变化：`>=1.2.3 <1.3`（`~1.2` = `<1.3`，`~1` = `<2`） |
| `>=1.0 \|\| <0.9` | 或 |
| 空 | `api_version` **不能为空**（缺字段直接报错） |

非法范围（如 `>>1.0`）会直接报错：`api_version 不是合法版本范围：…`。

## 4. 载入阶段与诊断

程序按下表五个阶段载入每个插件，任一阶段失败都只影响该插件：

| 阶段 | 做什么 | 典型失败 |
| --- | --- | --- |
| `manifest` | 读 `plugin.json`、白名单校验、解析依赖；扫描 `.data/` | 缺 `plugin.json`、JSON 语法错、未知字段、`entry` 不是 `plugin.py` |
| `dependency` | 检查依赖存在 / 版本 / 循环 | `缺少依赖插件：…`、`依赖插件 … 版本不满足`、`插件依赖存在循环：…` |
| `import` | 注册 `dm_plugin.<id>` 包并导入入口脚本 | 入口文件里的语法错、导入期异常 |
| `construct` | 找到插件类并实例化 | 入口里没有 `Plugin` 子类、构造函数抛错 |
| `setup` | 调用插件类的 `setup(ctx)` | 插件自己抛出的异常 |

- 失败信息记在插件上：`PluginInfo.error`（文本）+ `PluginInfo.error_phase`（阶段名），插件页会把它标成「异常」并禁用。
- **冲突不算载入失败**：冲突插件的 `error` 为空、照常载入，只是 `PluginInfo.conflict_with` 非空、`state_label` 显示「与插件冲突」且不能启用（见 2.3）。
- `PluginService.load_report()` 只汇总**载入过程**（import / construct / setup）的结果，且阶段消息是固定文案（`插件入口导入失败，详见日志` / `插件初始化失败，详见日志`），具体异常看日志。
- `manifest` 阶段的错误不会出现在 `load_report()` 里：用 `plugin_service.discover()` 或 `plugin_service.get(id).error_phase` 取。
- 一个插件坏掉不会影响其他插件：其余的照常载入。

### 常见错误信息对照

| 错误信息 | 原因 |
| --- | --- |
| `缺少 plugin.json：<目录名>` | 目录里没有清单 |
| `插件清单不是合法的 JSON：…` | JSON 语法错 |
| `插件清单缺少 id` / `插件清单缺少 name` / `插件清单缺少 entry（入口文件）` | 缺必填字段 |
| `协议 v2 的入口固定为 plugin.py，不能是：main.py` | `entry` 写了别的值 |
| `入口文件不存在：plugin.py` | 根目录里没有 `plugin.py` |
| `插件清单出现未知字段：foo、bar` | 写了协议里没有的字段（多半是旧协议残留） |
| `插件清单已取消类型字段：kind（协议不再区分插件类型）` | 旧协议的 `kind` / `kinds` / `kind_label` / `kind_description` / `kind_requires_extensions` |
| `插件清单不再支持字段 extensions：请改用库插件提供的扩展接口` | 旧协议把数据塞在清单里；改用库插件的扩展接口或 `.data/` |
| `插件清单已取消字段 data：请改用 数据文件固定放 .data/，用 ctx.data("name") 读` | 协议 v2 不再声明数据文件，放 `.data/` 即可 |
| `libraries 的库 viewer 的 module 必须是 .py 文件：viewer.txt` | 库模块必须是 `.py` |
| `库 viewer 声明的模块是空文件：plugin.py` | 库模块是空文件 |
| `依赖插件重复声明：x` / `插件不能依赖自己` | `depends` 写错 |
| `需要 SDK 版本 >=2.0，当前 1.0` | `api_version` 不满足 |
| `插件清单缺少 api_version（适配的 SDK 版本范围，例如 ">=1.0 <2.0"）` | 清单没写 `api_version` |
| `api_version 不是合法版本范围：1.x` | 范围写法不对 |

## 5. 命名空间与导入边界

- 载入每个插件前，程序把它注册成 `dm_plugin.<id>` 包（`register_plugin_namespace()`），并把 `.plugin/` 一并挂进包的搜索路径：入口模块名固定 `dm_plugin.<id>.plugin`，`.plugin/` 里的模块是 `dm_plugin.<id>.<模块名>`（子目录同理）。
- `dm_plugin` 这个名字是保留包名；插件不要自己去创建或劫持它。
- 插件源码**只能**依赖 `app.sdk`（以及 Python 标准库、qfluentwidgets 等第三方包）与 `dm_plugin.<依赖 id>`：

  ```python
  from app.sdk import Plugin, PluginContext      # ✅ 允许
  from dm_plugin.builtin.lib.ui.plugin import DialogApi        # ✅ 允许（depends 里声明过；plugin.py 是它 libraries 声明的模块）
  from dm_plugin.builtin.lib.ui.dialog_host import DialogApi   # ❌ 不允许：没写进 libraries，属于实现细节
  from app.services.item_service import ItemService            # ❌ 自检会报错
  ```

  自检 `plugin_imports` 会扫描插件源码：`app` 下**只放行 `app.sdk`**（其余一律报错），`dm_plugin.<id>` 必须匹配已知插件 id、顶层导入必须写进 `depends`。对方公开哪些模块由它的 `libraries` 决定（`plugin.py` → `dm_plugin.<id>.plugin`、`.plugin/api.py` → `dm_plugin.<id>.api`），没声明的模块是实现细节。
- `app.sdk` 里到底有什么（模块、接口、参数、扩展点与事件）看 [`SDK.md`](SDK.md)；本节只说「允许 import 谁」。
- IDE 与桩：`dm_plugin` 是运行期合成包，仓库里由 `scripts/plugin_stubs.py` 按清单生成 `stubs/dm_plugin/**` 的 `.pyi` 桩（把 `stubs` 标成源码根即可消除编辑器标红）；自检 `plugin_stubs_current` 保证桩与清单同步。

## 6. 状态文件 `.configs/plugins.json`

程序把用户对插件的操作存成一份 JSON：

```json
{
  "version": 1,
  "plugins": {
    "auto_tag": {"enabled": false},
    "builtin.viewer.image": {
      "enabled": true,
      "settings": {"fit_on_open": true, "zoom_step": "1.25", "smooth_scaling": true}
    }
  }
}
```

- `version` 是状态文件版本（当前 `1`），用于将来迁移。
- 每个插件一个条目：`enabled`（启用状态）、`settings`（`options` 的当前取值），外加插件页可编辑的显示名、备注等。
- 文件不存在、损坏或插件已删除时按默认值处理（内置插件默认启用）。
- 这份文件属于**运行期状态**，`.configs/` 不进版本库；删掉它相当于把所有插件恢复默认。
