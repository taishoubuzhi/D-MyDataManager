# 编辑器库（builtin.lib.editor）

编辑器插件的公共工具箱：编辑器基类、编辑器内容页、编辑器注册表，外加「按规则决定用哪个编辑器编辑」的调度实现与「编辑器」配置页。
编辑行为整体由本插件提供（扩展接口 `editor.open`）；程序本体只剩调度门面与类型别名（`app.services.editor_service`），注册表、规则、界面都在这里。
数据管理页右键的「编辑器 ▸」子菜单由程序本体搭出来（`ManagePage.editor_menu_items()` / `_build_editor_menu()` 读 `app.services.editor_service.editors_for()`），
本插件不贡献菜单项——禁用后子菜单仍在，只是只剩「系统默认程序 / 交给系统选择…」；
「设置 → 外观 → 左键双击」的配置项 `Layout/Double-Click-Action` 默认是「打开查看器」，改成「打开编辑器」后条目左键双击也走 `edit_path()`。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、`class = EditorLibraryPlugin`，depends `builtin.lib.ui`，provides `editor.open` |
| plugin.py | 库模块（也是入口文件）：EditorPlugin 基类、对外导出的名字、入口类 EditorLibraryPlugin（注册 editor.open 接口与「编辑器」配置页） |
| registry.py | 注册表：`Editor` 记录（id / name / extensions / kind / plugin_id / factory / opener / host / description / capabilities）、`EditorRegistry`、模块级 `editor_registry` 与 `reset()` |
| rules.py | 规则与决策：EditorRule / EditorDecision / EditorRules（读写 `.configs/editors.json`，resolve() 决定编辑方式） |
| provider.py | 扩展接口实现 EditorOpenApi：edit_path / edit_with / open_external、注册表段与规则的读 / 写 / 重置门面 |
| window.py | 打开调度：build_window / open_page_via_host（经界面工具库弹独立窗口）/ edit_editor |
| config_page.py | 「编辑器」配置页 EditorConfigPage（**插件页面**，用 builtin.lib.ui 的 SplitPage 与控件工厂搭出来） |
| editor_window.py | 编辑器内容页 EditorWindow：内容区 + 通过 attach_popup(popup) 把「保存」「用系统编辑器打开」「定位文件」挂到弹窗外壳的标题栏、未保存提示（页面不再自画标题栏） |

**具体怎么编辑一种文件**（文本页、表格编辑器等）不属于本库：每个编辑器插件在自己的目录里实现。

## 暴露的库

本插件的库模块是入口 `plugin.py`，导入名 `dm_plugin.builtin.lib.editor.plugin`。两种取法：

    from dm_plugin.builtin.lib.editor.plugin import EditorPlugin, EditorWindow   # 推荐：静态导入（要先 depends）
    library("builtin.lib.editor", "plugin")                                       # 兜底：运行时取

本插件同时声明两种对外面：`libraries` 给的是**类**（要继承 / 实例化），`provides: editor.open` 给的是**运行期那一个实例**
（消费方 `ctx.require("editor.open")`）。分工见 `../../docs/PLUGIN_PROTOCOL.md` 2.7。

EditorPlugin 提供：

- setup(ctx)：读清单 data 段声明的 editor.json（必须是对象，否则抛 SdkError），`kind` 为 `internal` 时用 ctx.require(host)
  确认界面宿主可用，再 ctx.add_editor(...) 登记编辑器，并把 opener 一起登记 —— 编辑文件时走的是「本库建窗口 → 界面工具库弹出」，
  主程序只负责调度，不认识任何具体格式。
- create_editor(path, parent=None)：子类必须实现（未实现时抛 NotImplementedError），返回自己的编辑器控件。
  控件可实现 `save()`（返回 `True` 或 `(bool, str)`）与 `is_dirty()`，窗口外壳据此启用保存按钮并提示未保存改动。
- open_editor(path, parent=None)：`kind="external"` 时直接 `app.sdk.ui.open_default()`；`kind="internal"` 时在**自己的插件目录**里
  造好控件，再交给 builtin.lib.ui 弹出；返回 (是否成功, 提示文字)。
- `_on_saved(path)`：保存成功后优先调 `ctx.host.refresh_path(path)`（重算条目 checksum / size / 内容并广播刷新），
  宿主不提供时退回 `app.sdk.ui.notify_items_changed()`。
- option(key, default=None)：读清单 options 声明的插件选项。
- 缺省值（子类可覆盖）：default_kind = `"internal"`（KIND_INTERNAL）、default_host = `"dialog"`（模块常量 DEFAULT_HOST）、default_order = 100。

