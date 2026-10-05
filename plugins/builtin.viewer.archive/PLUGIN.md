# 内置压缩包查看器（builtin.viewer.archive）

列出压缩包内的条目与大小，并预览包内文本文件。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 ArchiveViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（kind=archive、扩展名、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.ui（弹窗外壳）

## 贡献

- 查看器（app.viewer）：kind = archive，认领 zip / jar / whl / apk / tar / gz / tgz / bz2 / tbz / tbz2 / xz / txz，
  能力：条目列表 / 压缩比 / 包内文本预览。

## 数据文件

- data/viewer.json：元数据；zip 之外的格式按 tar 系列处理，成员上限来自程序侧常量。
