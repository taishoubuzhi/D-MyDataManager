# 内置界面工具库（builtin.lib.ui）

提供统一页面骨架、控件工厂与独立弹窗外壳，让插件不必各自手写 PyQt6 布局也能长得像同一个程序：
插件页只写「绑回调 + 填数据」，控件与两栏结构都从这里取。
本插件由原 builtin.lib.dialog 并入而来。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：provides 声明扩展接口 dialog（弹窗）与 ui（界面工具）；libraries 声明 plugin.py |
| plugin.py | 插件类 UiPlugin：setup() 里登记 DialogApi 与 UiApi；对外转发界面构建件 |
| dialog_host.py | 弹窗外壳 PopupWindow 与扩展接口 DialogApi 的实现 |
| ui_tools.py | 页面模板 PageTemplate / ScrollPageTemplate、控件工厂（标签 / 输入框 / 文本区 / 下拉 / 勾选 / 列表 / 表格 / 按钮 / 表单行 / 视图堆叠）、组合构件（ListPanel / DetailPanel / SplitPage / MediaBar / PlayerPanel / image_canvas）、分区卡片、工具条、提示条与确认框 |

## 贡献

- 扩展接口 provides: dialog —— 查看器等插件用 `ctx.require("dialog")` 取到 DialogApi。
- 扩展接口 provides: ui —— `ctx.require("ui")` 取到 UiApi（`open_page()`、`page()`、`section()`、`list_panel()`、`detail_panel()`、
  `split_page()`、`media_bar()`、`player_panel()`、`image_canvas()`、`windows()`、`close_all()`）。
- 库模块固定是 plugin.py（libraries 里的 ui -> plugin.py，转发 ui_tools.py 与 dialog_host.py 的实现）：
  - 页面模板：`PageTemplate(title, subtitle, parent, scroll=False)`、`ScrollPageTemplate(...)`、`page_template(...)`
    —— 自带标题区 `header`（标题 + 悬停说明 + `add_action()` 操作区）与正文 `body_layout`，
    并提供 `add_section(title, description) -> (卡片, 竖直布局)`、`add_widget()`、`add_row()`、`add_stretch()`。
  - 构件函数：`section_card`、`panel_card`、`toolbar`、`page_header`、`caption`、`empty_state`。
  - 控件工厂：`body_label / strong_label / title_label / status_label`、`line_edit / search_edit`、`text_area / text_browser`、
    `combo_box / check_box`、`list_item / list_view`、`read_only_table / fill_table`、`tool_button / icon_button / primary_button / push_button`、
    `form_row`、`view_stack` —— 都返回配好间距与主题的控件，回调直接传进来（`on_change` / `on_select` / `on_click` / `on_link`）。
  - 组合构件：`ListPanel`（左栏：搜索 + 列表 + 计数，`set_items([(data, 文本[, 气泡])])` / `filter()` / `select(data)`）、
    `DetailPanel`（右栏：标题 + 说明 + `add_row()` + 操作区）、`SplitPage`（左右两栏页面，`list_panel` / `detail_panel` 属性）、
    `MediaBar`（播放暂停 / 进度 / 音量条的纯控件）、`PlayerPanel(path, parent=None)`（基于 QtMultimedia 的完整播放控件，
    类属性 `shows_video` 决定要不要挂视频画面，`QtMultimedia` 到构造时才导入、缺依赖抛 RuntimeError）、
    `format_time(milliseconds)`（`1:05` / `1:02:03`）、`image_canvas(parent)`（返回 `(滚动区, 图片标签)`）。
  - 反馈：`toast_success / toast_info / toast_warning / toast_error(parent, title, content)`、`confirm(parent, title, content)`、
    `release_widget`、`clear_layout`。
  - 间距常量：`PAGE_MARGINS / PAGE_SPACING / PANEL_MARGINS / DETAIL_MARGINS / COMPACT_MARGINS / CARD_SPACING / ROW_SPACING`
    （与 app.sdk.ui 同源，插件不要写死数字）。
  - 弹窗：常量 `DIALOG_EXTENSION = "dialog"`；`DialogApi.open_page(title, content_factory, meta="", buttons=(), width=980, height=700) -> PopupWindow`
    （content_factory(parent) 返回内容控件；buttons 是 [(按钮文字, 回调)]）；`DialogApi.windows()` / `DialogApi.close_all()`；
    `PopupWindow` 标题栏 + 内容区，Esc 或关闭按钮关闭，WA_DeleteOnClose；content_factory 抛异常时退化成一行
    「页面无法显示：…」，不会影响主界面。

## 依赖

无。

## 说明

弹窗没有父控件，因此与主窗口相互独立；程序关闭时会调用 DialogApi.close_all() 收尾。

本插件同时提供两种对外面：`libraries` 暴露页面模板与弹窗**类**（别的插件可以 import、继承，或自己实例化），
`provides` 暴露当前程序里那**一个宿主实例**（消费方 `ctx.require("dialog")` / `ctx.require("ui")`，
本插件被禁用时它会随之消失，消费方应当提示而不是崩溃）。两者的分工见 `plugins/PLUGIN_PROTOCOL.md` 2.7。
内置 7 个查看器插件的 data/viewer.json 里都写 host: "dialog"，查看器库据此 require 本插件：
禁用本插件后，查看器会提示缺少界面工具库。

音频 / 视频查看器用的播放控件就是本库的 `PlayerPanel`：`builtin.audio` 与 `builtin.video` 的视图分别是
`class AudioViewer(PlayerPanel)` 与 `class VideoViewer(PlayerPanel)`，只差一个 `shows_video` 开关。
