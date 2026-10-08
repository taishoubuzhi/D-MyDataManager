"""core 通用 pip 安装器：建虚拟环境、装依赖、本地 whl、暂停 / 取消、依赖探测。

这个包是**引擎**，不是界面，也不对用户开放（程序本体没有「pip 安装」页）。它的用处是
让程序本体与插件共用同一套安装能力，而不是各自抄一份 `pip install` 的调用。

三个模块：

* `engine` —— 执行（`ensure` / `install_wheels` / `create_venv` / `stream` / `Control`），
  以及 `pip_command` / `check_wheels` / `package_versions` 这些纯工具的辅助函数；
* `probe` —— 问一个**别的**解释器「这些依赖装没装」；
* `tasks` —— 把一次安装放进后台线程（`PipTasks` / `PipTask`），支持暂停 / 继续 / 取消；
* 本模块 —— 把三者的公开名字再导出一次。

只依赖标准库，可以直接 `from app.core.pip import ensure`；需要 Qt 的东西一律不在这里
预导入。
"""

from __future__ import annotations

from .engine import (
    Control,
    CREATE_NO_WINDOW,
    PipError,
    PipStopped,
    STOP_POLL_SEC,
    WINDOWS_PATH_LIMIT,
    check_wheels,
    create_venv,
    dist_name,
    emit,
    ensure,
    ensure_packages,
    environment,
    github_asset_urls,
    github_retry,
    install_wheels,
    long_path_hint,
    mirror_github_urls,
    network_hint,
    package_versions,
    pip_command,
    read_log,
    stream,
    terminate,
    venv_python,
    wheel_dist_names,
    write_requirements,
)
from .probe import (
    MISSING_MARK,
    PROBE_ENV,
    PROBE_TIMEOUT,
    import_name,
    marked,
    missing_program_group,
    missing_program_packages,
    probe_python,
    run_probe,
    spec_base,
)
from .tasks import (
    FINAL_STATES,
    KIND_PACKAGES,
    KIND_WHEELS,
    PipTask,
    PipTasks,
    STATE_CANCELLED,
    STATE_DONE,
    STATE_ERROR,
    STATE_LABELS,
    STATE_PAUSED,
    STATE_PENDING,
    STATE_RUNNING,
)

__all__ = [
    "Control",
    "CREATE_NO_WINDOW",
    "FINAL_STATES",
    "KIND_PACKAGES",
    "KIND_WHEELS",
    "MISSING_MARK",
    "PROBE_ENV",
    "PROBE_TIMEOUT",
    "PipError",
    "PipStopped",
    "PipTask",
    "PipTasks",
    "STOP_POLL_SEC",
    "STATE_CANCELLED",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_LABELS",
    "STATE_PAUSED",
    "STATE_PENDING",
    "STATE_RUNNING",
    "WINDOWS_PATH_LIMIT",
    "check_wheels",
    "create_venv",
    "dist_name",
    "emit",
    "ensure",
    "ensure_packages",
    "environment",
    "github_asset_urls",
    "github_retry",
    "import_name",
    "install_wheels",
    "long_path_hint",
    "marked",
    "mirror_github_urls",
    "missing_program_group",
    "missing_program_packages",
    "network_hint",
    "package_versions",
    "pip_command",
    "probe_python",
    "read_log",
    "run_probe",
    "spec_base",
    "stream",
    "terminate",
    "venv_python",
    "wheel_dist_names",
    "write_requirements",
]
