# 查看器库（builtin.lib.viewer）

查看器插件的公共工具箱：查看器基类、查看器内容页、查看器注册表，外加「按规则决定用哪个查看器打开」的调度实现与「查看器」配置页。
打开行为整体由本插件提供（扩展接口 `viewer.open`）；程序本体只剩调度门面与类型别名（`app.services.viewer_service`），注册表、规则、界面都在这里。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、`class = ViewerLibraryPlugin`，depends `builtin.lib.ui`，provides `viewer.open` |
| plugin.py | 库模块（也是入口文件）：ViewerPlugin 基类、对外导出的名字、入口类 ViewerLibraryPlugin（注册 viewer.open 接口与配置页） |
| registry.py | 注册表：`Viewer` 记录（id / name / extensions / kind / plugin_id / factory / opener / host / description / capabilities）、`ViewerRegistry`、模块级 `viewer_registry` 与 `reset()` |
| rules.py | 规则与决策：ViewerRule / ViewerDecision / ViewerRules（读写 `.configs/viewers.json`，resolve() 决定打开方式） |
| provider.py | 扩展接口实现 ViewerOpenApi：open_path / open_viewer / open_external、注册表段与规则的读 / 写 / 重置门面 |
| window.py | 打开调度：build_window / open_page_via_host（经界面工具库弹独立窗口）/ open_viewer |
| config_page.py | 「查看器」配置页 ViewerConfigPage（**插件页面**，用 builtin.lib.ui 的 SplitPage 与控件工厂搭出来） |
| viewer_window.py | 查看器内容页 ViewerWindow：内容区 + 通过 attach_popup(popup) 把「用系统程序打开」「定位文件」挂到弹窗外壳的标题栏（页面不再自画标题栏） |

**具体怎么画一个文件**（文本页、图片页、播放器等）不属于本库：每个查看器插件在自己的目录里实现，
音频 / 视频的播放控件来自界面工具库 `builtin.lib.ui`（`PlayerPanel` + `format_time`），
两个插件都直接继承它，只差自己设置 `shows_video`。

## 暴露的库

本插件的库模块是入口 `plugin.py`，导入名 `dm_plugin.builtin.lib.viewer.plugin`。两种取法：

    from dm_plugin.builtin.lib.viewer.plugin import ViewerPlugin, ViewerWindow   # 推荐：静态导入（要先 depends）
    library("builtin.lib.viewer", "plugin")                                       # 兜底：运行时取

本插件同时声明两种对外面：`libraries` 给的是**类**（要继承 / 实例化），`provides: viewer.open` 给的是**运行期那一个实例**
（消费方 `ctx.require("viewer.open")`）。分工见 `../../docs/PLUGIN_PROTOCOL.md` 2.7。

ViewerPlugin 提供：

- setup(ctx)：读清单 data 段声明的 viewer.json（必须是对象，否则抛 SdkError），用 ctx.require(host) 确认宿主可用，
  再 ctx.add_viewer(...) 登记查看器，并把 opener 一起登记 —— 打开文件时走的是「本库建窗口 → 界面工具库弹出」，
  主程序只负责调度，不认识任何具体格式。
- create_view(path, parent=None)：子类必须实现（未实现时抛 NotImplementedError），返回自己的视图控件。
- open_view(path, parent=None)：在**自己的插件目录**里造好视图，再交给 builtin.lib.ui 弹出；返回 (是否成功, 提示文字)。
- option(key, default=None)：读清单 options 声明的插件选项。
- 缺省值（子类可覆盖）：default_kind = "text"、default_host = "dialog"（模块常量 DEFAULT_HOST）、default_order = 100。

入口类 ViewerLibraryPlugin 在 setup() 里：

- `ctx.provide(VIEWER_EXTENSION, ViewerOpenApi(ctx))` —— `VIEWER_EXTENSION = "viewer.open"` 定义在 `app.services.viewer_service`；
- `ctx.add_page(CONFIG_PAGE_KEY, "查看器", lambda: ViewerConfigPage(ctx, ctx.require(VIEWER_EXTENSION)), icon="VIEW", order=200)`
  —— 宿主没有提供界面接口（`app.ui`）时只记一条 warning，查看器照常可用；
