"""可选能力探测（D6）：把「装了哪个可选库 / 有没有外部工具」查清楚，缺了也不影响启动。

## 用法

```python
from app.core import capabilities

capabilities.capability("orjson")        # Capability(name, available, kind, version, hint, …)
for item in capabilities.report():       # 全部探测结果（按声明顺序）
    print(item.name, item.available, item.hint)
capabilities.missing()                   # 只差哪些
print(capabilities.summary_text())       # 一行中文摘要（缺几项、缺哪些）
```

## 约定

- 探测**不 import 重型库**：只用 `importlib.util.find_spec()`，版本号走 `importlib.metadata`；
  外部工具（ffmpeg / bsdtar）只查路径。因此 `report()` 可以在启动路径上安全调用。
- 缺依赖是正常状态：每项给出中文提示与可直接复制的 pip 命令（默认清华镜像 + `--no-cache-dir`）。
- 结果缓存：探测一次就记住，`refresh()` 可以在装完包后强制重查。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

__all__ = [
    "PYPI_MIRROR",
    "Capability",
    "capability",
    "configure_tools",
    "install_commands",
    "missing",
    "names",
    "python_packages",
    "refresh",
    "report",
    "summary_text",
]

#: 依赖安装提示里用的镜像（用户偏好清华源）
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
#: Windows 自带的 bsdtar（libarchive），压缩包能力靠它
WINDOWS_TAR = r"C:\Windows\System32\tar.exe"

#: 能力种类
KIND_PYTHON = "python"
KIND_TOOL = "tool"
KIND_STDLIB = "stdlib"


@dataclass(frozen=True)
class Capability:
    """一个可选能力：能不能用、缺什么、怎么装。"""

    name: str
    available: bool
    kind: str
    version: str = ""
    detail: str = ""
    unlocks: str = ""
    fallback: str = ""
    hint: str = ""

    @property
    def label(self) -> str:
        """一行状态：`可用（版本）` / `缺失`。"""
        if not self.available:
            return "缺失"
        return f"可用（{self.version}）" if self.version else "可用"


@dataclass(frozen=True)
class _Spec:
    """探测项：`module` 与 `dist` 是 python 包；`tools` 是外部可执行文件的候选路径。"""

    name: str
    kind: str
    unlocks: str
    module: str = ""
    dist: str = ""
    tools: tuple[str, ...] = ()
    tool_hint: str = ""
    fallback: str = ""


_SPECS: tuple[_Spec, ...] = (
    _Spec("orjson", KIND_PYTHON, "清单 / 配置 JSON 读写快 3–10 倍",
          module="orjson", dist="orjson",
          fallback="退回标准库 json：行为一致，清单与配置的读写明显变慢"),
    _Spec("fastjsonschema", KIND_PYTHON, "清单按 JSON Schema 严格校验",
          module="fastjsonschema", dist="fastjsonschema",
          fallback="只做「必须项 + items 结构」最小校验，不校验字段类型与取值"),
    _Spec("argon2", KIND_PYTHON, "口令散列用 argon2id",
          module="argon2", dist="argon2-cffi",
          fallback="退回 PBKDF2-HMAC-sha256（老散列仍能校验），抗暴力破解弱一些"),
    _Spec("watchdog", KIND_PYTHON, "库文件夹递归监听（子目录变动也能收到）",
          module="watchdog", dist="watchdog",
          fallback="退回 Qt 的 QFileSystemWatcher：只监听已绑定的目录，且有 300 个目录上限"),
    _Spec("puremagic", KIND_PYTHON, "按文件内容嗅探 MIME（改过扩展名也认得出）",
          module="puremagic", dist="puremagic",
          fallback="只按扩展名判断，扩展名被改过的文件认不出真实类型"),
    _Spec("charset_normalizer", KIND_PYTHON, "文本编码探测更准",
          module="charset_normalizer", dist="charset-normalizer",
          fallback="按固定顺序硬试编码（gb18030 / big5 这类仍能试出来，冷门编码可能判错）"),
    _Spec("huggingface_hub", KIND_PYTHON, "按仓库列模型文件（分片 / 成套文件识别更准）",
          module="huggingface_hub", dist="huggingface_hub",
          fallback="退回公开 HTTP API 列文件：大仓库慢一些、更依赖网络"),
    _Spec("pymupdf", KIND_PYTHON, "PDF 正文抽取与页面渲染",
          module="fitz", dist="pymupdf",
          fallback="PDF 不能抽正文、不能渲染页面，该功能直接不可用"),
    _Spec("jieba", KIND_PYTHON, "中文分词，自动关键词质量更高",
          module="jieba", dist="jieba",
          fallback="中文只能按退化的方式切分，自动关键词质量下降"),
    _Spec("usearch", KIND_PYTHON, "本地向量索引（相似图片 / 语义检索）",
          module="usearch", dist="usearch",
          fallback="没有本地向量索引，相似图片 / 语义检索不可用"),
    _Spec("sqlite_vec", KIND_PYTHON, "把向量检索下推到 SQLite（sqlite-vec 扩展）",
          module="sqlite_vec", dist="sqlite-vec",
          fallback="向量检索不能下推到 SQLite，只能取回内存里算（更慢、更占内存）"),
    _Spec("py7zr", KIND_PYTHON, "7z 压缩包直接列举与解包",
          module="py7zr", dist="py7zr",
          fallback="7z 不能直接用 python 库列举，只能交给系统 bsdtar 兜底"),
    _Spec("pyzipper", KIND_PYTHON, "加密 zip 的读取",
          module="pyzipper", dist="pyzipper",
          fallback="加密 zip 读不了（普通 zip 不受影响）"),
    _Spec("rarfile", KIND_PYTHON, "rar 压缩包列举（配合系统 bsdtar）",
          module="rarfile", dist="rarfile",
          fallback="rar 压缩包列不出内容（bsdtar 也帮不上，rarfile 才是入口）"),
    _Spec("hf_transfer", KIND_PYTHON, "HuggingFace 下载提速（大文件多线程）",
          module="hf_transfer", dist="hf-transfer",
          fallback="下载走普通单连接，大文件慢一些（不影响能不能下）"),
    _Spec("zstd", KIND_STDLIB, "标准库 compression.zstd：内容仓库默认压缩编码",
          module="compression.zstd",
          fallback="内容仓库退回 deflate：功能不丢，压缩率差一点（Python 3.14+ 自带，正常不会缺）"),
    _Spec("ffmpeg", KIND_TOOL, "视频抽帧 / 转码（缩略图、向量化前的解码）",
          tools=("ffmpeg", "ffmpeg.exe"), tool_hint="装 ffmpeg 并加进 PATH（或 pip install imageio-ffmpeg）",
          fallback="视频不能抽帧 / 转码，视频缩略图与视频向量化不可用"),
    _Spec("bsdtar", KIND_TOOL, "系统 libarchive：rar / 7z 等压缩包的外部解包器",
          tools=(WINDOWS_TAR, "bsdtar", "bsdtar.exe", "tar", "tar.exe"),
          tool_hint=f"Windows 自带 {WINDOWS_TAR}；其它系统装 libarchive",
          fallback="没有外部解包器，rar / 7z 只能靠 python 库自身支持"),
)

_CACHE: dict[str, Capability] = {}


def _module_available(module: str) -> tuple[bool, str]:
    """模块能不能 import（不真的 import。冻结环境里 find_spec 可能抛异常）。"""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError, AttributeError):
        return False, ""
    if spec is None:
        return False, ""
    origin = str(getattr(spec, "origin", "") or "")
    return True, origin


def _dist_version(dist: str) -> str:
    if not dist:
        return ""
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return ""
    except Exception:  # noqa: BLE001 - 元数据坏了不该影响探测
        return ""


def _tool_path(tools: tuple[str, ...]) -> str:
    for candidate in tools:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def _pip_command(spec: _Spec) -> str:
    return f"pip install {spec.dist or spec.module} -i {PYPI_MIRROR} --no-cache-dir"


def _probe(spec: _Spec) -> Capability:
    if spec.kind == KIND_TOOL:
        path = _tool_path(spec.tools)
        hint = "" if path else f"缺 {spec.name}：{spec.tool_hint}"
        return Capability(
            name=spec.name,
            available=bool(path),
            kind=spec.kind,
            version="",
            detail=path,
            unlocks=spec.unlocks,
            fallback=spec.fallback,
            hint=hint,
        )
    available, origin = _module_available(spec.module)
    version = _dist_version(spec.dist) if available else ""
    hint = "" if available else f"缺 {spec.name}：{_pip_command(spec)}"
    return Capability(
        name=spec.name,
        available=available,
        kind=spec.kind,
        version=version,
        detail=origin,
        unlocks=spec.unlocks,
        fallback=spec.fallback,
        hint=hint,
    )


def configure_tools() -> list[str]:
    """把外部解包器告诉需要的库，返回改过的项（当前只有 rarfile → 系统 bsdtar）。

    `rarfile` 默认找不到 unrar 就没法读 rar5；Windows 自带 libarchive 的 `tar.exe`
    能解 rar，把它指给 rarfile 就不用额外装 unrar。装不上 rarfile 时什么都不做。
    """
    changed: list[str] = []
    if not _module_available("rarfile")[0]:
        return changed
    try:
        import rarfile
    except Exception as exc:  # noqa: BLE001 - 依赖装坏了不该影响启动
        logger.debug("rarfile 载入失败，跳过外部解包器配置：{}", exc)
        return changed
    tar = _tool_path((WINDOWS_TAR, "bsdtar", "bsdtar.exe"))
    if tar and getattr(rarfile, "BSDTAR_TOOL", "") != tar:
        rarfile.BSDTAR_TOOL = tar
        changed.append(f"rarfile.BSDTAR_TOOL={tar}")
    return changed


def capability(name: str) -> Capability:
    """查一个能力；名字不在表里就抛 `KeyError`（列表见 `names()`）。"""
    key = str(name)
    if key in _CACHE:
        return _CACHE[key]
    for spec in _SPECS:
        if spec.name == key:
            found = _probe(spec)
            _CACHE[key] = found
            if key == "rarfile" and found.available:
                configure_tools()
            return found
    raise KeyError(f"没有登记名为 {key!r} 的能力")


def names() -> tuple[str, ...]:
    """全部能力名（声明顺序）。"""
    return tuple(spec.name for spec in _SPECS)


def report() -> tuple[Capability, ...]:
    """全部能力探测结果（缓存）。"""
    return tuple(capability(spec.name) for spec in _SPECS)


def missing() -> tuple[Capability, ...]:
    """缺失的能力。"""
    return tuple(item for item in report() if not item.available)


def python_packages() -> tuple[str, ...]:
    """全部 python 依赖的发行包名（按声明顺序；不含外部工具与标准库）。"""
    return tuple(spec.dist for spec in _SPECS if spec.kind == KIND_PYTHON and spec.dist)


def install_commands() -> list[str]:
    """缺失的 python 包对应的 pip 命令（按声明顺序、去重）。"""
    commands: list[str] = []
    for spec in _SPECS:
        item = _CACHE.get(spec.name) or capability(spec.name)
        if item.available or spec.kind != KIND_PYTHON:
            continue
        command = _pip_command(spec)
        if command not in commands:
            commands.append(command)
    return commands


def summary_text() -> str:
    """一行中文摘要（命令行诊断用）。"""
    items = report()
    gone = [item for item in items if not item.available]
    if not gone:
        return f"{len(items)} 项可选能力全部可用"
    return f"{len(gone)}/{len(items)} 项能力缺失（只是降级、不影响启动）：{'、'.join(item.name for item in gone)}"


def refresh() -> None:
    """清掉缓存，下次重新探测（装完包后调用）。"""
    _CACHE.clear()


def python_executable() -> str:
    """当前解释器路径（安装提示里偶尔要写全路径）。"""
    return sys.executable