入口类 EditorLibraryPlugin 在 setup() 里：

- `ctx.provide(EDITOR_EXTENSION, EditorOpenApi(ctx))` —— `EDITOR_EXTENSION = "editor.open"` 定义在 `app.services.editor_service`；
- `ctx.add_page(CONFIG_PAGE_KEY, "编辑器", lambda: EditorConfigPage(ctx, ctx.require(EDITOR_EXTENSION)), icon="EDIT", order=200)`
  —— 宿主没有提供界面接口（`app.ui`）时只记一条 warning，编辑器照常可用；
- `teardown()` 里 `reset_registry()` —— 本插件卸载时注册表跟着清空，重新载入时各编辑器插件再登记。

模块里还导出：

- `Editor` / `EditorRegistry` / `editor_registry` / `reset_registry` / `KIND_INTERNAL` / `KIND_EXTERNAL` / `KINDS`：注册表（见 `registry.py`）。
- EditorOpenApi(ctx, rules=None, registry=None)：扩展接口实现（见下）；EditorRules(config_file=None, registry=None) / EditorRule / EditorDecision：规则模型与决策。
- EditorConfigPage(ctx, api)：配置页；build_window / open_page_via_host / edit_editor / host_name：调度辅助。
- EditorWindow(path, build, name, on_saved=None, parent=None)：编辑器**内容页**（只有内容区）；build(container) 返回内容控件，异常会退化成一行提示。
  弹窗外壳建好后由外壳回调 `attach_popup(popup)`，页面这时才把「保存」「用系统编辑器打开」「定位文件」三个按钮与
  「编辑器名 · 文件信息（· 已修改）」副标题交给外壳标题栏；保存按钮的可用状态由页面按 `is_dirty()` 刷新。

## 扩展接口 editor.open

程序侧 `app.services.editor_service` 的 `edit_path()` / `edit_with()` / `open_system()` 都先取
`extension_registry.provider("editor.open")`：拿不到接口时退回系统默认编辑器（`rules_available()` 可用来判断）。EditorOpenApi 的成员：

| 分组 | 成员 |
| --- | --- |
| 打开 | `edit_path(path, parent=None)`、`edit_with(path, editor, parent=None)`、`open_external(path, *, ask=False)` |
| 注册表 | `add_editor(plugin_id, *, editor_id, name, extensions, kind, factory, opener, host, description, capabilities)`、`editors()`、`editor_by_id(id)`、`extensions()`、`plugin_ids()`、`editor_for(path)`、`editors_for(suffix)`、`clear()`、`unregister_plugin(plugin_id)` |
| 规则 | `rules()`、`rule_for(suffix)`、`set_rule(...)`、`remove_rule(...)`、`resolve(path)`、`has_builtin()`、`available_modes(suffix)`、`config_file` |
| 兼容门面 | `suffix_of()`、`editor_ids_for(plugin_id)`、`suffixes_of(editor_id)`、`suffixes_of_plugin(plugin_id)`、`current_editor_id(suffix)`、`set_editor(suffix, editor_id)`、`use_editor_for_all(editor_id, suffixes=None)`、`reset_editor(editor_id, suffixes=None)` |

程序本体登记编辑器仍走 `ctx.add_editor(...)`（`PluginContext`），由插件服务转交给 `editor.open` 的 `add_editor()`。

规则按扩展名（不含点）存在 `.configs/editors.json`，形如 `{"version": 1, "rules": {"<后缀>": {mode, program, args, editor_id}}}`，
`mode` 取 `builtin` / `inherit` / `custom`（`ask` 只在运行时生成，用于「交给系统选择」）。

## 保存后的数据同步

内部编辑器保存成功后，`EditorPlugin._on_saved(path)` 调宿主 `ctx.host.refresh_path(path)`：宿主用库内文件的当前位置找到对应条目，
按磁盘文件重算 checksum / size / 内容并广播 `signalBus.itemsChanged`，数据管理页据此刷新。这条链路只走程序公开的 SDK / 信号入口，
插件不直接写库或数据库。

## 依赖

- builtin.lib.ui（界面工具库：页面模板 / 控件工厂与弹窗外壳；本库通过 `ctx.require("dialog")` / `ctx.require("ui")` 要它，不直接认识主程序）。
- 被内置编辑器插件依赖（`builtin.editor.text` / `builtin.editor.office`）。

## 谁在用

`plugins/builtin.editor.text/`（文本 / 代码内部编辑器，继承 EditorPlugin 只实现 create_editor）与
`plugins/builtin.editor.office/`（`kind="external"`，只登记扩展名，交给系统默认程序）。
