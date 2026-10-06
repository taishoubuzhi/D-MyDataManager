"""loguru 日志初始化与全局异常记录。

日志文件模式由配置项 `Log/Mode` 决定，保留策略（文件数量 / 天数 / 单文件上限 /
总大小上限）在每次切分与每次启动时统一执行。
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

from loguru import logger

from . import paths
from .module_data import load_module_data
from ..config import config

_DATA = load_module_data(__file__, "runtime")

_CONFIGURED = False

#: 模式标识是代码约定（配置项、自检与界面多处引用），文案与能力表放在 `runtime.json`
MODE_SINGLE = "single"
MODE_SESSION = "session"
MODE_DAILY = "daily"
MODE_SIZE = "size"

_MODE_SPECS: tuple[dict, ...] = tuple(_DATA.value("log_modes"))

# 模式 -> 界面文案
MODE_LABELS: dict[str, str] = {spec["name"]: spec.get("label", spec["name"]) for spec in _MODE_SPECS}

# 模式 -> 是否需要「单个文件大小上限」
MODE_USES_FILE_SIZE = {spec["name"]: bool(spec.get("uses_file_size")) for spec in _MODE_SPECS}
# 模式 -> 是否需要「保留天数」
MODE_USES_KEEP_DAYS = {spec["name"]: bool(spec.get("uses_keep_days")) for spec in _MODE_SPECS}

LOG_GLOB = _DATA.value("log_glob")

#: 来源字段：所有日志行都带 `extra["source"]`，格式串里用 `{extra[source]}` 取。
SOURCE_EXTRA = "source"
#: 格式串里的来源占位符（loguru 的格式串取 extra 必须写 `{extra[source]}`）
SOURCE_FIELD = "extra[" + SOURCE_EXTRA + "]"
#: 插件模块的前缀（插件按 `dm_plugin.<插件 id>.<模块>` 导入）
PLUGIN_MODULE_PREFIX = "dm_plugin."
#: 统一格式规范：时间 | 级别 | 来源 | 函数:行 | 消息
DEFAULT_FORMAT = _DATA.value("log_format")
_LEGACY_FORMAT_TEXTS: tuple[str, ...] = tuple(_DATA.value("log_legacy_formats"))
#: 旧默认格式：没有来源列，启动时自动升级成 `DEFAULT_FORMAT`
LEGACY_DEFAULT_FORMAT = _LEGACY_FORMAT_TEXTS[0]
#: 中间版本用过的格式：`{source}` 这种写法 loguru 取不到 extra，同样升级掉
LEGACY_SOURCE_FORMAT = _LEGACY_FORMAT_TEXTS[1]
_LEGACY_FORMATS = frozenset(_LEGACY_FORMAT_TEXTS)


def source_of(name: str) -> str:
    """模块名 → 来源列：程序侧留 `app.…`，插件侧去掉 `dm_plugin.` 前缀。

    `__main__`（`src/main.py` 以脚本方式跑）记成 `app`；其余原样保留，
    保证任何一行都能一眼看出「谁发的」。
    """
    text = str(name or "").strip()
    if text.startswith(PLUGIN_MODULE_PREFIX):
        return text[len(PLUGIN_MODULE_PREFIX) :] or "plugin"
    if not text or text == "__main__":
        return "app"
    return text


def active_format() -> str:
    """当前日志格式：历史默认格式自动升级，用户自定义过的格式原样尊重。"""
    text = str(config.logFormat.value or "")
    if not text.strip() or text in _LEGACY_FORMATS:
        return DEFAULT_FORMAT
    return text


def _patch_record(record) -> None:
    """给每条日志补来源列（`logger.bind(source=...)` 显式给过的不覆盖）。"""
    extra = record["extra"]
    if not extra.get(SOURCE_EXTRA):
        extra[SOURCE_EXTRA] = source_of(record["name"])


def _file_name(mode: str) -> str:
    if mode == MODE_SINGLE:
        return "app.log"
    if mode == MODE_DAILY:
        return "{time:YYYY-MM-DD}.log"
    return "app-{time:YYYYMMDD-HHmmss}.log"


def _rotation(mode: str) -> str:
    if mode == MODE_DAILY:
        return "00:00"
    return f"{config.logMaxFileSizeMB.value} MB"


def _select_outdated(files: list[str]) -> list[str]:
    """按当前模式的保留策略挑出应删除的日志文件（最新的一个永不删除）。"""
    entries: list[tuple[Path, float, int]] = []
    for raw in files:
        path = Path(raw)
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path, stat.st_mtime, stat.st_size))
    if len(entries) < 2:
        return []
    entries.sort(key=lambda entry: entry[1])

    mode = str(config.logMode.value)
    protected = {entries[-1][0]}
    doomed: list[Path] = []

    if mode == MODE_DAILY:
        deadline = dt.datetime.now().timestamp() - int(config.logKeepDays.value) * 86400
        for path, mtime, _size in entries:
            if path in protected or mtime >= deadline:
                continue
            doomed.append(path)
            protected.add(path)

    keep_files = max(1, int(config.logKeepFiles.value))
    survivors = [entry for entry in entries if entry[0] not in protected]
    for path, _mtime, _size in survivors[: max(0, len(survivors) - keep_files + 1)]:
        doomed.append(path)
        protected.add(path)

    budget = max(1, int(config.logMaxTotalSizeMB.value)) * 1024 * 1024
    survivors = [entry for entry in entries if entry[0] not in protected]
    total = sum(size for _path, _mtime, size in survivors)
    for path, _mtime, size in survivors:
        if total <= budget:
            break
        total -= size
        doomed.append(path)

    return list(dict.fromkeys(str(path) for path in doomed))


def _retention(files) -> list[str]:
    """loguru 切分时的保留策略回调。"""
    return _select_outdated(list(files))


def _console_stream():
    """控制台流：GBK 控制台写不出 ✔/✗ 时会抛 UnicodeEncodeError，这里把编码错误降级。"""
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    return stream


def _cleanup_logs() -> None:
    """启动时按保留策略清理历史日志（loguru 只在切分时触发）。"""
    for raw in _select_outdated([str(path) for path in paths.LOG_DIR.glob(LOG_GLOB)]):
        Path(raw).unlink(missing_ok=True)


def _add_sink(sink, **kwargs):
    """添加日志输出；enqueue 在受限环境下不可用时自动退化为同步写入。"""
    try:
        return logger.add(sink, **kwargs)
    except Exception as exc:
        kwargs["enqueue"] = False
        print(f"[logging] enqueue 不可用，已退化为同步写入：{exc}", file=sys.stderr)
        return logger.add(sink, **kwargs)


#: 控制台配色：按等级上色（loguru 默认 INFO 只加粗，跟其他等级混在一起容易看错）
LEVEL_COLORS: dict[str, str] = {
    "TRACE": "<cyan>",
    "DEBUG": "<blue>",
    "INFO": "<green>",
    "SUCCESS": "<green><bold>",
    "WARNING": "<yellow>",
    "ERROR": "<red>",
    "CRITICAL": "<red><bold>",
}

#: 控制台格式里要上色的字段 -> 颜色标签（level 与 message 用当前等级的颜色）
FIELD_TAGS: tuple[tuple[str, str], ...] = (
    ("time", "green"),
    ("level", "level"),
    ("extra[source]", "magenta"),
    ("name", "cyan"),
    ("module", "cyan"),
    ("function", "cyan"),
    ("file", "cyan"),
    ("line", "cyan"),
    ("message", "level"),
)


def _tag_field(text: str, name: str, tag: str) -> str:
    """把格式串里的 `{name...}` 字段包进颜色标签。"""
    pattern = re.compile(r"\{" + re.escape(name) + r"[^}]*\}")
    return pattern.sub(lambda match: f"<{tag}>{match.group(0)}</{tag}>", text)


def console_format(template: str) -> str:
    """给控制台格式串加颜色：时间绿色、来源青色、等级与消息按等级上色。

    文件输出用原始格式（`colorize=False` 时 loguru 会自动去掉这些标签）。
    """
    text = template
    for name, tag in FIELD_TAGS:
        text = _tag_field(text, name, tag)
    return text


def _apply_level_colors() -> None:
    """显式指定各等级颜色，避免不同 loguru 版本的默认配色不一致。"""
    for name, color in LEVEL_COLORS.items():
        try:
            logger.level(name, color=color)
        except (TypeError, ValueError):
            continue


def setup_logging() -> None:
    """按配置装配日志输出（控制台 + 文件），可重复调用。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    mode = str(config.logMode.value)
    template = active_format()
    common = dict(
        level=config.logLevel.value,
        format=template,
        backtrace=config.logBacktrace.value,
        diagnose=config.logDiagnose.value,
        enqueue=config.logEnqueue.value,
    )

    logger.remove()
    logger.configure(patcher=_patch_record)
    _apply_level_colors()
    if config.logToConsole.value:
        # 控制台写 stdout：PyCharm 等 IDE 会把 stderr 整体标红，让 INFO 看着像错误
        console = dict(common)
        console["format"] = console_format(template)
        _add_sink(_console_stream(), colorize=True, **console)

    _add_sink(
        paths.LOG_DIR / _file_name(mode),
        rotation=_rotation(mode),
        retention=_retention,
        encoding="utf-8",
        serialize=config.logAsJson.value,
        **dict(common),
    )
    _cleanup_logs()

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.opt(exception=(exc_type, exc_value, exc_tb)).critical("未捕获的异常")

    sys.excepthook = _excepthook
    logger.debug(
        "日志系统已初始化，模式：{}，输出目录：{}", MODE_LABELS.get(mode, mode), paths.LOG_DIR
    )
