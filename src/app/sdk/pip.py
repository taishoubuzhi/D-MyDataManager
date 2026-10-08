"""pip 安装器接口（`pip.install`）：程序本体的通用安装能力，供程序本体与插件共用。

程序本体提供实现（见 `app.services.pip_api`），插件这样用：

    from app.sdk import pip

    task = pip.install(python, log=pip.task_log(python), packages=["onnxruntime"], title="装 onnxruntime")

这个接口**不对用户单独开放**：程序本体没有「pip 安装」页，界面由调用它的插件自己出。
红线与下载器一致 —— `install()` 默认先弹确认框告诉用户要装什么、装到哪个解释器，用户同意
才真的开跑；要跳过确认必须显式 `confirm=False`，那意味着调用方自己已经问过用户了。

安装跑在后台线程里，`PipRef` 是快照；暂停 / 继续 / 取消走这里的函数。纯工具函数
（`venv_python` / `check_wheels` / `package_versions` / `missing_program_packages`）直接转发
core 的实现，可以放心在插件的工作线程里调。

除了「发起任务」这一套，本模块还转发 core 安装引擎的**同步**件（`ensure` / `ensure_packages` /
`create_venv` / `install_wheels` / `stream` / `Control` / `PipStopped` / `PipError`）：插件的
兼容壳（例如 lib.model 的 `runtime`）自己管路径与留档，只借这里的执行能力，不必再抄一份
`pip install` 的调用。这些同步件跑在**调用方自己的线程**里，`install()` 才用后台任务。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.pip import (
    KIND_PACKAGES,
    KIND_WHEELS,
    Control,
    CREATE_NO_WINDOW,
    PipError,
    PipStopped,
    STOP_POLL_SEC,
    check_wheels,
    create_venv,
    emit,
    ensure,
    ensure_packages,
    github_asset_urls,
    import_name,
    install_wheels,
    long_path_hint,
    mirror_github_urls,
    missing_program_group,
    missing_program_packages,
    network_hint,
    package_versions,
    stream,
    terminate,
    venv_python,
    write_requirements,
)
from .errors import SdkError

__all__ = [
    "CREATE_NO_WINDOW",
    "Control",
    "FINAL_STATES",
    "KIND_PACKAGES",
    "KIND_WHEELS",
    "PIP_EXTENSION",
    "PipError",
    "PipRef",
    "PipStopped",
    "STATE_LABELS",
    "STOP_POLL_SEC",
    "available",
    "cancel",
    "check_wheels",
    "clear_finished",
    "create_venv",
    "emit",
    "ensure",
    "ensure_packages",
    "find",
    "forget",
    "github_asset_urls",
    "import_name",
    "install",
    "install_wheels",
    "jobs",
    "long_path_hint",
    "mirror_github_urls",
    "missing_program_group",
    "missing_program_packages",
    "network_hint",
    "package_versions",
    "pause",
    "provider",
    "resume",
    "stream",
    "task_log",
    "terminate",
    "venv_python",
    "wait",
    "write_requirements",
]

from ..core.pip import FINAL_STATES, STATE_LABELS  # noqa: E402  （放在常量区，便于阅读）

#: 扩展接口名：程序本体用它把通用 pip 安装器提供给插件
PIP_EXTENSION = "pip.install"


@dataclass(frozen=True)
class PipRef:
    """一次安装任务的只读快照。"""

    id: str = ""
    title: str = ""
    kind: str = KIND_PACKAGES
    state: str = ""
    state_label: str = ""
    error: str = ""
    log: str = ""
    python: str = ""
    result: str = ""
    tail: str = ""

    @property
    def finished(self) -> bool:
        return self.state in FINAL_STATES

    @property
    def paused(self) -> bool:
        return self.state == "paused"


def task_log(python: str | Path, name: str = "pip.log") -> Path:
    """给某个解释器安排一个默认日志文件（写在它自己目录下，方便出了问题去翻）。"""
    target = Path(python)
    folder = target.parent if target.parent != Path("") else Path(".")
    return folder / name


def provider() -> Any:
    """程序提供的 pip 接口（`pip.install`）；没有（脚本、测试）时为 None。"""
    from ..core.plugins.extensions import extension_registry

    return extension_registry.provider(PIP_EXTENSION)


def available() -> bool:
    """程序本体有没有提供 pip 安装器。"""
    return provider() is not None


def _api() -> Any:
    api = provider()
    if api is None:
        raise SdkError("程序没有提供 pip 安装接口 pip.install")
    return api


def install(
    python: str | Path,
    *,
    log: str | Path,
    packages: Iterable[str] = (),
    kind: str = KIND_PACKAGES,
    title: str = "",
    requirements: str | Path | None = None,
    wheels: Iterable[str] = (),
    venv: str | Path | None = None,
    base_python: str | Path | None = None,
    index_url: str = "",
    extra_index: Iterable[str] = (),
    urls: Iterable[str] = (),
    upgrade: bool = False,
    github_prefixes: Iterable[str] = (),
    on_line: Callable[[str], None] | None = None,
    confirm: bool = True,
    parent: Any = None,
    message: str = "",
) -> PipRef | None:
    """发起一次安装（后台跑）。

    `confirm=True`（默认）先弹确认框，用户同意返回任务快照，取消返回 `None`。
    `kind=KIND_WHEELS` 时按「从本地 whl 安装」走（`wheels` 是 whl 文件路径）。
    """
    return _api().install(
        python,
        log=log,
        packages=packages,
        kind=kind,
        title=title,
        requirements=requirements,
        wheels=wheels,
        venv=venv,
        base_python=base_python,
        index_url=index_url,
        extra_index=extra_index,
        urls=urls,
        upgrade=upgrade,
        github_prefixes=github_prefixes,
        on_line=on_line,
        confirm=confirm,
        parent=parent,
        message=message,
    )


def jobs() -> tuple[PipRef, ...]:
    """全部安装任务（含已结束的），按发起顺序。"""
    return tuple(_api().jobs())


def find(task_id: str) -> PipRef | None:
    return _api().find(task_id)


def wait(task_id: str, timeout: float | None = None) -> bool:
    """等某个任务进入终态。"""
    return bool(_api().wait(task_id, timeout))


def pause(task_id: str) -> bool:
    return bool(_api().pause(task_id))


def resume(task_id: str) -> bool:
    return bool(_api().resume(task_id))


def cancel(task_id: str) -> bool:
    return bool(_api().cancel(task_id))


def forget(task_id: str) -> bool:
    return bool(_api().forget(task_id))


def clear_finished() -> int:
    return int(_api().clear_finished())
