"""内置 Office / PDF 外部编辑器：为文档与表格登记「用系统默认程序编辑」。"""

from __future__ import annotations

from dm_plugin.builtin.lib.editor.plugin import KIND_EXTERNAL, EditorPlugin


class OfficeEditorPlugin(EditorPlugin):
    """doc / docx / xls / xlsx / ppt / pptx / pdf 等交给电脑上关联的程序编辑。"""

    default_kind = KIND_EXTERNAL
    default_host = ""  # 外部编辑器不弹程序窗口，不需要界面工具库
