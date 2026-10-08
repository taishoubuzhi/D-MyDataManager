"""批量导入的持久化任务：清单驱动，可暂停 / 继续 / 取消，崩溃后能接着做。

为什么需要这一层
----------------
原先的批量导入把「待导入清单」和进度都放在内存里，而且直到整批跑完才
`session.commit()`：进程异常退出后，已经 `shutil.copy2` 进库的文件没有对应的数据项，
管理页看不到（孤儿文件），还没轮到的文件连「要导入什么」都无从得知。

这里把待导入清单落成一份临时清单（`kind = "journal"`，见
`docs/MANIFEST_PROTOCOL.md` §2.1），并且**每处理完一个文件就提交一次**，于是：

* 已完成的项立刻在管理页可见（不再需要等整批结束）；
* 未完成的项留在清单里，重启后可以继续、可以逐项取消；
* 崩溃瞬间那一个文件（已复制进库、还没提交）由 `adopt_orphans()` 精确收养。

提交顺序与崩溃窗口
------------------
每个文件：`pending` →（内存）`active` → `import_file()` → `session.commit()` → `done` /
`skipped` / `failed`。崩溃可能落在任何一步，恢复逻辑对每个未结清的项按「已登记？→ 收养
孤儿？→ 重做」三步走，天然幂等：

* 提交前崩：文件可能已复制进库但没有数据项 → 第 ② 步收养；
* 提交后、清单落盘前崩：数据项已存在 → 第 ① 步按源路径认得它；
* 完全没轮到：第 ③ 步交给 `import_file()` 重做（顺带按当前重复策略重新判定）。

暂停 / 取消只在**文件之间**生效：`import_file()` 内部的复制不可中断，这是有意为之——
中断一次复制只会多留一个孤儿文件，不如让它跑完。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from ..core.journals import (
    ITEM_ACTIVE,
    ITEM_CANCELLED,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_OPEN_STATES,
    ITEM_PENDING,
    ITEM_SKIPPED,
    STATE_FINISHED,
    STATE_PAUSED,
    STATE_RUNNING,
    Journal,
    JournalStore,
)
from .blob_store import sha256_of
from .import_service import ImportService, _ignored, sanitize_subdir
from .user_service import UserService

#: 临时清单的类别（决定 `.configs/journals/import/` 这个目录）
IMPORT_KIND = "import"
#: 导入的清单会随文件数线性增长，逐项落盘会退化成 O(n²) 全量重写；窗口开大一点，
#: 崩溃最多丢掉最后一个窗口内的进度，而恢复本来就要逐项重新核对。
IMPORT_FLUSH_EVERY = 200
IMPORT_FLUSH_INTERVAL = 2.0

#: 单项事件的状态
STATUS_ACTIVE = "active"
STATUS_ADDED = "added"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

#: 停止原因
STOP_DONE = "done"
STOP_PAUSED = "paused"
STOP_CANCELLED = "cancelled"


@dataclass(frozen=True)
class ImportPlanItem:
    """待导入快照里的一项。

    `key` 是零填充序号（`000000`…）：即使将来某处按字典序排了一遍，顺序也不会乱。
    """

    key: str
    source: str
    subdir: str = ""


@dataclass(frozen=True)
class ImportProgress:
    """一项的进度事件（页面据此刷新逐项结果表）。"""

    index: int
    total: int
    key: str
    source: str
    status: str
    detail: str = ""
    item_id: int | None = None


@dataclass
class ImportRunResult:
    """一次 `run()` 的统计。"""

    total: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0
    adopted: int = 0
    remaining: int = 0
    stopped: str = STOP_DONE
    category: str = ""
    error: str = ""

    def summary(self) -> str:
        parts = [f"成功 {self.added}", f"跳过 {self.skipped}"]
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.cancelled:
            parts.append(f"取消 {self.cancelled}")
        if self.remaining:
            parts.append(f"剩余 {self.remaining}")
        return "，".join(parts)


def plan_files(sources: Iterable[str | Path]) -> list[ImportPlanItem]:
    """把一批文件路径做成待导入快照（保持给定顺序）。"""
    return [
        ImportPlanItem(key=f"{index:06d}", source=str(Path(source)))
        for index, source in enumerate(sources)
    ]


def plan_folder(directory: str | Path) -> list[ImportPlanItem]:
    """把整个文件夹递归做成待导入快照（跳过噪声目录与隐藏项，规则同导入服务）。"""
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"文件夹不存在：{root}")
    items: list[ImportPlanItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _ignored(path, root):
            continue
        relative = path.parent.relative_to(root).as_posix()
        items.append(
            ImportPlanItem(
                key=f"{len(items):06d}",
                source=str(path),
                subdir="" if relative == "." else relative,
            )
        )
    return items


class ImportControl:
    """后台线程与界面之间的停止信号（线程安全，只在文件之间生效）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pause = False
        self._cancel = False
        self._cancelled_items: set[str] = set()

    def request_pause(self) -> None:
        with self._lock:
            self._pause = True

    def request_resume(self) -> None:
        with self._lock:
            self._pause = False

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel = True
            self._pause = False

    def cancel_item(self, key: str) -> None:
        with self._lock:
            self._cancelled_items.add(str(key))

    def uncancel_item(self, key: str) -> None:
        with self._lock:
            self._cancelled_items.discard(str(key))

    def should_pause(self) -> bool:
        with self._lock:
            return self._pause and not self._cancel

    def should_cancel(self) -> bool:
        with self._lock:
            return self._cancel

    def cancelled_items(self) -> set[str]:
        with self._lock:
            return set(self._cancelled_items)

    def is_cancelled(self, key: str) -> bool:
        with self._lock:
            return str(key) in self._cancelled_items

    def reset(self) -> None:
        with self._lock:
            self._pause = False
            self._cancel = False
            self._cancelled_items.clear()


