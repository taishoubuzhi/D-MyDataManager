# 开发与维护说明

运行、依赖与自检脚本见 `README.md`。这里只记录与资源、文案相关的操作。

## 资源目录

`src/app/resource/` 下的图片由代码通过文件系统路径读取（见 `src/app/core/paths.py` 的 `RESOURCE_DIR` / `IMAGE_DIR`）：

- `images/logo.png`：窗口与打包用图标（`src/app/ui/main_window.py`）
- `images/header1.png`：首页横幅
- `icons/`：10 个 SVG 图标（`src/app/ui/common.py` 的类型图标来源）
- `i18n/`：4 个翻译文件

界面样式全部使用控件自带样式或内联 QSS，**不再使用 `.qrc` / `resource.py` 编译产物**，因此无需执行 `pyside6-rcc`；
重构时清理掉的 `qss/`、`images/controls/`、`resource.qrc`、`resource.py` 已不存在。

## 库文件夹布局

库文件夹**全局唯一**（配置项 `Storage/Library-Path`，默认 `resources/library`，可在「设置 → 库文件夹」中更改；
目标文件夹必须为空），内部结构由 `src/app/core/paths.py` 与 `src/app/services/library_service.py` 约定：

```
<库>/
  全局/                     全局资源
    store/                  内容寻址仓库（ab/cd/<sha256>）
    covers/                 封面缓存
    backups/                数据库备份
    .datamanager/           库元数据（layout-2.json 为布局标记）
  <用户名>/                 每个用户一个文件夹（名取自用户名，非法字符替换为 _）
    <分类目录>/<原文件名>    该用户的数据文件（各用户分类互不影响；重名自动加序号）
```

- 数据项记录的是**库内相对路径**（`<用户名>/<分类目录>/<文件名>`），因此用户改名时要调用
  `LibraryService.rename_user_dir()` 同步重命名文件夹并改写记录（「用户」页改名已接线）。
- 扫描只处理用户名文件夹下的文件，跳过 `全局/` 与点目录；识别不出所属用户的文件会被跳过并打日志
  （见 `LibraryService.scan()`）。
- **布局迁移**：启动时 `src/main.py` 在 `seed()` 之后调用 `migrate_layout()`（`src/app/services/layout_migration.py`）。
  若 `全局/.datamanager/layout-2.json` 不存在，就先备份数据库到 `全局/backups/data-before-layout-v2-<时间戳>.db.bak`，
  再把旧布局（`resources/store`、`resources/covers` 与顶层分类目录下的文件）搬进各用户名文件夹并改写记录，
  最后写入标记文件；迁移是幂等的，重复启动不会重复搬动。

## 翻译文件

界面文案直接写简体中文，`.qm` 文件只用于 FluentWidgets 控件的内建英文文本。修改 `.ts` 后重新编译：

```cmd
pyside6-lrelease src\app\resource\i18n\app.zh_CN.ts -qm src\app\resource\i18n\app.zh_CN.qm
pyside6-lrelease src\app\resource\i18n\app.en.ts -qm src\app\resource\i18n\app.en.qm
```

## 开发者自检

```cmd
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\python.exe scripts\dev_check.py
.venv\Scripts\python.exe scripts\dev_check_services.py
.venv\Scripts\python.exe scripts\dev_check_ui.py
.venv\Scripts\python.exe scripts\dev_check_flow.py
.venv\Scripts\python.exe scripts\seed_demo.py
```

前三个脚本内部会 `init_db(force=True)` 重建数据库：运行前由 `scripts/dev_check_guard.py` 备份真实数据库
（`logs/_selfcheck-data.db.bak`），结束后自动还原，因此可以随时重跑而不会弄丢已导入的数据。

`scripts/seed_demo.py` 依赖 `tests/dataset.py` 生成示例文件，向真实环境注入约 100 条数据（含隐藏项、回收站项、
第二个用户与一个存档快照）且不会自动删除；清空用应用内「设置 → 维护 → 恢复初始化」或 `scripts/dev_reset.py`。
两者的公共实现是 `src/app/services/maintenance.py`（`reset_config()` 重置全部设置、`reset_runtime_data()` 清数据并重建空库）。

在无图形界面的环境里，可先设置 `QT_QPA_PLATFORM=offscreen`（PowerShell：`$env:QT_QPA_PLATFORM='offscreen'`）。

## 标签体系

