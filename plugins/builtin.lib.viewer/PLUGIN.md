# 查看器库（builtin.lib.viewer）

查看器插件的公共工具箱：把「按扩展名打开文件」的通用逻辑、查看器窗口外壳与媒体播放页做成库模块。
子类只实现 create_view()，扩展名 / 显示名 / 能力 / 宿主都从自己的 data/viewer.json 里读，窗口与弹窗由本库包办。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、`class = ViewerLibraryPlugin`，并用 libraries 声明对外暴露的库模块 plugin.py |
| plugin.py | 库模块（也是入口文件）：ViewerPlugin 基类、库导出的名字、插件类 ViewerLibraryPlugin（库本身不做别的贡献） |
| viewer_window.py | 查看器窗口外壳 ViewerWindow：文件名 / 查看器名 / 「用系统程序打开」「定位文件」/ 内容区 |
| media_panel.py | 媒体播放页 MediaViewer 与时间格式化 format_time（builtin.audio / builtin.video 继承它） |

## 暴露的库

库模块固定写成入口 `plugin.py`（协议规定：一个插件对其他插件的公开面只有 plugin.py）。两种取法：

    from dm_plugin.builtin.lib.viewer.plugin import MediaViewer, ViewerPlugin, ViewerWindow   # 推荐：静态导入（要先 depends）
    library("builtin.lib.viewer", "plugin")                                                    # 兜底：运行时取

本插件只声明 `libraries`，不声明 `provides`：它给的是**类**（要继承 / 实例化），不是「运行期那一个实例」。
需要后者时用扩展接口，分工见 `plugins/PLUGIN_PROTOCOL.md` 2.7。

ViewerPlugin 提供：

- setup(ctx)：读清单 data 段声明的 viewer.json（必须是对象，否则抛 SdkError），用 ctx.require(host) 确认宿主可用，
  再 ctx.add_viewer(...) 登记查看器，并把 opener 一起登记 —— 打开文件时走的是「本库建窗口 → 弹窗工具库弹出」，
  主程序只负责调度，不认识任何具体格式。
- create_view(path, parent=None)：子类必须实现（未实现时抛 NotImplementedError），返回自己的视图控件。
- open_view(path, parent=None)：在**自己的插件目录**里造好视图，再交给 builtin.lib.dialog 弹出；返回 (是否成功, 提示文字)。
- option(key, default=None)：读清单 options 声明的插件选项。
- 缺省值（子类可覆盖）：default_kind = "text"、default_host = "dialog"（模块常量 DEFAULT_HOST）、default_order = 100。

模块里还导出：

- ViewerWindow(path, build, name, parent=None)：窗口外壳；build(container) 返回内容控件，异常会退化成一行提示。
- MediaViewer / format_time：音视频播放页；builtin.audio、builtin.video 只是它的子类（shows_video 不同）。
- Library：基类的「只当库用」实例（本插件自己不做贡献，入口类 ViewerLibraryPlugin 的 setup() 留空）。

## 依赖

- builtin.lib.dialog（弹窗外壳；本库通过 ctx.require("dialog") 要它，不直接认识主程序）。
- 被 7 个内置查看器插件依赖。

## 谁在用

builtin.image / builtin.text / builtin.markdown / builtin.spreadsheet / builtin.archive /
builtin.audio / builtin.video —— 它们都写 `depends: [{"id": "builtin.lib.viewer"}, {"id": "builtin.lib.dialog"}]`，
在自己的目录里放视图代码（例如 `plugins/builtin.image/image_view.py`），继承 ViewerPlugin 并只实现 create_view()。
