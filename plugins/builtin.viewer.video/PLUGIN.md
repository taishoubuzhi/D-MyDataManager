# 内置视频播放器（builtin.viewer.video）

播放 mp4 / mkv / avi 等视频：播放暂停、进度、音量、倍速、循环、全屏、前 / 后一秒与首末帧（都有提示）、画面缩放、截图、内嵌字幕与音轨选择；
系统解码器（QtMultimedia）播不了时，可以**经用户确认后**转码到临时文件再播。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖、data 声明、插件选项 options |
| plugin.py | 插件类 VideoViewerPlugin(ViewerPlugin)：只实现 create_view()，视图是 VideoViewer |
| .plugin/video_view.py | VideoViewer(PlayerPanel)：播放条动作、缩放、字幕、截图、音轨切换、设置项与转码兜底 |
| .data/viewer.json | 查看器元数据：名称、kind、宿主、扩展名、能力、排序 |

## 依赖

- builtin.lib.viewer（查看器基类 ViewerPlugin）
- builtin.lib.ui（弹窗外壳 host = dialog、通用播放控件 PlayerPanel / MediaBar、设置入口 settings.py）

## 贡献

- 查看器（扩展点 app.viewer）：viewer_id = builtin.viewer.video，kind = video，host = dialog，
  认领 mp4 / m4v / avi / mkv / mov / wmv / flv / webm / mpg / mpeg / rmvb / 3gp（12 个，见 .data/viewer.json），
  能力：播放暂停 / 进度拖动 / 音量 / 倍速 / 循环 / 全屏 / 前 / 后一秒 / 首末帧 / 画面缩放 / 截图 / 内嵌字幕 / 音轨选择。

## 数据文件

- .data/viewer.json：name、kind、host、extensions、capabilities、description、order(=100)。
  要再加一种视频格式，只改这里的 extensions 即可，不用动 plugin.py。

## 插件选项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| volume | int | 80 | 打开视频时的音量（0 - 100）。拖音量条会把新值写回这里 |
| muted | bool | 关 | 打开视频时先静音。按 M 或点喇叭按钮会把新值写回这里 |
| rate | choice | 1 | 打开视频时的播放倍速（0.5 / 0.75 / 1.25 / 1.5 / 2） |
| loop | bool | 关 | 打开视频时循环播放（播完回到开头） |
| aspect | choice | fit | 画面显示方式：适应窗口（保持比例，可能留黑边）/ 拉伸铺满（可能变形） |
| subtitle | bool | 开 | 文件里有内嵌字幕轨时自动在画面下方显示，按 B 随时开关 |
| zoom_step | choice | 1.25 | 每次放大 / 缩小的倍率步长（1.15 / 1.25 / 1.5） |
| zoom_hint | int | 1000 | 缩放后右下角倍率浮层显示多久（200 - 5000 毫秒） |

选项在「插件」页的「插件选项」对话框里改；**标题栏的齿轮**（内容页 `settings_items()` 声明、外壳加按钮）
也能就地把这些项改掉，改一下立即生效并回写；播放期间改音量 / 静音 / 倍速 / 循环 / 字幕同样自动写回
（`on_option` → `ctx.set_option`），下次打开沿用。

## 播放条动作

播放 / 暂停、进度条、时间、音量、静音、倍速下拉由 `PlayerPanel` 提供。画面相关的常用能力放在
**窗口标题栏**（`PlayerPanel.add_action` 的 `anchor` 决定放哪；有弹窗外壳时本插件用
`PopupWindow.add_action()` 把按钮挂到标题栏，没有外壳时（例如单独用播放控件）自动退回播放条）：

| 位置 | 按钮 | 提示 | 行为 |
| --- | --- | --- | --- |
| 标题栏 | 缩小 | 缩小画面 | 倍率除以 `zoom_step`，到最小（0.1）禁用 |
| 标题栏 | 放大 | 放大画面 | 倍率乘 `zoom_step`，到最大（8.0）禁用 |
| 标题栏 | 重置缩放 | 重置缩放 | **无条件**回 1.0（适应窗口）并清掉平移 |
| 标题栏 | 循环 | 循环播放 | 开关循环播放（与 `loop` 选项互通），并弹提示说明当前状态 |
| 标题栏 | 相机 | 截取当前画面 | 选保存位置后把当前画面存成 png / jpg |
| 标题栏 | 保存 | 把音轨导出成音频文件 | 按所选扩展名导出音轨（能直接搬就搬，否则重编码） |
| 标题栏 | 音乐 | 选择音轨 | 有两条以上音轨时才出现，切换后重新载入 |
| 标题栏 | 字幕 | 显示 / 隐藏内嵌字幕 | 有内嵌字幕轨时才出现 |
| 标题栏 | 全屏 | 全屏（F11） | 全屏后图标换成「退出全屏」，Esc 或再点一次退出；标题栏保留（按钮就在上面） |
| 标题栏 | 齿轮 | 设置 | 内容页 `settings_items()` 声明了 8 项，外壳自动补一个入口 |