标签分两类（`src/app/db/models.py` 的 `Tag`）：`is_global` 为真时对所有用户可见，`user_id` 记录归属用户，
`created_by` 记录创建者。可见性由 `src/app/repositories/tags.py` 的 `_scope()` 决定：全局标签 + 自己创建或归属自己的个人标签（默认用户传 None 表示不限制，因此能看到全部标签）；
`by_name()` 优先返回全局标签，因此新建个人标签不会与已有全局标签重名，转全局时若已有同名全局标签会被拒绝。
只有创建者可以在「标签」页把标签在全局 / 个人之间切换（`TaxonomyService.set_tag_global()`）。

数据库结构版本在 `src/app/db/database.py` 的 `SCHEMA_VERSION`：低版本库启动时按 `_upgrade_schema()` 原地补列并回填
（历史 `user_id IS NULL` 的标签视为全局），重名的历史全局标签会被重命名为「名称（N）」后再建唯一索引；
只有库版本高于程序版本（降级运行）时才会备份并重建。

## 用户与权限

`users` 表的 `is_default` 标记默认用户（管理员）：结构升级到 `SCHEMA_VERSION` 4 时把 `MIN(id)` 置位，种子数据建的第一个用户即管理员
（`src/app/db/seed.py` 的 `User(name=DEFAULT_USER, is_default=True)`，`UserRepository.default()` / `ensure_default()` 优先取它）。
`UserService.is_admin()` 决定界面权限：默认用户能在「用户」页新建 / 重命名 / 删除任意用户并设置或清除其口令，也才能用存档页的「还原整个存档」；
普通用户可以改自己或清除自己的口令并删除自己。口令按用户独立保存（`src/app/core/security.py`），切换用户与解锁隐藏数据都用当前用户口令。
「清除口令」按钮只对已设口令的用户显示（`UserPage._clear_password()`）：默认用户可以清除任何用户的口令，其他用户只能清除自己的，越权时给出提示。
`UserService.delete()` 保留默认用户（`if user.is_default: return False`），其余用户删除时：分类以「用户名」为一级分类镜像到默认用户
（`_mirror_categories()`，返回旧→新 id 映射并改写 `DataItem.category_id`），`<库>/<用户名>` 整目录搬到 `<库>/<目标用户>/<用户名>`
（`LibraryService.relocate_user_dir()`）并给 `DataItem.file_path` 加默认用户名前缀，标签归属与 `created_by` 一并改为默认用户（同名标签走 `TagRepository.merge()`），
存档条目 `user_id` / `user_name` 改为目标用户且 `category` 前缀上「用户名 / 」，最后删除其分类行与用户行。

## 数据管理页

上方工具栏用 qfluentwidgets 的 `FlowLayout`（`src/app/ui/pages/manage_page.py` 的 `_build_toolbar()`）：按钮按自身宽度流式排列，
宽度不足时自动换行；容器是 `_ToolbarView`（`QScrollArea`），高度由 `_fit_toolbar()` 按 `flow.heightForWidth(viewport 宽度)` 计算并在
`resizeEvent` 中重算，最多占 `TOOLBAR_MAX_ROWS`（2）行，再多则出现纵向滚动条。

右侧筛选面板（`src/app/ui/widgets/filter_panel.py`）的每个分组都是 `FilterSection`：标题栏是「箭头 + 加粗标题 + 搜索框 + 三态全选框」，
箭头或标题行控制折叠（`_toggle_body()`），选项区固定 `SECTION_BODY_HEIGHT`（116 px）高度、超出时自己滚动；
搜索框按显示名过滤选项（无匹配时显示「没有匹配的选项」），三态全选框由 `_sync_all()` / `_on_all_state()` 与分组内的勾选状态双向同步
（空 = 全不选、横杠 = 部分选中、勾 = 全选，点击空框即全选、点击勾框即全不选），`_syncing` 守卫避免信号回环。
`FilterPanel` 保留 `_type_boxes` / `_tag_boxes` / `_keyword_boxes` / `_category_boxes` 别名指向各分组的同一份 `boxes` 字典。

## 数据存档

存档是「快照 + 引用」：`Archive` 记录快照本身，`ArchiveEntry` 记录每个数据项当时的校验和、分类与路径，
`Blob` 按 SHA-256 内容寻址存放，相同内容只存一份（`src/app/services/archive_service.py`）。
`ArchiveService.entry_state()` 把条目与当前数据对比为 same / changed / removed / missing，还原只处理与当前不一致的条目。

