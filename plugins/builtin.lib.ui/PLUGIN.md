# 内置界面工具库（builtin.lib.ui）

提供统一页面骨架、控件工厂与独立弹窗外壳，让插件不必各自手写 PyQt6 布局也能长得像同一个程序：
插件页只写「绑回调 + 填数据」，控件与两栏结构都从这里取。
本插件由原 lib.dialog 并入而来。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：provides 声明扩展接口 dialog（弹窗）与 ui（界面工具）；libraries 声明 plugin.py |
| plugin.py | 插件类 UiPlugin：setup() 里登记 DialogApi 与 UiApi；对外转发界面构建件 |
| dialog_host.py | 弹窗外壳 PopupWindow 与扩展接口 DialogApi 的实现 |
| ui_tools.py | 页面模板 PageTemplate / ScrollPageTemplate、控件工厂（标签 / 输入框 / 文本区 / 下拉 / 勾选 / 数字 / 单选 / 进度条 / 列表 / 表格 / 按钮 / 表单行 / 组合行列 / 勾选网格 / 滚动区 / 视图堆叠）、弹窗外壳 FormDialog、组合构件（ListPanel / DetailPanel / SplitPage / MediaBar / PlayerPanel / image_canvas）、分区卡片、工具条、提示条与确认框 |

## 贡献

- 扩展接口 provides: dialog —— 查看器等插件用 `ctx.require("dialog")` 取到 DialogApi。
- 扩展接口 provides: ui —— `ctx.require("ui")` 取到 UiApi（`open_page()`、`page()`、`section()`、`form_dialog()`、`list_panel()`、`detail_panel()`、
  `split_page()`、`media_bar()`、`player_panel()`、`image_canvas()`、`windows()`、`close_all()`）。
