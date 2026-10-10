# 内置图片查看器（builtin.viewer.image）

查看 png / jpg / gif 等图片：缩放、旋转、适应窗口，并显示图片信息；工具条上有「上一张 / 下一张」按宿主给的列表顺序翻看。

## 上一张 / 下一张（浏览列表）

工具条上的「上一张 / 下一张」（`.plugin/image_view.py` 的 `_step_image()`）切的是**哪一串图片**，由两处决定，`browse_images(path, sources, extensions)` 挑一个：

1. 宿主给的 `sources`（数据管理页当前这一页的条目，按界面排序）：按它的顺序保留本查看器认的扩展名、去掉不存在的文件后去重；
   **只有这一串里多于一张且包含当前图片时才采用**；
2. 否则退回同目录的同类图片（`sibling_images()`，按文件名排序）。

只按同目录找是原来的做法，而库里的文件按分类平铺、导入时还保留来源子目录，同一目录往往只有一张图，
于是切换等于没反应（用户 m00828 报的就是这个）。宿主列表经 `builtin.lib.viewer` 的 `ViewerWindow` 用
`set_sources(paths)` 交进来（见 `../builtin.lib.viewer/PLUGIN.md`）；本页还会发 `pathChanged(str)`（外壳换标题、
「用系统程序打开」与「定位文件」跟着换目标）与 `captionChanged(str)`（外壳换副标题），状态栏在多张时显示「（第 i/n 张）」。
**只剩一张可切时两个按钮直接禁用**，tooltip 写「当前列表里没有别的图片可切换」——不再出现「点了没反应」。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖、data 声明、插件选项 options |
| plugin.py | 插件类 ImageViewerPlugin(ViewerPlugin)：只实现 create_view() |
| .data/viewer.json | 查看器元数据：名称、kind、宿主、扩展名、能力、排序 |

## 依赖

- builtin.lib.viewer（查看器基类 ViewerPlugin）
- builtin.lib.ui（弹窗外壳，host = dialog）

## 贡献

- 查看器（扩展点 app.viewer）：viewer_id = builtin.viewer.image，kind = image，
  认领 png / jpg / jpeg / jpe / bmp / gif / webp / tif / tiff / ico / ppm / pgm / jfif（13 个，见 .data/viewer.json），
  能力：缩放 / 旋转 / 适应窗口 / 图片信息。

## 数据文件

- .data/viewer.json：name、kind、host、extensions、capabilities、description、order(=100)。
  要再加一种图片格式，只改这里的 extensions 即可，不用动 plugin.py。

## 插件选项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| fit_on_open | bool | 开 | 打开图片时自动缩放到刚好填满窗口，小图也会放大 |
| zoom_step | choice | 1.25 | 缩放按钮每次的倍数（1.1 / 1.25 / 1.5） |
| smooth_scaling | bool | 开 | 缩放是否用平滑插值 |

选项在「插件」页的「插件选项」对话框里改，改完插件会重新载入，工厂闭包里的参数立即生效；
**标题栏的齿轮**也能就地改（`.plugin/image_view.py` 的 `ImageViewer.settings_items()` 声明这三项，改一下立即生效
并通过 `on_option` 回写），不必重新打开图片；plugin.py 里的 DEFAULT_FIT_ON_OPEN / DEFAULT_ZOOM_STEP / DEFAULT_SMOOTH
是这些默认值的来源。