自动清理可按数量 / 容量 / 时间 / 关闭（设置 → 存储 → 存档自动清理，对应 `prune()` / `prune_by_size()` / `prune_by_age()` /
`auto_prune()`），从最早的快照开始删并始终保留最新一份；**已标记的存档（`Archive.pinned`）会被全部清理策略跳过**，
只有先取消标记、或在存档页手动删除才会消失。`ArchiveService.set_pinned()` 切换标记，`SCHEMA_VERSION` 升到 5 时给旧库原地补上该列。
存档页的「标记存档 / 取消标记」按钮跟随选中存档（未选中时禁用），列表项与详情都会标出【已标记】。

## 打开方式与查看器插件

打开文件走 `src/app/services/open_with_service.py`：`OpenWithService.resolve(path)` 按顺序决定用哪种方式 —— 自定义规则且填了程序 →
`custom`；自定义规则没填程序 → `ask`（交给系统选择）；规则为内置且指定了 `viewer_id` 且该查看器还在 → 用指定查看器（查看器已不存在时
回退到该扩展名的默认查看器，reason 记为「指定插件不可用」）；规则为内置但没指定 → 该扩展名的默认查看器；没有可用查看器 → 回退 `inherit`；
没有规则时，有内置查看器就用内置，否则用系统默认。规则按扩展名（不含点）存在 `config/open_with.json`，形如 `{mode, program, args, viewer_id}`，
`mode` 取 `builtin` / `inherit` / `custom`，参数里的 `{path}` 会替换成实际路径（没有占位符时自动补在末尾，见 `src/app/core/shell.py` 的 `build_command()`）。

「打开方式」页（`src/app/ui/pages/open_with_page.py`）以「库里出现过的所有文件格式」为列表（`viewer_registry.extensions()` ∪ 已配置规则 ∪
`ItemService.extensions_in_use()`，可按扩展名 / 插件名搜索），选中后在右侧配置：打开方式下拉（「使用插件打开」/「继承系统默认」/「自定义程序」/
「每次询问」，可用项由 `OpenWithService.available_modes()` 决定）、具体插件下拉（仅「使用插件打开」且该格式有多个查看器时可选，「自动」表示按注册顺序）、
自定义程序与参数，另有「保存」「恢复默认」「测试打开」。当前状态直接写在列表项上（如 `.png — 使用插件（图片查看器）· 库中 12 项`）。

主程序在 `src/main.py` 里用 `plugin_service.bootstrap("app.open_with", open_with_api)` 把 `OpenWithApi` 登记为扩展接口，插件可以据此查询 /
修改打开方式（`set_viewer` / `use_viewer_for_all` / `reset_viewer` 等），详见 [PLUGIN.md](PLUGIN.md)；执行外部程序、交给系统选择、
在资源管理器中定位分别是 `shell.open_with_program()` / `ask_open_with()` / `reveal()`，失败只记日志、不抛异常。

内置插件也放在 `plugins/` 下（`builtin.dialog` 弹窗页面插件，以及七个查看器插件：图片 / 视频 / 音频 / 文本 / Markdown /
压缩包 / 表格），与外部插件走同一条载入路径：`PluginService.load_viewers()` 扫描 `plugins/<id>/plugin.json`，用
`importlib.util.spec_from_file_location()` 加载入口脚本并调用其中的 `register(api)`。查看器控件是普通 `QWidget`，构造签名
`(path, parent=None)`，可提供 `caption` 属性作为补充说明；控件模块在真正打开文件时才导入 Qt。

查看器不自己建窗口，而是在注册时声明 `host="dialog"` 并依赖内置弹窗页面插件（`api.require("dialog")`）：`src/app/ui/viewers/window.py`
的 `open_viewer()` 从 `src/app/core/extensions.py` 的 `extension_registry` 取出 `dialog` 扩展，调 `DialogApi.open_page()` 在程序本体
之外弹出独立顶层窗口（Esc 或标题栏关闭按钮退出），查看器内容作为内容页嵌在其中；缺少该插件时返回「缺少弹窗页面插件（dialog），请到
「插件」页启用后重试」。数据管理页的条目在激活或「打开」时（`ManagePage._on_item_activated()` / `ManagePage._on_open()`）取出
`ItemService.file_path_of()` 的路径，交给 `src/app/ui/viewers/open_flow.py` 的 `open_path()` 打开。

