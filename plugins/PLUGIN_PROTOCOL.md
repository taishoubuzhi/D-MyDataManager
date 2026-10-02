# 插件协议（plugin.json）

这份文档是**规范**：清单字段、依赖与版本、目录约定、载入阶段、状态文件。

- 想学「怎么写一个插件」，看根目录的 [`../PLUGIN.md`](../PLUGIN.md)（教程）。
- 想查「程序开放了哪些扩展点、会广播哪些事件」，看 [`EXTENSION_POINTS.md`](EXTENSION_POINTS.md)。

版本：SDK 协议 `1.0`（`src/app/sdk/version.py` 的 `SDK_VERSION`），程序版本 `0.1.0`（`src/app/core/version.py` 的 `MANAGER_VERSION`）。

## 1. 目录规范

每个插件一个目录，目录名就是插件 `id`：

```
plugins/
  <id>/
    plugin.json      必需：清单
    plugin.py        入口脚本（清单 entry 指向它）
    <库模块>.py      可选：libraries 声明的库模块
    data/            可选：插件自己的数据文件
    PLUGIN.md        必需：用一句话说清这个插件有什么用、贡献了什么
```

- `plugin.json` 缺字段、写错类型、出现未知字段都会让插件**载入失败**（见第 6 节），不会影响程序启动。
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
| `entry` | 非内置必填 | 字符串 | 入口脚本，相对插件目录，文件必须存在 |
| `class` | 否 | 字符串 | 插件类名；入口里只有一个 `Plugin` 子类时可以省略 |
| `api_version` | 否 | 范围字符串 | 要求的 SDK 版本范围，默认 `>=1.0` |
| `manager_version` | 否 | 范围字符串 | 要求的程序版本范围，默认不限；不满足直接报错 |
| `depends` | 否 | 数组 | 依赖的插件，见 2.2 |
| `incompatible` | 否 | 字符串数组 | 不能同时载入的插件 id（冲突） |
| `load_after` | 否 | 字符串数组 | 只影响载入顺序，不要求目标存在 |
| `provides` | 否 | 字符串数组 | 对外暴露的**扩展接口名**（`ctx.provide()` / `ctx.require()`） |
| `libraries` | 否 | 数组 | 对外暴露的**库模块**，见 2.4 |
| `data` | 否 | 对象 | 数据文件声明（键 → 相对路径），见 2.5 |
| `options` | 否 | 数组 | 用户可配置项，见 2.6 |
| `builtin` | 否 | 布尔 | 只由内置插件声明；导入的插件会被强制改回 `false` |
| `enabled` | 否 | 布尔 | 默认启用状态（默认 `true`）；内置插件可用它默认禁用自己，用户在「插件」页改动后以状态文件为准 |

### 2.2 依赖声明

`depends` 支持简写与完整写法混用：

