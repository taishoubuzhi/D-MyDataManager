"""压缩包查看器：列出条目、预览文本内容、整体解压后用系统程序打开。

工具条、列表与预览区都来自 builtin.lib.ui：这里只把筛选、选中与解压接成回调。
"""

from __future__ import annotations

import tarfile
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from app.sdk import ui
from app.sdk.data import ArchiveMember, archive_members, archive_read, decode_text, human_size, looks_binary
from app.sdk.ui import COMPACT_MARGINS
from dm_plugin.builtin.lib.ui.plugin import (
    ListPanel,
    caption,
    icon_button,
    search_edit,
    status_label,
    text_area,
    toolbar,
)

PREVIEW_LIMIT = 64 * 1024


def _safe_target(root: Path, name: str) -> Path:
    """阻止 `..` / 绝对路径逃出解压目录。"""
    candidate = (root / name).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        raise ValueError(f"压缩包条目路径不安全：{name}")
    return candidate


def extract_all(path: Path, target: Path) -> int:
    """把压缩包解压到 target，返回条目数。仅支持 zip 与 tar 系列。"""
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            for info in members:
                _safe_target(target, info.filename)
            archive.extractall(target)
            return len(members)
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            for member in members:
                _safe_target(target, member.name)
            archive.extractall(target)
            return len(members)
    raise ValueError("该压缩格式暂不支持解压，可用系统程序打开")


class ArchiveViewer(QWidget):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        try:
            self._members: list[ArchiveMember] = archive_members(self._path)
        except Exception as exc:
            self._members = []
            self._error = str(exc)
        else:
            self._error = ""
        total = sum(member.size for member in self._members)
        self.caption = f"{self._path.name} · {len(self._members)} 个条目 · {human_size(total)}"
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*COMPACT_MARGINS)
        root.setSpacing(8)

        bar, row = toolbar(self)
        self._filter = search_edit(bar, placeholder="筛选条目名称", width=220, on_change=lambda _text: self._reload())
        row.addWidget(self._filter)
        row.addStretch(1)
        self.status_label = status_label(bar, self.caption)
        row.addWidget(self.status_label)
        row.addWidget(icon_button(bar, FluentIcon.ZIP_FOLDER, "解压并打开", self._on_extract))
        row.addWidget(icon_button(bar, FluentIcon.LINK, "用系统程序打开", lambda: ui.open_default(self._path)))
        root.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self._panel = ListPanel(self, title="", width=340, on_select=self._on_select)
        self._list = self._panel.list
        body.addWidget(self._panel.card)
        self._preview = text_area(self, read_only=True)
        body.addWidget(self._preview, 1)
        root.addLayout(body, 1)

        if self._error:
            self._preview.setPlainText(f"无法读取压缩包：{self._error}")

    # ------------------------------------------------------------------ 行为
    def _shown_members(self) -> list[ArchiveMember]:
        keyword = self._filter.text().strip().lower()
        if not keyword:
            return self._members
        return [member for member in self._members if keyword in member.name.lower()]

    def _reload(self) -> None:
        members = self._shown_members()
        self._panel.set_items(
            [
                (
                    index,
                    member.name + ("/" if member.is_dir else ""),
                    "" if member.is_dir else f"{member.name}   {human_size(member.size)}",
                )
                for index, member in enumerate(members)
            ]
        )
        shown = len(members)
        self.status_label.setText(f"{shown}/{len(self._members)} 个条目 · {self.caption}")
        if shown:
            self._list.setCurrentRow(0)
        else:
            self._preview.setPlainText("没有匹配的条目")

    def _on_select(self, data, _text: str) -> None:
        members = self._shown_members()
        if data is None or not (0 <= int(data) < len(members)):
            return
        member = members[int(data)]
        if member.is_dir:
            self._preview.setPlainText("这是一个目录条目。")
            return
        try:
            raw = archive_read(self._path, member.name, limit=PREVIEW_LIMIT)
        except Exception as exc:
            self._preview.setPlainText(f"无法读取该条目：{exc}")
            return
        if looks_binary(raw):
            self._preview.setPlainText(f"二进制内容（{human_size(member.size)}），可解压后用系统程序打开。")
            return
        text, used = decode_text(raw)
        note = ""
        if member.size > PREVIEW_LIMIT:
            note = f"\n\n—— 仅预览前 {human_size(PREVIEW_LIMIT)} ——"
        self._preview.setPlainText(text + note)
        if used:
            self.status_label.setText(f"{member.name} · 编码 {used}")

    def _on_extract(self) -> None:
        target = Path(tempfile.mkdtemp(prefix="dm-archive-"))
        try:
            count = extract_all(self._path, target)
        except Exception as exc:
            self.status_label.setText(f"解压失败：{exc}")
            return
        self.status_label.setText(f"已解压 {count} 个条目到 {target}")
        ui.open_default(target)
