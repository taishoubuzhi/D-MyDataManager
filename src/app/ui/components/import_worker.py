"""批量导入的后台任务：驱动持久化的导入清单，支持暂停 / 继续 / 取消。

和旧版的区别：旧的 `ImportWorker` 把「导入哪些文件」「做到哪了」都放在线程内存里，
整批跑完才 `session.commit()` 一次；进程一挂，清单丢了、进度丢了、已复制进库的文件
成了管理页看不见的孤儿。

现在清单先落盘（`services/import_job.py` 的 `ImportJob`），每个文件单独提交，
于是暂停 = 线程在文件之间停下、清单留着；继续 = 再开一轮读同一份清单；
取消 = 剩余项全部结清、清单按规则删除。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from loguru import logger

from ...core.journals import ITEM_FAILED
from ...db import database
from ...services.import_job import (
    STATUS_ADDED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    ImportControl,
    ImportJob,
    ImportPlanItem,
    ImportProgress,
    create_job,
    import_store,
    resume_job,
)
from ...services.import_service import ImportService
from ...services.library_service import LibraryService


class ImportWorker(QThread):
    """在后台线程里跑一轮批量导入（可被暂停、取消）。"""

    #: (已处理项序号, 总项数, 当前文件名)
    progressed = pyqtSignal(int, int, str)
    #: (文件, 状态, 说明)，状态取 `import_job.STATUS_*`
    evented = pyqtSignal(str, str, str)
    #: 清单已建好 / 已接回，页面据此拿到可以逐项取消的清单 id
    jobReady = pyqtSignal(str)
    #: 这一轮结束时的清单状态（running / paused / cancelled / done）
    stateChanged = pyqtSignal(str)
    finished_job = pyqtSignal(dict)

    def __init__(
        self,
        items: list[ImportPlanItem] | None = None,
        *,
        options: dict | None = None,
        journal_id: str = "",
        adopt: bool = False,
        title: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items or [])
        self._options = dict(options or {})
        self._journal_id = str(journal_id or "")
        self._adopt = bool(adopt)
        self._title = title
        #: 页面直接持有它来喊「暂停 / 取消」（线程安全）
        self.control = ImportControl()
        #: 清单 id（新建或接回的），`jobReady` 之后有效
        self.journal_id = self._journal_id

    # ---------------------------------------------------------------- 控制
    def request_pause(self) -> None:
        self.control.request_pause()

    def request_cancel(self) -> None:
        self.control.request_cancel()

    def cancel_item(self, key: str) -> None:
        self.control.cancel_item(key)

    # ---------------------------------------------------------------- 执行
    def run(self) -> None:  # noqa: D102 —— QThread 约定
        session = database.new_session()
        payload = {
            "ok": 0, "skipped": 0, "failed": [], "cancelled": 0, "remaining": 0,
            "category": "", "total": 0, "error": "", "stopped": "", "journal": "",
        }
        job: ImportJob | None = None
        try:
            store = import_store()
            if self._journal_id:
                job = resume_job(store.load(self._journal_id), store=store)
            else:
                job = create_job(self._items, options=self._options, title=self._title, store=store)
            self.journal_id = job.id
            payload["journal"] = job.id
            self.jobReady.emit(job.id)

            service = ImportService(session, library=LibraryService(session).ensure_default())
            result = job.run(service, on_event=self._hook, control=self.control, adopt=self._adopt)
            payload.update(
                ok=result.added,
                skipped=result.skipped,
                failed=[
                    (str(item.get("source") or ""), str(item.get("detail") or ""))
                    for item in job.journal.items
                    if str(item.get("status")) == ITEM_FAILED
                ],
                cancelled=result.cancelled,
                remaining=result.remaining,
                category=result.category,
                stopped=result.stopped,
                total=result.total,
            )
        except Exception as exc:  # noqa: BLE001 —— 整轮失败也要把原因带回界面
            session.rollback()
            logger.exception("批量导入失败")
            payload["error"] = str(exc)
        finally:
            session.close()
        state = str(payload.get("stopped") or "")
        self.stateChanged.emit(state or ("error" if payload.get("error") else "done"))
        self.finished_job.emit(payload)

    # ---------------------------------------------------------------- 回调
    def _hook(self, event: ImportProgress) -> None:
        """清单的进度回调 → Qt 信号（工作线程里发，槽在主线程执行）。"""
        status = event.status
        if status in (STATUS_ADDED, STATUS_SKIPPED, STATUS_FAILED, STATUS_CANCELLED):
            self.evented.emit(event.source, status, event.detail)
        self.progressed.emit(event.index, event.total, event.source)


__all__ = ["ImportWorker"]
