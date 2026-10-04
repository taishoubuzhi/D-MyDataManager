"""日志 / 控制台检查：统一格式规范，插件只能走 SDK 控制台。

统一格式：`时间 | 级别 | 来源 | 函数:行 | 消息`（`app.core.logging_setup.DEFAULT_FORMAT`）。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from .harness import BUILTIN_PLUGINS, Case, check

#: 统一格式渲染出来的一行：`2026-10-04 18:41:43.404 | INFO     | app | _stage:72 | 启动 1/8：…`
LINE = re.compile(r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) \| (?P<level>\w+)\s*\| (?P<source>.*?) \| (?P<where>\w+):(?P<line>\d+) \| (?P<message>.*)$")

#: 本检查自己要渲染的那两行（sink 里可能混进别的日志行）
_SAMPLES = {"控制台自检：绑定来源", "控制台自检：按调用模块推来源"}


@check("console_source_mapping", "services")
def console_source_mapping(case: Case) -> None:
    """来源列映射：插件去掉 `dm_plugin.` 前缀，`__main__` 记成 app，旧格式串自动升级。"""
    from app.core import logging_setup
    from app.core.config import Config

    problems = []
    cases = {
        "dm_plugin.builtin.lib.model.registry": "builtin.lib.model.registry",
        "dm_plugin.builtin.lib.ui.plugin": "builtin.lib.ui.plugin",
        "__main__": "app",
        "app.sdk.data": "app.sdk.data",
        "": "app",
    }
    for name, expected in cases.items():
        actual = logging_setup.source_of(name)
        if actual != expected:
            problems.append(f"source_of({name!r}) = {actual!r}，应为 {expected!r}")
    if logging_setup.SOURCE_FIELD not in logging_setup.DEFAULT_FORMAT:
        problems.append(f"默认格式串缺少来源列：{logging_setup.DEFAULT_FORMAT}")
    if logging_setup.SOURCE_FIELD not in str(Config.logFormat.defaultValue):
        problems.append(f"设置项声明的默认格式串没更新：{Config.logFormat.defaultValue}")
    # 历史格式（含老配置文件里存下来的）必须自动升级；用户自己写的格式原样尊重
    previous = logging_setup.config
    try:
        for stored in ("", logging_setup.LEGACY_DEFAULT_FORMAT, logging_setup.LEGACY_SOURCE_FORMAT):
            logging_setup.config = SimpleNamespace(logFormat=SimpleNamespace(value=stored))
            if logging_setup.active_format() != logging_setup.DEFAULT_FORMAT:
                problems.append(f"历史格式没升级：{stored!r} → {logging_setup.active_format()!r}")
        logging_setup.config = SimpleNamespace(logFormat=SimpleNamespace(value="{time} - {message}"))
        if logging_setup.active_format() != "{time} - {message}":
            problems.append("用户自定义格式被改动了")
    finally:
        logging_setup.config = previous
    if logging_setup.source_of(logging_setup.PLUGIN_MODULE_PREFIX + "x.y") != "x.y":
        problems.append("插件前缀没有被剥掉")
    assert not problems, "来源映射不对：" + "；".join(problems[:8])


@check("console_line_format", "services")
def console_line_format(case: Case) -> None:
    """真正渲染一行：时间 / 级别 / 来源 / 函数:行 / 消息 五段齐全，来源与行号指到调用点。"""
    from loguru import logger

    from app.core import logging_setup
    from app.sdk.console import console_for, warning

    lines: list[tuple[str, dict]] = []

    def sink(message) -> None:
        lines.append((str(message), message.record))

    # 格式串要取 `{extra[source]}`：装上程序里同一套补字段逻辑，别的日志行才不会渲染失败
    logger.configure(patcher=logging_setup._patch_record)
    sink_id = logger.add(sink, level="DEBUG", format=logging_setup.active_format(), colorize=False)
    try:
        bound_line = _emit_bound(console_for)
        plain_line = _emit_plain(warning)
    finally:
        logger.remove(sink_id)

    rows = {str(record["message"]): text for text, record in lines if str(record["message"]) in _SAMPLES}
    problems = []
    if len(rows) != 2:
        assert False, f"应渲染两行，实际 {sorted(rows)}"
    for text in rows.values():
        if not LINE.match(text):
            problems.append(f"格式不符：{text!r}")

    bound_match = LINE.match(rows["控制台自检：绑定来源"])
    plain_match = LINE.match(rows["控制台自检：按调用模块推来源"])
    if bound_match and plain_match:
        if bound_match.group("level") != "INFO":
            problems.append(f"级别列不对：{bound_match.group('level')}")
        if bound_match.group("source") != "builtin.lib.model":
            problems.append(f"来源列不对：{bound_match.group('source')!r}")
        if bound_match.group("where") != "_emit_bound" or int(bound_match.group("line")) != bound_line:
            problems.append(f"调用点不对：{bound_match.group('where')}:{bound_match.group('line')}（应为 _emit_bound:{bound_line}）")
        if bound_match.group("message") != "控制台自检：绑定来源":
            problems.append(f"消息被改动：{bound_match.group('message')!r}")
        expected_source = logging_setup.source_of(__name__)
        if plain_match.group("source") != expected_source:
            problems.append(f"没给来源时应按模块推：{plain_match.group('source')!r} ≠ {expected_source!r}")
        if plain_match.group("where") != "_emit_plain" or int(plain_match.group("line")) != plain_line:
            problems.append(f"调用点不对：{plain_match.group('where')}:{plain_match.group('line')}（应为 _emit_plain:{plain_line}）")
    for _, record in lines:
        if not record["extra"].get("source"):
            problems.append(f"记录里少了来源字段：{record['message']!r}")
    assert not problems, "统一格式没落地：" + "；".join(problems[:8])


def _emit_bound(console_for) -> int:
    """记下这一行在文件里的行号，方便断言 `函数:行` 指到调用点。"""
    line = _here() + 1
    console_for("builtin.lib.model").info("控制台自检：绑定来源")
    return line


def _emit_plain(warning) -> int:
    line = _here() + 1
    warning("控制台自检：按调用模块推来源")
    return line


def _here() -> int:
    import sys

    return sys._getframe(1).f_lineno


@check("plugin_logging_via_sdk", "services")
def plugin_logging_via_sdk(case: Case) -> None:
    """内置插件不许直接 import loguru，日志必须走 SDK 控制台；且确实都接上了。"""
    problems = []
    wired = 0
    for path in sorted(BUILTIN_PLUGINS.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(BUILTIN_PLUGINS.parent).as_posix()
        if re.search(r"^\s*(from loguru import|import loguru)", text, re.M):
            problems.append(f"{relative} 直接 import loguru")
        if re.search(r"^\s+logger\.", text, re.M):
            problems.append(f"{relative} 还在调 logger.*")
        if "_console = console_for(" in text:
            wired += 1
    if wired < 10:
        problems.append(f"只有 {wired} 个插件模块接上了 SDK 控制台，应该在 10 个以上")
    if not (BUILTIN_PLUGINS / "builtin.lib.model" / "adapters" / "worker.py").exists():
        problems.append("找不到 worker 适配器")
    assert not problems, "插件日志没统一走 SDK 控制台：" + "；".join(problems[:8])
