# 内置 Office 外部编辑器（builtin.editor.office）

为 Word / Excel / PowerPoint / PDF 等文档与表格后缀登记**外部编辑器**：不提供程序内控件，点「编辑器 → 系统默认程序」时直接交给电脑上关联的默认程序打开编辑。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、depends `builtin.lib.editor`，`data: {"editor": "data/editor.json"}` |
| plugin.py | 入口：`OfficeEditorPlugin(EditorPlugin)`，`default_kind = KIND_EXTERNAL`、`default_host = ""` |
| data/editor.json | 显示名、`kind="external"`、扩展名清单、能力、排序 |

## 扩展名

`doc docx docm dot dotx xls xlsx xlsm xlsb csv ppt pptx pptm odt ods odp rtf pdf`

`csv` 同时被内置文本编辑器认领：默认按注册表规则（后注册 / 用户指定）决定用内部编辑器还是系统程序，可在「编辑器」页调整。

## 行为

- `kind="external"` 不弹程序内窗口，`EditorPlugin.open_editor()` 直接 `app.sdk.ui.open_default()`（系统默认程序）；
  程序侧 `edit_path()` 命中该条目时同样走外部打开。
- 不需要界面工具库（`default_host = ""`），因此清单只依赖 `builtin.lib.editor`。
- 系统默认程序里做的修改同样落在库内文件上；若用户希望保存后立即刷新条目，可在外部程序关闭后使用程序的「扫描 / 刷新」入口
  （外部程序无法回调本插件，故不自动触发 `refresh_path()`）。

## 依赖

- builtin.lib.editor（编辑器基类、注册表与规则）
