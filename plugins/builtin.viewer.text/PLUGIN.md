# 内置文本查看器（builtin.viewer.text）

查看纯文本与代码文件：自动探测编码、可切换编码，大文件按上限截断，并统计行数。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：依赖与 data 声明 |
| plugin.py | 插件类 TextViewerPlugin(ViewerPlugin)：只实现 create_view() |
| data/viewer.json | 查看器元数据（名称、kind=text、宿主、扩展名、能力） |

## 依赖

- builtin.lib.viewer（查看器基类）
- builtin.lib.ui（弹窗外壳）

## 贡献

- 查看器（app.viewer）：kind = text，认领 60 多个常见文本与代码扩展名
  （txt / log / json / xml / yaml / toml / ini / py / js / ts / java / c / cpp / go / rs / sh / ps1 / sql / html / css …），
  能力：编码探测 / 编码切换 / 大文件截断 / 行号统计。完整清单见 data/viewer.json。

## 数据文件

- data/viewer.json：想多认领一种后缀（比如 .md2 之外的），只改这里的 extensions。

## 说明

读取上限与编码表来自 SDK 的纯函数（`app.sdk.data` 的 ENCODINGS / TEXT_LIMIT）——插件只能用 `app.sdk`。
