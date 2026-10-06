"""SDK 控制台接口：默认落 loguru，程序提供 console.output 时转发，插件侧用 ctx.console 播报。"""

from __future__ import annotations

import re
import sys
import types
import unittest

from loguru import logger

import tests.harness  # noqa: F401  载入测试基座（把 src 加进 sys.path）
from app.core.runtime import logging_setup
from app.core.plugins.extensions import extension_registry
from app.sdk import console as console_api
from app.sdk.context import PluginContext

#: 统一格式渲染出来的一行：时间 | 级别 | 来源 | 函数:行 | 消息
LINE = re.compile(r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) \| (?P<level>\w+)\s*\| (?P<source>.*?) \| (?P<where>\w+):(?P<line>\d+) \| (?P<message>.*)$")


class _Recorder:
    """既当 loguru sink，也当 console.output 假实现。"""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.records: list = []
        self.calls: list[dict[str, str]] = []

    def sink(self, message) -> None:
        self.messages.append(str(message))
        self.records.append(message.record)

    def write(self, *, level: str = "info", message: str = "", source: str = "", stage: str = "") -> None:
        self.calls.append({"level": level, "message": message, "source": source, "stage": stage})


def _here() -> int:
    """当前行号：配合 `+ 1` 断言下一行才是调用点。"""
    return sys._getframe(1).f_lineno


class ConsoleSdkCase(unittest.TestCase):
    def setUp(self) -> None:
        self.recorder = _Recorder()
        # 程序里统一格式取 `{extra[source]}`：装上同一套补字段逻辑，别的日志行才不会渲染失败
        logger.configure(patcher=logging_setup._patch_record)
        self.sink = logger.add(self.recorder.sink, level="DEBUG", format="{message}")
        self.previous = extension_registry.provider(console_api.CONSOLE_EXTENSION)
        self.previous_owner = extension_registry.provider_plugin(console_api.CONSOLE_EXTENSION)

    def tearDown(self) -> None:
        logger.remove(self.sink)
        extension_registry.drop_plugin("console-test")
        extension_registry.provide(console_api.CONSOLE_EXTENSION, self.previous, self.previous_owner)

    def _install(self) -> None:
        extension_registry.provide(console_api.CONSOLE_EXTENSION, self.recorder, "console-test")

    def test_default_output_goes_to_loguru(self) -> None:
        extension_registry.provide(console_api.CONSOLE_EXTENSION, None, "")

        assert not console_api.available(), "没有实现时 available() 应为假"
        console_api.info("默认输出", source="demo")
        console_api.stage("下载权重", "开始下载")
        console_api.progress(2, 5, "解压")

        assert [text.strip() for text in self.recorder.messages] == [
            "默认输出",
            "[下载权重] 开始下载",
            "进度 解压 2/5",
        ], self.recorder.messages
        assert self.recorder.records[0]["extra"]["source"] == "demo", self.recorder.records[0]["extra"]
        assert self.recorder.calls == [], "没有实现时不该走转发"

    def test_default_format_renders_source_and_caller(self) -> None:
        """统一格式：时间 | 级别 | 来源 | 函数:行 | 消息，函数:行 指到真正调用的那一行。"""
        extension_registry.provide(console_api.CONSOLE_EXTENSION, None, "")
        lines: list[tuple[str, str]] = []

        def sink(message) -> None:
            lines.append((str(message), str(message.record["message"])))

        sink_id = logger.add(sink, level="DEBUG", format=logging_setup.active_format(), colorize=False)
        try:
            line = _here() + 1
            console_api.info("统一格式自检", source="lib.model")
        finally:
            logger.remove(sink_id)

        rendered = [text for text, plain in lines if plain == "统一格式自检"]
        assert len(rendered) == 1, f"应渲染一行，实际 {rendered}"
        rendered = rendered[0]
        match = LINE.match(rendered)
        assert match is not None, f"格式不符：{rendered!r}"
        assert match.group("level") == "INFO", rendered
        assert match.group("source") == "lib.model", rendered
        assert match.group("where") == "test_default_format_renders_source_and_caller", rendered
        assert int(match.group("line")) == line, rendered
        assert match.group("message") == "统一格式自检", rendered

    def test_exception_appends_traceback(self) -> None:
        extension_registry.provide(console_api.CONSOLE_EXTENSION, None, "")

        try:
            raise ZeroDivisionError("除法自检")
        except ZeroDivisionError:
            console_api.exception("操作炸了", source="demo")

        rendered = self.recorder.messages[-1]
        assert "操作炸了" in rendered and "ZeroDivisionError" in rendered, rendered
        assert self.recorder.records[-1]["level"].name == "ERROR", self.recorder.records[-1]["level"]
        assert self.recorder.records[-1]["extra"]["source"] == "demo", self.recorder.records[-1]["extra"]

    def test_active_format_upgrades_legacy_default(self) -> None:
        """老配置里存的是旧格式串，载入后要自动换成统一格式；用户自定义的格式原样尊重。"""
        assert logging_setup.source_of("dm_plugin.lib.model.registry") == "lib.model.registry"
        assert logging_setup.source_of("__main__") == "app"
        assert logging_setup.SOURCE_FIELD in logging_setup.DEFAULT_FORMAT

        previous = logging_setup.config
        try:
            for legacy in (logging_setup.LEGACY_DEFAULT_FORMAT, logging_setup.LEGACY_SOURCE_FORMAT):
                logging_setup.config = types.SimpleNamespace(logFormat=types.SimpleNamespace(value=legacy))
                assert logging_setup.active_format() == logging_setup.DEFAULT_FORMAT, f"历史格式应自动升级：{legacy}"
            logging_setup.config = types.SimpleNamespace(
                logFormat=types.SimpleNamespace(value="{time} - {message}")
            )
            assert logging_setup.active_format() == "{time} - {message}", "用户自定义格式应原样保留"
        finally:
            logging_setup.config = previous

    def test_write_forwards_level_message_source_stage(self) -> None:
        self._install()

        assert console_api.available(), "提供实现后 available() 应为真"
        console_api.console_for("lib.model").success("装好了")
        console_api.stage("下载", "第二片", source="lib.model")
        console_api.warning("注意", source="demo", stage="扫描")

        assert self.recorder.calls == [
            {"level": "success", "message": "装好了", "source": "lib.model", "stage": ""},
            {"level": "info", "message": "第二片", "source": "lib.model", "stage": "下载"},
            {"level": "warning", "message": "注意", "source": "demo", "stage": "扫描"},
        ], self.recorder.calls
        assert self.recorder.messages == [], "有实现时不该再直接落 loguru"

    def test_plugin_context_exposes_bound_console(self) -> None:
        assert isinstance(PluginContext.console, property), "插件上下文应提供 console 属性"
        self._install()

        console_api.console_for("example.model_usage").info("阶段一")
        console_api.console_for().stage("没有来源的阶段")

        assert self.recorder.calls[0]["source"] == "example.model_usage", self.recorder.calls
        assert self.recorder.calls[1] == {
            "level": "info",
            "message": "没有来源的阶段",
            "source": "",
            "stage": "",
        }, self.recorder.calls


if __name__ == "__main__":
    unittest.main()
