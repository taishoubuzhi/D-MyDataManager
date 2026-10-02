"""导入任务的后台线程：把服务层的进度回调转成 Qt 信号，供导入页订阅。"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from loguru import logger

from ...db import database
from ...services import ImportService, LibraryService


class ImportWorker(QThread):
    """后台执行批量导入：把服务层的进度回调转成 Qt 信号，避免界面卡死。"""

    progressed = pyqtSignal(int, int, str)
    evented = pyqtSignal(str, str, str)
    finished_job = pyqtSignal(dict)

    def __init__(self, kind: str, sources: list[str], options: dict) -> None:
        super().__init__()
        self._kind = kind
        self._sources = sources
        self._options = options

    def run(self) -> None:  # noqa: D102
        session = database.new_session()
        payload = {"ok": 0, "skipped": 0, "failed": [], "category": "", "total": 0, "error": ""}
        try:
            service = ImportService(session, library=LibraryService(session).ensure_default())

            def hook(event) -> None:
                self.evented.emit(event.source, event.status, event.detail)
                self.progressed.emit(event.index, event.total, event.source)

            if self._kind == "folder":
                result = service.import_folder(self._sources[0], on_event=hook, **self._options)
            else:
                result = service.import_files(self._sources, on_event=hook, **self._options)
            session.commit()
            payload.update(
                ok=result.added_count,
                skipped=len(result.skipped),
                failed=result.failed,
                category=result.category_name,
                total=result.total,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("批量导入失败")
            payload["error"] = str(exc)
        finally:
            session.close()
        self.finished_job.emit(payload)