- 库模块固定是 plugin.py（libraries 里的 ui -> plugin.py，转发 ui_tools.py 与 dialog_host.py 的实现）：
  - 页面模板：`PageTemplate(title, subtitle, parent, scroll=False)`、`ScrollPageTemplate(...)`、`page_template(...)`
    —— 自带标题区 `header`（标题 + 悬停说明 + `add_action()` 操作区）与正文 `body_layout`，
    并提供 `add_section(title, description) -> (卡片, 竖直布局)`、`add_widget()`、`add_row()`、`add_stretch()`。
  - 构件函数：`section_card`、`panel_card`、`toolbar`、`page_header`、`caption`、`empty_state`。
  - 控件工厂：`body_label / strong_label / title_label / status_label`、`line_edit / search_edit`、`text_area / text_browser`、
    `combo_box / check_box`、`list_item / list_view`、`read_only_table / fill_table`、`tool_button / icon_button / primary_button / push_button`、
    `form_row`、`view_stack` —— 都返回配好间距与主题的控件，回调直接传进来（`on_change` / `on_select` / `on_click` / `on_link`）。
  - 表单与自绘控件：`text_edit`（多行，可 `height=`）、`spin_box`（数字，`suffix` / `step` / `on_change`）、`radio_button`（单选，`on_change`）、
    `progress_bar`（`indeterminate=True` 是忙等条）、`widget_row(parent, *widgets, stretches=…)`（横排一行）、`widget_column(parent, *widgets)`（竖排一列）、
    `check_grid(parent, items, columns=3, checked=…)`（返回 `(控件, {key: CheckBox})`，适合能力多选）、`field(parent, label, widget)`（标签在上的一格）、
    `scroll_area(parent, widget=…, minimum_height=…, maximum_height=…)` —— 都是给插件页面/弹窗拼布局用的，缺件先补这里再让插件用。
  - 弹窗外壳：`class FormDialog(parent=None, *, title="", width=560, scroll=True, minimum_height=320, maximum_height=None)`
    —— 基于 qfluentwidgets `MessageBoxBase`：`titleLabel` 是标题、`body` 放字段、`area` 是 `scroll=True` 时的滚动区，
    方法 `add_widget(w)` / `add_field(label, w)` / `add_row(label, w)` / `add_hint(text)` / `set_buttons(yes=…, cancel=…)`，
    点「确定」`exec()` 返回真；工厂 `form_dialog(parent, title, **fields)` 一行搭一个表单弹窗。
  - 可点卡片：`click_card(parent, on_click=…)`（返回能整块点击的 `ClickCard`，`app.sdk.ui` 也导出 `ClickCard` 给跨插件用）。
  - 组合构件：`ListPanel`（左栏：搜索 + 列表 + 计数，`set_items([(data, 文本[, 气泡])])` / `filter()` / `select(data)`）、
    `DetailPanel`（右栏：标题 + 说明 + `add_row()` + 操作区）、`SplitPage`（左右两栏页面，`list_panel` / `detail_panel` 属性）、
    `MediaBar`（播放暂停 / 进度 / 音量条的纯控件；`add_action(icon, tooltip, callback, *, anchor="right")`
    能往条上插动作按钮，`anchor="before-play" / "after-play"` 插在播放键左右、默认插在时间与音量之间；
    `remove_action(button)` 反向摘掉）、
    `PlayerPanel(path, parent=None)`（基于 QtMultimedia 的完整播放控件，
    类属性 `shows_video` 决定要不要挂视频画面，`QtMultimedia` 到构造时才导入、缺依赖抛 RuntimeError；
    视频画面是 `QGraphicsView` + `QGraphicsVideoItem` **合成绘制**（不是原生 `QVideoWidget` 子窗口），
    所以提示浮层 / 倍率 / 弹窗能画在画面之上，放大的画面被视口裁掉；
    `add_action` / `remove_action` 转发给 `MediaBar`，另有 `set_native_size()` 供插件把探测到的分辨率先喂进来；
    **播放位置**（`seek_by(seconds) -> int` 按秒快进 / 快退、`go_first_frame()` / `go_last_frame()`，
    三者都**返回本次落到的时间（毫秒）**，方便调用方照着弹提示；`step_frames(frames)` 按帧长挪动，供需要逐帧的场景用）；
    **播放状态**（`loop` / `set_loop`、`autoplay` / `set_autoplay` —— 打开时读取偏好，构造参数 `options` 里
    的 `autoplay` 为真就等媒体加载好自动开播，中途改 `set_autoplay(True)` 也会立刻播；`save_option(key, value)`
    是「立即生效 + 回写插件选项」的入口）；
    **画面缩放**（`set_zoom` / `zoom_in` / `zoom_out` / `reset_zoom` / `can_zoom_in` / `can_zoom_out` / `zoom`
    / `zoomChanged(倍率, 到最小?, 到最大?)` / `zoom_text()`，1.0 = 适应窗口，范围 `MEDIA_ZOOM_MIN` ~ `MEDIA_ZOOM_MAX`），
    放大后按住画面拖动平移（`_pan_by_mouse()`，平移由 `_pan_limits()` / `_clamp_pan()` 夹住）、
    `StageOverlay` 是挂在画面容器右下角的浮层提示（`flash(text)` 显示一小会儿后自动隐藏，时长可调））、
    `format_time(milliseconds)`（`1:05` / `1:02:03`）、`image_canvas(parent)`（返回 `(滚动区, 图片标签)`）。
  - 反馈：`toast_success / toast_info / toast_warning / toast_error(parent, title, content)`、`confirm(parent, title, content)`、
    `release_widget`、`clear_layout`。
  - 间距常量：`PAGE_MARGINS / PAGE_SPACING / PANEL_MARGINS / DETAIL_MARGINS / COMPACT_MARGINS / CARD_SPACING / ROW_SPACING`
    （与 app.sdk.ui 同源，插件不要写死数字）。
  - 弹窗：常量 `DIALOG_EXTENSION = "dialog"`；`DialogApi.open_page(title, content_factory, meta="", buttons=(), width=980, height=700) -> PopupWindow`
    （content_factory(parent) 返回内容控件；buttons 是 [(按钮文字, 回调)]）；`DialogApi.windows()` / `DialogApi.close_all()`；
    `PopupWindow` 标题栏 + 内容区，Esc 或关闭按钮关闭，WA_DeleteOnClose；content_factory 抛异常时退化成一行
    「页面无法显示：…」，不会影响主界面。
  - 标题栏只有**一条**，且归 `PopupWindow`：内容页不要在页内再画一条「文件名 + 关闭」的栏。内容页需要往外壳放动作时，
    实现 `attach_popup(popup)` —— `open_page()` 造好内容控件后会回调它（没有该方法就跳过），页面在回调里调
    `popup.add_action(icon, tooltip, callback)`（插到关闭按钮左侧，返回按钮本体；配套 `popup.remove_action(button)`
    可反向摘掉，内容页换外壳时用）与 `popup.set_meta(text)` /
    `popup.set_title(text)`（改副标题 / 标题）、`popup.set_bar_visible(visible)`（整条标题栏显隐）、
    `popup.set_escape_handler(handler)`（Esc 先给内容页，返回 True 表示不关窗）。查看器与编辑器内容页就是这么做的，见
    [`plugins/builtin.lib.viewer/PLUGIN.md`](../viewer/PLUGIN.md) 与 [`plugins/builtin.lib.editor/PLUGIN.md`](../editor/PLUGIN.md)。

## 依赖

无。

## 说明

弹窗没有父控件，因此与主窗口相互独立；程序关闭时会调用 DialogApi.close_all() 收尾。

本插件同时提供两种对外面：`libraries` 暴露页面模板与弹窗**类**（别的插件可以 import、继承，或自己实例化），
`provides` 暴露当前程序里那**一个宿主实例**（消费方 `ctx.require("dialog")` / `ctx.require("ui")`，
本插件被禁用时它会随之消失，消费方应当提示而不是崩溃）。两者的分工见 `../../docs/PLUGIN_PROTOCOL.md` 2.7。
内置 7 个查看器插件的 .data/viewer.json 里都写 host: "dialog"，查看器库据此 require 本插件：
禁用本插件后，查看器会提示缺少界面工具库。

音频 / 视频查看器用的播放控件就是本库的 `PlayerPanel`：`builtin.viewer.audio` 与 `builtin.viewer.video` 的视图分别是
`class AudioViewer(PlayerPanel)` 与 `class VideoViewer(PlayerPanel)`，只差一个 `shows_video` 开关；视频那个另外把常用动作
挂到弹窗标题栏（`attach_popup` + `popup.add_action`），没有外壳时（单独用播放控件）自动退回播放条。