播放条上只留播放相关的动作，**按播放按钮分两组**：

| 位置 | 按钮 | 提示 | 行为 |
| --- | --- | --- | --- |
| 播放键左 | 跳到第一帧 | 跳到第一帧（Home） | 跳到 0（图标 `PAGE_LEFT`），并提示「已跳到第一帧 · 当前位置 0:00」 |
| 播放键左 | 后退一秒 | 后退一秒（F） | 时间轴 -1 秒（不越过开头），并提示当前位置 |
| 播放键右 | 前进一秒 | 前进一秒（G） | 时间轴 +1 秒（不越过结尾），并提示当前位置 |
| 播放键右 | 跳到最后一帧 | 跳到最后一帧（End） | 跳到末尾（留 200 毫秒，图标 `PAGE_RIGHT`），并提示当前位置 |

这四个位置动作都走 `PlayerPanel.seek_by()` / `go_first_frame()` / `go_last_frame()`（它们**返回落到的时间**），
插件在外面包一层弹提示——所以提示里的时间是刚跳到的那个位置，不会因为播放器的位置回报滞后而写错。
`PlayerPanel.step_frames()`（按帧率逐帧，仍是库里的能力）不再出现在这个插件的界面上。

快捷键：空格播放 / 暂停，左右 5 秒，上下音量，M 静音，F / G 后退 / 前进一秒，Home / End 首末帧，B 字幕，F11 全屏，
Ctrl + `=` / `-` / `0` 放大 / 缩小 / 重置缩放。

## 画面缩放

画面是 `QGraphicsView` + `QGraphicsVideoItem` **合成绘制**的（不是原生 `QVideoWidget` 子窗口），
所以缩放、浮层提示、设置对话框都能画在画面之上，放大的画面也被视口裁掉、不会盖住上下工具条。
1.0 = 适应窗口，可以缩到 0.1（变成 0.x 显示），也能放到 8.0：只改画面项的显示尺寸
（`item.setSize(适应尺寸 × 倍率)`，`sceneRect` 始终等于视口），放大后按住画面拖动平移，
平移上限由 `_pan_limits()` 算出、`_clamp_pan()` 夹住，露不出白边。
`zoomChanged(倍率, 到最小?, 到最大?)` 信号驱动按钮禁用；每次真的变了就 `StageOverlay.flash()`
在右下角浮出倍率（一直显示到 `zoom_hint` 毫秒后自动消失），`reset_zoom()` 无条件回 1.0。

## 转码兜底

QtMultimedia 打不开某个文件时（缺解码器、格式不认识）：

1. 先用 `app.sdk.media.remux()` 试**转封装**（只换容器、不动编码，最快且不损画质）；
2. 转封装失败再用 `app.sdk.media.transcode()` **重编码**到 mp4；
3. 两条路都在用户点「确定」之后才走 —— **不确认就取消播放**（不会静默转码）；
4. 目标放在 `ROOT/.tmp/media`（`media_service.temp_path()`）；转录在 `QThread` 里跑并回报进度；
5. 关窗时删临时文件：播放器可能还占着句柄，先松手再重试几次（`_close_player()` + `_pump_events()`），
   仍删不掉就留给 `media_service` 的退出兜底清理。

## 媒体能力从哪来

播放本身是 QtMultimedia；探测、时长 / 分辨率 / 字幕轨、截图、音轨切换与上面两条兜底，
全部走 SDK 门面 `app.sdk.media`（`media.open`，由 `app.services.media_service` 实现）。
插件**不**直接 import `av`，也**不**自己调 ffmpeg —— 引擎缺失时这些功能整体降级，播放（QtMultimedia 能解的部分）照常。

## 界面日志

QtMultimedia 自带那份 FFmpeg 会往控制台打 `Input #0 …` 与硬件解码器探测消息，这些由程序本体的
`core/runtime/qt_media.py` 统一压掉，插件不用管（用户 m02529 第 1 条）。
