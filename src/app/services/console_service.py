"""程序本体的控制台输出实现：插件通过扩展接口 `console.output` 播报阶段与进度。

程序把插件输出与自己的日志合成一套：都走 loguru，因此「日志 → 输出到控制台」
开关与文件日志同时生效；以后要加时间戳前缀或落库，只改这里即可。
"""

from __future__ import annotations

__all__ = ["ConsoleOutput"]

from app.sdk import console as console_api


class ConsoleOutput:
    """控制台输出接口实现：`write(*, level, message, source, stage)`。"""

    def write(self, *, level: str = "info", message: str = "", source: str = "", stage: str = "") -> None:
        console_api.emit(level, message, source=source, stage=stage)
