"""设备与依赖探测：给设置页的「推理设备」下拉和「运行环境」状态用。

两条探测都在**子进程**里跑一小段脚本，避免把 torch 这类大库 import 进程序进程：

- `detect_hardware(pythons)` / `detect_devices(pythons)`：按顺序试解释器，返回当前机器真实
  可用的设备（至少含 CPU）。设备名带真实型号：CPU 走注册表 `ProcessorNameString`，显卡走
  `nvidia-smi`、Windows 显示适配器注册表键，再看 torch 到底能不能用它（CPU 版 torch 也会
  把 NVIDIA 显卡列出来，只是标 `usable=False`）；
- `missing_program_packages(packages, python=…)` / `missing_program_group(groups, python=…)`：在程序自己的
  解释器里看 profile 声明的包在不在（先查 pip 发行版元数据，再看 import 名），给「允许把依赖装进
  程序自己的 Python 环境（高级选项）」显示状态用。
  后者一次问多套（一个子进程查完所有 profile），免得十几套运行环境各起一个解释器排成长队。

探测只做「失败得起」的事：起不来 / 看不懂输出都退化成空结果或 `None`，绝不抛给界面。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["detect_devices", "detect_hardware", "import_name", "missing_program_group", "missing_program_packages"]

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIMEOUT = 30.0
_DEVICE_MARK = "__DEVICES__"
_MISSING_MARK = "__MISSING__"

#: 子进程 stdout 强制 UTF-8：中文型号在 GBK 控制台下会变成问号
_PROBE_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

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

#: 设备探测脚本：只用标记行输出 JSON，避免被第三方库的其它打印干扰
_DEVICE_PROBE = """
import json
import platform
import subprocess


def _cpu_name():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0")
        name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        if name:
            return name
    except Exception:
        pass
    return str(platform.processor() or platform.machine() or "CPU").strip() or "CPU"


def _registry_adapters():
    names = []
    try:
        import winreg
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}",
        )
    except Exception:
        return names
    index = 0
    while index < 64:
        try:
            key_name = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        if not key_name.isdigit():
            continue
        try:
            sub = winreg.OpenKey(root, key_name)
            name = str(winreg.QueryValueEx(sub, "DriverDesc")[0]).strip()
        except Exception:
            continue
        if name and name not in names:
            names.append(name)
    return names


def _nvidia_gpus():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    gpus = []
    for line in (result.stdout or "").splitlines():
        parts = [piece.strip() for piece in line.split(",")]
        if parts and parts[0]:
            gpus.append({"name": parts[0], "memory": parts[1] if len(parts) > 1 else ""})
    return gpus


def _torch_info():
    info = {"cuda": False, "count": 0, "names": [], "mps": False}
    try:
        import torch
    except Exception:
        return info
    try:
        info["cuda"] = bool(torch.cuda.is_available())
        info["count"] = int(torch.cuda.device_count()) if info["cuda"] else 0
        for index in range(info["count"]):
            try:
                info["names"].append(str(torch.cuda.get_device_name(index)))
            except Exception:
                info["names"].append("CUDA %d" % index)
    except Exception:
        pass
    try:
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        info["mps"] = bool(mps is not None and mps.is_available())
    except Exception:
        pass
    return info


torch_info = _torch_info()
nvidia = _nvidia_gpus()
adapters = _registry_adapters()
devices = []
others = []
gpu_names = []
for index, gpu in enumerate(nvidia):
    label = gpu["name"]
    gpu_names.append(label)
    memory = gpu.get("memory") or ""
    suffix = ("，" + memory) if memory else ""
    usable = bool(torch_info["cuda"]) and index < torch_info["count"]
    devices.append({"id": "cuda:%d" % index, "name": "%s%s（CUDA %d）" % (label, suffix, index), "usable": usable})
if not nvidia:
    for index in range(torch_info["count"]):
        label = torch_info["names"][index] if index < len(torch_info["names"]) else ("CUDA %d" % index)
        gpu_names.append(label)
        devices.append({"id": "cuda:%d" % index, "name": "%s（CUDA %d）" % (label, index), "usable": True})
if torch_info["mps"]:
    devices.append({"id": "mps", "name": "Apple MPS（Metal）", "usable": True})
devices.append({"id": "cpu", "name": "%s（处理器）" % _cpu_name(), "usable": True})
for name in adapters:
    low = name.lower()
    if any(word in low for word in ("virtual", "basic display", "remote")):
        continue
    if any(low == used.lower() for used in gpu_names):
        continue
    others.append(name)
print("__DEVICES__" + json.dumps({"devices": devices, "others": others}, ensure_ascii=True))
"""

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

#: 批量依赖探测脚本：一个子进程查完所有 profile 声明的包
_MISSING_BATCH_PROBE = _INSTALLED_HELPERS + """
import json

groups = json.loads('__GROUPS__')

report = {}
for key, wanted in groups.items():
    report[key] = [name for name, module in wanted.items() if installed(name, module)]