class ImportJob:
    """一份导入任务的清单 + 执行逻辑。"""

    def __init__(self, store: JournalStore, journal: Journal, *, control: ImportControl | None = None) -> None:
        self.store = store
        self.journal = journal
        self.control = control if control is not None else ImportControl()

    # ------------------------------------------------------------------ 读
    @property
    def id(self) -> str:
        return self.journal.id

    @property
    def state(self) -> str:
        return self.journal.state

    @property
    def total(self) -> int:
        return len(self.journal.items)

    @property
    def options(self) -> dict[str, Any]:
        return self.journal.options

    def counts(self) -> dict[str, int]:
        return self.journal.counts()

    def pending_keys(self) -> list[str]:
        """还没到终态的项（界面据此允许「取消这一项」）。"""
        return [str(item.get("key")) for item in self.journal.open_items()]

    def source_of(self, key: str) -> str:
        item = self.journal.find(key)
        return str(item.get("source") or "") if item else ""

    def is_open(self) -> bool:
        return not self.journal.terminal()

    def key_for_source(self, source: str) -> str:
        """按源路径反查项 key（结果表里的一行 → 清单项）。"""
        target = str(source)
        for item in self.journal.items:
            if str(item.get("source")) == target:
                return str(item.get("key"))
        return ""

    # ---------------------------------------------------------------- 控制
    def pause(self) -> None:
        """请求暂停：当前文件跑完就停在原地，清单保留。"""
        self.control.request_pause()
        self.journal.state = STATE_PAUSED
        self.store.flush(self.journal)

    def resume(self) -> None:
        """请求继续（界面重新开一轮 `run()` 之前调用）。"""
        self.control.request_resume()
        self.journal.state = STATE_RUNNING
        self.store.flush(self.journal)

    def cancel(self) -> None:
        """请求取消整批：剩余未处理的项都会被标成「已取消」。"""
        self.control.request_cancel()
        self.store.flush(self.journal)

    def cancel_item(self, key: str) -> bool:
        """取消某一项还没开始的导入请求。

        同时写进清单（所以暂停中 / 重启后依然有效）并知会正在跑的那一轮线程：
        清单里的状态让它变成终态，线程那边则在文件之间跳过它。
        """
        item = self.journal.find(key)
        if item is None or str(item.get("status")) not in ITEM_OPEN_STATES:
            return False
        self.control.cancel_item(key)
        self._mark(item, ITEM_CANCELLED, detail="用户取消")
        self.store.flush(self.journal)
        return True

    def uncancel_item(self, key: str) -> bool:
        """把「已取消」的项放回待导入（用户反悔）。"""
        item = self.journal.find(key)
        if item is None or str(item.get("status")) != ITEM_CANCELLED:
            return False
        self.control.uncancel_item(key)
        self._mark(item, ITEM_PENDING, detail="")
        self.store.flush(self.journal)
        return True

    def abandon(self) -> None:
        """用户放弃这次导入：未结清的项全部按「已取消」结清，然后删清单。"""
        for item in self.journal.items:
            if str(item.get("status")) in ITEM_OPEN_STATES:
                self._mark(item, ITEM_CANCELLED, detail="用户放弃")
        self.journal.state = STATE_FINISHED
        self.store.flush(self.journal)
        self.store.settle(self.journal)

    # ------------------------------------------------------------ 崩溃恢复
    def adopt_orphans(self, service: ImportService, *, on_event: Callable[[ImportProgress], None] | None = None) -> int:
        """核对上次没做完的项：已登记的认回来、库里的孤儿文件收养，其余留给重做。

        返回「认领成功、不必重做」的项数。
        """
        adopted = 0
        total = self.total
        for ordinal, item in enumerate(list(self.journal.items), start=1):
            if str(item.get("status")) not in ITEM_OPEN_STATES:
                continue
            source = Path(str(item.get("source") or ""))
            if not source.is_file():
                self._mark(item, ITEM_FAILED, detail="源文件不存在")
                self._emit(on_event, ordinal, total, item, STATUS_FAILED, "源文件不存在")
                continue
            try:
                checksum = sha256_of(source)
            except OSError as exc:
                logger.warning("核对导入项失败（读不了源文件）：{}：{}", source, exc)
                continue

            existing = self._registered_for(service, checksum, source)
            if existing is not None:
                self._mark(item, ITEM_DONE, item_id=existing.id, rel_path=existing.file_path, detail="已入库")
                adopted += 1
                self._emit(on_event, ordinal, total, item, STATUS_ADDED, "已入库")
                continue

            found = self._find_orphan(service, item, source, checksum)
            if found is not None:
                rel_path, path = found
                if self._adopt(service, item, rel_path=rel_path, path=path):
                    adopted += 1
                    self._emit(on_event, ordinal, total, item, STATUS_ADDED, "收养孤儿文件")
                    continue
            # 没有可认领的痕迹 → 留给 run() 重做
            logger.info("导入项需要重做：{}", source)

        self.store.flush(self.journal)
        return adopted

    def _registered_for(self, service: ImportService, checksum: str, source: Path):
        """库内已经登记、且来源就是同一个文件的项。"""
        for data_item in service.items.by_checksum(checksum):
            if str(data_item.source_path or "") == str(source):
                return data_item
        return None

    def _find_orphan(self, service: ImportService, item: dict, source: Path, checksum: str):
        """在「这一项本该落到的目录」里找没登记、内容和源文件一致的文件。

        只看目标分类目录一层（不递归、不全库扫），因为导入只往那里写；拿不到就返回 None。
        """
        library = service.library
        options = self.journal.options
        user_id = _owner_id(service, options.get("user_id"))
        # 和 `import_file` 用同一条解析：没指定分类时实际落到「未分类」目录，
        # 直接拿 None 去拼目录会指向用户名文件夹本身，那就永远找不到孤儿。
        category_id = service.category_for(options.get("category_id"), options.get("user_id"))
        is_hidden = bool(options.get("is_hidden"))
        subdir = sanitize_subdir(str(item.get("subdir") or ""), is_hidden)
        base = service.libraries.directory_for(library, category_id, user_id)
        if subdir:
            base = base.joinpath(*(part for part in Path(subdir).parts if part not in ("", ".", "..")))
        if not base.is_dir():
            return None
        try:
            size = source.stat().st_size
            entries = sorted(base.iterdir())
        except OSError as exc:
            logger.warning("核对孤儿文件失败（读不了目录）：{}：{}", base, exc)
            return None
        known = service.items.library_paths(library.id)
        root = Path(library.path)
        for candidate in entries:
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size != size:
                    continue
            except OSError:
                continue
            rel_path = candidate.relative_to(root).as_posix()
            if rel_path in known:
                continue
            try:
                if sha256_of(candidate) != checksum:
                    continue
            except OSError:
                continue
            return rel_path, candidate
        return None

    def _adopt(self, service: ImportService, item: dict, *, rel_path: str, path: Path) -> bool:
        """把孤儿文件登记成数据项（不复制、不移动文件）。"""
        options = self.journal.options
        try:
            data_item = service.register_file(
                path,
                library=service.library,
                rel_path=rel_path,
                category_id=service.category_for(options.get("category_id"), options.get("user_id")),
                user_id=options.get("user_id"),
                is_hidden=bool(options.get("is_hidden")),
            )
            service.session.commit()
        except Exception as exc:  # noqa: BLE001 —— 收养失败不该拖垮整轮恢复
            service.session.rollback()
            logger.warning("收养孤儿文件失败：{}：{}", path, exc)
            return False
        item_id = data_item.id
        self._mark(item, ITEM_DONE, item_id=item_id, rel_path=rel_path, detail="收养孤儿文件")
        return True

    # ---------------------------------------------------------------- 执行
    def run(
        self,
        service: ImportService,
        *,
        on_event: Callable[[ImportProgress], None] | None = None,
        control: ImportControl | None = None,
        adopt: bool = False,
    ) -> ImportRunResult:
        """把还没做完的项跑完；中途停下来的结果都写进清单。

        `control` 由调用方给（界面要在另一条线程里喊暂停 / 取消）；不传就用 job 自己的。
        `adopt=True` 表示这是「崩溃恢复后的继续」：先收养孤儿/认回已登记的项，再往下跑。
        """
        if control is not None:
            self.control = control
        result = ImportRunResult(total=self.total)
        self._ensure_category(service)
        result.category = str(self.journal.options.get("category_name") or "")
        self.journal.state = STATE_RUNNING
        self.store.flush(self.journal)

        if adopt:
            result.adopted = self.adopt_orphans(service, on_event=on_event)

        total = self.total
        for ordinal, item in enumerate(list(self.journal.items), start=1):
            status = str(item.get("status") or ITEM_PENDING)
            if status not in ITEM_OPEN_STATES:
                _tally(result, status)
                continue
            key = str(item.get("key"))

            if self.control.is_cancelled(key):
                self._mark(item, ITEM_CANCELLED, detail="用户取消")
                result.cancelled += 1
                self._emit(on_event, ordinal, total, item, STATUS_CANCELLED, "用户取消")
                continue

            if self.control.should_cancel():
                self._cancel_rest(result, on_event=on_event, ordinal=ordinal, total=total)
                break

            if self.control.should_pause():
                break

            self._process_one(service, item, ordinal=ordinal, total=total, on_event=on_event, result=result)

        result.remaining = len(self.journal.open_items())
        if result.remaining == 0:
            self.journal.state = STATE_FINISHED
            self.store.flush(self.journal)
            # 「所有待导入项都到了终态（导入完或已取消）才删清单」
            self.store.settle(self.journal)
            if result.stopped != STOP_CANCELLED:
                result.stopped = STOP_DONE
        elif result.stopped == STOP_CANCELLED:
            self.store.flush(self.journal)
        else:
            self.journal.state = STATE_PAUSED if self.control.should_pause() else STATE_RUNNING
            self.store.flush(self.journal)
            result.stopped = STOP_PAUSED if self.control.should_pause() else STOP_DONE
        return result

    # ---------------------------------------------------------------- 内部
    def _ensure_category(self, service: ImportService) -> None:
        """文件夹导入：清单里只记了文件夹名，分类等真正开跑时再建。

        放在这里而不是建清单那一刻，是因为「建分类」要动数据库；清单落盘不该依赖它，
        而且崩溃恢复时同一份清单还能把这个分类补回来（靠 `category_id` 是否已记下来判断）。
        """
        options = self.journal.options
        name = str(options.get("name") or "")
        if not name or options.get("category_id") is not None:
            return
        try:
            category = service.ensure_category(name, user_id=options.get("user_id"))
            service.session.commit()
        except Exception as exc:  # noqa: BLE001 —— 建不了分类也要能继续导入
            service.session.rollback()
            logger.warning("建立导入分类失败：{}：{}", name, exc)
            return
        options["category_id"] = category.id
        options["category_name"] = category.name
        self.store.flush(self.journal)

    def _process_one(
        self,
        service: ImportService,
        item: dict,
        *,
        ordinal: int,
        total: int,
        on_event: Callable[[ImportProgress], None] | None,
        result: ImportRunResult,
    ) -> None:
        source = str(item.get("source") or "")
        self._mark(item, ITEM_ACTIVE, detail="")
        self._emit(on_event, ordinal, total, item, STATUS_ACTIVE, "")
        options = self._item_options(item)
        try:
            if not Path(source).is_file():
                raise FileNotFoundError(f"文件不存在：{source}")
            data_item = service.import_file(source, **options)
            item_id = data_item.id if data_item is not None else None
            rel_path = data_item.file_path if data_item is not None else ""
            # 逐项提交：这一项立刻在管理页可见，崩溃也只影响当前这一项
            service.session.commit()
        except Exception as exc:  # noqa: BLE001 —— 单项失败不影响整批
            service.session.rollback()
            logger.exception("导入失败：{}", source)
            self._mark(item, ITEM_FAILED, detail=str(exc))
            result.failed += 1
            self._emit(on_event, ordinal, total, item, STATUS_FAILED, str(exc))
        else:
            if data_item is None:
                self._mark(item, ITEM_SKIPPED, detail="内容重复")
                result.skipped += 1
                self._emit(on_event, ordinal, total, item, STATUS_SKIPPED, "内容重复")
            else:
                self._mark(item, ITEM_DONE, item_id=item_id, rel_path=rel_path, detail="")
                result.added += 1
                self._emit(on_event, ordinal, total, item, STATUS_ADDED, rel_path or "")
        self.store.save(self.journal)

    def _cancel_rest(
        self,
        result: ImportRunResult,
        *,
        on_event: Callable[[ImportProgress], None] | None,
        ordinal: int,
        total: int,
    ) -> None:
        for index, item in enumerate(list(self.journal.items), start=1):
            if str(item.get("status")) not in ITEM_OPEN_STATES:
                continue
            self._mark(item, ITEM_CANCELLED, detail="用户取消")
            result.cancelled += 1
            self._emit(on_event, index if index >= ordinal else ordinal, total, item, STATUS_CANCELLED, "用户取消")
        result.stopped = STOP_CANCELLED

    def _item_options(self, item: dict) -> dict[str, Any]:
        options = self.journal.options
        return {
            "user_id": options.get("user_id"),
            "category_id": options.get("category_id"),
            "keywords": list(options.get("keywords") or []),
            "tags": list(options.get("tags") or []),
            "is_hidden": bool(options.get("is_hidden")),
            "subdir": str(item.get("subdir") or ""),
        }

    def _mark(self, item: dict, status: str, *, detail: str | None = None, **fields: Any) -> None:
        item["status"] = status
        if detail is not None:
            item["detail"] = detail
        item.update(fields)

    def _emit(
        self,
        on_event: Callable[[ImportProgress], None] | None,
        ordinal: int,
        total: int,
        item: dict,
        status: str,
        detail: str,
    ) -> None:
        if on_event is None:
            return
        event = ImportProgress(
            index=ordinal,
            total=total,
            key=str(item.get("key") or ""),
            source=str(item.get("source") or ""),
            status=status,
            detail=detail,
            item_id=item.get("item_id"),
        )
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 —— 页面回调出错不该影响导入
            logger.exception("导入进度回调出错")


