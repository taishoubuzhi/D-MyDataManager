"""封面缩略图的异步加载与 LRU 缓存。"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

DEFAULT_CACHE_LIMIT = 400
DEFAULT_THREADS = 2


class _LoaderSignals(QObject):
    loaded = pyqtSignal(str, int, QImage)


class _CoverTask(QRunnable):
    """在工作线程中解码图片并缩放到目标尺寸。"""

    def __init__(self, path: str, size: int, signals: _LoaderSignals) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self._signals = signals

    def run(self) -> None:
        # 封面同样在资源文件夹里：受保护时先瞬时放行再读取
        from ...services.privacy_service import privacy

        with privacy.guard():
            image = QImage(self._path)
        if image.isNull():
            self._signals.loaded.emit(self._path, self._size, QImage())
            return
        self._signals.loaded.emit(
            self._path,
            self._size,
            image.scaled(
                self._size,
                self._size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ),
        )


class CoverLoader(QObject):
    """按 (路径, 尺寸) 缓存缩略图；未命中时异步加载一次并通知等待者。"""

    def __init__(
        self,
        parent: QObject | None = None,
        cache_limit: int = DEFAULT_CACHE_LIMIT,
        threads: int = DEFAULT_THREADS,
    ) -> None:
        super().__init__(parent)
        self._cache_limit = max(1, int(cache_limit))
        self._cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()
        self._pending: dict[tuple[str, int], list[Callable[[QPixmap | None], None]]] = {}
        self._missing: set[tuple[str, int]] = set()
        self._signals = _LoaderSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, int(threads)))

    def cached(self, path: str, size: int) -> QPixmap | None:
        key = (path, size)
        pixmap = self._cache.get(key)
        if pixmap is not None:
            self._cache.move_to_end(key)
        return pixmap

    def request(self, path: str, size: int, callback: Callable[[QPixmap | None], None]) -> None:
        """请求缩略图；已缓存或已确认为空时立即回调。"""
        key = (path, size)
        pixmap = self.cached(path, size)
        if pixmap is not None:
            callback(pixmap)
            return
        if key in self._missing:
            callback(None)
            return
        waiting = self._pending.setdefault(key, [])
        waiting.append(callback)
        if len(waiting) == 1:
            self._pool.start(_CoverTask(path, size, self._signals))

    def clear(self) -> None:
        self._cache.clear()
        self._missing.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def _on_loaded(self, path: str, size: int, image: QImage) -> None:
        key = (path, size)
        callbacks = self._pending.pop(key, [])
        pixmap: QPixmap | None = None
        if image.isNull():
            self._missing.add(key)
        else:
            pixmap = QPixmap.fromImage(image)
            self._cache[key] = pixmap
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)
        for callback in callbacks:
            try:
                callback(pixmap)
            except RuntimeError:
                continue


_loader: CoverLoader | None = None


def cover_loader() -> CoverLoader:
    """进程内共享的封面加载器（首次调用时创建）。"""
    global _loader
    if _loader is None:
        _loader = CoverLoader()
    return _loader


def shutdown_cover_loader() -> None:
    """退出前停止后台加载（加载器尚未创建时什么都不做）。"""
    if _loader is not None:
        _loader.shutdown()


__all__ = ["CoverLoader", "cover_loader", "shutdown_cover_loader"]