print("__MISSING__" + json.dumps(report))
"""


def _spec_base(spec: str) -> str:
    """取声明里的 pip 名：`faster-whisper>=1.0` → `faster-whisper`。"""
    return re.split(r"[<>=!~\[;\s]", str(spec or "").strip(), maxsplit=1)[0].strip()


def import_name(spec: str) -> str:
    """把 `faster-whisper>=1.0` 这种声明转成 import 名。"""
    name = _spec_base(spec)
    if not name:
        return ""
    return _IMPORT_NAMES.get(name, name.replace("-", "_").replace(".", "_"))


def _run(python: Path, code: str, *, timeout: float = _TIMEOUT) -> str:
    """跑一段探测脚本，返回 stdout；任何失败返回空串。"""
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_PROBE_ENV,
            creationflags=_CREATE_NO_WINDOW,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _marked(text: str, mark: str) -> Any:
    """取标记行后面的 JSON。"""
    for line in reversed((text or "").splitlines()):
        if line.startswith(mark):
            try:
                return json.loads(line[len(mark):])
            except ValueError:
                return None
    return None


def _candidates(pythons: Sequence[Any] | None) -> list[Path]:
    found: list[Path] = []
    for item in pythons or ():
        try:
            path = Path(str(item))
        except (TypeError, ValueError):
            continue
        if path not in found:
            found.append(path)
    if not found:
        found.append(Path(sys.executable))
    return found


def _clean_devices(payload: Any) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for item in payload or ():
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        key = str(item["id"])
        items.append(
            {
                "id": key,
                "name": str(item.get("name") or key),
                "usable": bool(item.get("usable", True)),
            }
        )
    return tuple(items)


def detect_hardware(pythons: Sequence[Any] | None = None) -> dict[str, Any]:
    """按顺序试解释器，返回 `{"devices", "others", "python"}`。

    - `devices`：`{"id", "name", "usable"}` 列表，id 是后端认的设备串（`auto` 由界面补），
      name 带真实型号，usable 表示当前解释器的 torch 能不能真的用它；
    - `others`：这台机器上装了、但本程序不会拿去推理的显卡（Intel 核显之类）；
    - `python`：报出设备表的解释器路径，全都起不来时为空串。
    """
    for python in _candidates(pythons):
        if not python.exists():
            continue
        payload = _marked(_run(python, _DEVICE_PROBE), _DEVICE_MARK)
        if not isinstance(payload, Mapping):
            continue
        devices = _clean_devices(payload.get("devices"))
        if not devices:
            continue
        others = tuple(str(name) for name in (payload.get("others") or ()) if str(name).strip())
        return {"devices": devices, "others": others, "python": str(python)}
    return {"devices": ({"id": "cpu", "name": "CPU（处理器）", "usable": True},), "others": (), "python": ""}


def detect_devices(pythons: Sequence[Any] | None = None) -> tuple[dict[str, Any], ...]:
    """只要设备表的简写；要拿「本程序用不了的显卡」就调 `detect_hardware`。"""
    return tuple(detect_hardware(pythons)["devices"])


def missing_program_packages(packages: Iterable[Any], *, python: Any = None) -> tuple[str, ...] | None:
    """在解释器里检查这些声明装没装，返回**没装**的那些声明（原样）。

    先按 pip 名查发行版元数据，查不到再看 import 名：`onnxruntime-gpu` 装上了的模块
    其实叫 `onnxruntime`，只看 import 名会把装好的包报成缺。
    返回 `None` 表示探测本身失败（解释器起不来 / 输出看不懂），界面按「未检测」显示。
    """
    pairs: list[tuple[str, str, str]] = []
    for spec in (str(item).strip() for item in (packages or ())):
        base = _spec_base(spec)
        if base:
            pairs.append((spec, base, import_name(spec)))
    if not pairs:
        return ()
    wanted = {base: module for _spec, base, module in pairs}
    target = Path(str(python)) if python is not None else Path(sys.executable)
    code = _MISSING_PROBE.replace("__NAMES__", json.dumps(wanted))
    payload = _marked(_run(target, code), _MISSING_MARK)
    if not isinstance(payload, list):
        return None
    present = {str(item) for item in payload}
    return tuple(spec for spec, base, _module in pairs if base not in present)

def missing_program_group(
    groups: Mapping[str, Iterable[Any]], *, python: Any = None
) -> dict[str, tuple[str, ...]] | None:
    """一次看多套依赖：返回 `{key: (没装的声明, …)}`，`None` 表示探测本身失败。

    `groups` 是 `{key: [包声明, …]}`；所有组共用一个子进程——十几套运行环境各起一个解释器太慢。
    """
    prepared: dict[str, list[tuple[str, str, str]]] = {}
    for key, packages in (groups or {}).items():
        pairs: list[tuple[str, str, str]] = []
        for spec in (str(item).strip() for item in (packages or ())):
            base = _spec_base(spec)
            if base:
                pairs.append((spec, base, import_name(spec)))
        if pairs:
            prepared[str(key)] = pairs
    if not prepared:
        return {}
    wanted = {key: {base: module for _spec, base, module in pairs} for key, pairs in prepared.items()}
    target = Path(str(python)) if python is not None else Path(sys.executable)
    code = _MISSING_BATCH_PROBE.replace("__GROUPS__", json.dumps(wanted))
    payload = _marked(_run(target, code), _MISSING_MARK)
    if not isinstance(payload, Mapping):
        return None
    report: dict[str, tuple[str, ...]] = {}
    for key, pairs in prepared.items():
        present = {str(item) for item in (payload.get(key) or ())}
        report[key] = tuple(spec for spec, base, _module in pairs if base not in present)
    return report
