"""库文件夹变更监听：目录被外部改动时提示用户手动扫描登记。

监听只负责提醒，不会自动收录文件：库内文件必须由用户点击“扫描并登记”才成为数据项。

优先用 `watchdog`（各平台的原生递归监听，绑一个根目录即可，没有目录数上限）；
没装 watchdog 时退回 Qt 的 `QFileSystemWatcher`——那条路必须逐个目录绑定，
所以仍留一个目录数上限防爆。watchdog 的回调跑在它自己的线程里，只发一个
「有动静」的信号，去抖与刷新仍在 GUI 线程做（Qt 的跨线程信号会自动排队）。
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication
from sqlalchemy.orm import Session

from ...services.library_service import LibraryService

try:  # 首选 watchdog；缺依赖时退回 Qt 自己的文件系统监听
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - 取决于运行环境
    FileSystemEventHandler = None  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]

#: 退回 Qt 监听时的目录数上限（watchdog 递归监听没有这个限制）
MAX_WATCHED_DIRS = 300
DEBOUNCE_MS = 1500
PAUSE_SECONDS = 5.0


def watchdog_available() -> bool:
    """当前环境有没有 watchdog（没有就退回 Qt 的 `QFileSystemWatcher`）。"""
    return Observer is not None


if FileSystemEventHandler is not None:

    class _WatchdogHandler(FileSystemEventHandler):
        """watchdog 回调：只把「有动静」转成一个信号，这里不碰任何 Qt 对象。"""

        def __init__(self, notify) -> None:
            super().__init__()
            self._notify = notify

        def on_any_event(self, event) -> None:
            self._notify()


class LibraryWatcher(QObject):
    """监听唯一库文件夹的目录变化，必要时请求界面提醒用户扫描。"""

    changed = pyqtSignal()
    #: watchdog 线程里只发这个信号，真正的去抖在 GUI 线程做
    _touched = pyqtSignal()

    def __init__(self, session: Session, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._paths: set[str] = set()
        self._pending = False
        self._ignore_until = 0.0
        self._root: Path | None = None
        self._observer = Observer() if Observer is not None else None
        self._handler = _WatchdogHandler(self._touched.emit) if FileSystemEventHandler is not None else None
        self._qt_watcher = QFileSystemWatcher(self) if self._observer is None else None
        if self._qt_watcher is not None:
            self._qt_watcher.directoryChanged.connect(self._on_directory_changed)
        self._touched.connect(self._on_touched)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._emit_pending)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        self.refresh()

    # ------------------------------------------------------------- 绑定
    def refresh(self) -> None:
        """重新绑定库文件夹（库路径变更、扫描登记后调用）。"""
        root = Path(LibraryService(self.session).ensure_default().path)
        if self._observer is not None:
            self._watch_recursive(root)
            return
        self._watch_with_qt(root)

    def _watch_recursive(self, root: Path) -> None:
        """watchdog 路线：绑一个根目录、递归生效，没有目录数上限。"""
        if not root.is_dir():
            self._stop_observer()
            return
        if self._root == root and self._observer is not None and self._observer.is_alive():
            return  # 已经盯着这个根目录
        self._stop_observer()
        try:
            self._observer.schedule(self._handler, str(root), recursive=True)
            self._observer.start()
        except OSError as exc:
            logger.warning("库文件夹监听启动失败：{}", exc)
            self._stop_observer()
            return
        self._root = root
        self._paths = {str(root)}
        logger.debug("库文件夹监听已绑定（watchdog 递归）：{}", root)

    def _watch_with_qt(self, root: Path) -> None:
        """兜底路线：Qt 只能逐个目录绑定，目录多了会漏掉后面的。"""
        watcher = self._qt_watcher
        if watcher is None:
            return
        for path in watcher.directories():
            watcher.removePath(path)
        self._paths.clear()
        self._root = root if root.is_dir() else None
        if self._root is None:
            return
        for directory in self._directories(root):
            if watcher.addPath(str(directory)):
                self._paths.add(str(directory))
        logger.debug("库文件夹监听已绑定 {} 个目录（Qt）", len(self._paths))

    def _stop_observer(self) -> None:
        """停掉 watchdog 线程并准备一个新的观察者（停过的观察者不能重启）。"""
        observer, self._observer = self._observer, Observer() if Observer is not None else None
        self._root = None
        self._paths = set()
        if observer is None or not observer.is_alive():
            return
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception as exc:  # noqa: BLE001 - 退出路径上不该再抛
            logger.debug("停止库文件夹监听失败：{}", exc)

    def shutdown(self) -> None:
        """退出时停掉监听线程（watchdog 线程不停，解释器可能挂着不退出）。可重复调用。"""
        self._timer.stop()
        self._stop_observer()

    @property
    def watched(self) -> int:
        """监听中的目录数（watchdog 递归只算绑定的那一个根目录）。"""
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

    def _on_touched(self) -> None:
        """watchdog 线程的信号落到 GUI 线程后才去抖（QTimer 只能在 GUI 线程动）。"""
        self._pending = True
        self._timer.start()

    def _on_directory_changed(self, path: str) -> None:
        if path not in self._paths:
            return
        self._pending = True
        self._timer.start()

    def _emit_pending(self) -> None:
        pending, self._pending = self._pending, False
        # 每次都重绑一次：Qt 兜底路线要借此收进外部新建的子目录（watchdog 路线是空操作）
        self.refresh()
        if not pending:
            return
        if time.monotonic() < self._ignore_until:
            logger.debug("忽略库文件夹的内部写入")
            return
        self.changed.emit()


__all__ = ["LibraryWatcher", "watchdog_available"]
