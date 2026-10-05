"""模型页用到的对话框：模板选择、本地模型新增/编辑、外部模型新增/编辑。

弹窗外壳、字段、控件全部来自 UI 工具库（`dm_plugin.builtin.lib.ui.plugin`）：
本文件只写「有哪些字段、字段之间怎么联动」，不再自己拼布局或算滚动区高度。
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QWidget

from app.sdk.data import human_size

from dm_plugin.builtin.lib.ui.plugin import (
    FormDialog,
    check_grid,
    combo_box,
    line_edit,
    list_view,
    push_button,
    radio_button,
    status_label,
    strong_label,
    text_area,
    text_edit,
    widget_row,
)

from ..constants import ADAPTER_LABELS, ADAPTER_OPENAI, ADAPTER_OLLAMA, ADAPTER_LLAMA_SERVER, BACKENDS, BACKEND_LABELS, CAPABILITIES, CAPABILITY_LABELS

__all__ = [
    "CleanupWeightsDialog",
    "CompleteRuntimesDialog",
    "DeleteModelDialog",
    "ExternalModelDialog",
    "LocalModelDialog",
    "ModelLogDialog",
    "ReplaceWeightsDialog",
    "TemplateDialog",
]

_NONE_TEMPLATE = "（不使用模板）"
_NONE_MODEL = "（用上面填的模型名）"
_NO_MODEL_LIST = "（这个供应商没带模型清单，直接填模型名）"
_EXTERNAL_ADAPTERS = (ADAPTER_OPENAI, ADAPTER_OLLAMA, ADAPTER_LLAMA_SERVER)
#: 能力多选框的取值与文案（顺序就是界面上的顺序）。
_CAPABILITY_ITEMS = tuple((name, CAPABILITY_LABELS.get(name, name)) for name in CAPABILITIES)


def _capability_grid(parent: QWidget, checked: list[str] | None = None):
    """能力多选：三列勾选网格，返回 (容器, {能力: 勾选框})。"""
    return check_grid(parent, _CAPABILITY_ITEMS, columns=3, checked=checked or ())


class TemplateDialog(FormDialog):
    """从模板列表里挑一条（本地权重模板 / 外部接口模板共用）。"""

    def __init__(self, parent: QWidget, templates: list[dict], *, title: str = "选择模板") -> None:
        super().__init__(parent, title=title, width=520, scroll=False)
        self._templates = list(templates)
        self._chosen: dict | None = None

        self.list = list_view(self, minimum_height=240)
        for entry in self._templates:
            self.list.addItem(str(entry.get("name") or entry.get("id") or "未命名"))
        self.list.currentRowChanged.connect(self._on_row)
        self.add_widget(self.list)

        self.detail = self.add_hint("")

        self.set_buttons(yes="使用这条模板", cancel="取消")
        if self._templates:
            self.list.setCurrentRow(0)

    def _on_row(self, row: int) -> None:
        if 0 <= row < len(self._templates):
            entry = self._templates[row]
            self._chosen = entry
            self.detail.setText(str(entry.get("description") or ""))

    def chosen(self) -> dict | None:
        return self._chosen


class LocalModelDialog(FormDialog):
    """本地模型：可以从模板 + 仓库信息下载，也可以只填目录后扫本地文件。"""

    def __init__(self, parent: QWidget, *, templates: list[dict], profiles: list[dict], record=None) -> None:
        super().__init__(parent, title="本地模型" if record is None else "编辑本地模型", width=560)
        self._templates = list(templates)
        self._profiles = list(profiles)
        self._applying = False
        self._files: list[str] = []

        self.name_edit = line_edit(self, placeholder="例如 Qwen2.5 1.5B（给这张卡片起的名字）")
        self.template_box = combo_box(self, items=(_NONE_TEMPLATE, *(
            str(entry.get("name") or entry.get("id")) for entry in self._templates
        )))
        self.template_box.currentIndexChanged.connect(self._apply_template)
        self.repo_edit = line_edit(self, placeholder="HuggingFace 仓库，例如 Qwen/Qwen2.5-1.5B-Instruct-GGUF")
        self.file_edit = line_edit(
            self,
            placeholder="仓库内文件名，例如 qwen2.5-1.5b-instruct-q4_k_m.gguf；留空表示下整个仓库",
        )
        self.revision_edit = line_edit(self, placeholder="分支 / 版本，默认 main")
        self.profile_box = combo_box(self, items=tuple(str(entry.get("name") or entry.get("id")) for entry in self._profiles))
        self.backend_box = combo_box(self, items=tuple(BACKEND_LABELS.get(name, name) for name in BACKENDS))
        self.capability_holder, self.capability_boxes = _capability_grid(self)
        self.description_edit = line_edit(self, placeholder="备注（可留空）")

        for label, widget in (
            ("名称", self.name_edit),
            ("模板（可选，选中会自动填下面几项）", self.template_box),
            ("模型仓库", self.repo_edit),
            ("仓库内文件", self.file_edit),
            ("版本", self.revision_edit),
            ("能力（可多选）", self.capability_holder),
            ("运行环境", self.profile_box),
            ("推理后端", self.backend_box),
            ("备注", self.description_edit),
        ):
            self.add_field(label, widget)

        for widget in (self.name_edit, self.repo_edit, self.file_edit, self.revision_edit, self.description_edit):
            widget.textEdited.connect(self._on_field_edited)
        for box in self.capability_boxes.values():
            box.toggled.connect(self._on_field_edited)
        self.profile_box.currentIndexChanged.connect(self._on_field_edited)
        self.backend_box.currentIndexChanged.connect(self._on_field_edited)

        self.hint = status_label(
            self,
            "保存后卡片是「未填充」状态：在卡片上点「下载」拉权重，或把权重丢进目录后点「扫描目录」。",
        )
        self.add_widget(self.hint)

        self.set_buttons(yes="保存", cancel="取消")

        if record is not None:
            self._load_record(record)

    # ------------------------------------------------------------ 数据

    def _on_field_edited(self, *_args) -> None:
        """用户手动改了字段：模板下拉回落到「不使用模板」，免得提示的还是旧模板。"""
        self._files = []
        if self._applying or self.template_box.currentIndex() <= 0:
            return
        self._applying = True
        try:
            self.template_box.setCurrentIndex(0)
        finally:
            self._applying = False

    def _apply_template(self, index: int) -> None:
        if index <= 0:
            return
        self._applying = True
        entry = self._templates[index - 1]
        if not self.name_edit.text():
            self.name_edit.setText(str(entry.get("name") or ""))
        source = entry.get("source") or {}
        self._files = [str(item) for item in (source.get("files") or []) if str(item).strip()]
        self.repo_edit.setText(str(source.get("repo") or ""))
        self.file_edit.setText(str(source.get("file") or ""))
        self.revision_edit.setText(str(source.get("revision") or ""))
        for name, box in self.capability_boxes.items():
            box.setChecked(name in (entry.get("capabilities") or []))
        runtime = entry.get("runtime") or {}
        backend = str(runtime.get("backend") or "")
        for index2, name in enumerate(BACKENDS):
            if name == backend:
                self.backend_box.setCurrentIndex(index2)
        profile = str(runtime.get("profile") or "")
        for index2, item in enumerate(self._profiles):
            if str(item.get("id") or "") == profile:
                self.profile_box.setCurrentIndex(index2)
        self.description_edit.setText(str(entry.get("description") or ""))
        self._applying = False

    def _load_record(self, record) -> None:
        self.name_edit.setText(record.name)
        self._files = [str(item) for item in (record.source.get("files") or []) if str(item).strip()]
        self.repo_edit.setText(str(record.source.get("repo") or ""))
        self.file_edit.setText(str(record.source.get("file") or ""))
        self.revision_edit.setText(str(record.source.get("revision") or ""))
        self.description_edit.setText(record.description or "")
        for name, box in self.capability_boxes.items():
            box.setChecked(name in record.capabilities)
        backend = str(record.runtime.get("backend") or "")
        for index, name in enumerate(BACKENDS):
            if name == backend:
                self.backend_box.setCurrentIndex(index)
        profile = str(record.runtime.get("profile") or "")
        for index, item in enumerate(self._profiles):
            if str(item.get("id") or "") == profile:
                self.profile_box.setCurrentIndex(index)

    def values(self) -> dict:
        profile = self._profiles[self.profile_box.currentIndex()] if self._profiles else {}
        source = {
            "provider": "huggingface",
            "repo": self.repo_edit.text().strip(),
            "file": self.file_edit.text().strip(),
            "revision": self.revision_edit.text().strip() or "main",
        }
        if self._files:
            source["files"] = list(self._files)
        return {
            "name": self.name_edit.text().strip(),
            "capabilities": [name for name, box in self.capability_boxes.items() if box.isChecked()],
            "description": self.description_edit.text().strip(),
            "source": source,
            "runtime": {
                "adapter": "worker",
                "backend": BACKENDS[self.backend_box.currentIndex()] if BACKENDS else "llama_cpp",
                "profile": str(profile.get("id") or ""),
            },
        }


class ExternalModelDialog(FormDialog):
    """外部模型：填接口信息，常见服务可以用模板一键填。"""

    def __init__(self, parent: QWidget, *, templates: list[dict], record=None) -> None:
        super().__init__(parent, title="外部模型" if record is None else "编辑外部模型", width=560)
        self._templates = list(templates)
        self._applying = False

        self.name_edit = line_edit(self, placeholder="例如 DeepSeek 线上（给这张卡片起的名字）")
        self.template_box = combo_box(self, items=(_NONE_TEMPLATE, *(
            str(entry.get("name") or entry.get("id")) for entry in self._templates
        )))
        self.template_box.currentIndexChanged.connect(self._apply_template)

        self.model_box = combo_box(self)
        self._sync_model_box(None)
        self.model_box.currentIndexChanged.connect(self._apply_model)

        self.adapter_box = combo_box(self, items=tuple(ADAPTER_LABELS.get(name, name) for name in _EXTERNAL_ADAPTERS))
        self.base_edit = line_edit(self, placeholder="接口地址，例如 https://api.deepseek.com/v1")
        self.model_edit = line_edit(self, placeholder="模型名，例如 deepseek-chat")
        self.key_ref_edit = line_edit(self, placeholder="密钥名称（只是引用名，例如 deepseek）")
        self.key_edit = line_edit(
            self,
            placeholder="API Key（只存本地 .configs/models.json，日志里会打码）",
            password=True,
        )
        self.capability_holder, self.capability_boxes = _capability_grid(self)
        self.params_edit = text_edit(self, placeholder='附加参数 JSON，例如 {"temperature": 0.7}', height=64)
        self.description_edit = line_edit(self)

        for label, widget in (
            ("名称", self.name_edit),
            ("供应商（可选，选中会自动填接口）", self.template_box),
            ("模型（可选，跟着供应商走）", self.model_box),
            ("适配器", self.adapter_box),
            ("接口地址", self.base_edit),
            ("模型名", self.model_edit),
            ("密钥名称", self.key_ref_edit),
            ("API Key", self.key_edit),
            ("能力（可多选）", self.capability_holder),
            ("附加参数", self.params_edit),
            ("备注", self.description_edit),
        ):
            self.add_field(label, widget)

        for widget in (
            self.name_edit,
            self.base_edit,
            self.model_edit,
            self.key_ref_edit,
            self.key_edit,
            self.description_edit,
        ):
            widget.textEdited.connect(self._on_field_edited)
        self.params_edit.textChanged.connect(self._on_field_edited)
        for box in self.capability_boxes.values():
            box.toggled.connect(self._on_field_edited)
        self.adapter_box.currentIndexChanged.connect(self._on_field_edited)

        self.set_buttons(yes="保存", cancel="取消")

        if record is not None:
            self._load_record(record)

    # ------------------------------------------------------------ 联动

    def _on_field_edited(self, *_args) -> None:
        """用户手动改了字段：模板下拉回落到「不使用模板」，免得提示的还是旧模板。"""
        if self._applying or self.template_box.currentIndex() <= 0:
            return
        self._applying = True
        try:
            self.template_box.setCurrentIndex(0)
        finally:
            self._applying = False

    def _apply_template(self, index: int) -> None:
        if index <= 0:
            self._sync_model_box(None)
            return
        self._applying = True
        entry = self._templates[index - 1]
        if not self.name_edit.text():
            self.name_edit.setText(str(entry.get("name") or ""))
        self.base_edit.setText(str(entry.get("base_url") or ""))
        self.model_edit.setText(str(entry.get("model") or ""))
        self.key_ref_edit.setText(str(entry.get("api_key_ref") or ""))
        for name, box in self.capability_boxes.items():
            box.setChecked(name in (entry.get("capabilities") or []))
        adapter = str(entry.get("adapter") or "")
        for index2, name in enumerate(_EXTERNAL_ADAPTERS):
            if name == adapter:
                self.adapter_box.setCurrentIndex(index2)
        self.description_edit.setText(str(entry.get("description") or ""))
        self._sync_model_box(entry)
        self._applying = False
        self._apply_model(self.model_box.currentIndex())

    def _sync_model_box(self, entry: dict | None) -> None:
        """按供应商刷新「模型」下拉；老格式（没有 models 列表）留一条提示。"""
        models = [item for item in ((entry or {}).get("models") or []) if isinstance(item, dict)]
        previous = self._applying
        self._applying = True
        try:
            self.model_box.clear()
            self.model_box.addItem(_NONE_MODEL if models else _NO_MODEL_LIST)
            for item in models:
                self.model_box.addItem(str(item.get("name") or item.get("id") or ""))
            preferred = str((entry or {}).get("model") or "")
            current = 0
            for offset, item in enumerate(models):
                if str(item.get("id") or "") == preferred:
                    current = offset + 1
                    break
            self.model_box.setCurrentIndex(current)
            self.model_box.setEnabled(bool(models))
        finally:
            self._applying = previous

    def _apply_model(self, index: int) -> None:
        """选中某个具体模型：填模型名，并把它的能力 / 备注盖上去。"""
        if self._applying:
            return
        vendor = self.template_box.currentIndex()
        entry = self._templates[vendor - 1] if vendor > 0 else {}
        models = [item for item in ((entry or {}).get("models") or []) if isinstance(item, dict)]
        if index <= 0 or index > len(models):
            return
        item = models[index - 1]
        previous = self._applying
        self._applying = True
        try:
            self.model_edit.setText(str(item.get("id") or ""))
            caps = [str(name) for name in (item.get("capabilities") or [])] or list(entry.get("capabilities") or [])
            for name, box in self.capability_boxes.items():
                box.setChecked(name in caps)
            note = str(item.get("description") or "")
            if note:
                self.description_edit.setText(note)
            if not self.name_edit.text():
                label = str(item.get("name") or item.get("id") or "")
                prefix = str(entry.get("name") or "")
                self.name_edit.setText(f"{prefix} · {label}" if prefix and label else (label or prefix))
        finally:
            self._applying = previous

    def _load_record(self, record) -> None:
        self.name_edit.setText(record.name)
        self.base_edit.setText(str(record.api.get("base_url") or ""))
        self.model_edit.setText(str(record.api.get("model") or ""))
        self.key_ref_edit.setText(str(record.api.get("api_key_ref") or ""))
        self.description_edit.setText(record.description or "")
        for name, box in self.capability_boxes.items():
            box.setChecked(name in record.capabilities)
        adapter = str(record.api.get("adapter") or "")
        for index, name in enumerate(_EXTERNAL_ADAPTERS):
            if name == adapter:
                self.adapter_box.setCurrentIndex(index)
        params = record.api.get("params") or {}
        if params:
            self.params_edit.setPlainText(json.dumps(params, ensure_ascii=False))
        self._match_template(record)

    def _match_template(self, record) -> None:
        """按接口地址把两个下拉对到模板上；只动下拉，不覆盖已经填好的字段。"""
        target = str(record.api.get("base_url") or "").rstrip("/")
        if not target:
            return
        for offset, entry in enumerate(self._templates):
            if str(entry.get("base_url") or "").rstrip("/") != target:
                continue
            previous = self._applying
            self._applying = True
            try:
                self.template_box.setCurrentIndex(offset + 1)
                self._sync_model_box(entry)
                model = str(record.api.get("model") or "")
                models = [item for item in (entry.get("models") or []) if isinstance(item, dict)]
                for index, item in enumerate(models):
                    if str(item.get("id") or "") == model:
                        self.model_box.setCurrentIndex(index + 1)
                        break
            finally:
                self._applying = previous
            return

    def values(self) -> dict:
        params: dict = {}
        text = self.params_edit.toPlainText().strip()
        if text:
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    params = loaded
            except json.JSONDecodeError:
                params = {}
        adapter = _EXTERNAL_ADAPTERS[self.adapter_box.currentIndex()] if _EXTERNAL_ADAPTERS else ADAPTER_OPENAI
        return {
            "name": self.name_edit.text().strip(),
            "capabilities": [name for name, box in self.capability_boxes.items() if box.isChecked()],
            "description": self.description_edit.text().strip(),
            "adapter": adapter,
            "base_url": self.base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "api_key_ref": self.key_ref_edit.text().strip(),
            "api_key": self.key_edit.text(),
            "params": params,
        }


class DeleteModelDialog(FormDialog):
    """删除模型登记：只删登记，权重文件一个字节都不动。"""

    def __init__(self, parent: QWidget, *, name: str, folder: Path | None, note: str = "") -> None:
        super().__init__(parent, title=f"删除「{name}」？", width=520, scroll=False)
        self._folder = Path(folder) if folder is not None else None
        self._note = str(note or "")

        self.detail = self.add_hint("")
        self.hint = self.add_hint("")
        self._sync_hint()

        self.set_buttons(yes="删除", cancel="取消")

    def _sync_hint(self) -> None:
        """权重不跟着登记一起删：删错几个 G 得重下，要清理走「清理未使用的权重」。"""
        if self._folder is None:
            self.detail.setText(self._note or "这条模型没有本地权重目录，删掉登记就完了。")
            self.hint.setText("")
            return
        self.detail.setText("只删这条登记，磁盘上的权重文件留着。")
        self.hint.setText(f"权重目录会保留：{self._folder}\n想清理没人用的权重，点工具条上的「清理未使用的权重」。")


_WEIGHT_FILTER = (
    "模型文件 (*.gguf *.safetensors *.bin *.onnx *.pt *.pth *.model *.json *.txt);;所有文件 (*)"
)


class ReplaceWeightsDialog(FormDialog):
    """权重已经有了：换一组磁盘上的文件，或者按仓库信息重新下载。"""

    def __init__(self, parent: QWidget, *, name: str, files: tuple[str, ...], folder: Path | None, note: str = "") -> None:
        super().__init__(parent, title=f"「{name}」已经有权重了", width=560, scroll=False)
        self._folder = Path(folder) if folder is not None else None
        self._paths: list[str] = []

        self.add_hint(_files_text(files))

        self.files_radio = radio_button(
            self,
            text="换成磁盘上已有的权重文件（会复制进这个模型的权重目录）",
            checked=True,
        )
        self.add_widget(self.files_radio)

        self.browse_button = push_button(self, "选择文件…", self._browse)
        self.picked = status_label(self, "还没有选文件")
        self.add_widget(widget_row(self, self.browse_button, self.picked, stretches=(0, 1)))

        self.download_radio = radio_button(self, text="按仓库信息重新下载（先删掉现有的权重文件）")
        self.add_widget(self.download_radio)

        if note:
            self.add_hint(note)

        self.set_buttons(yes="确定", cancel="取消")

    def _browse(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileNames(
            self,
            "选择这个模型的权重文件",
            str(self._folder or ""),
            _WEIGHT_FILTER,
        )
        if chosen:
            self._paths = list(chosen)
            self.files_radio.setChecked(True)
            self.picked.setText("已选：" + "、".join(Path(item).name for item in self._paths))

    def choice(self) -> tuple[str, list[str]]:
        """返回 `("files", 选中的文件)` / `("download", [])`；没选就是 `("cancel", [])`。"""
        if self.files_radio.isChecked():
            return ("files", list(self._paths)) if self._paths else ("cancel", [])
        if self.download_radio.isChecked():
            return "download", []
        return "cancel", []


class CleanupWeightsDialog(FormDialog):
    """清理没人登记的权重：列出候选，勾谁删谁。"""

    def __init__(self, parent: QWidget, *, entries: list[dict]) -> None:
        super().__init__(parent, title="下面这些权重没有任何模型登记在用", width=620, scroll=False)
        self._entries = list(entries)
        self._boxes: list[tuple] = []

        self.add_hint("默认全不勾：只删你选中的。删之前请确认没有别的地方还在用它。")

        self.total = status_label(self, _entries_text(entries))
        pick_all = push_button(self, "全选", lambda: self._set_all(True))
        pick_none = push_button(self, "全不选", lambda: self._set_all(False))
        self.add_widget(widget_row(self, pick_all, pick_none, self.total, stretches=(0, 0, 1)))

        content = QWidget(self)
        from PyQt6.QtWidgets import QVBoxLayout

        stack = QVBoxLayout(content)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(6)
        from dm_plugin.builtin.lib.ui.plugin import check_box

        for entry in self._entries:
            box = check_box(content, text=f"{entry.get('name')} · {entry.get('size_text')} · {entry.get('kind')}")
            box.setToolTip(str(entry.get("path")))
            stack.addWidget(box)
            self._boxes.append((box, entry))

        from dm_plugin.builtin.lib.ui.plugin import scroll_area

        self.add_widget(scroll_area(self, widget=content, minimum_height=240, maximum_height=360))

        self.set_buttons(yes="删除选中的", cancel="取消")

    def _set_all(self, checked: bool) -> None:
        for box, _entry in self._boxes:
            box.setChecked(checked)

    def selected(self) -> list[dict]:
        return [entry for box, entry in self._boxes if box.isChecked()]


class CompleteRuntimesDialog(FormDialog):
    """一键补全前的调度选择：几个环境一起装，还是挨个装。"""

    def __init__(self, parent: QWidget, *, detail: str, count: int = 0) -> None:
        super().__init__(parent, title="一键补全运行环境？", width=560, scroll=False)
        self.add_hint(detail)
        self.parallel_radio = radio_button(
            self, text="并发安装：几个环境同时装（快；带宽与磁盘一起用）", checked=True
        )
        self.sequential_radio = radio_button(
            self, text="挨个安装：装完一个再装下一个（稳；输出不会互相挤在一起）"
        )
        self.add_widget(self.parallel_radio)
        self.add_widget(self.sequential_radio)
        self.set_buttons(yes=f"开始安装（{count} 个）" if count else "开始安装", cancel="取消")

    def sequential(self) -> bool:
        """用户选了「挨个安装」吗。"""
        return bool(self.sequential_radio.isChecked())


def _files_text(files: tuple[str, ...]) -> str:
    names = [str(item) for item in (files or ()) if str(item)]
    if not names:
        return "现在的登记里没有文件名。"
    shown = "、".join(names[:4])
    return f"现有文件：{shown}{'…' if len(names) > 4 else ''}"


def _entries_text(entries: list[dict]) -> str:
    total = 0
    for entry in entries:
        try:
            total += int(entry.get("size") or 0)
        except (TypeError, ValueError):
            continue
    return f"共 {len(entries)} 项 · {human_size(total) if total else '大小未知'}"

class ModelLogDialog(FormDialog):
    """看一条模型的最新运行日志（只读）。

    一个模型只有一份日志、每次运行重写，所以这里不用挑文件；没跑过就显示一句提示。
    """

    def __init__(self, parent: QWidget, *, name: str, path: Path | None, text: str) -> None:
        super().__init__(parent, title=f"{name} · 运行日志", width=760, scroll=False, minimum_height=440)
        self.add_hint(str(path) if path is not None else "这条模型还没有日志：加载或测试过一次才会写。")
        self.log_text = text_area(self, text=text or "（空）", read_only=True, monospace=True)
        self.log_text.setMinimumHeight(340)
        self.add_widget(self.log_text)
        self.cancelButton.hide()
        self.set_buttons(yes="关闭")
