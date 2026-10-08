"""core 通用 pip 安装引擎：建 venv、装依赖、本地 whl、暂停 / 取消。

与模型工具库那套安装器相比，这版**不认任何具体插件**：

1. 解释器、venv、requirements、日志都是显式参数，落盘位置由调用方给出；
2. 日志走 `logging`，pip 的每一行输出走 `on_line` 回调；
3. 「正在安装」与「装好了」两种留档交给 `on_start` / `on_finish` 钩子，core 不猜
   调用方的目录布局，也不碰清单。

pip 跑起来是**子进程**，暂停 / 取消必须能立刻收掉它：它下载轮子时可能好几分钟不
输出，只靠「读一行查一次」在界面上看就是完全没反应。所以有两条路——

* `Control`：界面持有，点一下置位并当场收掉子进程；
* `should_cancel` / `should_pause` 回调：老式调用方与测试用，另开一个
  `STOP_POLL_SEC` 的守护线程兜底。

两者可以同时给：`Control` 说了算，回调仍会在每行输出之间被复查。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_logger = logging.getLogger(__name__)

__all__ = [
    "Control",
    "CREATE_NO_WINDOW",
    "PipError",
    "PipStopped",
    "STOP_POLL_SEC",
    "WINDOWS_PATH_LIMIT",
    "check_wheels",
    "create_venv",
    "dist_name",
    "ensure",
    "ensure_packages",
    "github_asset_urls",
    "github_retry",
    "install_wheels",
    "long_path_hint",
    "mirror_github_urls",
    "network_hint",
    "package_versions",
    "pip_command",
    "read_log",
    "stream",
    "venv_python",
    "wheel_dist_names",
    "write_requirements",
]

#: Windows 上别弹出黑框
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
#: 老式回调的轮询间隔（`Control` 不需要它）
STOP_POLL_SEC = 0.2
#: Windows 单条路径的长度上限，超了 pip 写包内文件会报「找不到」
WINDOWS_PATH_LIMIT = 260


class PipError(RuntimeError):
    """安装过程中的失败（消息直接给用户看）。"""


class PipStopped(PipError):
    """被用户叫停：`paused` 为真表示是「暂停」而不是「取消」。"""

    def __init__(self, message: str, *, paused: bool = False) -> None:
        super().__init__(message)
        self.paused = bool(paused)


# ------------------------------------------------------------------ 小工具


def text(value: Any) -> str:
    """去掉首尾空白的字符串。"""
    return str(value or "").strip()


def _clean(values: Iterable[Any] | str | None) -> tuple[str, ...]:
    """把「包声明 / 网址」这类输入统一成非空字符串元组（兼容字符串与逗号分隔）。"""
    if values is None:
        return ()
    if isinstance(values, str):
        raw = [part.strip() for part in values.replace(",", "\n").splitlines()]
    elif isinstance(values, (list, tuple, set, frozenset)):
        raw = [str(item).strip() for item in values]
    else:
        raw = [str(values).strip()]
    return tuple(item for item in raw if item)


def venv_python(venv: Path) -> Path:
    """venv 里的解释器路径（按平台猜：Windows 是 `Scripts/python.exe`）。"""
    root = Path(venv)
    if sys.platform.startswith("win"):
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def write_requirements(path: Path, packages: Iterable[str]) -> Path:
    """把包清单写成 `requirements.txt`（空清单也写一个空文件）。"""
    target = Path(path)
    items = _clean(packages)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(items) + ("\n" if items else ""), encoding="utf-8")
    return target


def pip_command(
    python: Path,
    requirements: Path,
    *,
    index_url: str = "",
    extra_index: Iterable[str] = (),
    urls: Iterable[str] = (),
    upgrade: bool = False,
) -> list[str]:
    """拼一条 `pip install` 命令。

    索引 / 镜像 / 直链都交给 pip 自己处理；`urls` 是显式直链（GitHub 资源换成镜像后的
    地址），pip 会优先用它们。空 `index_url` = 用 pip 默认源。
    """
    command = [str(python), "-m", "pip", "install", "--progress-bar", "off"]
    if upgrade:
        command.append("--upgrade")
    source = text(index_url)
    if source:
        command += ["--index-url", source]
    for extra in _clean(extra_index):
        command += ["--extra-index-url", extra]
    command += _clean(urls)
    command += ["-r", str(requirements)]
    return command


def dist_name(value: str) -> str:
    """把发行版名或需求串归一化成 PEP 503 形式（`llama_cpp_python>=0.3` → `llama-cpp-python`）。"""
    head = re.split(r"[<>=!~;\[\]()\s]", str(value).strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def wheel_dist_names(wheels: Iterable[Path]) -> set[str]:
    """这些 whl 各自提供哪个发行版（用来把清单里已经被 whl 覆盖的那条去掉）。"""
    names: set[str] = set()
    for path in wheels:
        name = Path(path).name
        head = name[:-4] if name.lower().endswith(".whl") else name
        names.add(dist_name(head.split("-")[0]))
    return names


def read_log(log: Path) -> str:
    """读一段安装日志；读不到就当空的。"""
    try:
        return Path(log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def network_hint(log: Path) -> str:
    """pip 失败时看一眼日志：轮子挂在 github.com 上而当前网络连不上，是很常见的一种。"""
    body = read_log(log)
    if "github.com" not in body or "TimeoutError" not in body:
        return ""
    return (
        "｜提示：这个环境的轮子托管在 github.com 上，当前网络连不上"
        "——可在设置里把「GitHub 下载源」换成镜像，或用「本地 whl…」装一份离线轮子"
    )


def long_path_hint(log: Path) -> str:
    """pip 说「`[Errno 2] No such file or directory`」时，多半是路径太长顶破了 Windows 上限。

    报错里那个「找不到」的文件其实是 pip 正要写出来的**包内文件**（例如 torch 里长达
    137 字符的 CUDA 头文件），Windows 上超限后 `makedirs` 就抛 `FileNotFoundError`，
    于是看起来像是「源里的包不完整」。
    """
    body = read_log(log)
    if "No such file or directory" not in body or "site-packages" not in body:
        return ""
    return (
        f"｜提示：安装路径太长，顶破了 Windows 单条路径 {WINDOWS_PATH_LIMIT} 字符的上限"
        "（报错里那个「找不到」的文件其实是 pip 正要写出来的包内文件）。"
        "把运行环境换到更浅的位置，或用管理员权限开启系统「长路径支持」后重启程序"
    )


#: pip 输出里 GitHub 资源的直链（wheel / sdist / 压缩包）
_GITHUB_ASSET = re.compile(
    r"https?://(?:[A-Za-z0-9._-]+\.)?(?:github\.com|codeload\.github\.com|githubusercontent\.com)"
    r"/\S+?\.(?:whl|zip|tar\.gz)",
    re.IGNORECASE,
)


def github_asset_urls(body: str, limit: int = 8) -> list[str]:
    """从 pip 输出里挑出 GitHub 上的资源直链（就是它没能下载下来的那些）。"""
    found: list[str] = []
    for match in _GITHUB_ASSET.finditer(str(body or "")):
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
        clean = text(prefix).rstrip("/")
        if not clean:
            continue
        for url in urls or ():
            target = text(url)
            candidate = f"{clean}/{target}"
            if target and candidate not in result:
                result.append(candidate)
    return result


def github_retry(
    log: Path,
    *,
    prefixes: Iterable[str],
    rebuild: Callable[[list[str]], list[str]],
    on_line: Callable[[str], None] | None = None,
) -> list[str] | None:
    """pip 失败后：把日志里的 GitHub 直链按「GitHub 下载源」换成镜像地址，重拼一条命令。

    `rebuild(镜像直链列表)` 由调用方给出（装依赖 / 装 whl 的命令形状不同）；没换出地址
    就返回 None，调用方照旧报错。换成官方（空前缀）不算重试。
    """
    body = read_log(log)
    if "github.com" not in body and "githubusercontent.com" not in body:
        return None
    mirrored = mirror_github_urls(github_asset_urls(body), prefixes)
    if not mirrored:
        return None
    emit(on_line, "改用 GitHub 下载源重试：" + "、".join(mirrored))
    return rebuild(mirrored)


# ------------------------------------------------------------------ 起停开关


def emit(on_line: Callable[[str], None] | None, message: str) -> None:
    """把一行输出交给调用方；回调自己炸了也不该影响安装。"""
    if on_line is None:
        return
    try:
        on_line(message)
    except Exception:
        _logger.exception("安装输出回调失败")


def stop_reason(
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
) -> PipStopped | None:
    """看一眼两个回调：取消优先于暂停；都没喊停就返回 None。"""
    if should_cancel is not None and should_cancel():
        return PipStopped("安装已取消")
    if should_pause is not None and should_pause():
        return PipStopped("安装已暂停", paused=True)
    return None


def check_stop(
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
) -> None:
    """命中取消 / 暂停就抛 `PipStopped`。"""
    stopped = stop_reason(should_cancel, should_pause)
    if stopped is not None:
        raise stopped


def terminate(process: subprocess.Popen) -> None:
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
    每行输出之间复查用（这时通常已经收到 `PipStopped` 了）。
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
        self.stop_processes()

    def pause(self) -> None:
        """暂停：置暂停位并立刻停掉子进程（venv 半成品与 pip 缓存都留着）。"""
        self._pause.set()
        self.stop_processes()

    def clear_pause(self) -> None:
        """清掉暂停位（重新开工前调一次）。"""
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
            terminate(process)

    def unbind(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def stop_processes(self) -> None:
        """收掉当前所有被认领的子进程。"""
        with self._lock:
            targets = list(self._processes)
        for process in targets:
            terminate(process)


def watch_stop(
    process: subprocess.Popen,
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
) -> tuple[threading.Thread | None, list[PipStopped]]:
    """另开一个线程盯两个回调，命中就收掉子进程。

    只靠「每读一行查一次」是不够的：pip 下载轮子时会闷头好几秒甚至几分钟不输出，
    这时候点「暂停 / 取消」在界面看来就是完全没反应。这里 `STOP_POLL_SEC` 查一次。
    """
    if should_cancel is None and should_pause is None:
        return None, []
    hits: list[PipStopped] = []

    def poll() -> None:
        while process.poll() is None:
            stopped = stop_reason(should_cancel, should_pause)
            if stopped is not None:
                hits.append(stopped)
                terminate(process)
                return
            time.sleep(STOP_POLL_SEC)

    thread = threading.Thread(target=poll, name="pip-stop-watch", daemon=True)
    thread.start()
    return thread, hits


def stream(
    command: Sequence[str],
    *,
    log: Path,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
) -> int:
    """跑一条命令，把 stdout+stderr 逐行写进日志并回调；返回退出码。

    读每一行都查一次两个回调。传了 `control` 就把它绑到子进程上——界面按「暂停 /
    取消」时由 `Control` 直接收掉子进程，**不需要**另外开线程轮询；只给老式回调
    （测试与旧调用方）时才开那个 `STOP_POLL_SEC` 的守护线程。

    被叫停时抛 `PipStopped`（不是返回退出码）。
    """
    target = Path(log)
    target.parent.mkdir(parents=True, exist_ok=True)
    if control is not None:
        # `Control` 说了算：它由界面持有，点一下就把子进程收掉。两个回调仍要用它自己的
        # 开关注入进去，这样「被谁喊停、是暂停还是取消」在收尾复查时还看得见。
        should_cancel, should_pause = control.should_cancel, control.should_pause
    with target.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write("$ " + " ".join(str(part) for part in command) + "\n")
        handle.flush()
        try:
            process = subprocess.Popen(
                [str(part) for part in command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise PipError(f"无法启动命令（{command[0]}）：{exc}") from exc
        if control is not None:
            control.bind(process)
            watch: threading.Thread | None = None
            hits: list[PipStopped] = []
        else:
            watch, hits = watch_stop(process, should_cancel, should_pause)
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    body = line.rstrip("\r\n")
                    handle.write(body + "\n")
                    handle.flush()
                    emit(on_line, body)
                    stopped = stop_reason(should_cancel, should_pause)
                    if stopped is not None:
                        if stopped not in hits:
                            hits.append(stopped)
                        terminate(process)
                        break
            process.wait()
            if not hits:
                # `Control` 是**外面**喊停的（它只负责收子进程，不负责记原因），子进程被
                # 收掉后读循环会自然结束——这里补看一眼两个开关，否则暂停会被上层当成
                # 普通的「退出码非零」，用户点的是暂停却收到一条安装失败。
                stopped = stop_reason(should_cancel, should_pause)
                if stopped is not None:
                    hits.append(stopped)
            if hits:
                raise hits[0]
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
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


# ------------------------------------------------------------------ wheel 体检


def _wheel_filename_parts(path: Path) -> tuple[str, str, str] | None:
    """拆 wheel 文件名（`发行版-版本-python-abi-平台.whl`）→ (python, abi, 平台)。"""
    name = Path(path).name
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
        if (
            abi == "abi3"
            and token.startswith(f"cp{major}")
            and token[3:].isdigit()
            and int(token[3:]) <= minor
        ):
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
    for item in wheels or ():
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


# ------------------------------------------------------------------ 安装


def create_venv(
    base_python: Path,
    venv: Path,
    *,
    log: Path,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
) -> Path:
    """用 `base_python` 在 `venv` 建一个虚拟环境，返回里面的解释器路径。

    失败抛 `PipError`（绝大多数情况是基础解释器不可用）。目录半成品留着：重新装一次
    会覆盖，venv 模块本身就支持在已有目录上补全。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    root = Path(venv)
    root.parent.mkdir(parents=True, exist_ok=True)
    emit(on_line, f"创建运行环境：{root}")
    code = stream(
        [str(base_python), "-m", "venv", str(root)],
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    python = venv_python(root)
    if code != 0 or not python.exists():
        raise PipError(f"创建运行环境失败（退出码 {code}），详见 {log}")
    return python


def ensure_packages(
    python: Path,
    *,
    packages: Iterable[str] = (),
    log: Path,
    requirements: Path | None = None,
    index_url: str = "",
    extra_index: Iterable[str] = (),
    urls: Iterable[str] = (),
    upgrade: bool = False,
    github_prefixes: Iterable[str] = (),
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    on_finish: Callable[[Path], None] | None = None,
) -> Path:
    """往**已经存在**的解释器里装这些包；返回该解释器。

    会先把清单写成 `requirements.txt`（给了 `requirements` 路径的话），再跑
    `pip install -r`；卡在 github.com 上时按 `github_prefixes` 换镜像直链重试一次。
    空清单只写文件、不跑 pip（那种情况下「装好了」由调用方的 `on_finish` 表达）。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    target = Path(python)
    items = _clean(packages)
    check_stop(should_cancel, should_pause)
    if requirements is not None:
        write_requirements(Path(requirements), items)
    if not items:
        if on_finish is not None:
            on_finish(target)
        return target

    command = pip_command(
        target,
        Path(requirements) if requirements is not None else Path(log).with_name("requirements.txt"),
        index_url=index_url,
        extra_index=extra_index,
        urls=urls,
        upgrade=upgrade,
    )
    emit(on_line, "安装依赖：" + "、".join(items))
    code = stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        retry = github_retry(
            log,
            prefixes=github_prefixes,
            on_line=on_line,
            rebuild=lambda mirrored: pip_command(
                target,
                Path(requirements) if requirements is not None else Path(log).with_name("requirements.txt"),
                index_url=index_url,
                extra_index=extra_index,
                urls=mirrored,
                upgrade=upgrade,
            ),
        )
        if retry is not None:
            code = stream(
                retry,
                log=log,
                on_line=on_line,
                should_cancel=should_cancel,
                should_pause=should_pause,
                control=control,
            )
    if code != 0:
        raise PipError(
            f"安装依赖失败（退出码 {code}），详见 {log}"
            + network_hint(log)
            + long_path_hint(log)
        )
    check_stop(should_cancel, should_pause)
    if on_finish is not None:
        on_finish(target)
    return target


def ensure(
    python: Path,
    *,
    packages: Iterable[str] = (),
    log: Path,
    requirements: Path | None = None,
    venv: Path | None = None,
    base_python: Path | None = None,
    index_url: str = "",
    extra_index: Iterable[str] = (),
    urls: Iterable[str] = (),
    upgrade: bool = False,
    github_prefixes: Iterable[str] = (),
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    on_start: Callable[[tuple[str, ...]], None] | None = None,
    on_finish: Callable[[Path], None] | None = None,
) -> Path:
    """建 venv（`python` 不存在时）→ 写 requirements → `pip install -r`；返回解释器。

    只负责执行，不负责询问用户；失败抛 `PipError`，被叫停抛 `PipStopped`，输出（含
    pip 的）追加进 `log`。要建环境必须同时给出 `venv` 与 `base_python`——core 不知道
    调用方把运行环境放在哪儿，也不该猜。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    target = Path(python)
    items = _clean(packages)
    check_stop(should_cancel, should_pause)
    if on_start is not None:
        on_start(items)
    if not target.exists():
        if venv is None or base_python is None:
            raise PipError(
                f"运行环境不存在（{target}），而且没给出创建它需要的 venv / 基础解释器"
            )
        create_venv(
            Path(base_python),
            Path(venv),
            log=log,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
        if not target.exists():
            raise PipError(f"创建运行环境失败：{target} 仍不存在，详见 {log}")
    check_stop(should_cancel, should_pause)
    return ensure_packages(
        target,
        packages=items,
        log=log,
        requirements=requirements,
        index_url=index_url,
        extra_index=extra_index,
        urls=urls,
        upgrade=upgrade,
        github_prefixes=github_prefixes,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
        on_finish=on_finish,
    )


def install_wheels(
    python: Path,
    wheels: Sequence[str],
    *,
    packages: Iterable[str] = (),
    log: Path,
    venv: Path | None = None,
    base_python: Path | None = None,
    index_url: str = "",
    extra_index: Iterable[str] = (),
    upgrade: bool = False,
    github_prefixes: Iterable[str] = (),
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
    on_start: Callable[[tuple[str, ...]], None] | None = None,
    on_finish: Callable[[Path], None] | None = None,
) -> Path:
    """用本地 `.whl` 装这个环境（`python` 不存在就先建 venv）；返回解释器。

    和 `ensure()` 的区别只在「装什么」：装传进来的这些文件，外加 `packages` 里**没被
    这些 whl 覆盖**的包（比如 `-gpu` 环境的 `nvidia-*-cu12`，它们不在 GitHub 上、能从
    镜像拉），这样离线装完就是一个能跑的环境；剩下的依赖 pip 会按 `index_url` 自己补。
    `packages` 里剩下的包若也卡在 github.com 上，会按 `github_prefixes` 重试一次。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    target = Path(python)
    files = [Path(str(item)) for item in wheels or ()]
    if not files:
        raise PipError("没有选任何 whl 文件")
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        raise PipError("找不到这些文件：" + "、".join(missing))
    items = _clean(packages)
    check_stop(should_cancel, should_pause)
    if on_start is not None:
        on_start(tuple(str(path) for path in files))
    if not target.exists():
        if venv is None or base_python is None:
            raise PipError(
                f"运行环境不存在（{target}），而且没给出创建它需要的 venv / 基础解释器"
            )
        create_venv(
            Path(base_python),
            Path(venv),
            log=log,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
        if not target.exists():
            raise PipError(f"创建运行环境失败：{target} 仍不存在，详见 {log}")
    check_stop(should_cancel, should_pause)

    head = [str(target), "-m", "pip", "install", "--progress-bar", "off"]
    if upgrade:
        head.append("--upgrade")
    source = text(index_url)
    if source:
        head += ["--index-url", source]
    for extra in _clean(extra_index):
        head += ["--extra-index-url", extra]
    provided = wheel_dist_names(files)
    leftover = tuple(item for item in items if dist_name(item) not in provided)
    command = [*head, *[str(path) for path in files], *leftover]

    emit(on_line, "从本地 whl 安装：" + "、".join(path.name for path in files))
    if leftover:
        emit(on_line, "清单里没被 whl 覆盖的包一起装：" + "、".join(leftover))
    code = stream(
        command,
        log=log,
        on_line=on_line,
        should_cancel=should_cancel,
        should_pause=should_pause,
        control=control,
    )
    if code != 0:
        retry = github_retry(
            log,
            prefixes=github_prefixes,
            on_line=on_line,
            rebuild=lambda mirrored: [*head, *[str(path) for path in files], *leftover, *mirrored],
        )
        if retry is not None:
            code = stream(
                retry,
                log=log,
                on_line=on_line,
                should_cancel=should_cancel,
                should_pause=should_pause,
                control=control,
            )
    if code != 0:
        raise PipError(
            f"从本地 whl 安装失败（退出码 {code}），详见 {log}"
            + network_hint(log)
            + long_path_hint(log)
        )
    check_stop(should_cancel, should_pause)
    if on_finish is not None:
        on_finish(target)
    return target


def package_versions(python: Path) -> dict[str, str]:
    """`pip list` 的结果（包名 → 版本）；任何失败都返回 `{}`。"""
    try:
        result = subprocess.run(
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "null")
    except ValueError:
        return {}
    if not isinstance(payload, list):
        return {}
    versions: dict[str, str] = {}
    for item in payload:
        if isinstance(item, Mapping):
            name = text(item.get("name"))
            if name:
                versions[name] = text(item.get("version"))
    return versions


def environment() -> dict[str, Any]:
    """当前进程这个解释器的概况（给「装到程序本体」这类调用方看）。"""
    return {
        "python": str(sys.executable),
        "version": f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}",
        "platform": sys.platform,
        "path_limit": WINDOWS_PATH_LIMIT if sys.platform.startswith("win") else 0,
        "no_window": bool(os.name == "nt" and CREATE_NO_WINDOW),
    }
