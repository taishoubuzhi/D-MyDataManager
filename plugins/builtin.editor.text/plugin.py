"""内置文本编辑器：继承编辑器基类，为文本与代码格式提供可编辑控件。"""

from __future__ import annotations

from dm_plugin.builtin.lib.editor.plugin import EditorPlugin


class TextEditorPlugin(EditorPlugin):
    """打开纯文本 / 代码 / markdown / csv / json 等，控件里可编辑并保存。"""

    def create_editor(self, path, parent=None):
        from .text_editor import TextEditor

        return TextEditor(path, parent)
