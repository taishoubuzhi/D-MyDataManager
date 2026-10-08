"""core 通用依赖探测：某个解释器里这些依赖装没装。

从模型工具库里搬出来的部分**只有依赖检查**——设备 / 显卡探测那些跟具体插件的数据模型
绑在一起，留在插件里。这里只做一件事：把包声明（`faster-whisper>=1.0`）拿到目标解释器
里查一遍，回答「哪些没装」。

为什么要起子进程而不是在本进程里 `import`：要查的往往是**别的解释器**（各个运行环境
的 venv），本进程里 `find_spec` 查的是自己那套。子进程只 print 一行 JSON 标记，绕开
第三方库乱七八糟的打印——探测器自己会先装好再输出。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from .engine import CREATE_NO_WINDOW

__all__ = [
    "MISSING_MARK",
    "PROBE_ENV",
    "PROBE_TIMEOUT",
    "import_name",
    "marked",
    "missing_program_group",
    "missing_program_packages",
    "probe_python",
    "run_probe",
    "spec_base",
]

#: 探测结果的标记前缀
MISSING_MARK = "__MISSING__"
#: 单次探测的超时（秒）
PROBE_TIMEOUT = 30.0

#: 子进程 stdout 强制 UTF-8：中文型号在 GBK 控制台下会变成问号
PROBE_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

#: pip 名 → import 名；表里没有的按 `-`/`.` → `_` 猜
_IMPORT_NAMES = {
    "faster-whisper": "faster_whisper",
    "piper-tts": "piper",
    "llama-cpp-python": "llama_cpp",
    "sentence-transformers": "sentence_transformers",
    "rapidocr-onnxruntime": "rapidocr_onnxruntime",
    "Pillow": "PIL",
    "huggingface-hub": "huggingface_hub",
    "onnxruntime-gpu": "onnxruntime",
}

#: 依赖检查的公共片段：先看发行版元数据（pip 名），再看 import 名。
#: 两者经常对不上——`onnxruntime-gpu` 的模块叫 `onnxruntime`、`Pillow` 叫 `PIL`，
#: 而 `nvidia-*` 这类轮子干脆不给顶层模块；只按 import 名查会误报「缺」。
_INSTALLED_HELPERS = """
import importlib.metadata
import importlib.util


def has_dist(name):
    try:
        importlib.metadata.version(name)
        return True
    except Exception:
        return False


def has_module(name):
    if not name:
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def installed(name, module):
    return has_dist(name) or has_module(module)
"""

#: 依赖探测脚本：报告哪些声明（pip 名）已经装上了
_MISSING_PROBE = _INSTALLED_HELPERS + """
import json

wanted = json.loads('__NAMES__')

present = [name for name, module in wanted.items() if installed(name, module)]
print("__MISSING__" + json.dumps(present))
"""

#: 批量依赖探测脚本：一个子进程查完所有分组的声明
_MISSING_BATCH_PROBE = _INSTALLED_HELPERS + """
import json

groups = json.loads('__GROUPS__')

report = {}
for key, wanted in groups.items():
    report[key] = [name for name, module in wanted.items() if installed(name, module)]
print("__MISSING__" + json.dumps(report))
"""


def spec_base(spec: str) -> str:
    """取声明里的 pip 名：`faster-whisper>=1.0` → `faster-whisper`。"""
    return re.split(r"[<>=!~\[;\s]", str(spec or "").strip(), maxsplit=1)[0].strip()


def import_name(spec: str) -> str:
    """把 `faster-whisper>=1.0` 这种声明转成 import 名。"""
    name = spec_base(spec)
    if not name:
        return ""
    return _IMPORT_NAMES.get(name, name.replace("-", "_").replace(".", "_"))


def probe_python(python: Any = None) -> Path:
    """要用哪个解释器去查；不给就是当前进程这个。"""
    return Path(str(python)) if python is not None else Path(sys.executable)


def run_probe(python: Path, code: str, *, timeout: float = PROBE_TIMEOUT) -> str:
    """跑一段探测脚本，返回 stdout；任何失败返回空串。"""
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=PROBE_ENV,
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def marked(body: str, mark: str = MISSING_MARK) -> Any:
    """取标记行后面的 JSON。"""
    for line in reversed((body or "").splitlines()):
        if line.startswith(mark):
            try:
                return json.loads(line[len(mark):])
            except ValueError:
                return None
    return None


def missing_program_packages(
    packages: Iterable[Any], *, python: Any = None
) -> tuple[str, ...] | None:
    """在解释器里检查这些声明装没装，返回**没装**的那些声明（原样）。

    先按 pip 名查发行版元数据，查不到再看 import 名：`onnxruntime-gpu` 装上了的模块
    其实叫 `onnxruntime`，只看 import 名会把装好的包报成缺。
    返回 `None` 表示探测本身失败（解释器起不来 / 输出看不懂），界面按「未检测」显示。
    """
    pairs: list[tuple[str, str, str]] = []
    for spec in (str(item).strip() for item in (packages or ())):
        base = spec_base(spec)
        if base:
            pairs.append((spec, base, import_name(spec)))
    if not pairs:
        return ()
    wanted = {base: module for _spec, base, module in pairs}
    code = _MISSING_PROBE.replace("__NAMES__", json.dumps(wanted))
    payload = marked(run_probe(probe_python(python), code))
    if not isinstance(payload, list):
        return None
    present = {str(item) for item in payload}
    return tuple(spec for spec, base, _module in pairs if base not in present)


def missing_program_group(
    groups: Mapping[str, Iterable[Any]], *, python: Any = None
) -> dict[str, tuple[str, ...]] | None:
    """一次看多套依赖：返回 `{key: (没装的声明, …)}`，`None` 表示探测本身失败。

    `groups` 是 `{key: [包声明, …]}`；所有组共用一个子进程——十几套运行环境各起一个
    解释器太慢。
    """
    prepared: dict[str, list[tuple[str, str, str]]] = {}
    for key, packages in (groups or {}).items():
        pairs: list[tuple[str, str, str]] = []
        for spec in (str(item).strip() for item in (packages or ())):
            base = spec_base(spec)
            if base:
                pairs.append((spec, base, import_name(spec)))
        if pairs:
            prepared[str(key)] = pairs
    if not prepared:
        return {}
    wanted = {key: {base: module for _spec, base, module in pairs} for key, pairs in prepared.items()}
    code = _MISSING_BATCH_PROBE.replace("__GROUPS__", json.dumps(wanted))
    payload = marked(run_probe(probe_python(python), code))
    if not isinstance(payload, Mapping):
        return None
    report: dict[str, tuple[str, ...]] = {}
    for key, pairs in prepared.items():
        present = {str(item) for item in (payload.get(key) or ())}
        report[key] = tuple(spec for spec, base, _module in pairs if base not in present)
    return report
