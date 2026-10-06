"""控制台输出扩展接口：插件把「正在做什么、进行到第几步」播报到程序控制台。

插件侧统一用这里，或者用已经绑好插件 id 的 `ctx.console`：

    ctx.console.info("开始加载模型")
    ctx.console.stage("下载", f"正在下载 {name}")
    ctx.console.progress(3, 10, "解压分片")
    ctx.console.exception("加载失败")      # 带当前异常栈

统一格式（见 `app.core.runtime.logging_setup.DEFAULT_FORMAT`）：

    时间 | 级别 | 来源 | 函数:行 | 消息

「来源」由这里保证填对：调用点落在插件里就是插件 id，落在程序里就是 `app`；
`函数:行` 用 `logger.opt(depth=…)` 跳过本模块与 `console.output` 实现，指到真正发日志的那一行。

程序本体通过扩展接口 `console.output` 提供实现（见 `app.services.console_service`）；
没有实现时直接落到 loguru——控制台 sink 由「日志 → 输出到控制台」设置控制，
所以插件在没有界面的脚本 / 测试环境里也能照常输出，不需要额外判断。
"""

from __future__ import annotations

import sys
import traceback
from typing import Any

from loguru import logger

__all__ = [
    "CONSOLE_EXTENSION",
    "Console",
    "available",
    "console_for",
    "debug",
    "emit",
    "error",
    "exception",
    "info",
    "progress",
    "stage",
    "success",
    "warning",
    "write",
]

#: 控制台输出接口：实现方需提供 `write(*, level, message, source, stage)`。
CONSOLE_EXTENSION = "console.output"

#: 输出管线自身的模块：算真实调用点时整段跳过
_PLUMBING_MODULES = frozenset({"app.sdk.console", "app.services.console_service"})


def _caller() -> tuple[int, str]:
    """返回 `(loguru depth, 调用点模块名)`，跳过 `console` 与 `console.output` 实现。

    `depth` 用 loguru 的语义算：0 是 `emit()` 自己这一帧，1 是它的调用者，依此类推。
    """
    frame = sys._getframe(2)
    depth = 1
    while frame is not None:
        name = str(frame.f_globals.get("__name__") or "")
        if name not in _PLUMBING_MODULES:
            return depth, name
        depth += 1
        frame = frame.f_back
    return depth, ""


def _provider() -> Any:
    """取程序本体注册的控制台实现；没有（脚本、测试）就返回 None。"""
    try:
        from ..core.plugins.extensions import extension_registry
    except Exception:
        return None
    return extension_registry.provider(CONSOLE_EXTENSION)


def available() -> bool:
    """程序是否提供了控制台输出实现（没有也能输出，只是直接走 loguru）。"""
    return _provider() is not None


def _source_for(source: str, module: str) -> str:
    """来源列：显式给过的优先，否则按调用点的模块名推（脚本环境里推不出来就留空）。"""
    text = str(source or "").strip()
    if text:
        return text
    if not module:
        return ""
    try:
        from ..core.runtime.logging_setup import source_of
    except Exception:  # 插件库代码可能在没有 app 包的子进程里跑
        return "" if module == "__main__" else module
    return source_of(module)


def emit(level: str, message: Any, *, source: str = "", stage: str = "") -> None:
    """把一行按级别交给 loguru（控制台 sink 与文件 sink 共用这套日志）。"""
    depth, module = _caller()
    text = f"[{stage}] {message}" if stage else str(message)
    record = logger.opt(depth=depth)
    name = _source_for(source, module)
    if name:
        record = record.bind(source=name)
    if level == "success":
        record.success(text)
    elif level in ("warning", "warn"):
        record.warning(text)
    elif level == "error":
        record.error(text)
    elif level == "debug":
        record.debug(text)
    elif level == "progress":
        record.info(f"进度 {text}")
    else:
        record.info(text)


def exception(message: Any, *, source: str = "", stage: str = "") -> None:
    """出错时用：按 error 级别写一行，并附上当前异常栈。"""
    text = str(message or "")
    trace = traceback.format_exc().strip()
    if trace and not trace.startswith("NoneType: None"):
        text = f"{text}\n{trace}" if text else trace
    write(text, level="error", source=source, stage=stage)


def write(message: Any, *, level: str = "info", source: str = "", stage: str = "") -> None:
    """往程序控制台写一行；level 取 debug / info / success / warning / error / progress。"""
    text = str(message)
    provider = _provider()
    handler = getattr(provider, "write", None) if provider is not None else None
    if callable(handler):
        handler(level=str(level or "info"), message=text, source=str(source or ""), stage=str(stage or ""))
        return
    emit(str(level or "info"), text, source=source, stage=stage)


def stage(name: str, message: Any = "", *, level: str = "info", source: str = "") -> None:
    """播报一个阶段：`stage("下载", "开始")` 输出 `[下载] 开始`。"""
    text = str(message or "")
    if text:
        write(text, level=level, source=source, stage=str(name))
    else:
        write(str(name), level=level, source=source)


def progress(done: int, total: int | None = None, message: Any = "", *, source: str = "") -> None:
    """播报进度：`progress(3, 10, "解压")` 输出 `进度 解压 3/10`。"""
    counter = f"{done}/{total}" if total else str(done)
    write(f"{message} {counter}".strip(), level="progress", source=source)


def debug(message: Any, *, source: str = "", stage: str = "") -> None:
    write(message, level="debug", source=source, stage=stage)


def info(message: Any, *, source: str = "", stage: str = "") -> None:
    write(message, level="info", source=source, stage=stage)


def success(message: Any, *, source: str = "", stage: str = "") -> None:
    write(message, level="success", source=source, stage=stage)


def warning(message: Any, *, source: str = "", stage: str = "") -> None:
    write(message, level="warning", source=source, stage=stage)


def error(message: Any, *, source: str = "", stage: str = "") -> None:
    write(message, level="error", source=source, stage=stage)


class Console:
    """绑定了来源的语法糖：`ctx.console` 就是 `Console(plugin_id)`。"""

    __slots__ = ("_source",)

    def __init__(self, source: str = "") -> None:
        self._source = str(source or "")

    @property
    def source(self) -> str:
        return self._source

    def write(self, message: Any, *, level: str = "info", stage: str = "") -> None:
        write(message, level=level, source=self._source, stage=stage)

    def stage(self, name: str, message: Any = "", *, level: str = "info") -> None:
        stage(name, message, level=level, source=self._source)

    def progress(self, done: int, total: int | None = None, message: Any = "") -> None:
        progress(done, total, message, source=self._source)

    def debug(self, message: Any, *, stage: str = "") -> None:
        write(message, level="debug", source=self._source, stage=stage)

    def info(self, message: Any, *, stage: str = "") -> None:
        write(message, level="info", source=self._source, stage=stage)

    def success(self, message: Any, *, stage: str = "") -> None:
        write(message, level="success", source=self._source, stage=stage)

    def warning(self, message: Any, *, stage: str = "") -> None:
        write(message, level="warning", source=self._source, stage=stage)

    def error(self, message: Any, *, stage: str = "") -> None:
        write(message, level="error", source=self._source, stage=stage)

    def exception(self, message: Any, *, stage: str = "") -> None:
        exception(message, source=self._source, stage=stage)


def console_for(source: str = "") -> Console:
    """给某个来源（一般是插件 id）造一个控制台门面。"""
    return Console(source)
