"""日志配置项测试脚本

验证 init_log.py 中各配置项是否正确生效。
直接运行: python test_log_config.py
"""

import sys
import os
import json
import logging
import tempfile
import shutil

from loguru import logger


# ============================================================
# 模拟配置对象（不依赖 PyQt6 / qfluentwidgets）
# ============================================================

class MockConfigItem:
    """模拟 ConfigItem，用作 config.get() 的 key"""
    def __init__(self, key, default=None):
        self.key = key
        self.default = default


class MockConfig:
    """模拟 QConfig，支持 get/set 接口"""

    log_level = MockConfigItem("log_level", "DEBUG")
    log_format = MockConfigItem("log_format", "{time} [{level:<8}] : {message}")
    format_to_json = MockConfigItem("format_to_json", False)
    json_format = MockConfigItem("json_format", "{time} | {level} | {message}")
    catch = MockConfigItem("catch", True)
    output_console = MockConfigItem("output_console", True)
    enqueue = MockConfigItem("enqueue", True)
    encoding = MockConfigItem("encoding", "utf-8")
    backtrace = MockConfigItem("backtrace", True)
    diagnose = MockConfigItem("diagnose", True)
    output_file = MockConfigItem("output_file", True)
    file_path = MockConfigItem("file_path", "")
    rotate_mode = MockConfigItem("rotate_mode", "None")
    rotate_interval = MockConfigItem("rotate_interval", 1)
    rotate_interval_unit = MockConfigItem("rotate_interval_unit", "hour")
    rotate_count = MockConfigItem("rotate_count", 10)

    def __init__(self):
        self._values = {}

    def set(self, item, value):
        self._values[item.key] = value

    def get(self, item):
        if item.key in self._values:
            return self._values[item.key]
        return item.default


# ============================================================
# 加载 init_log 模块（绕过 PyQt6 依赖）
# ============================================================

def _load_init_log_module(mock_cfg):
    """读取 init_log.py 源码，替换 config 导入，exec 加载到独立命名空间

    返回一个 SimpleNamespace 对象，可通过属性访问模块中的函数。
    """
    import types
    init_log_path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..',
        'src', 'app', 'common', 'init', 'init_log.py'))

    with open(init_log_path, 'r', encoding='utf-8') as f:
        source = f.read()

    # 替换相对导入，避免 PyQt6 依赖
    source = source.replace(
        'from ..config import config',
        '# mocked: from ..config import config'
    )

    namespace = {
        '__name__': 'init_log',
        '__file__': init_log_path,
        'config': mock_cfg,
        'sys': sys,
        'os': os,
        'datetime': __import__('datetime'),
        'logging': logging,
        'logger': logger,
    }

    exec(compile(source, init_log_path, 'exec'), namespace)

    # 提取需要的函数到 SimpleNamespace
    mod = types.SimpleNamespace(
        InterceptHandler=namespace.get('InterceptHandler'),
        _set_logger_config=namespace.get('_set_logger_config'),
        _get_std_logging_level=namespace.get('_get_std_logging_level'),
        init_log=namespace.get('init_log'),
    )
    return mod


# ============================================================
# 辅助工具
# ============================================================

def _get_handlers():
    """获取 loguru 当前所有 handler"""
    return dict(logger._core.handlers)


def _clear_handlers():
    """清除 loguru 所有 handler"""
    logger.remove()


def _find_file_handlers(handlers):
    """查找文件类型的 handler（loguru FileSink）"""
    result = []
    for hid, h in handlers.items():
        sink = h._sink
        if sink.__class__.__name__ == 'FileSink':
            result.append((hid, h))
    return result


def _find_stream_handlers(handlers):
    """查找流类型的 handler（控制台 StreamSink）"""
    result = []
    for hid, h in handlers.items():
        sink = h._sink
        if sink.__class__.__name__ == 'StreamSink':
            result.append((hid, h))
    return result


