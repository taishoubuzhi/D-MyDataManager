"""库文件夹变更监听：目录被外部改动时提示用户手动扫描登记。

监听只负责提醒，不会自动收录文件：库内文件必须由用户点击“扫描并登记”才成为数据项。
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal
from sqlalchemy.orm import Session

from ...services.library_service import LibraryService

MAX_WATCHED_DIRS = 300
DEBOUNCE_MS = 1500
PAUSE_SECONDS = 5.0


class LibraryWatcher(QObject):
    """监听唯一库文件夹的目录变化，必要时请求界面提醒用户扫描。"""

    changed = pyqtSignal()

    def __init__(self, session: Session, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._paths: set[str] = set()
        self._pending = False
        self._ignore_until = 0.0
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._emit_pending)
        self.refresh()

    # ------------------------------------------------------------- 绑定
    def refresh(self) -> None:
        """重新绑定库文件夹内的目录（库路径变更、扫描登记后调用）。"""
        for path in self._watcher.directories():
            self._watcher.removePath(path)
        self._paths.clear()
        root = Path(LibraryService(self.session).ensure_default().path)
        if not root.is_dir():
            return
        for directory in self._directories(root):
            if self._watcher.addPath(str(directory)):
                self._paths.add(str(directory))
        logger.debug("库文件夹监听已绑定 {} 个目录", len(self._paths))

    @property
    def watched(self) -> int:
        return len(self._paths)

    @staticmethod
    def _directories(root: Path, limit: int = MAX_WATCHED_DIRS) -> list[Path]:
        dirs = [root]
        try:
            for path in root.rglob("*"):
                if path.is_dir():
                    dirs.append(path)
                    if len(dirs) >= limit:
                        logger.warning("库目录过多，仅监听前 {} 个目录", limit)
                        break
        except OSError as exc:
            logger.warning("遍历库目录失败：{}", exc)
        return dirs

    # ------------------------------------------------------------- 事件
    def pause(self, seconds: float = PAUSE_SECONDS) -> None:
        """内部写入（导入、移动、扫描）期间忽略事件，避免自我提醒。"""
        self._ignore_until = max(self._ignore_until, time.monotonic() + seconds)

    def resume(self) -> None:
        """结束忽略窗口（内部写入完成后立即恢复提醒）。"""
        self._ignore_until = 0.0

    def _on_directory_changed(self, path: str) -> None:
        if path not in self._paths:
            return
        self._pending = True
        self._timer.start()

    def _emit_pending(self) -> None:
        pending, self._pending = self._pending, False
        self.refresh()
        if not pending:
            return
        if time.monotonic() < self._ignore_until:
            logger.debug("忽略库文件夹的内部写入")
            return
        self.changed.emit()


__all__ = ["LibraryWatcher"]
