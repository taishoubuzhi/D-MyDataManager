"""运行环境（runtime）：每个 profile 一套独立 venv，按需创建与安装依赖。

设计约束（见 MODEL_PLUGIN.md §6）：

- venv 落在 `.resources/models/runtime/<profile>/venv`，随「资源文件夹」设置一起移动；
- `ensure()` 只负责**执行**安装，是否安装由页面弹确认后决定，这里绝不静默安装；
- profile 清单来自插件 `data/runtime_profiles.json`（页面通过 `ctx.data("runtime_profiles")` 读）；
  没有 ctx 时（测试 / 脚本）直接读插件目录里的同名文件，文件不存在就返回空元组。

public API：`profiles / profile_of / venv_dir / python_path / installed / marker_path /
requirements_path / ensure / ensure_system / discard / system_python / system_requirements_path /
system_log_file / uninstall / package_versions / log_file / github_asset_urls / mirror_github_urls`。

`ensure()` / `ensure_system()` 都接受 `should_cancel` 与 `should_pause` 两个回调：页面上的
「取消」和「暂停」各自置位，安装线程每读到一行 pip 输出就查一次，命中就收掉 pip 子进程并抛
`RuntimeStopped`（`paused=True` 表示暂停，缓存与半成品保留，再调一次即可续装）。

`ensure()` 把依赖装进 profile 自己的 venv；`ensure_system()` 是 P4 的「程序环境安装模式」：
把同样的清单装进**程序自己的解释器**（`sys.executable`），由页面在二次确认后调用。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..paths import logs_dir, runtime_root

__all__ = [
    "Control",
    "RuntimeError_",
    "RuntimeProfile",
    "RuntimeStopped",
    "discard",
    "clear_logs",
    "clear_pending",
    "check_wheels",
    "ensure",
    "ensure_system",
    "github_asset_urls",
    "has_dir",
    "installed",
    "installed_ids",
    "install_wheels",
    "interrupted",
    "log_file",
    "marker_path",
    "mirror_github_urls",
    "package_versions",
    "pending_path",
    "profile_of",
    "profiles",
    "python_path",
    "requirements_path",
    "resolve_id",
    "system_log_file",
    "system_python",
    "system_requirements_path",
    "twin_ids",
    "uninstall",
    "uninstall_system",
    "venv_dir",
]

#: Windows 上不弹控制台窗口（照 `src/app/core/acl.py:22`）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: 后台「盯暂停 / 取消」的轮询间隔（秒）：只给老式的 should_cancel/should_pause 回调用。
#: 传了 `Control` 的调用方不走轮询——它们直接收掉子进程（见 `Control.cancel()`）。
_STOP_POLL_SEC = 0.2

#: 插件自带的 profile 清单；没有 ctx 时的兜底路径
_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "runtime_profiles.json"


class RuntimeError_(RuntimeError):
    """运行环境自己的错误：建 venv 失败、pip 装失败、安装被取消。

    名字带下划线是为了不和内置 `RuntimeError` 混淆（调用方常同时用到两者）。
    """


class RuntimeStopped(RuntimeError_):
    """用户中途叫停了安装：pip 子进程已终止并等干净。

    `paused=True` 表示只是暂停——venv 半成品、pip 缓存和日志都留着，
    再调一次 `ensure()` 就是接着装；`paused=False` 表示取消，调用方应清掉半成品。
    """

    def __init__(self, message: str, *, paused: bool = False) -> None:
        super().__init__(message)
        self.paused = bool(paused)


@dataclass
class RuntimeProfile:
    """一个运行环境 profile：装哪些包、用哪个解释器、支持哪些后端。"""

    id: str
    name: str = ""
    description: str = ""
    #: pip 包清单，例如 `("llama-cpp-python>=0.3",)`
    packages: tuple[str, ...] = ()
    #: 主索引地址；空 = 用 PyPI（或 pip 自己的默认）
    index_url: str = ""
    #: 额外索引（镜像）
    extra_index: tuple[str, ...] = ()
    #: 基础解释器路径；空 = 用程序的 `sys.executable`
    python: str = ""
    #: 体积提示，例如 `约 400 MB`
    size_hint: str = ""
    #: 该 profile 支持的 backend 名
    backends: tuple[str, ...] = ()


# ------------------------------------------------------------------ 解析


def _text(value: Any) -> str:
    return str(value or "").strip()


def _items(value: Any) -> tuple[str, ...]:
    """把清单字段统一成非空字符串元组（兼容字符串 / 列表 / 逗号分隔）。"""
    if value is None:
        return ()
    if isinstance(value, str):
        raw = [part.strip() for part in value.replace(",", "\n").splitlines()]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item).strip() for item in value]
    else:
        return ()
    return tuple(item for item in raw if item)


def _parse_profile(data: Mapping[str, Any]) -> RuntimeProfile | None:
    """把一条 JSON 记录解析成 profile；没有 id 的条目直接丢弃。"""
    profile_id = _text(data.get("id"))
    if not profile_id:
        return None
    extra = data.get("extra_index")
    if extra is None:
        extra = data.get("mirrors")
    return RuntimeProfile(
        id=profile_id,
        name=_text(data.get("name")) or profile_id,
        description=_text(data.get("description") or data.get("note")),
        packages=_items(data.get("packages")),
        index_url=_text(data.get("index_url") or data.get("index")),
        extra_index=_items(extra),
        python=_text(data.get("python")),
        size_hint=_text(data.get("size_hint")),
        backends=_items(data.get("backends")),
    )


def _payload(ctx: Any = None) -> Any:
    """读 profile 清单：优先 ctx，其次插件目录里的同名 JSON 文件。"""
    if ctx is not None:
        try:
            data = ctx.data("runtime_profiles")
        except Exception:
            data = None
        if isinstance(data, Mapping):
            return data
    try:
        text = _DATA_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {}


def profiles(ctx: Any = None) -> tuple[RuntimeProfile, ...]:
    """读全部运行环境 profile；文件不存在或格式不对时返回空元组。"""
    data = _payload(ctx)
    if isinstance(data, Mapping):
        raw = data.get("profiles")
    else:
        raw = data
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[RuntimeProfile] = []
    for item in raw:
        if isinstance(item, Mapping):
            profile = _parse_profile(item)
            if profile is not None:
                result.append(profile)
    return tuple(result)


def profile_of(profile_id: str, ctx: Any = None) -> RuntimeProfile | None:
    """按 id 取一个 profile；没有就返回 None。"""
    key = _text(profile_id)
    for item in profiles(ctx):
        if item.id == key:
            return item
    return None


# ------------------------------------------------------------------ 路径


def venv_dir(profile_id: str) -> Path:
    """某个 profile 的 venv 目录。"""
    return runtime_root() / _text(profile_id) / "venv"


def python_path(profile_id: str) -> Path:
    """venv 里的解释器：Windows 是 `Scripts/python.exe`，其它平台是 `bin/python`。"""
    folder = "Scripts" if sys.platform.startswith("win") else "bin"
    name = "python.exe" if sys.platform.startswith("win") else "python"
    return venv_dir(profile_id) / folder / name


def twin_ids(profile_id: str) -> tuple[str, ...]:
    """同一个后端的两套环境：`llama-cpp` ↔ `llama-cpp-gpu`。"""
    key = _text(profile_id)
    if not key:
        return ()
    return (key[:-4],) if key.endswith("-gpu") else (key + "-gpu",)


def resolve_id(profile_id: str) -> str:
    """按 id 找**实际装了**的那套环境：精确的没有就看 CPU / GPU 双胞胎。

    两套 profile 装的是同一批包，只是索引不同（GPU 版多 CUDA 运行库），所以模型声明
    `llama-cpp`、用户装的是 `llama-cpp-gpu` 时也应该能直接跑起来。
    """
    key = _text(profile_id)
    if not key:
        return ""
    if python_path(key).exists():
        return key
    for other in twin_ids(key):
        if python_path(other).exists():
            return other
    return key


def installed_ids() -> tuple[str, ...]:
    """已经装了（解释器在盘上）的 profile id；报错时用来告诉用户手头有什么。"""
    return tuple(profile.id for profile in profiles() if python_path(profile.id).exists())

def requirements_path(profile_id: str) -> Path:
    """venv 同级的 `requirements.txt`（安装清单留档，便于查看/复现）。"""
    return runtime_root() / _text(profile_id) / "requirements.txt"


def log_file(profile_id: str) -> Path:
    """`ensure()` 的输出日志（追加写）。"""
    return logs_dir() / f"runtime-{_text(profile_id)}.log"


def marker_path(profile_id: str) -> Path:
    """安装完成标记：`ensure()` 真的装完才写。

    有了它才能把「已装好」和「暂停 / 中断留下的半成品 venv」分开——只看解释器在不在的话，
    pip 刚建完 venv 就被暂停也会被当成已安装。
    """
    return runtime_root() / _text(profile_id) / "installed.json"


def pending_path(profile_id: str, *, system: bool = False) -> Path:
    """安装进行中的标记：程序被强杀 / 断电时它会留在盘上。

    完成标记（`installed.json`）要整个流程跑完才写，光看它只能知道「没装完」；
    这个标记记下「正在装」，页面据此把上次断掉的半个 venv 显示成「未完成」，
    再点安装就能接着装（pip 缓存与已装好的包都还在）。
    """
    name = _text(profile_id)
    if system:
        return runtime_root() / "system" / f"installing-{name}.json"
    return runtime_root() / name / "installing.json"


def interrupted(profile_id: str, *, system: bool = False) -> bool:
    """上次安装是不是没跑完就没了：有进行中标记、又没有完成标记。"""
    if system:
        return pending_path(profile_id, system=True).exists()
    return pending_path(profile_id).exists() and not marker_path(profile_id).exists()


def clear_pending(profile_id: str, *, system: bool = True) -> None:
    """抹掉中断标记（取消安装 / 卸载的收尾）。

    `system=True` 连程序环境那份标记一起清：取消一个装到一半的环境，用户要的就是
    「回到没装过」，没必要因为装法不同留半截状态。
    """
    _clear_pending(profile_id)
    if system:
        _clear_pending(profile_id, system=True)


def system_python() -> Path:
    """程序自己的解释器（P4 程序环境安装模式的目标）。"""
    return Path(sys.executable)


def system_requirements_path(profile_id: str) -> Path:
    """装进程序环境时留下的清单留档：`runtime/system/requirements-<profile>.txt`。"""
    return runtime_root() / "system" / f"requirements-{_text(profile_id)}.txt"


def system_log_file(profile_id: str) -> Path:
    """程序环境安装的输出日志（追加写）。"""
    return logs_dir() / f"runtime-system-{_text(profile_id)}.log"


# ------------------------------------------------------------------ 检测


def installed(profile_id: str) -> bool:
    """装完了没有：完成标记在、解释器存在且 `--version` 能跑通。"""
    profile_id = _text(profile_id)
    if not marker_path(profile_id).exists():
        return False
    python = python_path(profile_id)
    if not python.exists():
        return False
    try:
        result = subprocess.run(
            [str(python), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def has_dir(profile_id: str) -> bool:
    """profile 目录在不在、里面有没有东西。

    用户手工往 `.resources/models/runtime/<id>/` 里塞过依赖（没走页面安装）时
    没有完成标记，但目录是有内容的——页面据此把它当成「手工装好的」，
    免得出现「显示未安装、卸载却是亮的」这种自相矛盾。
    """
    target = runtime_root() / _text(profile_id)
    try:
        if not target.is_dir():
            return False
        next(target.iterdir())
    except (OSError, StopIteration):
        return False
    return True


def package_versions(python: Path) -> dict[str, str]:
    """`pip list` 的结果（包名 → 版本）；任何失败都返回 `{}`。"""
    try:
        result = subprocess.run(
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return {}
    if not isinstance(payload, list):
        return {}
    versions: dict[str, str] = {}
    for item in payload:
        if isinstance(item, Mapping):
            name = _text(item.get("name"))
            if name:
                versions[name] = _text(item.get("version"))
    return versions


# ------------------------------------------------------------------ 安装 / 卸载


def _emit(on_line: Callable[[str], None] | None, text: str) -> None:
    if on_line is None:
        return
    try:
        on_line(text)
    except Exception:
        pass


def _stop_reason(
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
) -> RuntimeStopped | None:
    """看一眼两个回调：取消优先于暂停；都没喊停就返回 None。"""
    if should_cancel is not None and should_cancel():
        return RuntimeStopped("安装已取消")
    if should_pause is not None and should_pause():
        return RuntimeStopped("安装已暂停", paused=True)
    return None


def _check_stop(
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
) -> None:
    stopped = _stop_reason(should_cancel, should_pause)
    if stopped is not None:
        raise stopped


def _terminate(process: subprocess.Popen) -> None:
    """先礼后兵地收掉子进程：terminate → 等 5 秒 → kill；已经退出的直接返回。"""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
    except OSError:
        pass


class Control:
    """安装的停止开关：页面点「暂停 / 取消」时直接喊停，不用后台轮询。

    `cancel()` / `pause()` 先置位，再把当前绑定的子进程收掉——pip 闷头下载几秒
    不输出时也是点一下就停。`should_cancel()` / `should_pause()` 给安装函数在
    每行输出之间复查用（这时通常已经收到 `RuntimeStopped` 了）。
    """

    __slots__ = ("_cancel", "_pause", "_lock", "_processes")

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def cancel(self) -> None:
        """取消：置取消位、清暂停位，并立刻停掉正在跑的子进程。"""
        self._cancel.set()
        self._pause.clear()
        self._stop_processes()

    def pause(self) -> None:
        """暂停：置暂停位并立刻停掉子进程（venv 半成品与 pip 缓存都留着）。"""
        self._pause.set()
        self._stop_processes()

    def clear_pause(self) -> None:
        self._pause.clear()

    def reset(self) -> None:
        """开工前清掉两个位：上一次的取消位不该把这一次当场掐掉。"""
        self._cancel.clear()
        self._pause.clear()

    def should_cancel(self) -> bool:
        return self._cancel.is_set()

    def should_pause(self) -> bool:
        return self._pause.is_set()

    def bind(self, process: subprocess.Popen) -> None:
        """认领一个刚起的子进程；如果已经喊停了就当场收掉它。"""
        with self._lock:
            self._processes.add(process)
            stopping = self._cancel.is_set() or self._pause.is_set()
        if stopping:
            _terminate(process)

    def unbind(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def _stop_processes(self) -> None:
        with self._lock:
            targets = list(self._processes)
        for process in targets:
            _terminate(process)


def _base_python(profile: RuntimeProfile) -> Path:
    """建 venv 用的基础解释器：profile 指定且存在就用它，否则用程序的解释器。"""
    candidate = _text(getattr(profile, "python", ""))
    if candidate:
        path = Path(candidate)
        if path.exists():
            return path
    return Path(sys.executable)


def _watch_stop(
    process: subprocess.Popen,
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
) -> tuple[threading.Thread | None, list[RuntimeStopped]]:
    """另开一个线程盯两个回调，命中就收掉子进程。

    只靠「每读一行查一次」是不够的：pip 下载轮子时会闷头好几秒甚至几分钟不输出，
    这时候点「暂停 / 取消」在界面看来就是完全没反应。这里 0.2 秒查一次。
    """
    if should_cancel is None and should_pause is None:
        return None, []
    hits: list[RuntimeStopped] = []

    def poll() -> None:
        while process.poll() is None:
            stopped = _stop_reason(should_cancel, should_pause)
            if stopped is not None:
                hits.append(stopped)
                _terminate(process)
                return
            time.sleep(_STOP_POLL_SEC)

    thread = threading.Thread(target=poll, name="runtime-stop-watch", daemon=True)
    thread.start()
    return thread, hits


def _stream(
    command: list[str],
    *,
    log: Path,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
) -> int:
    """跑一条命令，把 stdout+stderr 逐行写进日志并回调；返回退出码。

    读每一行都查一次两个回调。传了 `control` 就把它绑到子进程上——页面按「暂停 /
    取消」时由 `Control` 直接收掉子进程，**不需要**另外开线程轮询；只给老式回调
    （测试与旧调用方）时才开那个 `_STOP_POLL_SEC` 的守护线程。
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write("$ " + " ".join(command) + "\n")
        handle.flush()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        if control is not None:
            control.bind(process)
            watch, hits = None, []
        else:
            watch, hits = _watch_stop(process, should_cancel, should_pause)
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    text = line.rstrip("\r\n")
                    handle.write(text + "\n")
                    handle.flush()
                    _emit(on_line, text)
                    stopped = _stop_reason(should_cancel, should_pause)
                    if stopped is not None:
                        if stopped not in hits:
                            hits.append(stopped)
                        _terminate(process)
                        break
            process.wait()
            if hits:
                raise hits[0]
        finally:
            if control is not None:
                control.unbind(process)
            if watch is not None:
                watch.join(timeout=5)
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait()
    return int(process.returncode or 0)