```json
{
  "depends": [
    "builtin.lib.dialog",
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

- `incompatible`：列表里任意一个插件也被载入时，本插件标记为「与插件不兼容：…」并且不载入。
- `load_after`：只用来表达「同层里我想排在它后面」，目标不存在时忽略。
- 实际顺序：先按依赖拓扑排序（被依赖的先载入），同层内按 `(内置优先, id 字典序)` 排。
- 循环依赖报错文本：`插件依赖存在循环：a → b → a`。

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
- `module` 必须是插件目录下的 `.py` 文件、必须存在且非空；**约定（也是协议要求）固定写入口 `plugin.py`**：
  一个插件对其他插件的公开面只有 `plugin.py`，其他模块都是实现细节，外部不允许直接导入。
- 使用者有两种取法：
  - **推荐（方案 A，静态导入）**：先 `depends` 该插件，再直接

    ```python
    from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin
    ```

    程序在载入每个插件前会把它注册成 `dm_plugin.<id>` 包，所以只要声明过依赖，导入必然成功，而且可被静态检查。
  - **兜底（方案 B，运行时取）**：`library("builtin.lib.viewer", "plugin")`（`app.sdk.library`）；目标不存在时抛 `SdkError`。
- `requires("<id>")` 用来断言「我确实在 `depends` 里声明过它」，避免隐式耦合：没声明就抛 `SdkError`。
- 注意区分：`libraries` 是**代码模块**，`provides` 是**运行期对象**（`ctx.provide()` 登记的扩展接口）。查看器基类走 `libraries`，弹窗外壳走 `provides`。

### 2.5 数据文件（data）

```json
{
  "data": {"viewer": "data/viewer.json"}
}
```

- 键不能为空，路径必须是**相对插件目录**的路径（`..` 越界会报错），目标文件必须存在。
- 插件里读：

  ```python
  data = ctx.data("viewer")            # .json 自动解析成 dict/list，其余按文本读
  path = ctx.data_path("viewer")       # 想自己处理时拿路径
  ```

- 约定：**清单只放「参数与配置」，具体数据放 `data/`**。查看器的扩展名、能力、弹窗外壳之类都放 `data/`，不要塞回清单——清单里已经没有 `extensions` / `capabilities` 字段了。

### 2.6 插件选项（options）

```json
{
  "options": [
    {"key": "fit_on_open", "label": "打开时适应窗口", "kind": "bool", "default": true,
     "description": "打开图片时自动缩放到刚好填满窗口。"},
    {"key": "zoom_step", "label": "缩放步长", "kind": "choice", "default": "1.25",
     "choices": {"1.1": "1.1×（细腻）", "1.25": "1.25×（默认）", "1.5": "1.5×（快速）"}}
  ]
}
```

- `kind` 只有三种：`bool` / `text` / `choice`。
- `key` 匹配 `^[a-z][a-z0-9_.\-]{0,63}$`，同一个插件里不能重复。
- `choice` 必须有非空 `choices`，且 `default` 必须是其中一个取值。
- 插件里读：`ctx.option("zoom_step", 1.25)`；选项值由程序规范化，认不出的值回退默认值。
- 用户在「插件」页改选项会写进 `config/plugins.json`，并**重新载入该插件**，所以 `setup()` 里读到的就是最新值。

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
| 空 | 不限（`api_version` / `manager_version` 为空时按默认值处理） |

非法范围（如 `>>1.0`）会直接报错：`manager_version 不是合法版本范围：…` / `api_version 不是合法版本范围：…`。

## 4. 载入阶段与诊断

程序按下表五个阶段载入每个插件，任一阶段失败都只影响该插件：

| 阶段 | 做什么 | 典型失败 |
| --- | --- | --- |
| `manifest` | 读 `plugin.json`、白名单校验、解析依赖与数据声明 | 缺 `plugin.json`、JSON 语法错、未知字段、`data` 文件不存在 |
| `dependency` | 检查依赖存在 / 版本 / 冲突 / 循环 | `缺少依赖插件：…`、`依赖插件 … 版本不满足`、`与插件不兼容：…` |
| `import` | 注册 `dm_plugin.<id>` 包并导入入口脚本 | 入口文件里的语法错、导入期异常 |
| `construct` | 找到插件类并实例化 | 入口里没有 `Plugin` 子类、构造函数抛错 |
| `setup` | 调用插件类的 `setup(ctx)` | 插件自己抛出的异常 |

- 失败信息记在插件上：`PluginInfo.error`（文本）+ `PluginInfo.error_phase`（阶段名），插件页会把它标成「异常」并禁用。
- `PluginService.load_report()` 只汇总**载入过程**（import / construct / setup）的结果，且阶段消息是固定文案（`插件入口导入失败，详见日志` / `插件初始化失败，详见日志`），具体异常看日志。
- `manifest` 阶段的错误不会出现在 `load_report()` 里：用 `plugin_service.discover()` 或 `plugin_service.get(id).error_phase` 取。
- 一个插件坏掉不会影响其他插件：其余的照常载入。

### 常见错误信息对照

| 错误信息 | 原因 |
| --- | --- |
| `缺少 plugin.json：<目录名>` | 目录里没有清单 |
| `插件清单不是合法的 JSON：…` | JSON 语法错 |
| `插件清单缺少 id` / `插件清单缺少 name` / `插件清单缺少 entry（入口文件）` | 缺必填字段 |
| `入口文件不存在：plugin.py` | `entry` 指向的文件不在目录里 |
| `插件清单出现未知字段：foo、bar` | 写了协议里没有的字段（多半是旧协议残留） |
| `插件清单已取消类型字段：kind（协议不再区分插件类型）` | 旧协议的 `kind` / `kinds` / `kind_label` / `kind_description` / `kind_requires_extensions` |
| `插件清单不再支持字段 extensions：请放进 data/ 由对应库插件读取` | 旧协议把数据塞在清单里；改放 `data/viewer.json` 之类 |
| `data 的 viewer 指向的文件不存在：data/viewer.json` | `data` 声明的文件缺失 |
| `libraries 的库 viewer 的 module 必须是 .py 文件：viewer.txt` | 库模块必须是 `.py` |
| `库 viewer 声明的模块是空文件：plugin.py` | 库模块是空文件 |
| `依赖插件重复声明：x` / `插件不能依赖自己` | `depends` 写错 |
| `需要程序版本 >=2.0，当前 0.1.0` | `manager_version` 不满足 |
| `需要 SDK 版本 >=2.0，当前 1.0` | `api_version` 不满足 |

## 5. 命名空间与导入边界

- 载入每个插件前，程序把它的目录注册成 `dm_plugin.<id>` 包（`register_plugin_namespace()`），入口模块名是 `dm_plugin.<id>.<entry 去后缀>`。
- `dm_plugin` 这个名字是保留包名；插件不要自己去创建或劫持它。
- 插件源码**只能**依赖 `app.sdk`（以及 Python 标准库、qfluentwidgets 等第三方包）与 `dm_plugin.<依赖 id>`：

  ```python
  from app.sdk import Plugin, PluginContext      # ✅ 允许
  from dm_plugin.builtin.lib.dialog.plugin import DialogApi        # ✅ 允许（depends 里声明过；只能取 plugin.py）
  from dm_plugin.builtin.lib.dialog.dialog_host import DialogApi   # ❌ 不允许：其他模块是实现细节
  from app.services.item_service import ItemService            # ❌ 自检会报错
  ```

  自检 `plugin_imports` 会扫描插件源码：`app` 下**只放行 `app.sdk`**（其余一律报错），`dm_plugin.<id>` 必须匹配已知插件 id、写进 `depends`，且只能落在对方的 `plugin.py`。
- IDE 与桩：`dm_plugin` 是运行期合成包，仓库里由 `scripts/plugin_stubs.py` 按清单生成 `stubs/dm_plugin/**` 的 `.pyi` 桩（把 `stubs` 标成源码根即可消除编辑器标红）；自检 `plugin_stubs_current` 保证桩与清单同步。

## 6. 状态文件 `config/plugins.json`

程序把用户对插件的操作存成一份 JSON：

```json
{
  "version": 1,
  "plugins": {
    "example.ui_extension": {"enabled": false},
    "builtin.image": {
      "enabled": true,
      "settings": {"fit_on_open": true, "zoom_step": "1.25", "smooth_scaling": true}
    }
  }
}
```

- `version` 是状态文件版本（当前 `1`），用于将来迁移。
- 每个插件一个条目：`enabled`（启用状态）、`settings`（`options` 的当前取值），外加插件页可编辑的显示名、备注等。
- 文件不存在、损坏或插件已删除时按默认值处理（内置插件默认启用）。
- 这份文件属于**运行期状态**，`config/` 不进版本库；删掉它相当于把所有插件恢复默认。