查看器由 `src/app/core/viewers.py` 的 `ViewerRegistry` 按扩展名索引（同一扩展名取最后注册者），`PluginService.load_viewers()` 在启动时
（`src/main.py`）清空 `ViewerRegistry` 与 `ExtensionRegistry`，按依赖顺序载入所有已启用插件（非查看器插件也要载入，它们负责
`api.provide()` 扩展接口），单个插件出错只把错误记在该插件上。插件之间的依赖由 `src/app/core/plugins.py` 的 `sort_by_dependency()`
拓扑排序，缺依赖、依赖未启用或循环依赖都会让插件标为「异常」并强制禁用。`src/app/core/viewer_data.py` 提供查看器共用的纯函数：
文本解码与截断、xlsx / csv 解析（xlsx 用 `zipfile` + `ElementTree` 自解析，不依赖 openpyxl）、压缩包成员列表与读取、图片信息（Pillow）。

插件协议（`plugins/<id>/plugin.json`）：

```json
{
  "id": "sample.viewer",
  "name": "示例查看器",
  "version": "1.0.0",
  "kind": "viewer",
  "kind_label": "示例类型",
  "kind_description": "该类型的说明",
  "description": "说明文字",
  "author": "作者",
  "manager_version": "0.1.0",
  "entry": "sample_plugin.py",
  "depends": ["builtin.dialog"],
  "provides": ["viewer"],
  "capabilities": ["图片查看"],
  "extensions": ["dmx"],
  "builtin": false
}
```

