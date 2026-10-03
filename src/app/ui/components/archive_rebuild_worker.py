"""「重新加载存档文件」的后台线程：把重建进度转成 Qt 信号，供存档页订阅。"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from loguru import logger

from ...db import database
from ...services import ArchiveService


class ArchiveRebuildWorker(QThread):
    """后台重建物理层（§5.8）：独立会话 + 进度信号 + 可取消。"""

    progressed = pyqtSignal(str, int, int, str)
    finished_job = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消：服务层在阶段边界检查，已还原的文件保留。"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """供服务层的 `cancel` 回调调用。"""
        return self._cancelled

    def run(self) -> None:  # noqa: D102
        session = database.new_session()
        try:
            report = ArchiveService(session).rebuild_storage(
                on_event=self._on_event, cancel=self.is_cancelled
            )
            session.commit()
            payload = {
                "ok": True,
                "error": "",
                "summary": report.summary(),
                "reused": report.reused,
                "diverged": report.diverged,
                "missing": report.missing,
                "failed": report.failed,
            }
        except Exception as exc:  # noqa: BLE001 - 失败原因交给页面提示
            session.rollback()
            logger.warning("重新加载存档文件失败：{}", exc)
            payload = {
                "ok": False,
                "error": str(exc),
                "summary": "",
                "reused": 0,
                "diverged": 0,
                "missing": 0,
                "failed": 0,
            }
        finally:
            session.close()
        self.finished_job.emit(payload)

    def _on_event(self, phase: str, index: int, total: int, detail: str) -> None:
        self.progressed.emit(str(phase), int(index), int(total), str(detail))


__all__ = ["ArchiveRebuildWorker"]
