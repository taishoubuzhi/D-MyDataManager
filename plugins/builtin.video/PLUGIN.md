# 内置视频播放器（builtin.video）

播放 mp4 / mkv / avi 等视频：播放暂停、进度拖动、音量与全屏。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 VideoViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（kind=video、扩展名、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.dialog（弹窗外壳）

## 贡献

- 打开方式（app.viewer）：kind = video，认领 mp4 / m4v / avi / mkv / mov / wmv / flv / webm / mpg / mpeg / rmvb / 3gp，
  能力：播放暂停 / 进度拖动 / 音量 / 全屏。

## 数据文件

- data/viewer.json：元数据。播放能力取决于 Qt 多媒体后端。
