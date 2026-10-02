"""压缩包查看器：列出条目、预览文本内容、整体解压后用系统程序打开。"""

from __future__ import annotations

import tarfile
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtWidgets import QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    ListWidget,
    PlainTextEdit,
    PushButton,
    SearchLineEdit,
)

from ..framework import COMPACT_MARGINS
from ...core import shell
from ...core.viewer_data import ArchiveMember, archive_members, archive_read, decode_text, human_size, looks_binary

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

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._filter = SearchLineEdit(self)
        self._filter.setPlaceholderText("筛选条目名称")
        self._filter.setFixedWidth(220)
        self._filter.textChanged.connect(self._reload)
        bar.addWidget(self._filter)
        bar.addStretch(1)
        self.status_label = CaptionLabel(self.caption, self)
        bar.addWidget(self.status_label)
        self._extract_button = PushButton(FluentIcon.ZIP_FOLDER, "解压并打开", self)
        self._extract_button.clicked.connect(self._on_extract)
        bar.addWidget(self._extract_button)
        external_button = PushButton(FluentIcon.LINK, "用系统程序打开", self)
        external_button.clicked.connect(lambda: shell.open_default(self._path))
        bar.addWidget(external_button)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(8)
        self._list = ListWidget(self)
        self._list.setFixedWidth(340)
        self._list.currentRowChanged.connect(self._on_select)
        body.addWidget(self._list)
        self._preview = PlainTextEdit(self)
        self._preview.setReadOnly(True)
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
        self._list.clear()
        members = self._shown_members()
        for member in members:
            label = member.name + ("/" if member.is_dir else "")
            extra = "" if member.is_dir else f"   {human_size(member.size)}"
            self._list.addItem(QListWidgetItem(f"{label}{extra}"))
        shown = len(members)
        self.status_label.setText(f"{shown}/{len(self._members)} 个条目 · {self.caption}")
        if shown:
            self._list.setCurrentRow(0)
        else:
            self._preview.setPlainText("没有匹配的条目")

    def _on_select(self, row: int) -> None:
        members = self._shown_members()
        if row < 0 or row >= len(members):
            return
        member = members[row]
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
        shell.open_default(target)
