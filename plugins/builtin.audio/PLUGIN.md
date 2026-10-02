# 内置音频播放器（builtin.audio）

播放 mp3 / wav / flac 等音频：播放暂停、进度拖动与音量。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 AudioViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（kind=audio、扩展名、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.dialog（弹窗外壳）

## 贡献

- 打开方式（app.viewer）：kind = audio，认领 mp3 / wav / flac / aac / ogg / oga / m4a / wma / opus / aiff / ape / mid，
  能力：播放暂停 / 进度拖动 / 音量。

## 数据文件

- data/viewer.json：元数据。播放能力取决于 Qt 多媒体后端，认不出的编码会提示播放失败。
