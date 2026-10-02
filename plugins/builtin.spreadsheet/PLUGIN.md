# 内置表格查看器（builtin.spreadsheet）

查看 xlsx / csv 表格：可切换工作表，只预览前若干行，csv 自动识别分隔符。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 SheetViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（kind=spreadsheet、扩展名、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.dialog（弹窗外壳）

## 贡献

- 打开方式（app.viewer）：kind = spreadsheet，认领 xlsx / xlsm / csv / tsv，
  能力：工作表切换 / 前 300 行预览 / 分隔符识别。

## 数据文件

- data/viewer.json：元数据；解析逻辑用程序侧的 SheetData / xlsx_sheets / csv_rows 纯函数。