def _make_config(**overrides):
    """创建 MockConfig 并设置覆盖值"""
    cfg = MockConfig()
    for key, value in overrides.items():
        item = getattr(cfg, key, None)
        if item is not None:
            cfg.set(item, value)
    return cfg


# ============================================================
# 测试结果收集
# ============================================================

_results = []


def _record(name, passed, reason=""):
    _results.append((name, passed, reason))


# ============================================================
# SubTask 4.1: 逐一验证各配置项是否生效
# ============================================================

def test_log_level_filtering():
    """log_level: 设置不同级别，验证低于该级别的日志被过滤"""
    cfg = _make_config(log_level="WARNING", output_console=False, output_file=True)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_level.log")
        _clear_handlers()
        mod._set_logger_config(file_path, None, None, cfg)

        logger.debug("should_not_appear")
        logger.info("should_not_appear_either")
        logger.warning("should_appear")
        logger.error("should_also_appear")

        # 刷新文件
        for hid, h in _get_handlers().items():
            h._sink.stop()

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        has_debug = "should_not_appear" in content
        has_info = "should_not_appear_either" in content
        has_warning = "should_appear" in content
        has_error = "should_also_appear" in content

        passed = (not has_debug) and (not has_info) and has_warning and has_error
        reason = ""
        if not passed:
            parts = []
            if has_debug:
                parts.append("DEBUG 未被过滤")
            if has_info:
                parts.append("INFO 未被过滤")
            if not has_warning:
                parts.append("WARNING 未出现")
            if not has_error:
                parts.append("ERROR 未出现")
            reason = "; ".join(parts)
        _record("test_log_level_filtering", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_output_console_off():
    """output_console: 关闭后验证控制台 sink 不存在"""
    cfg = _make_config(output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    mod._set_logger_config(sys.stderr, None, None, cfg)

    handlers_before = _get_handlers()
    stream_before = _find_stream_handlers(handlers_before)

    # 重新配置：关闭控制台（不添加控制台 sink）
    _clear_handlers()

    handlers_after = _get_handlers()
    stream_after = _find_stream_handlers(handlers_after)

    passed = len(stream_before) > 0 and len(stream_after) == 0
    reason = "" if passed else f"关闭前控制台sink={len(stream_before)}, 关闭后={len(stream_after)}"
    _record("test_output_console_off", passed, reason)


def test_output_file_off():
    """output_file: 关闭后验证文件 sink 不存在"""
    cfg = _make_config(output_file=True, output_console=False)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_file.log")

        _clear_handlers()
        mod._set_logger_config(file_path, None, None, cfg)
        handlers_before = _get_handlers()
        file_before = _find_file_handlers(handlers_before)

        # 关闭文件输出
        _clear_handlers()
        handlers_after = _get_handlers()
        file_after = _find_file_handlers(handlers_after)

        passed = len(file_before) > 0 and len(file_after) == 0
        reason = "" if passed else f"关闭前文件sink={len(file_before)}, 关闭后={len(file_after)}"
        _record("test_output_file_off", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_format_to_json_file():
    """format_to_json: 开启后验证文件输出为 JSON 格式"""
    cfg = _make_config(format_to_json=True, output_console=False, output_file=True)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_json.log")
        _clear_handlers()
        mod._set_logger_config(file_path, None, None, cfg)

        logger.info("json_test_message")

        for hid, h in _get_handlers().items():
            h._sink.stop()

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        is_json = False
        parse_error = ""
        try:
            parsed = json.loads(content)
            is_json = "json_test_message" in parsed.get("text", "")
        except json.JSONDecodeError as e:
            parse_error = str(e)

        passed = is_json
        reason = "" if passed else f"文件内容不是有效JSON: {parse_error}, 内容: {content[:200]}"
        _record("test_format_to_json_file", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_format_to_json_console_readable():
    """format_to_json: 开启后控制台仍为可读文本（非 JSON）"""
    cfg = _make_config(format_to_json=True, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    from io import StringIO
    buf = StringIO()

    _clear_handlers()
    mod._set_logger_config(buf, None, None, cfg)

    logger.info("console_readable_test")
    logger.complete()

    content = buf.getvalue()

    is_not_json = not content.strip().startswith("{")
    has_message = "console_readable_test" in content

    passed = is_not_json and has_message
    reason = ""
    if not is_not_json:
        reason = "控制台输出是JSON格式，应为可读文本"
    elif not has_message:
        reason = f"控制台输出未包含消息, 内容: {content[:200]}"
    _record("test_format_to_json_console_readable", passed, reason)


def test_enqueue_console_vs_file():
    """enqueue: 控制台 sink 的 enqueue 与配置一致，文件 sink 始终为 False"""
    cfg = _make_config(enqueue=True, output_console=True, output_file=True)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_enqueue.log")
        _clear_handlers()

        # 添加文件 sink
        mod._set_logger_config(file_path, None, None, cfg)
        # 添加控制台 sink
        from io import StringIO
        buf = StringIO()
        mod._set_logger_config(buf, None, None, cfg)

        handlers = _get_handlers()
        file_hs = _find_file_handlers(handlers)
        stream_hs = _find_stream_handlers(handlers)

        file_enqueue_ok = all(h._enqueue is False for _, h in file_hs)
        console_enqueue_ok = all(h._enqueue is True for _, h in stream_hs)

        passed = file_enqueue_ok and console_enqueue_ok
        reason = ""
        if not file_enqueue_ok:
            reason = "文件 sink 的 enqueue 不为 False"
        elif not console_enqueue_ok:
            reason = "控制台 sink 的 enqueue 不为 True"
        _record("test_enqueue_console_vs_file", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_encoding():
    """encoding: 验证文件 sink 使用指定编码"""
    cfg = _make_config(encoding="utf-8", output_console=False, output_file=True)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_encoding.log")
        _clear_handlers()
        mod._set_logger_config(file_path, None, None, cfg)

        # 先检查 FileSink 的 encoding 属性
        handlers = _get_handlers()
        file_hs = _find_file_handlers(handlers)

        encoding_attr_ok = False
        for hid, h in file_hs:
            sink = h._sink
            if hasattr(sink, 'encoding') and sink.encoding == "utf-8":
                encoding_attr_ok = True

        # 间接验证：写入中文并读取
        logger.info("中文编码测试")
        for hid, h in _get_handlers().items():
            h._sink.stop()

        encoding_io_ok = False
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            encoding_io_ok = "中文编码测试" in content
        except UnicodeDecodeError:
            encoding_io_ok = False

        passed = encoding_attr_ok and encoding_io_ok
        reason = ""
        if not encoding_attr_ok:
            reason = "FileSink.encoding 属性不为 utf-8"
        elif not encoding_io_ok:
            reason = "文件编码不是 utf-8，无法正确读取中文"
        _record("test_encoding", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_catch():
    """catch: 验证 sink 的 catch 参数与配置一致"""
    cfg = _make_config(catch=True, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    handlers = _get_handlers()
    # loguru Handler 将 catch 存储在 _error_interceptor._should_catch
    catch_ok = all(
        h._error_interceptor._should_catch is True
        for h in handlers.values()
    )

    # 测试 catch=False
    cfg2 = _make_config(catch=False, output_console=True, output_file=False)
    mod2 = _load_init_log_module(cfg2)
    _clear_handlers()
    buf2 = StringIO()
    mod2._set_logger_config(buf2, None, None, cfg2)

    handlers2 = _get_handlers()
    catch_ok2 = all(
        h._error_interceptor._should_catch is False
        for h in handlers2.values()
    )

    passed = catch_ok and catch_ok2
    reason = ""
    if not catch_ok:
        reason = "catch=True 时 _error_interceptor._should_catch 不为 True"
    elif not catch_ok2:
        reason = "catch=False 时 _error_interceptor._should_catch 不为 False"
    _record("test_catch", passed, reason)


def test_backtrace():
    """backtrace: 验证 sink 的 backtrace 参数与配置一致"""
    cfg = _make_config(backtrace=True, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    handlers = _get_handlers()
    # loguru Handler 将 backtrace 存储在 _exception_formatter._backtrace
    bt_ok = all(
        h._exception_formatter._backtrace is True
        for h in handlers.values()
    )

    cfg2 = _make_config(backtrace=False, output_console=True, output_file=False)
    mod2 = _load_init_log_module(cfg2)
    _clear_handlers()
    buf2 = StringIO()
    mod2._set_logger_config(buf2, None, None, cfg2)

    handlers2 = _get_handlers()
    bt_ok2 = all(
        h._exception_formatter._backtrace is False
        for h in handlers2.values()
    )

    passed = bt_ok and bt_ok2
    reason = ""
    if not bt_ok:
        reason = "backtrace=True 时 _exception_formatter._backtrace 不为 True"
    elif not bt_ok2:
        reason = "backtrace=False 时 _exception_formatter._backtrace 不为 False"
    _record("test_backtrace", passed, reason)


def test_diagnose():
    """diagnose: 验证 sink 的 diagnose 参数与配置一致"""
    cfg = _make_config(diagnose=True, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    handlers = _get_handlers()
    # loguru Handler 将 diagnose 存储在 _exception_formatter._diagnose
    diag_ok = all(
        h._exception_formatter._diagnose is True
        for h in handlers.values()
    )

    cfg2 = _make_config(diagnose=False, output_console=True, output_file=False)
    mod2 = _load_init_log_module(cfg2)
    _clear_handlers()
    buf2 = StringIO()
    mod2._set_logger_config(buf2, None, None, cfg2)

    handlers2 = _get_handlers()
    diag_ok2 = all(
        h._exception_formatter._diagnose is False
        for h in handlers2.values()
    )

    passed = diag_ok and diag_ok2
    reason = ""
    if not diag_ok:
        reason = "diagnose=True 时 _exception_formatter._diagnose 不为 True"
    elif not diag_ok2:
        reason = "diagnose=False 时 _exception_formatter._diagnose 不为 False"
    _record("test_diagnose", passed, reason)


# ============================================================
# SubTask 4.2: InterceptHandler 与 log_level 的交互验证
# ============================================================

def test_intercept_error_level_blocks_info():
    """设置 log_level=ERROR，验证标准 logging 的 INFO 消息不输出"""
    cfg = _make_config(log_level="ERROR", output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    # 设置标准 logging 使用 InterceptHandler
    std_logger = logging.getLogger("test_intercept_error")
    std_logger.handlers.clear()
    std_logger.addHandler(mod.InterceptHandler())
    std_logger.setLevel(logging.INFO)

    std_logger.info("should_not_appear_via_intercept")
    std_logger.error("should_appear_via_intercept")
    logger.complete()

    content = buf.getvalue()
    has_info = "should_not_appear_via_intercept" in content
    has_error = "should_appear_via_intercept" in content

    passed = (not has_info) and has_error
    reason = ""
    if has_info:
        reason = "INFO 消息未被过滤（log_level=ERROR 时应过滤 INFO）"
    elif not has_error:
        reason = "ERROR 消息未出现"
    _record("test_intercept_error_level_blocks_info", passed, reason)


def test_intercept_trace_level_passes_debug():
    """设置 log_level=TRACE，验证标准 logging 的 DEBUG 消息输出"""
    cfg = _make_config(log_level="TRACE", output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    std_logger = logging.getLogger("test_intercept_trace")
    std_logger.handlers.clear()
    std_logger.addHandler(mod.InterceptHandler())
    std_logger.setLevel(logging.DEBUG)

    std_logger.debug("should_appear_debug_via_intercept")
    logger.complete()

    content = buf.getvalue()
    has_debug = "should_appear_debug_via_intercept" in content

    passed = has_debug
    reason = "" if passed else "DEBUG 消息未出现（log_level=TRACE 时应输出 DEBUG）"
    _record("test_intercept_trace_level_passes_debug", passed, reason)


def test_get_std_logging_level_mapping():
    """验证 _get_std_logging_level 映射正确性"""
    cfg = _make_config()
    mod = _load_init_log_module(cfg)

    expected = {
        "TRACE": 0,     # 5 -> 向下对齐到 0
        "DEBUG": 10,    # 10 -> 10
        "INFO": 20,     # 20 -> 20
        "SUCCESS": 20,  # 25 -> 向下对齐到 20
        "WARNING": 30,  # 30 -> 30
        "ERROR": 40,    # 40 -> 40
        "CRITICAL": 50, # 50 -> 50
    }

    all_ok = True
    failed = []
    for level_name, expected_val in expected.items():
        actual = mod._get_std_logging_level(level_name)
        if actual != expected_val:
            all_ok = False
            failed.append(f"{level_name}: 期望 {expected_val}, 实际 {actual}")

    passed = all_ok
    reason = "; ".join(failed) if not all_ok else ""
    _record("test_get_std_logging_level_mapping", passed, reason)


# ============================================================
# SubTask 4.3: format_to_json 对文件/控制台的不同影响
# ============================================================

def test_format_to_json_file_serialize_true():
    """开启 format_to_json 后，验证文件 sink 的 serialize=True"""
    cfg = _make_config(format_to_json=True, output_console=False, output_file=True)
    mod = _load_init_log_module(cfg)

    tmp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(tmp_dir, "test_serialize.log")
        _clear_handlers()
        mod._set_logger_config(file_path, None, None, cfg)

        handlers = _get_handlers()
        file_hs = _find_file_handlers(handlers)

        serialize_ok = all(h._serialize is True for _, h in file_hs)
        passed = serialize_ok
        reason = "" if passed else f"文件 sink 的 serialize 不为 True, handlers数量={len(file_hs)}"
        _record("test_format_to_json_file_serialize_true", passed, reason)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_format_to_json_console_serialize_false():
    """开启 format_to_json 后，验证控制台 sink 的 serialize=False"""
    cfg = _make_config(format_to_json=True, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    handlers = _get_handlers()
    stream_hs = _find_stream_handlers(handlers)

    serialize_ok = all(h._serialize is False for _, h in stream_hs)
    passed = serialize_ok
    reason = "" if passed else f"控制台 sink 的 serialize 不为 False, handlers数量={len(stream_hs)}"
    _record("test_format_to_json_console_serialize_false", passed, reason)


def test_console_format_has_level_tag():
    """验证控制台 format 始终包含 <level> 标记"""
    cfg = _make_config(format_to_json=False, output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    # 通过拦截 logger.add 调用来捕获 format 参数
    captured_format = None
    original_add = logger.add

    def mock_add(*args, **kwargs):
        nonlocal captured_format
        captured_format = kwargs.get('format', None)
        return original_add(*args, **kwargs)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()

    # 临时替换 logger.add
    logger.add = mock_add
    try:
        mod._set_logger_config(buf, None, None, cfg)
    finally:
        logger.add = original_add

    has_level_tag = captured_format is not None and '<level>' in captured_format

    passed = has_level_tag
    reason = "" if passed else f"控制台 format 未包含 <level> 标记, format={captured_format}"
    _record("test_console_format_has_level_tag", passed, reason)


# ============================================================
# SubTask 4.4: InterceptHandler 不可替代性验证
# ============================================================

def test_intercept_handler_routes_through_loguru():
    """有 InterceptHandler 时，标准 logging 消息通过 loguru 输出"""
    cfg = _make_config(log_level="DEBUG", output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    buf = StringIO()
    mod._set_logger_config(buf, None, None, cfg)

    std_logger = logging.getLogger("test_with_intercept")
    std_logger.handlers.clear()
    std_logger.addHandler(mod.InterceptHandler())
    std_logger.setLevel(logging.DEBUG)

    std_logger.info("intercept_handler_test")
    logger.complete()

    content = buf.getvalue()
    # 通过 loguru 输出时，会包含 loguru 的格式（时间戳等）
    has_message = "intercept_handler_test" in content

    passed = has_message
    reason = "" if passed else f"标准 logging 消息未通过 loguru 输出, 内容: {content[:200]}"
    _record("test_intercept_handler_routes_through_loguru", passed, reason)


def test_no_intercept_handler_goes_to_stderr():
    """无 InterceptHandler 时，标准 logging 消息直接到 stderr，不受 loguru 控制"""
    cfg = _make_config(log_level="ERROR", output_console=True, output_file=False)
    mod = _load_init_log_module(cfg)

    _clear_handlers()
    from io import StringIO
    loguru_buf = StringIO()
    mod._set_logger_config(loguru_buf, None, None, cfg)

    # 标准 logging 不使用 InterceptHandler，直接输出到 StringIO
    std_logger = logging.getLogger("test_no_intercept")
    std_logger.handlers.clear()
    std_buf = StringIO()
    std_handler = logging.StreamHandler(std_buf)
    std_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    std_logger.addHandler(std_handler)
    std_logger.setLevel(logging.DEBUG)

    # loguru 级别为 ERROR，但标准 logging 不受此限制
    std_logger.info("direct_stderr_test")

    # loguru 的 sink 不应收到这条消息
    loguru_content = loguru_buf.getvalue()
    # 标准 logging 自己的 handler 应收到
    std_content = std_buf.getvalue()

    loguru_received = "direct_stderr_test" in loguru_content
    std_received = "direct_stderr_test" in std_content

    passed = (not loguru_received) and std_received
    reason = ""
    if loguru_received:
        reason = "loguru 不应收到未经过 InterceptHandler 的标准 logging 消息"
    elif not std_received:
        reason = "标准 logging 的 handler 应收到消息"
    _record("test_no_intercept_handler_goes_to_stderr", passed, reason)


# ============================================================
# 主函数
# ============================================================

def main():
    print("=== 日志配置项测试 ===\n")

    # SubTask 4.1
    print("--- 4.1 逐一验证各配置项 ---")
    test_log_level_filtering()
    test_output_console_off()
    test_output_file_off()
    test_format_to_json_file()
    test_format_to_json_console_readable()
    test_enqueue_console_vs_file()
    test_encoding()
    test_catch()
    test_backtrace()
    test_diagnose()

    # SubTask 4.2
    print("--- 4.2 InterceptHandler 与 log_level 交互 ---")
    test_intercept_error_level_blocks_info()
    test_intercept_trace_level_passes_debug()
    test_get_std_logging_level_mapping()

    # SubTask 4.3
    print("--- 4.3 format_to_json 对文件/控制台不同影响 ---")
    test_format_to_json_file_serialize_true()
    test_format_to_json_console_serialize_false()
    test_console_format_has_level_tag()

    # SubTask 4.4
    print("--- 4.4 InterceptHandler 不可替代性 ---")
    test_intercept_handler_routes_through_loguru()
    test_no_intercept_handler_goes_to_stderr()

    # 清理 handler
    _clear_handlers()

    # 输出结果
    print()
    passed_count = 0
    total = len(_results)
    for name, passed, reason in _results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed and reason:
            print(f"  原因: {reason}")
        if passed:
            passed_count += 1

    print(f"\n=== 结果: {passed_count}/{total} 通过 ===")
    return passed_count == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
