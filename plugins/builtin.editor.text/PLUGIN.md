# 内置文本编辑器（builtin.editor.text）

为纯文本、代码、Markdown、CSV / TSV / JSON 等文本类后缀提供**程序内**编辑器：在独立窗口里直接改内容，保存回库内文件。

## 目录

| 文件 | 作用 |
| --- | --- |
| plugin.json | 清单：id、名称、depends `builtin.lib.editor` + `builtin.lib.ui`，`data: {"editor": "data/editor.json"}` |
| plugin.py | 入口：`TextEditorPlugin(EditorPlugin)`，只实现 `create_editor()` |
| text_editor.py | 编辑控件 `TextEditor(QWidget)`：编码下拉、自动换行、复制全文、行数 / 编码状态，`is_dirty()` / `save()` |
| data/editor.json | 显示名、`kind="internal"`、宿主 `dialog`、扩展名清单、能力、排序 |

## 扩展名

`txt text log nfo srt vtt rst org tex diff patch json xml yaml yml toml ini cfg conf env properties sql html htm css scss less`
`md markdown py pyw js mjs cjs ts tsx jsx java kt gradle c h cpp hpp cc cs go rs rb php pl lua r swift m mm scala`
`sh bash zsh fish bat cmd ps1 csv tsv dockerfile makefile cmake mk gitignore editorconfig m3u m3u8`

同一后缀也会被查看器插件认领，二者互不影响：查看器负责「打开看」，编辑器负责右键「编辑器」子菜单里的「编辑内容」。

## 行为

- 打开时用 `app.sdk.data.read_text()` 做编码探测，下拉可在 `自动检测` 与 `app.sdk.data.ENCODINGS` 间切换；超过 `TEXT_LIMIT` 的大文件按只读处理，保存返回失败并提示。
- 控件提供 `is_dirty()` 与 `save()`：窗口外壳据此启用「保存」按钮、显示「已修改」，关闭前询问未保存改动。
- 保存写回原文件（沿用探测 / 选择的编码），成功后走 `builtin.lib.editor` 的 `_on_saved()` → `ctx.host.refresh_path()`，重算条目 checksum / size / 内容并广播刷新。
- 工具条上的「用系统编辑器打开」「定位文件」由窗口外壳提供。

## 依赖

- builtin.lib.editor（编辑器基类与窗口外壳）
- builtin.lib.ui（窗口 / 控件工厂）
