"""临时清单（journal）机制：可暂停、可续、崩溃可恢复的长任务。

它解决什么问题
--------------
「把一批待处理项先落盘，中途允许暂停 / 继续 / 取消，进程异常退出后还能接着做」这类
需求反复出现（批量导入、下载任务索引）。它复用 `app.core.manifest` 的存储与格式，
但语义和普通清单不同：

* 普通清单是「固定、但可能需要被外部替换的数据」（见 `docs/MANIFEST_PROTOCOL.md` §1），
  长期存在、面向用户；
* 临时清单是**运行期产生的易失状态**：所有待处理项都到达终态（做完或被取消）或者用户
  主动放弃时就会被删除；崩溃后留下来的那一份要在下次启动被重新登记、重新呈现给用户。

所以这一层只补三件普通清单不做的事：

1. **启动扫描 + 重新登记**（`JournalStore.scan`）。`ManifestRegistry` 只活在内存里，
   进程一退登记就没；残留的 JSON 不重新 `register` 就没人认识，`manifest_kit.load()`
   会直接报「没有登记名为 … 的清单」。没有这一步，崩溃后的清单就是一份孤儿文件。
2. **写入节流**（`JournalStore.save`）。逐项写整个清单是 O(n²)（n 项要重写 n 次全量
   JSON）。默认按 `FLUSH_INTERVAL` 秒 / `FLUSH_EVERY` 条合批，只在状态跃迁、显式
   `flush()`、`remove()` 时强制落盘。崩溃最多丢掉最后一个合批窗口内的进度，而恢复逻辑
   本来就要「重新核对」（见 `Journal.recover`），所以这个取舍是安全的。
3. **终态清理**（`Journal.terminal` / `JournalStore.clear_settled`）——「所有待处理项为
   空（要么已经做完，要么已经取消）时才删清单」这条规则。

单项状态
--------
`pending`（待处理）→ `active`（进行中）→ `done` / `skipped` / `failed` / `cancelled`
（终态）。恢复时进行中的项由 `Journal.recover()` 退回 `pending`：异常退出时它到底做完
没有是不知道的，交给调用方按自己的语义重新核对（导入侧就是查 checksum、收养孤儿文件）。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .manifest.kit import MANIFEST_VERSION, ManifestError, manifest_kit, read_json
from .manifest.registry import ManifestEntry
from .runtime import paths

_logger = logging.getLogger(__name__)

#: 临时清单的清单类型（见 `docs/MANIFEST_PROTOCOL.md` §2）
JOURNAL_KIND = "journal"
#: 临时清单的统一归属，便于 `manifest_kit.describe()` 与人工排查
JOURNAL_OWNER = "core"
#: 清单 id 前缀：`core.journal.<类别>.<批次>`
JOURNAL_PREFIX = "core.journal"
#: 存放目录（相对 `.configs/`）：`.configs/journals/<类别>/`
JOURNAL_DIR_NAME = "journals"

#: 清单整体状态
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_FINISHED = "finished"
STATE_ABANDONED = "abandoned"
JOURNAL_STATES = (STATE_RUNNING, STATE_PAUSED, STATE_FINISHED, STATE_ABANDONED)

#: 单项状态
ITEM_PENDING = "pending"
ITEM_ACTIVE = "active"
ITEM_DONE = "done"
ITEM_SKIPPED = "skipped"
ITEM_FAILED = "failed"
ITEM_CANCELLED = "cancelled"
#: 还没到终态的单项状态（决定清单能不能删）
ITEM_OPEN_STATES = (ITEM_PENDING, ITEM_ACTIVE)

#: 合批落盘的阈值：距上次落盘超过这么多秒，或累计这么多条改动，就强制写一次
FLUSH_INTERVAL = 1.0
FLUSH_EVERY = 20

_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    """把任意字符串压成清单 id 允许的一段（`[a-z][a-z0-9_]*`）。"""
    text = _SLUG_PATTERN.sub("_", str(value).strip().lower()).strip("_")
    if not text:
        text = "journal"
    if not ("a" <= text[0] <= "z"):
        text = f"b{text}"
    return text


def new_batch() -> str:
    """新的批次标识（时间 + 随机后缀，同一秒也不会重名）。"""
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def journal_id(kind: str, batch: str) -> str:
    """临时清单的 id：`core.journal.<类别>.<批次>`。"""
    return f"{JOURNAL_PREFIX}.{_slug(kind)}.{_slug(batch)}"


@dataclass
class Journal:
    """一份临时清单的内存视图（`items` 的顺序就是处理顺序）。"""

    id: str
    path: Path
    journal_kind: str
    batch: str
    state: str = STATE_RUNNING
    title: str = ""
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)

    # ---------------------------------------------------------------- 读
    def find(self, key: str) -> dict[str, Any] | None:
        for item in self.items:
            if str(item.get("key")) == str(key):
                return item
        return None

    def status_of(self, key: str) -> str:
        item = self.find(key)
        return str(item.get("status") or "") if item else ""

    def counts(self) -> dict[str, int]:
        """各单项状态各有多少（页面摘要用）。"""
        result: dict[str, int] = {}
        for item in self.items:
            status = str(item.get("status") or ITEM_PENDING)
            result[status] = result.get(status, 0) + 1
        return result

    def open_items(self) -> list[dict[str, Any]]:
        """还没到终态的项。

        没写 `status` 的项按待处理算——宁可多留一份清单，也不能把还有待处理项的清单
        当成「已完成」删掉。
        """
        return [
            item
            for item in self.items
            if str(item.get("status") or ITEM_PENDING) in ITEM_OPEN_STATES
        ]

    def terminal(self) -> bool:
        """待处理项为空——按规则此时该删清单。"""
        return not self.open_items()

    # ---------------------------------------------------------------- 写
    def add_item(self, key: str, **fields: Any) -> dict[str, Any]:
        item: dict[str, Any] = {"key": str(key), "status": ITEM_PENDING, **fields}
        self.items.append(item)
        return item

    def update_item(self, key: str, **fields: Any) -> dict[str, Any] | None:
        item = self.find(key)
        if item is None:
            return None
        item.update(fields)
        return item

    def set_status(self, key: str, status: str, *, detail: str | None = None, **fields: Any) -> dict[str, Any] | None:
        patch: dict[str, Any] = {"status": status, **fields}
        if detail is not None:
            patch["detail"] = detail
        return self.update_item(key, **patch)

    def recover(self) -> list[dict[str, Any]]:
        """把「进行中」的项退回待处理，返回被退回的项。

        异常退出时正在处理的那一项结果未知（文件可能已经复制进库却没登记），退回
        `pending` 让调用方重新核对，比直接当成失败或当成成功都安全。
        """
        touched: list[dict[str, Any]] = []
        for item in self.items:
            if item.get("status") == ITEM_ACTIVE:
                item["status"] = ITEM_PENDING
                item["detail"] = ""
                touched.append(item)
        return touched

    # ------------------------------------------------------------ 序列化
    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "manifest": MANIFEST_VERSION,
            "id": self.id,
            "version": "1",
            "kind": JOURNAL_KIND,
            "title": self.title or f"临时清单 {self.id}",
            "state": self.state,
            "journal_kind": self.journal_kind,
            "batch": self.batch,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "options": dict(self.options),
            "items": [dict(item) for item in self.items],
        }
        if self.description:
            data["description"] = self.description
        return data

    @classmethod
    def from_payload(cls, path: Path | str, payload: Mapping[str, Any], *, manifest_id: str = "") -> Journal:
        target = Path(path)
        return cls(
            id=str(payload.get("id") or manifest_id),
            path=target,
            journal_kind=str(payload.get("journal_kind") or ""),
            batch=str(payload.get("batch") or target.stem),
            state=str(payload.get("state") or STATE_RUNNING),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            options=dict(payload.get("options") or {}),
            items=[dict(item) for item in (payload.get("items") or []) if isinstance(item, Mapping)],
        )


class JournalStore:
    """某一类临时清单的目录 + 登记表 + 落盘节流。

    每类（`kind`，如 `import`）一个实例，文件落在 `.configs/journals/<kind>/`。
    目录按**当前** `paths.CONFIG_DIR` 现算，所以测试重定向路径后无需重建实例。
    """

    def __init__(
        self,
        kind: str,
        *,
        root: str | Path | None = None,
        owner: str = JOURNAL_OWNER,
        flush_every: int | None = None,
        flush_interval: float | None = None,
    ) -> None:
        self._kind = _slug(kind)
        self._owner = owner
        self._root = Path(root) if root is not None else None
        # 节流参数：不传就每次按模块常量现算（调用方想调大窗口时再显式给值）
        self._flush_every = None if flush_every is None else max(1, int(flush_every))
        self._flush_interval = None if flush_interval is None else max(0.0, float(flush_interval))
        #: 已改但还没落盘的清单（id → Journal）
        self._dirty: dict[str, Journal] = {}
        self._last_flush: dict[str, float] = {}
        self._writes: dict[str, int] = {}

    # ------------------------------------------------------------ 目录
    @property
    def kind(self) -> str:
        return self._kind

    @property
    def root(self) -> Path:
        if self._root is not None:
            return self._root
        return paths.CONFIG_DIR / JOURNAL_DIR_NAME / self._kind

    def ensure_root(self) -> Path:
        paths.make_dir(self.root)
        return self.root

    # ------------------------------------------------------------ 登记
    def scan(self) -> list[Journal]:
        """扫描本类残留清单，重新登记进清单表并返回（启动时调用一次）。

        `ManifestRegistry` 只在内存里，进程重启后登记全丢；盘上留下的 JSON 必须在这里
        重新 `register`，`manifest_kit` 才认得它们。读不出来的文件只记日志、原样留着，
        不会自作主张删除——里面可能有用户想看的线索。
        """
        self.ensure_root()
        found: list[Journal] = []
        for path in sorted(self.root.glob("*.json")):
            journal = self._read_path(path)
            if journal is None:
                continue
            self._register(journal)
            found.append(journal)
        return found

    def _read_path(self, path: Path) -> Journal | None:
        """读一份残留清单并登记它。

        一定要先直接读盘、拿到文件里写的 `id`，再用那个 id 登记：光靠文件名推出来的
        id 只是兜底，按错的 id 登记会让真正的 id 依然「查无此清单」。
        """
        try:
            payload = read_json(path)
        except (ManifestError, OSError) as exc:
            _logger.warning("临时清单读不出来，跳过：%s：%s", path, exc)
            return None
        if not isinstance(payload, Mapping):
            _logger.warning("临时清单格式不对（顶层不是对象），跳过：%s", path)
            return None
        manifest_id = str(payload.get("id") or _path_id(path))
        self._register_entry(manifest_id, path)
        return Journal.from_payload(path, payload, manifest_id=manifest_id)

    def _register(self, journal: Journal) -> None:
        self._register_entry(journal.id, journal.path)

    def _register_entry(self, manifest_id: str, path: Path) -> None:
        if manifest_id in manifest_kit.ids():
            return
        entry = ManifestEntry(
            id=manifest_id,
            path=Path(path),
            kind=JOURNAL_KIND,
            owner=self._owner,
            description=f"临时清单（{self._kind}）",
        )
        try:
            manifest_kit.register(entry, source=__name__)
        except ValueError:  # 并发下另一个线程刚登记过
            _logger.debug("临时清单已登记：%s", manifest_id)

    # ------------------------------------------------------------ 创建
    def create(
        self,
        *,
        batch: str = "",
        options: Mapping[str, Any] | None = None,
        items: list[Mapping[str, Any]] | None = None,
        state: str = STATE_RUNNING,
        title: str = "",
        description: str = "",
    ) -> Journal:
        """新建一份临时清单并立刻落盘（第一次必须落盘，否则崩溃就无从恢复）。"""
        # 批次串必须过 `_slug`：`new_batch()` 以数字开头，直接当 id 尾段会违反 ID_PATTERN
        # （清单 id 每段必须以字母开头），`_slug` 会给它补一个 `b` 前缀。
        slug = _slug(batch) if batch else _slug(new_batch())
        manifest_id = f"{JOURNAL_PREFIX}.{self._kind}.{slug}"
        timestamp = _now()
        journal = Journal(
            id=manifest_id,
            path=self.ensure_root() / f"{slug}.json",
            journal_kind=self._kind,
            batch=slug,
            state=state,
            title=title,
            description=description,
            created_at=timestamp,
            updated_at=timestamp,
            options=dict(options or {}),
            # 单项至少要带上 `status`：漏了它就等于「待处理项为空」，清单会被误删
            items=[
                {"status": ITEM_PENDING, **dict(item), "key": str(item.get("key", ""))}
                for item in (items or [])
            ],
        )
        self.flush(journal)
        return journal

    def load(self, manifest_id: str) -> Journal:
        """按 id 读回一份临时清单。"""
        entry = manifest_kit.registry.entry(manifest_id)
        payload = manifest_kit.raw(manifest_id)
        return Journal.from_payload(entry.path, payload, manifest_id=manifest_id)

    # ------------------------------------------------------------ 落盘
    def save(self, journal: Journal, *, force: bool = False) -> None:
        """登记改动，必要时落盘（节流规则见模块说明）。"""
        journal.updated_at = _now()
        self._dirty[journal.id] = journal
        if force or self._should_flush(journal):
            self.flush(journal)

    def flush(self, journal: Journal) -> None:
        """立刻把这份清单写到盘上（原子写，不备份——它是易失状态，备份没有意义）。"""
        self._dirty.pop(journal.id, None)
        self._writes.pop(journal.id, None)
        self._last_flush[journal.id] = time.monotonic()
        journal.updated_at = _now()
        self._register(journal)
        manifest_kit.write(journal.id, journal.payload(), backup=False)

    def flush_all(self) -> None:
        """把所有待落盘的改动写出去（暂停、退出前调用）。"""
        for journal in list(self._dirty.values()):
            try:
                self.flush(journal)
            except ManifestError as exc:  # 落盘失败不能把退出流程带走
                _logger.warning("临时清单落盘失败：%s：%s", journal.id, exc)

    def _should_flush(self, journal: Journal) -> bool:
        count = self._writes.get(journal.id, 0) + 1
        self._writes[journal.id] = count
        if journal.id not in self._last_flush:
            return True
        every = self._flush_every if self._flush_every is not None else FLUSH_EVERY
        interval = self._flush_interval if self._flush_interval is not None else FLUSH_INTERVAL
        if count >= every:
            return True
        return (time.monotonic() - self._last_flush[journal.id]) >= interval

    # ------------------------------------------------------------ 删除
    def remove(self, journal: Journal) -> None:
        """删掉这份临时清单（文件 + 登记）。"""
        self.forget(journal.id)
        try:
            Path(journal.path).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _logger.warning("临时清单删不掉：%s：%s", journal.path, exc)

    def forget(self, manifest_id: str) -> None:
        """只清内存里的登记与节流状态，不碰文件。"""
        self._dirty.pop(manifest_id, None)
        self._writes.pop(manifest_id, None)
        self._last_flush.pop(manifest_id, None)
        manifest_kit.drop(manifest_id)

    def clear_settled(self) -> list[str]:
        """删除所有「待处理项为空」的临时清单，返回被删的 id。

        调用方应在导入 / 下载结束时调用；正在跑的清单不在其中（它的项还没到终态）。
        """
        removed: list[str] = []
        for journal in self.scan():
            if journal.terminal():
                self.remove(journal)
                removed.append(journal.id)
        return removed

    def settle(self, journal: Journal) -> bool:
        """清单已到终态就按规则删除它，返回是否删了。"""
        if not journal.terminal():
            return False
        self.remove(journal)
        return True


def _path_id(path: Path) -> str:
    """从文件名兜底推个清单 id（`<slug>.json` → `core.journal.<kind>.<slug>`）。"""
    stem = _slug(path.stem)
    parent = _slug(path.parent.name)
    return f"{JOURNAL_PREFIX}.{parent}.{stem}"


__all__ = [
    "FLUSH_EVERY",
    "FLUSH_INTERVAL",
    "ITEM_ACTIVE",
    "ITEM_CANCELLED",
    "ITEM_DONE",
    "ITEM_FAILED",
    "ITEM_OPEN_STATES",
    "ITEM_PENDING",
    "ITEM_SKIPPED",
    "JOURNAL_DIR_NAME",
    "JOURNAL_KIND",
    "JOURNAL_OWNER",
    "JOURNAL_PREFIX",
    "JOURNAL_STATES",
    "STATE_ABANDONED",
    "STATE_FINISHED",
    "STATE_PAUSED",
    "STATE_RUNNING",
    "Journal",
    "JournalStore",
    "journal_id",
    "new_batch",
]