- `id` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_.\-]{1,63}$`。
- `kind` 是插件类型，**不使用枚举**：清单里写什么就是什么，类型表（`src/app/core/plugin_kinds.py`）随已发现的插件不断累积。
  内置类型是 `viewer`（打开方式，必须声明 `extensions`）与 `page`（弹窗页面，只提供扩展接口）；插件可以自定义类型，
  并用可选的 `kind_label`（显示名）、`kind_description`（说明）、`kind_requires_extensions`（该类型是否必须声明扩展名）
  声明自己的类型信息 —— 第一个声明某个类型的插件决定它的显示名，之后的插件只做去重累积：重复声明同一个类型不会报错，
  已有的非空字段优先，`plugins` 记录声明过它的插件。类型名要匹配 `^[a-z][a-z0-9_.\-]{1,63}$`，
  只有校验通过的清单才会登记类型，非法类型的插件不会污染类型表。插件页的类型下拉读的就是这张表，无需改动。
- `manager_version` 是插件要求的管理器版本，由 `src/app/core/version.py` 的 `is_compatible()` 比较，要求过高直接报错。
- `entry` 是入口脚本（外部插件必填且文件必须存在，内置插件可省略），载入时用 `importlib.util.spec_from_file_location()` 加载并调用其中的 `register(api)`。
- `depends` / `provides` 分别是依赖的插件 id 与对外提供的扩展接口名（扩展接口名匹配 `^[a-z][a-z0-9_.\-]{1,63}$`）；
  `capabilities` 是功能说明文字。
- `extensions` 只有查看器插件需要；`builtin` 只由内置插件声明，导入的插件会被强制改回 `false`。

入口脚本的 `register(api)` 收到 `src/app/services/plugin_service.py` 的 `PluginApi`：`api.plugin_id` / `api.plugin_name` /
`api.default_extensions` 是插件信息；`api.add_viewer(name, extensions, factory, kind, description, capabilities, host)` 注册控件工厂
（`factory(path, parent)` 返回 `QWidget`，`host` 指定负责显示它的扩展接口）；`api.add(kind, name, *args, **fields)` 是按类型注册的通用入口：
内置类型会路由到对应的注册方法（如 `viewer` → `add_viewer`），类型表里声明了 `contributor` 的自定义类型同样按名字路由到该方法；
没有注册方法的类型**不会报错**，这次调用会被记成一条 `PluginContribution`（用 `api.entries(kind)` 读自己的条目，
`PluginService.contributions(kind)` 可汇总所有已载入插件的条目）；`api.provide(name, provider)` 对外暴露扩展接口，
`api.require(name)` / `api.has(name)` 使用别的插件提供的接口（缺失时 `require` 直接报错）。
插件可以用清单里的 `options` 声明用户可配置项（`src/app/core/plugin_options.py` 的 `parse_options()`：`bool` / `text` / `choice` 三种，
`key` 匹配 `^[a-z][a-z0-9_.\-]{0,63}$`，`choice` 必须有 `choices` 且默认值必须在其中）。插件页的「插件选项」按钮弹出
`src/app/ui/plugin_options_dialog.py` 的 `PluginOptionsDialog`，按声明生成控件并把改动写进 `config/plugins.json` 的 `options` 字段
（`PluginService.set_option()` / `reset_options()` / `options_of()`）；插件在 `register(api)` 里用 `api.option("键")` 读回（取值经
`coerce_option()` 规范化，无法识别时回退默认值），选项改动后插件会整体重新载入，因此工厂闭包里的参数立即生效。若插件注册了查看器，
该对话框还会列出其扩展名的勾选框，勾选即调用 `app.open_with` 的 `set_viewer()`，用户不必去「打开方式」页逐个设置。
「插件」页按类型 / 来源（内置 / 外部）/ 创建者 / 状态与关键词筛选，支持按默认顺序 / 名称 / 类型 / 来源 / 创建者 / 状态 / 版本排序并切换正序、逆序
（`PluginService.all(kind, query, state, source, author, order, reverse)` 与 `PLUGIN_ORDERS`）。列表项显示启用标记与「类型 · 来源」徽标，
全部类型下**所有插件都会出现**（内置弹窗插件排在最前）；详情区显示版本 / 创建者 / 类型 / 来源 / 状态、扩展名、协议字段、插件选项摘要与清单路径。
操作包括导入插件目录或 `.zip` 包（解压时拒绝 `..` 与绝对路径）、启用 / 禁用、改显示名 / 说明 / 备注、打开插件目录（内置插件同样可以打开）、
「插件选项」配置、删除外部插件；启用状态 / 备注 / 插件选项都存 `config/plugins.json`。内置插件不能删除；载入失败的插件在列表里标为「异常」
并强制禁用，不影响程序启动。

插件不仅可以提供查看器与扩展接口，还能直接往主界面加页面：程序本体在 `src/main.py` 里用
`plugin_service.bootstrap("app.ui", AppUiApi())`（`src/app/core/app_ui.py`）把 `app.ui` 接口登记进 `extension_registry`，
插件在 `register(api)` 里写 `api.require("app.ui").add_page("hello", "演示页", _build, icon="HOME", plugin_id=api.plugin_id)`
即可登记一个导航页 —— 主窗口（`src/app/ui/main_window.py` 的 `_sync_plugin_pages()`）会按 `PageSpec.route`（`plugin.<key>`）
装配 / 移除导航项与堆叠页，页面工厂抛异常时退化成一条提示页而不影响其它页面；`load_viewers()` 每次重载都会重新提供 `app.ui`
并调用它的 `sync_plugins(已载入插件 id)`，因此**插件的页面会随启用 / 禁用自动出现与消失**。`app.ui` 之外，插件还可以
`api.provide(name, provider)` 暴露自己的扩展接口供其它插件 `api.require(name)` 使用，用 `api.add(kind, ...)` 往自定义类型里
登记条目（`PluginService.contributions(kind)` 汇总）。**写插件的完整说明（清单字段、`PluginApi` 全部接口、查看器约定、
大型插件示例、排错）见仓库根目录的 [PLUGIN.md](PLUGIN.md)。**

## 日志与保留策略

`src/app/core/logging_setup.py` 按 `Log/Mode` 决定文件名与切分方式：`single`（恒为 `app.log`）、`session`（每次启动 `app-<时间戳>.log`）、
`daily`（`YYYY-MM-DD.log`，每天 0 点切换）、`size`（同样按启动命名，按 `Log/Max-File-Size-MB` 切分）。
保留策略由 `_select_outdated()` 在启动时与该次切分后执行：最新一个文件永不删除，`daily` 模式先按 `Log/Keep-Days` 删除过期文件，
再按 `Log/Keep-Files` 限制数量，最后按 `Log/Max-Total-Size-MB` 限制总量（从最早的文件删起）。

控制台输出按级别着色：TRACE 青、DEBUG 蓝、INFO 绿、SUCCESS 绿（加粗）、WARNING 黄、ERROR 红、CRITICAL 红（加粗），
时间字段绿、模块与函数名青、消息与等级同色。配色由 `logging_setup.py` 的 `LEVEL_COLORS` 显式写死并覆盖 loguru 默认值
（loguru 默认的 INFO 不带颜色，若终端用 IDE 的配色规则就会看成红色）；控制台 sink 写 `sys.stdout` 而不是 `sys.stderr`，
避免 PyCharm 等 IDE 把整行 `stderr` 标红；写日志文件时 `colorize=False`，不会写入 ANSI 转义序列。

## 打包与发布

使用 PyAppify（配置见 `pyappify.yml`、`icons/`、`.github/workflows/build.yml`），发布新版本只需推送标签：

```cmd
git tag v1.0.0
git push origin v1.0.0
```

推送后 GitHub Actions 会构建在线安装包与整包并挂到对应 Release；用户端启动器按标签增量更新。

本项目**不再使用 PyInstaller**（相关 spec/脚本已删除）。