def _owner_id(service: ImportService, value: Any) -> int | None:
    """把清单里记的归属解析成用户 id；与 `ImportService._resolve_user_id` 语义一致。"""
    if value:
        return int(value)
    current = UserService(service.session).current_id()
    return int(current) if current else None


def _tally(result: ImportRunResult, status: str) -> None:
    if status == ITEM_DONE:
        result.added += 1
    elif status == ITEM_SKIPPED:
        result.skipped += 1
    elif status == ITEM_FAILED:
        result.failed += 1
    elif status == ITEM_CANCELLED:
        result.cancelled += 1


# -------------------------------------------------------------------- 入口
def import_store(
    *,
    flush_every: int = IMPORT_FLUSH_EVERY,
    flush_interval: float = IMPORT_FLUSH_INTERVAL,
) -> JournalStore:
    """导入用的临时清单仓库（窗口开大，避免逐项全量重写）。"""
    return JournalStore(IMPORT_KIND, flush_every=flush_every, flush_interval=flush_interval)


def open_journals(store: JournalStore | None = None) -> list[Journal]:
    """启动扫描：把还留着的导入清单重新登记，返回还没做完的那些。

    顺手清掉「待导入项已经为空」的残留清单（正常结束的清单会自己删，这里是兜底）。
    """
    store = store or import_store()
    journals = [journal for journal in store.scan() if journal.journal_kind in ("", IMPORT_KIND)]
    store.clear_settled()
    return [journal for journal in journals if not journal.terminal()]