- `teardown()` 里 `reset_registry()` —— 本插件卸载时注册表跟着清空，重新载入时各查看器插件再登记。

模块里还导出：

- `Viewer` / `ViewerRegistry` / `viewer_registry` / `reset_registry`：注册表（见 `registry.py`）。
- ViewerOpenApi(ctx, rules=None, registry=None)：扩展接口实现（见下）；ViewerRules(config_file=None, registry=None) / ViewerRule / ViewerDecision：规则模型与决策。
- ViewerConfigPage(ctx, api)：配置页；build_window / open_page_via_host / open_viewer / host_name：调度辅助。
- ViewerWindow(path, build, name, parent=None)：查看器**内容页**（只有内容区）；build(container) 返回内容控件，异常会退化成一行提示。
  弹窗外壳建好后由外壳回调 `attach_popup(popup)`，页面这时才把「用系统程序打开」「定位文件」两个按钮与「查看器名 · 文件信息」副标题
  交给外壳标题栏——文件名 / 查看器名 / 关闭按钮都归外壳一份，避免外层套一圈。

## 扩展接口 viewer.open

程序侧 `app.services.viewer_service` 的 `open_path()` / `open_viewer_with()` / `open_system()` 都先取
`extension_registry.provider("viewer.open")`：拿不到接口时退回系统默认（`rules_available()` 可用来判断）。ViewerOpenApi 的成员：

| 分组 | 成员 |
| --- | --- |
| 打开 | `open_path(path, parent=None)`、`open_viewer(path, viewer, parent=None)`、`open_external(path, *, ask=False)` |
| 注册表 | `add_viewer(plugin_id, *, viewer_id, name, extensions, kind, factory, opener, host, description, capabilities)`、`viewers()`、`viewer_by_id(id)`、`extensions()`、`plugin_ids()`、`viewer_for(path)`、`clear()`、`unregister_plugin(plugin_id)` |
| 规则 | `rules()`、`rule_for(suffix)`、`set_rule(...)`、`remove_rule(...)`、`resolve(path)`、`viewers_for(suffix)`、`rule_viewer_for(suffix)`、`has_builtin()`、`available_modes(suffix)`、`config_file` |
| 兼容门面 | `suffix_of()`、`viewer_ids_for(plugin_id)`、`suffixes_of(viewer_id)`、`suffixes_of_plugin(plugin_id)`、`current_viewer_id(suffix)`、`set_viewer(suffix, viewer_id)`、`use_viewer_for_all(viewer_id, suffixes=None)`、`reset_viewer(viewer_id, suffixes=None)` |

程序本体登记查看器仍走 `ctx.add_viewer(...)`（`PluginContext`），由插件服务转交给 `viewer.open` 的 `add_viewer()`。

规则按扩展名（不含点）存在 `.configs/viewers.json`，形如 `{"version": 1, "rules": {"<后缀>": {mode, program, args, viewer_id}}}`，
`mode` 取 `builtin` / `inherit` / `custom`；旧版 `.configs/open_with.json` 在首次读取时自动迁移过来。

## 依赖

- builtin.lib.ui（界面工具库：页面模板 / 控件工厂与弹窗外壳；本库通过 `ctx.require("dialog")` / `ctx.require("ui")` 要它，不直接认识主程序）。
- 被 7 个内置查看器插件依赖。

## 谁在用

builtin.viewer.image / builtin.viewer.text / builtin.viewer.markdown / builtin.viewer.spreadsheet / builtin.viewer.archive /
builtin.viewer.audio / builtin.viewer.video —— 它们都写 `depends: [{"id": "builtin.lib.viewer"}, {"id": "builtin.lib.ui"}]`，
在自己的目录里放视图代码（例如 `plugins/builtin.viewer.image/.plugin/image_view.py`），继承 ViewerPlugin 并只实现 create_view()。
