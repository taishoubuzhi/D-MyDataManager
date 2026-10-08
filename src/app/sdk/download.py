"""下载器接口（`download.open`）：插件用程序本体的下载队列下东西。

程序本体提供实现（见 `app.services.download_api`），插件这样用：

    from app.sdk import download

    job_id = download.request(["https://example.com/a.bin"], name="a.bin")
    if job_id is None:
        ...  # 用户没同意

三条约定：

* **插件不许替用户决定下载**。`request()` 默认先弹确认框（地址与保存位置都在里面，用户
  可以改），用户点了「开始下载」才入队，用户关掉就返回 `None`。要跳过确认必须显式
  `confirm=False`，那意味着调用方自己已经问过用户了；
* **队列是程序本体那一份**：插件发起的下载会出现在「下载管理」页里，也统一受下载设置
  （并行数、镜像规则、代理、超时与重试）约束，插件不需要也不该另建队列；
* 拿到的是 `DownloadRef` 快照，改它不影响队列；要动任务只能走这里的函数。
* 要自己做任务行（每行带暂停/继续/取消）的插件用 `manager()` 直接拿那一份队列；但那是
  共享对象，**别** `shutdown()` 它。

只依赖 `app.sdk`，Qt 相关实现在函数内部延迟导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..core.download import (
    FINAL_STATES,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_ERROR,
    STATE_PAUSED,
    STATE_QUEUED,
    STATE_RUNNING,
    USER_AGENT,
    DownloadError,
    DownloadJob,
    DownloadManager,
    DownloadOptions,
    build_opener,
    describe_error,
    part_path,
    to_int,
)
from .errors import SdkError

__all__ = [
    "DOWNLOAD_EXTENSION",
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "DownloadOptions",
    "DownloadRef",
    "FINAL_STATES",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_PAUSED",
    "STATE_QUEUED",
    "STATE_RUNNING",
    "USER_AGENT",
    "available",
    "build_opener",
    "cancel",
    "cancel_all",
    "choose_target",
    "clear_finished",
    "default_dir",
    "describe_error",
    "enqueue",
    "find",
    "forget",
    "jobs",
    "manager",
    "part_path",
    "pause",
    "pause_all",
    "provider",
    "request",
    "resume",
    "resume_all",
    "retry",
    "to_int",
]

#: 扩展接口名：程序本体用它把下载队列提供给插件
DOWNLOAD_EXTENSION = "download.open"


@dataclass(frozen=True)
class DownloadRef:
    """一个下载任务的只读快照。"""

    id: str = ""
    urls: tuple[str, ...] = ()
    target: str = ""
    label: str = ""
    state: str = ""
    state_label: str = ""
    done_bytes: int = 0
    total_bytes: int = 0
    progress: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    detail: str = ""
    sha256: str = ""
    error: str = ""

    @property
    def name(self) -> str:
        """显示名：有标签用标签，否则用文件名。"""
        if self.label:
            return self.label
        text = str(self.target or "").replace("\\", "/")
        return text.rsplit("/", 1)[-1] if text else self.id

    @property
    def finished(self) -> bool:
        from ..core.download.engine import FINAL_STATES

        return self.state in FINAL_STATES


def provider() -> Any:
    """程序提供的下载接口（`download.open`）；没有（脚本、测试）时为 None。"""
    from ..core.plugins.extensions import extension_registry

    return extension_registry.provider(DOWNLOAD_EXTENSION)


def available() -> bool:
    """程序本体有没有提供下载器（插件要降级运行时先问这个）。"""
    return provider() is not None


def manager() -> DownloadManager:
    """程序本体那**一个**下载队列（低层入口）。

    普通插件用 `request()` / `jobs()` 就够了；只有要自己做任务行（每行带暂停/继续/取消）
    的插件才需要它——拿到的是共享对象，**不要** `shutdown()` 它，那会把别人的下载一起停掉。
    队列在这里头一次被真正建起来（会读配置，所以延迟到调用时导入）。
    """
    from ..core.download import service

    return service.manager()


def _api() -> Any:
    api = provider()
    if api is None:
        raise SdkError("程序没有提供下载接口 download.open")
    return api


def request(
    urls: Iterable[str],
    *,
    target: str = "",
    name: str = "",
    label: str = "",
    sha256: str = "",
    total_bytes: int = 0,
    confirm: bool = True,
    parent: Any = None,
) -> str | None:
    """请程序本体下载。

    `confirm=True`（默认）会先弹确认框，用户同意返回任务 id，取消返回 `None`。
    `target` 为空时按 `name`（或地址里的文件名）落到下载设置里的默认目录。
    """
    return _api().request(
        urls,
        target=target or None,
        name=name,
        label=label,
        sha256=sha256,
        total_bytes=total_bytes,
        confirm=confirm,
        parent=parent,
    )


def enqueue(
    urls: Iterable[str],
    target: str,
    *,
    label: str = "",
    sha256: str = "",
    total_bytes: int = 0,
) -> str:
    """直接入队（**不弹确认框**）。只有调用方自己已经问过用户时才该用。"""
    return _api().enqueue(urls, target, label=label, sha256=sha256, total_bytes=total_bytes)


def choose_target(name: str = "") -> str | None:
    """弹「选择下载位置」对话框；用户取消返回 `None`。"""
    return _api().choose_target(name)


def default_dir() -> str:
    """下载设置里的默认下载目录。"""
    return _api().default_dir()


def jobs() -> tuple[DownloadRef, ...]:
    """全部下载任务（含已结束的），按加入顺序。"""
    return tuple(_api().jobs())


def find(job_id: str) -> DownloadRef | None:
    """按任务 id 查一个任务。"""
    return _api().find(job_id)


def pause(job_id: str) -> bool:
    return bool(_api().pause(job_id))


def resume(job_id: str) -> bool:
    return bool(_api().resume(job_id))


def cancel(job_id: str) -> bool:
    return bool(_api().cancel(job_id))


def retry(job_id: str) -> bool:
    return bool(_api().retry(job_id))


def forget(job_id: str) -> bool:
    return bool(_api().forget(job_id))


def pause_all() -> int:
    return int(_api().pause_all())


def resume_all() -> int:
    return int(_api().resume_all())


def cancel_all() -> int:
    return int(_api().cancel_all())


def clear_finished() -> int:
    return int(_api().clear_finished())