def create_job(
    items: Iterable[ImportPlanItem],
    *,
    options: dict[str, Any] | None = None,
    title: str = "",
    description: str = "",
    store: JournalStore | None = None,
) -> ImportJob:
    """新建一份导入任务（清单立刻落盘，这样崩溃才有得恢复）。"""
    target = store or import_store()
    journal = target.create(
        options=dict(options or {}),
        items=[{"key": item.key, "source": item.source, "subdir": item.subdir} for item in items],
        title=title or "批量导入",
        description=description,
    )
    return ImportJob(target, journal)


def resume_job(journal: Journal, *, store: JournalStore | None = None) -> ImportJob:
    """把盘上的一份清单接回来继续做。"""
    target = store or import_store()
    return ImportJob(target, journal)


__all__ = [
    "IMPORT_FLUSH_EVERY",
    "IMPORT_FLUSH_INTERVAL",
    "IMPORT_KIND",
    "ImportControl",
    "ImportJob",
    "ImportPlanItem",
    "ImportProgress",
    "ImportRunResult",
    "STATUS_ACTIVE",
    "STATUS_ADDED",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STOP_CANCELLED",
    "STOP_DONE",
    "STOP_PAUSED",
    "create_job",
    "import_store",
    "open_journals",
    "plan_files",
    "plan_folder",
    "resume_job",
]
