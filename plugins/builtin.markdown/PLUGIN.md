# 内置 Markdown 查看器（builtin.markdown）

渲染 Markdown 文档；超长文档只显示前 512 KiB，避免一次性渲染把界面卡住。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 MarkdownViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（kind=markdown、扩展名 md/markdown、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.ui（弹窗外壳）

## 贡献

- 查看器（app.viewer）：kind = markdown，认领 md / markdown，能力：Markdown 渲染 / 大文件截断。

## 数据文件

- data/viewer.json：元数据；截断阈值 512 KiB 来自程序侧的读取上限。
