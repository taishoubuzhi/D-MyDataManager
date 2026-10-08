"""程序本体的下载接口实现：插件通过扩展接口 `download.open` 用 core 的下载器。

设计要点：

- **队列只有一条**：永远走 `app.core.download.service`（程序本体那一份，和「下载管理」页
  用的是同一个），插件不另建队列 —— 于是插件发起的下载会出现在下载管理页里，也统一受
  并行数 / 镜像规则 / 代理 / 超时重试这些设置约束；
- **红线**：`request()` 默认先弹确认框（地址与保存位置都能改），用户点了「开始下载」才
  入队，用户关掉就返回 `None`。`confirm=False` 只有调用方自己已经问过用户时才该用；
- **只给快照**：插件拿到的是 `DownloadRef`，改它不影响队列；要动任务只能走这里的方法。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..core.download import service as download_service
from ..core.download.settings import default_target
from ..sdk import download as download_api
from ..sdk.errors import SdkError

__all__ = ["DownloadApi", "api", "shutdown", "to_ref"]

#: 程序本体共用的那一个（无状态，但统一从 `api()` 取，便于以后加缓存）
_DEFAULT: "DownloadApi | None" = None


def api() -> "DownloadApi":
    """取程序本体共用的下载接口实例（没有就建一个）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DownloadApi()
    return _DEFAULT


def shutdown(wait: float = 3.0) -> None:
    """退出前收工：停掉还在跑的下载（队列本身在 core.download.service 里收尾）。"""
    from ..core.download import service as download_service

    download_service.shutdown(wait)


def _urls(values: Iterable[str] | str | None) -> list[str]:
    """去空白、去重、保持顺序。"""
    if isinstance(values, str):
        values = (values,)
    items: list[str] = []
    for raw in values or ():
        text = str(raw).strip()
        if text and text not in items:
            items.append(text)
    return items


def _name_of(urls: Iterable[str]) -> str:
    """从地址里猜一个文件名（给「选好位置再下载」当默认名）。"""
    for url in urls:
        text = url.split("?")[0].split("#")[0].rstrip("/")
        tail = text.rsplit("/", 1)[-1]
        if tail:
            return tail
    return ""


def to_ref(job: Any) -> download_api.DownloadRef:
    """把 core 的下载任务转成插件能看的只读快照。"""
    return download_api.DownloadRef(
        id=str(job.id),
        urls=tuple(str(item) for item in (getattr(job, "urls", ()) or ())),
        target=str(job.target),
        label=str(getattr(job, "label", "") or ""),
        state=str(job.state),
        state_label=str(job.state_label),
        done_bytes=int(job.done_bytes or 0),
        total_bytes=int(job.total_bytes or 0),
        progress=float(job.progress or 0.0),
        speed=float(job.speed or 0.0),
        eta=float(job.eta or 0.0),
        detail=str(job.detail_label or ""),
        sha256=str(getattr(job, "sha256", "") or ""),
        error=str(getattr(job, "error", "") or ""),
    )


class DownloadApi:
    """`download.open` 的实现。"""

    # ------------------------------------------------------------------ 队列
    def manager(self):
        """程序本体的下载队列（高级用法：需要直接读引擎对象时用）。"""
        return download_service.manager()

    def jobs(self) -> tuple[download_api.DownloadRef, ...]:
        return tuple(to_ref(job) for job in self.manager().jobs())

    def find(self, job_id: str) -> download_api.DownloadRef | None:
        job = self.manager().find(str(job_id or ""))
        return to_ref(job) if job is not None else None

    def default_dir(self) -> str:
        """下载设置里的默认下载目录。"""
        return str(default_target(""))

    # ------------------------------------------------------------------ 发起
    def request(
        self,
        urls: Iterable[str] | str,
        *,
        target: str | Path | None = None,
        name: str = "",
        label: str = "",
        sha256: str = "",
        total_bytes: int = 0,
        confirm: bool = True,
        parent: Any = None,
        title: str = "新建下载",
        hint: str = "",
    ) -> str | None:
        """请程序本体下载：先让用户确认（默认），再入队。

        返回任务 id；用户在确认框里取消（或地址为空）返回 `None`。
        """
        candidates = _urls(urls)
        if not candidates:
            return None
        wanted = name or _name_of(candidates)
        if target:
            resolved = Path(target)
        elif wanted:
            resolved = default_target(wanted)
        else:
            resolved = None
        if confirm:
            picked = self._confirm(candidates, resolved, name=wanted, parent=parent, title=title, hint=hint)
            if picked is None:
                return None
            candidates, resolved = picked
            if resolved is None:
                return None
        if resolved is None:
            return None
        return self._enqueue(candidates, resolved, label=label or name, sha256=sha256, total_bytes=total_bytes)

    def enqueue(
        self,
        urls: Iterable[str] | str,
        target: str | Path,
        *,
        label: str = "",
        sha256: str = "",
        total_bytes: int = 0,
    ) -> str:
        """直接入队，**不弹确认框**（调用方自己负责问过用户）。"""
        candidates = _urls(urls)
        if not candidates:
            raise SdkError("没有可用的下载地址")
        return self._enqueue(candidates, Path(target), label=label, sha256=sha256, total_bytes=total_bytes)

    def choose_target(self, name: str = "") -> str | None:
        """弹「选择下载位置」对话框；用户取消返回 `None`。"""
        from PyQt6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(None, "选择保存位置", self.default_dir())
        if not folder:
            return None
        return str(Path(folder) / Path(name or "").name) if name else str(Path(folder))

    # ------------------------------------------------------------------ 控制
    def pause(self, job_id: str) -> bool:
        return bool(self.manager().pause(str(job_id or "")))

    def resume(self, job_id: str) -> bool:
        return bool(self.manager().resume(str(job_id or "")))

    def cancel(self, job_id: str) -> bool:
        return bool(self.manager().cancel(str(job_id or "")))

    def retry(self, job_id: str) -> bool:
        return bool(self.manager().retry(str(job_id or "")))

    def forget(self, job_id: str) -> bool:
        return bool(self.manager().forget(str(job_id or "")))

    def pause_all(self) -> int:
        return int(self.manager().pause_all())

    def resume_all(self) -> int:
        return int(self.manager().resume_all())

    def cancel_all(self) -> int:
        return int(self.manager().cancel_all())

    def clear_finished(self) -> int:
        return int(self.manager().clear_finished())

    # ------------------------------------------------------------------ 内部
    def _enqueue(
        self,
        urls: Iterable[str],
        target: Path,
        *,
        label: str = "",
        sha256: str = "",
        total_bytes: int = 0,
    ) -> str:
        job = self.manager().enqueue(
            list(urls),
            target,
            sha256=str(sha256 or ""),
            total_bytes=int(total_bytes or 0),
            label=str(label or ""),
        )
        return str(job.id)

    def _confirm(
        self,
        urls: list[str],
        target: Path | None,
        *,
        name: str = "",
        parent: Any = None,
        title: str = "新建下载",
        hint: str = "",
    ) -> tuple[list[str], Path | None] | None:
        """弹确认框（就是下载管理页那个对话框，地址与位置都预填好）。"""
        from ..ui.components.download_view import AddDownloadDialog

        directory = str(target.parent) if target is not None else self.default_dir()
        dialog = AddDownloadDialog(parent, directory=directory, title=title, urls=urls, name=name, hint=hint)
        try:
            if not dialog.exec():
                return None
            picked = dialog.urls()
            if not picked:
                return None
            return picked, dialog.target()
        finally:
            dialog.deleteLater()