def _packages(profile: RuntimeProfile) -> tuple[str, ...]:
    """profile 的包清单，去掉空白项。"""
    raw = getattr(profile, "packages", ()) or ()
    return tuple(text for text in (str(item).strip() for item in raw) if text)


def _write_requirements(path: Path, packages: tuple[str, ...]) -> None:
    """把包清单写成 `requirements.txt`（空清单也写一个空文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(packages) + ("\n" if packages else ""), encoding="utf-8")


def _write_marker(profile_id: str) -> None:
    """写安装完成标记（`installed()` 认它）；装到一半不会走到这里。"""
    path = marker_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"installed_at": time.time()}, ensure_ascii=False), encoding="utf-8")


def _write_pending(profile_id: str, packages: tuple[str, ...], *, system: bool = False) -> None:
    """记下「正在安装」：中途被强杀（关窗口 / 断电）时靠它认出半成品。"""
    payload = {
        "started_at": round(time.time(), 3),
        "pid": os.getpid(),
        "system": bool(system),
        "packages": list(packages),
    }
    path = pending_path(profile_id, system=system)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _clear_pending(profile_id: str, *, system: bool = False) -> None:
    """安装收尾（或卸载）时抹掉进行中标记。"""
    try:
        pending_path(profile_id, system=system).unlink()
    except OSError:
        pass


def _pip_command(
    python: Path,
    profile: RuntimeProfile,
    requirements: Path,
    *,
    upgrade: bool = False,
    index_url: str = "",
    urls: Iterable[str] = (),
) -> list[str]:
    """拼 `pip install` 命令：索引 / 镜像 / 代理都交给 pip 自己按环境变量处理。

    profile 自带 `index_url` 的以 profile 为准（尊重包来源的特殊要求），
    否则用页面设置里选的安装源（官方源为空串 = 用 pip 默认）。
    `urls` 是显式直链（GitHub 资源换成镜像后的地址），pip 会优先用它们。
    """
    command = [str(python), "-m", "pip", "install", "--progress-bar", "off"]
    if upgrade:
        command.append("--upgrade")
    index = _text(getattr(profile, "index_url", "")) or _text(index_url)
    if index:
        command += ["--index-url", index]
    for extra in getattr(profile, "extra_index", ()) or ():
        extra_text = _text(extra)
        if extra_text:
            command += ["--extra-index-url", extra_text]
    command += [_text(url) for url in urls or () if _text(url)]
    command += ["-r", str(requirements)]
    return command


def _github_retry(
    log: Path,
    *,
    prefixes: Iterable[str],
    rebuild: Callable[[list[str]], list[str]],
    on_line: Callable[[str], None] | None = None,
) -> list[str] | None:
    """pip 失败后：把日志里的 GitHub 直链按「GitHub 下载源」换成镜像地址，重拼一条命令。

    `rebuild(镜像直链列表)` 由调用方给出（ensure / ensure_system / install_wheels 的命令
    形状各不相同）；没换出地址就返回 None，调用方照旧报错。换成官方（空前缀）不算重试。
    """
    text = _read_log(log)
    if "github.com" not in text and "githubusercontent.com" not in text:
        return None
    mirrored = mirror_github_urls(github_asset_urls(text), prefixes)
    if not mirrored:
        return None
    _emit(on_line, "改用 GitHub 下载源重试：" + "、".join(mirrored))
    return rebuild(mirrored)


def ensure(
    profile: RuntimeProfile,
    *,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    upgrade: bool = False,
    index_url: str = "",
    github_prefixes: Iterable[str] = (),
) -> Path:
    """建 venv → 写 requirements.txt → `pip install -r`；返回 venv 里的解释器路径。

    只负责执行，不负责询问用户；失败抛 `RuntimeError_`，被叫停抛 `RuntimeStopped`，
    输出（含 pip 的）追加进 `log_file()`。传 `control` 时由它说了算（并负责收掉
    pip 子进程）。`github_prefixes` 是「GitHub 下载源」的前缀链：pip 卡在
    github.com 上时会用镜像地址重试一次。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    _check_stop(should_cancel, should_pause)

    packages = _packages(profile)
    _write_pending(profile_id, packages)
    python = python_path(profile_id)
    requirements = requirements_path(profile_id)
    log = log_file(profile_id)

    if not python.exists():
        venv = venv_dir(profile_id)
        venv.parent.mkdir(parents=True, exist_ok=True)
        base = _base_python(profile)
        _emit(on_line, f"创建运行环境：{venv}")
        code = _stream(
            [str(base), "-m", "venv", str(venv)],
            log=log,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
        if code != 0 or not python.exists():
            raise RuntimeError_(f"创建运行环境失败（退出码 {code}），详见 {log}")
    _check_stop(should_cancel, should_pause)

    _write_requirements(requirements, packages)
    if not packages:
        _write_marker(profile_id)
        _clear_pending(profile_id)
        return python

    command = _pip_command(python, profile, requirements, upgrade=upgrade, index_url=index_url)

    _emit(on_line, "安装依赖：" + "、".join(packages))
    code = _stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        retry = _github_retry(
            log,
            prefixes=github_prefixes,
            on_line=on_line,
            rebuild=lambda urls: _pip_command(
                python, profile, requirements, upgrade=upgrade, index_url=index_url, urls=urls
            ),
        )
        if retry is not None:
            code = _stream(
                retry,
                log=log,
                on_line=on_line,
                should_cancel=should_cancel,
                should_pause=should_pause,
                control=control,
            )
    if code != 0:
        raise RuntimeError_(f"安装依赖失败（退出码 {code}），详见 {log}" + _network_hint(log))
    _check_stop(should_cancel, should_pause)
    _write_marker(profile_id)
    _clear_pending(profile_id)
    return python


def _dist_name(text: str) -> str:
    """把发行版名或需求串归一化成 PEP 503 形式（`llama_cpp_python>=0.3` → `llama-cpp-python`）。"""
    head = re.split(r"[<>=!~;\[\]()\s]", str(text).strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def _wheel_dist_names(wheels: Iterable[Path]) -> set[str]:
    """这些 whl 各自提供哪个发行版（用来把清单里已经被 whl 覆盖的那条去掉）。"""
    names: set[str] = set()
    for path in wheels:
        head = path.name[:-4] if path.name.lower().endswith(".whl") else path.name
        names.add(_dist_name(head.split("-")[0]))
    return names


def _read_log(log: Path) -> str:
    """读一段安装日志；读不到就当空的。"""
    try:
        return log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _network_hint(log: Path) -> str:
    """pip 失败时看一眼日志：轮子挂在 github.com 上而当前网络连不上，是很常见的一种。"""
    text = _read_log(log)
    if "github.com" not in text or "TimeoutError" not in text:
        return ""
    return "｜提示：这个环境的轮子托管在 github.com 上，当前网络连不上——可在设置里把「GitHub 下载源」换成镜像，或用「本地 whl…」装一份离线轮子"


#: pip 输出里 GitHub 资源的直链（wheel / sdist / 压缩包）
_GITHUB_ASSET = re.compile(
    r"https?://(?:[A-Za-z0-9._-]+\.)?(?:github\.com|codeload\.github\.com|githubusercontent\.com)"
    r"/\S+?\.(?:whl|zip|tar\.gz)",
    re.IGNORECASE,
)


def github_asset_urls(text: str, limit: int = 8) -> list[str]:
    """从 pip 输出里挑出 GitHub 上的资源直链（就是它没能下载下来的那些）。"""
    found: list[str] = []
    for match in _GITHUB_ASSET.finditer(str(text or "")):
        url = match.group(0).rstrip(".,;)'\"")
        if url and url not in found:
            found.append(url)
        if len(found) >= max(1, int(limit)):
            break
    return found


def mirror_github_urls(urls: Iterable[str], prefixes: Iterable[str]) -> list[str]:
    """按「GitHub 下载源」把直链换成镜像前缀地址；空白前缀（官方）跳过。"""
    result: list[str] = []
    for prefix in prefixes or ():
        clean = _text(prefix).rstrip("/")
        if not clean:
            continue
        for url in urls or ():
            target = _text(url)
            candidate = f"{clean}/{target}"
            if target and candidate not in result:
                result.append(candidate)
    return result


def _wheel_filename_parts(path: Path) -> tuple[str, str, str] | None:
    """拆 wheel 文件名（`发行版-版本-python-abi-平台.whl`）→ (python, abi, 平台)。"""
    name = path.name
    if name.lower().endswith(".whl"):
        name = name[:-4]
    parts = name.split("-")
    if len(parts) < 5:
        return None
    return parts[-3], parts[-2], parts[-1]


def _python_tag_ok(tag: str, abi: str, major: int, minor: int) -> bool:
    """python 标签认不认：`py3` / `py312` / `cp312`，以及低版本的 `abi3` 包。"""
    for token in str(tag).split("."):
        if token in ("py3", f"py{major}", f"py{major}{minor}", f"cp{major}{minor}"):
            return True
        if abi == "abi3" and token.startswith(f"cp{major}") and token[3:].isdigit() and int(token[3:]) <= minor:
            return True
    return False


def _platform_tag_ok(tag: str) -> bool:
    """平台标签认不认：`any`，或者和这台机器对得上（只看大类，架构细节交给 pip）。"""
    bits = 64 if sys.maxsize > 2**32 else 32
    for token in str(tag).split("."):
        if token == "any":
            return True
        if sys.platform.startswith("win"):
            if token == ("win_amd64" if bits == 64 else "win32"):
                return True
        elif sys.platform == "darwin":
            if token.startswith("macosx") or token == "universal2":
                return True
        elif sys.platform.startswith("linux"):
            if token.startswith(("linux", "manylinux", "musllinux")):
                return True
    return False


def check_wheels(wheels: Iterable[str]) -> list[str]:
    """查一遍这些 whl 能不能装到本机上，返回说人话的问题清单（空 = 没看出问题）。

    只看文件名里的 python / 平台标签——真正的兼容性还是以 pip 的判断为准。
    """
    version = sys.version_info
    major, minor = version[0], version[1]
    problems: list[str] = []
    for item in wheels:
        path = Path(str(item))
        if not path.exists():
            problems.append(f"{path.name}：文件不存在")
            continue
        if path.suffix.lower() != ".whl":
            problems.append(f"{path.name}：不是 .whl 文件")
            continue
        parts = _wheel_filename_parts(path)
        if parts is None:
            problems.append(f"{path.name}：文件名不像 wheel（发行版-版本-python-abi-平台.whl）")
            continue
        tag_py, tag_abi, tag_platform = parts
        if not _python_tag_ok(tag_py, tag_abi, major, minor):
            problems.append(f"{path.name}：这个包是给 Python {tag_py} 的，装不进 Python {major}.{minor}")
        if not _platform_tag_ok(tag_platform):
            problems.append(f"{path.name}：这个包是给 {tag_platform} 平台的，装不进这台机器（{sys.platform}）")
    return problems


def install_wheels(
    profile: RuntimeProfile,
    wheels: Sequence[str],
    *,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    index_url: str = "",
    upgrade: bool = False,
    github_prefixes: Iterable[str] = (),
) -> Path:
    """用本地 `.whl` 装这个运行环境：建 venv（没有就建）→ `pip install <whl…>` → 写完成标记。

    和 `ensure()` 的区别只在「装什么」：装传进来的这些文件，外加清单里**没被这些 whl 覆盖**
    的包（比如 `-gpu` 环境的 `nvidia-*-cu12`，它们不在 GitHub 上、能从镜像拉），这样离线装完
    就是一个能跑的环境；剩下的依赖 pip 会按 `index_url` 自己去补。离线 / 内网拿 whl 装就靠它。
    清单里剩下的包若也卡在 github.com 上，会按 `github_prefixes`（GitHub 下载源）重试一次。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    files = [Path(str(item)) for item in wheels]
    if not files:
        raise RuntimeError_("没有选任何 whl 文件")
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        raise RuntimeError_("找不到这些文件：" + "、".join(missing))
    _check_stop(should_cancel, should_pause)

    _write_pending(profile_id, tuple(str(path) for path in files))
    python = python_path(profile_id)
    log = log_file(profile_id)

    if not python.exists():
        venv = venv_dir(profile_id)
        venv.parent.mkdir(parents=True, exist_ok=True)
        base = _base_python(profile)
        _emit(on_line, f"创建运行环境：{venv}")
        code = _stream(
            [str(base), "-m", "venv", str(venv)],
            log=log,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
        if code != 0 or not python.exists():
            raise RuntimeError_(f"创建运行环境失败（退出码 {code}），详见 {log}")
    _check_stop(should_cancel, should_pause)

    command = [str(python), "-m", "pip", "install", "--progress-bar", "off"]
    if upgrade:
        command.append("--upgrade")
    index = _text(getattr(profile, "index_url", "")) or _text(index_url)
    if index:
        command += ["--index-url", index]
    for extra in getattr(profile, "extra_index", ()) or ():
        extra_text = _text(extra)
        if extra_text:
            command += ["--extra-index-url", extra_text]
    provided = _wheel_dist_names(files)
    leftover = tuple(item for item in _packages(profile) if _dist_name(item) not in provided)
    head = list(command)
    command += [str(path) for path in files]
    command += list(leftover)

    _emit(on_line, "从本地 whl 安装：" + "、".join(path.name for path in files))
    if leftover:
        _emit(on_line, "清单里没被 whl 覆盖的包一起装：" + "、".join(leftover))
    code = _stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        retry = _github_retry(
            log,
            prefixes=github_prefixes,
            on_line=on_line,
            rebuild=lambda urls: [
                *head,
                *[str(path) for path in files],
                *list(leftover),
                *urls,
            ],
        )
        if retry is not None:
            code = _stream(
                retry,
                log=log,
                on_line=on_line,
                should_cancel=should_cancel,
                should_pause=should_pause,
                control=control,
            )
    if code != 0:
        raise RuntimeError_(f"从本地 whl 安装失败（退出码 {code}），详见 {log}" + _network_hint(log))
    _check_stop(should_cancel, should_pause)
    _write_marker(profile_id)
    _clear_pending(profile_id)
    return python


def ensure_system(
    profile: RuntimeProfile,
    *,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    upgrade: bool = False,
    index_url: str = "",
    python: Path | None = None,
    github_prefixes: Iterable[str] = (),
) -> Path:
    """P4：把 profile 的依赖装进**程序自己的解释器**（不建 venv）；返回目标解释器。

    和 `ensure()` 一样只负责执行：是否安装由页面二次确认后决定。
    清单留档写到 `system_requirements_path()`，输出追加进 `system_log_file()`。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    _check_stop(should_cancel, should_pause)

    target = Path(python) if python is not None else system_python()
    if not target.exists():
        raise RuntimeError_(f"程序解释器不存在：{target}")

    packages = _packages(profile)
    _write_pending(profile_id, packages, system=True)
    requirements = system_requirements_path(profile_id)
    _write_requirements(requirements, packages)
    if not packages:
        _emit(on_line, "清单为空，没有需要安装的包。")
        _clear_pending(profile_id, system=True)
        return target

    log = system_log_file(profile_id)
    command = _pip_command(target, profile, requirements, upgrade=upgrade, index_url=index_url)
    _emit(on_line, "安装进程序环境：" + "、".join(packages))
    _emit(on_line, f"目标解释器：{target}")
    code = _stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        retry = _github_retry(
            log,
            prefixes=github_prefixes,
            on_line=on_line,
            rebuild=lambda urls: _pip_command(
                target, profile, requirements, upgrade=upgrade, index_url=index_url, urls=urls
            ),
        )
        if retry is not None:
            code = _stream(
                retry,
                log=log,
                on_line=on_line,
                should_cancel=should_cancel,
                should_pause=should_pause,
                control=control,
            )
    if code != 0:
        raise RuntimeError_(f"装进程序环境失败（退出码 {code}），详见 {log}" + _network_hint(log))
    _check_stop(should_cancel, should_pause)
    _clear_pending(profile_id, system=True)
    return target



def uninstall_system(
    profile: RuntimeProfile,
    *,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    python: Path | None = None,
) -> Path:
    """P4：把 profile 声明的依赖从**程序自己的解释器**里卸载（`pip uninstall -y`）；返回目标解释器。

    和 `ensure_system()` 对称：只卸载这个 profile 声明的包，清单为空就什么都不跑。
    输出追加进 `system_log_file()`，失败抛 `RuntimeError_`。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    _check_stop(should_cancel, should_pause)

    target = Path(python) if python is not None else system_python()
    if not target.exists():
        raise RuntimeError_(f"程序解释器不存在：{target}")

    packages = _packages(profile)
    if not packages:
        _emit(on_line, "清单为空，没有需要卸载的包。")
        _clear_pending(profile_id, system=True)
        return target

    log = system_log_file(profile_id)
    command = [str(target), "-m", "pip", "uninstall", "-y", *packages]
    _emit(on_line, "从程序环境卸载：" + "、".join(packages))
    _emit(on_line, f"目标解释器：{target}")
    code = _stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        raise RuntimeError_(f"从程序环境卸载失败（退出码 {code}），详见 {log}")
    _check_stop(should_cancel, should_pause)
    _clear_pending(profile_id, system=True)
    return target


def uninstall(profile_id: str) -> bool:
    """整目录删除这个 profile 的 venv 与 requirements；不存在也算成功。"""
    target = runtime_root() / _text(profile_id)
    _clear_pending(profile_id)
    if not target.exists():
        return True
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


def discard(profile_id: str, *, logs: bool = True) -> bool:
    """取消安装后的收尾：删掉半成品 venv / requirements，可选连安装日志一起删。

    和 `uninstall()` 的区别只在语义（这是「装到一半不要了」），删除动作一样；
    返回目录是否真的清干净了。
    """
    removed = uninstall(profile_id)
    if logs:
        clear_logs(profile_id, system=True)
    return removed


def clear_logs(profile_id: str, *, system: bool = False) -> None:
    """删掉这个 profile 的安装日志（卸载时一并清；`system=True` 连程序环境的日志一起删）。"""
    paths = [log_file(profile_id)]
    if system:
        paths.append(system_log_file(profile_id))
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
