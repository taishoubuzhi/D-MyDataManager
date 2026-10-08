"""运行环境（runtime）：每个 profile 一套独立 venv，按需创建与安装依赖。

设计约束（见 MODEL_PLUGIN.md §6）：

- venv 落在**模型根目录**的 `runtime/<profile>/venv` 下（默认 `<程序目录>/.models/runtime/...`，
  可在插件设置里改「模型目录」），不再挂在资源文件夹下面，改资源文件夹也不会把它搬走；
- 万一模型目录还是深到让 venv 顶破 Windows 单条 260 字符上限（`path_too_long()`，系统又没开
  长路径支持），这里**不会**自作主张换个地方装：页面用 `path_limit_info()` 说明情况，请用户把
  「模型目录」改浅；用户不改就取消这次安装（`ensure()` 只负责执行安装，从不静默改路径）；
- `ensure()` 只负责**执行**安装，是否安装由页面弹确认后决定，这里绝不静默安装；
- profile 清单来自插件 `.data/runtime_profiles.json`（统一清单格式，页面通过
  `ctx.data("runtime_profiles")` 读）；没有 ctx 时（测试 / 脚本）直接读插件目录里的
  同名文件，文件不存在就返回空元组。

public API：`profiles / profile_of / profile_root / venv_dir / python_path / installed / marker_path /
requirements_path / ensure / ensure_system / discard / system_python / system_requirements_path /
system_log_file / uninstall / package_versions / log_file / github_asset_urls / mirror_github_urls /
path_too_long / path_limit_info`。

`ensure()` / `ensure_system()` 都接受 `should_cancel` 与 `should_pause` 两个回调：页面上的
「取消」和「暂停」各自置位，安装线程每读到一行 pip 输出就查一次，命中就收掉 pip 子进程并抛
`RuntimeStopped`（`paused=True` 表示暂停，缓存与半成品保留，再调一次即可续装）。

`ensure()` 把依赖装进 profile 自己的 venv；`ensure_system()` 是 P4 的「程序环境安装模式」：
把同样的清单装进**程序自己的解释器**（`sys.executable`），由页面在二次确认后调用。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.sdk import pip as _engine
from app.sdk import storage
from app.sdk.manifest import items_of

from ..paths import logs_dir, models_root, runtime_root

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
    "path_limit_info",
    "path_too_long",
    "pending_path",
    "profile_of",
    "profile_root",
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

#: Windows 上不弹控制台窗口、后台「盯暂停 / 取消」的轮询间隔：跟 core 的安装引擎共用一份。
_CREATE_NO_WINDOW = _engine.CREATE_NO_WINDOW
_STOP_POLL_SEC = _engine.STOP_POLL_SEC

#: 插件自带的 profile 清单；没有 ctx 时的兜底路径（协议 v2：数据在 `.data/`）
_DATA_FILE = Path(__file__).resolve().parents[2] / ".data" / "runtime_profiles.json"


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
        return storage.loads(text)
    except ValueError:
        return {}


def profiles(ctx: Any = None) -> tuple[RuntimeProfile, ...]:
    """读全部运行环境 profile；文件不存在或格式不对时返回空元组。"""
    raw = items_of(_payload(ctx))
    result: list[RuntimeProfile] = []
    for item in raw:
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

#: Windows 单条路径的硬上限（老 API，不含 `\\?\` 前缀）。超限后 `os.makedirs` 抛
#: `FileNotFoundError`（`[Errno 2] No such file or directory`），pip 解包就直接整包失败。
_WINDOWS_PATH_LIMIT = 260

#: 包解包后包内最深的成员还要占的余量：torch 的
#: `Lib\site-packages\torch\include\ATen\native\transformers\cuda\mem_eff_attention\`
#: `iterators\predicated_tile_access_iterator_residual_last.h` 一共 137 字符，取 140。
_PACKAGE_MEMBER_BUDGET = 140

#: venv 目录本身超过这个长度就不该再往下装（260 − 140，再留 5 给别的变数）。
_VENV_PATH_LIMIT = _WINDOWS_PATH_LIMIT - _PACKAGE_MEMBER_BUDGET - 5

#: 「系统有没有开长路径支持」查一次就记住
_LONG_PATHS_CACHE: list[bool] = []


def _long_paths_ok() -> bool:
    """系统开没开 Windows 长路径支持（`LongPathsEnabled`）；非 Windows 一律当作开了。

    开了的话 260 上限不再是问题，运行环境放哪都不会被路径长度卡住。
    """
    if not sys.platform.startswith("win"):
        return True
    if _LONG_PATHS_CACHE:
        return _LONG_PATHS_CACHE[0]
    enabled = False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        )
    except (ImportError, OSError):
        key = None
    if key is not None:
        try:
            enabled = bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
        except OSError:
            enabled = False
        finally:
            winreg.CloseKey(key)
    _LONG_PATHS_CACHE.append(enabled)
    return enabled


def _fits(path: Path) -> bool:
    """这个 venv 路径短到不会顶破 Windows 上限吗。"""
    return len(str(path)) <= _VENV_PATH_LIMIT


def path_too_long(profile_id: str) -> bool:
    """这个 profile 的 venv 会不会顶破 Windows 260 上限。

    非 Windows、或系统开了长路径支持时一律返回假。真的时候不要再自作主张换个地方装
    ——运行环境就放在模型根目录下，页面应当请用户把「模型目录」改到更浅的位置
    （见 `path_limit_info()`）。
    """
    if _long_paths_ok():
        return False
    return not _fits(profile_root(profile_id) / "venv")


def path_limit_info(profile_id: str) -> dict[str, Any]:
    """给页面弹窗用的路径长度信息（`too_long` 为假时其余字段只作展示参考）。"""
    key = _text(profile_id)
    return {
        "too_long": path_too_long(key),
        "current": str(profile_root(key) / "venv"),
        "models_root": str(models_root()),
        "limit": _WINDOWS_PATH_LIMIT,
        "limit_for_venv": _VENV_PATH_LIMIT,
    }


def profile_root(profile_id: str) -> Path:
    """某个 profile 的目录：venv、`requirements.txt`、安装标记都放它下面。

    就在模型根目录的 `runtime/<id>` 下，跟着「模型目录」设置走（见 `paths.models_root()`）：
    模型目录默认为程序目录下的 `.models`，比资源文件夹浅，也就不再顶破 260 上限。
    """
    return runtime_root() / _text(profile_id)


def venv_dir(profile_id: str) -> Path:
    """某个 profile 的 venv 目录。"""
    return profile_root(profile_id) / "venv"


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
    return profile_root(profile_id) / "requirements.txt"


def log_file(profile_id: str) -> Path:
    """`ensure()` 的输出日志（追加写）。"""
    return logs_dir() / f"runtime-{_text(profile_id)}.log"


def marker_path(profile_id: str) -> Path:
    """安装完成标记：`ensure()` 真的装完才写。

    有了它才能把「已装好」和「暂停 / 中断留下的半成品 venv」分开——只看解释器在不在的话，
    pip 刚建完 venv 就被暂停也会被当成已安装。
    """
    return profile_root(profile_id) / "installed.json"


def pending_path(profile_id: str, *, system: bool = False) -> Path:
    """安装进行中的标记：程序被强杀 / 断电时它会留在盘上。

    完成标记（`installed.json`）要整个流程跑完才写，光看它只能知道「没装完」；
    这个标记记下「正在装」，页面据此把上次断掉的半个 venv 显示成「未完成」，
    再点安装就能接着装（pip 缓存与已装好的包都还在）。
    """
    name = _text(profile_id)
    if system:
        return runtime_root() / "system" / f"installing-{name}.json"
    return profile_root(name) / "installing.json"


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
    target = profile_root(profile_id)
    try:
        if not target.is_dir():
            return False
        next(target.iterdir())
    except (OSError, StopIteration):
        return False
    return True


# ------------------------------------------------------------------ 安装 / 卸载


def _base_python(profile: RuntimeProfile) -> Path:
    """建 venv 用的基础解释器：profile 指定且存在就用它，否则用程序的解释器。"""
    candidate = _text(getattr(profile, "python", ""))
    if candidate:
        path = Path(candidate)
        if path.exists():
            return path
    return Path(sys.executable)


def _packages(profile: RuntimeProfile) -> tuple[str, ...]:
    """profile 的包清单，去掉空白项。"""
    raw = getattr(profile, "packages", ()) or ()
    return tuple(text for text in (str(item).strip() for item in raw) if text)


def _write_marker(profile_id: str) -> None:
    """写安装完成标记（`installed()` 认它）；装到一半不会走到这里。"""
    path = marker_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write_json(path, {"installed_at": time.time()})


def _write_pending(profile_id: str, packages: tuple[str, ...], *, system: bool = False) -> None:
    """记下「正在安装」：中途被强杀（关窗口 / 断电）时靠它认出半成品。"""
    payload = {
        "started_at": round(time.time(), 3),
        "pid": os.getpid(),
        "system": bool(system),
        "packages": list(packages),
    }
    path = pending_path(profile_id, system=system)
    storage.write_json(path, payload)


def _clear_pending(profile_id: str, *, system: bool = False) -> None:
    """安装收尾（或卸载）时抹掉进行中标记。"""
    try:
        pending_path(profile_id, system=system).unlink()
    except OSError:
        pass


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
    """在 profile 自己的 venv 里装依赖（没有就先建），返回 venv 里的解释器。

    只负责执行，不负责询问用户：失败抛 `RuntimeError_`，被叫停抛 `RuntimeStopped`。
    真正干活的引擎在 core（core 不知道运行环境放哪儿，所以这里把路径算好递过去）；
    开工前写「正在安装」的标记（`_write_pending`），装完写 `installed.json` 并抹掉标记。
    中途失败**故意不清标记**，页面据此显示「未完成（上次安装中断）」。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    _check_stop(should_cancel, should_pause)
    try:
        return _engine.ensure(
            python_path(profile_id),
            packages=_packages(profile),
            log=log_file(profile_id),
            requirements=requirements_path(profile_id),
            venv=venv_dir(profile_id),
            base_python=_base_python(profile),
            index_url=_text(getattr(profile, "index_url", "")) or _text(index_url),
            extra_index=_items(getattr(profile, "extra_index", ())),
            upgrade=upgrade,
            github_prefixes=github_prefixes,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
            on_start=lambda packages: _write_pending(profile_id, tuple(packages)),
            on_finish=lambda _python: _finish(profile_id),
        )
    except (_engine.PipStopped, _engine.PipError) as exc:
        raise _convert(exc) from exc


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
    """用本地 whl 装这个 profile（没有 venv 就先建）；返回解释器。

    装的是这些轮子，外加 profile 里**没被它们覆盖**的包（这样离线也装得完整）；
    引擎在 core，这里同样只补路径与留档（开工写标记，装完写 `installed.json`）。
    """
    if control is not None:
        should_cancel, should_pause = control.should_cancel, control.should_pause
    profile_id = _text(getattr(profile, "id", ""))
    if not profile_id:
        raise RuntimeError_("运行环境 profile 缺少 id")
    _check_stop(should_cancel, should_pause)
    try:
        return _engine.install_wheels(
            python_path(profile_id),
            [str(item) for item in wheels or ()],
            packages=_packages(profile),
            log=log_file(profile_id),
            venv=venv_dir(profile_id),
            base_python=_base_python(profile),
            index_url=_text(getattr(profile, "index_url", "")) or _text(index_url),
            extra_index=_items(getattr(profile, "extra_index", ())),
            upgrade=upgrade,
            github_prefixes=github_prefixes,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
            on_start=lambda files: _write_pending(profile_id, tuple(files)),
            on_finish=lambda _python: _finish(profile_id),
        )
    except (_engine.PipStopped, _engine.PipError) as exc:
        raise _convert(exc) from exc


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

    和 `ensure()` 一样只负责执行：是否安装由页面二次确认后决定。清单留档写到
    `system_requirements_path()`，输出追加进 `system_log_file()`。引擎在 core
    （`ensure_packages`），失败仍以「装进程序环境失败」开头，页面照旧认得出。
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
    requirements = system_requirements_path(profile_id)
    _write_pending(profile_id, packages, system=True)
    if not packages:
        _engine.write_requirements(requirements, packages)
        _emit(on_line, "清单为空，没有需要安装的包。")
        _clear_pending(profile_id, system=True)
        return target

    log = system_log_file(profile_id)
    _emit(on_line, "安装进程序环境：" + "、".join(packages))
    _emit(on_line, f"目标解释器：{target}")
    try:
        result = _engine.ensure_packages(
            target,
            packages=packages,
            log=log,
            requirements=requirements,
            index_url=_text(getattr(profile, "index_url", "")) or _text(index_url),
            extra_index=_items(getattr(profile, "extra_index", ())),
            upgrade=upgrade,
            github_prefixes=github_prefixes,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
    except _engine.PipStopped as exc:
        raise _convert(exc) from exc
    except _engine.PipError as exc:
        raise RuntimeError_(
            f"装进程序环境失败：{exc}"
            + _engine.network_hint(log)
            + _engine.long_path_hint(log)
        ) from exc
    _check_stop(should_cancel, should_pause)
    _clear_pending(profile_id, system=True)
    return result


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
    key = _text(profile_id)
    _clear_pending(profile_id)
    target = profile_root(key)
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


# ------------------------------------------------------------------ 引擎转发

#: 建 venv、跑 pip、GitHub 镜像重试、whl 校验这些通用件都在 core（`app.core.pip`），
#: 插件经 `app.sdk.pip` 用同一份实现（以前这里各抄了一份，两边得一起改）。
#: 下面只做两件事：把 core 的异常翻成插件自己的 `RuntimeStopped` / `RuntimeError_`
#: （页面按这两个类型分流），以及把与 profile 无关的纯工具直接指向 core。
Control = _engine.Control
check_wheels = _engine.check_wheels
package_versions = _engine.package_versions
github_asset_urls = _engine.github_asset_urls
mirror_github_urls = _engine.mirror_github_urls


def _emit(on_line: Callable[[str], None] | None, text: str) -> None:
    """把一行输出转给调用方（回调自己抛异常不算安装失败，和 core 一致）。"""
    _engine.emit(on_line, text)


def _convert(exc: Exception) -> RuntimeError_:
    """core 的安装异常 → 插件自己的异常类型（页面只认这两个）。"""
    if isinstance(exc, _engine.PipStopped):
        return RuntimeStopped(str(exc), paused=bool(getattr(exc, "paused", False)))
    return RuntimeError_(str(exc))


def _stream(
    command: Sequence[str],
    *,
    log: Path,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    control: Control | None = None,
) -> int:
    """跑一条命令并逐行回调（core 的 `stream`），被叫停按插件类型抛出。"""
    try:
        return _engine.stream(
            command,
            log=log,
            on_line=on_line,
            should_cancel=should_cancel,
            should_pause=should_pause,
            control=control,
        )
    except (_engine.PipStopped, _engine.PipError) as exc:
        raise _convert(exc) from exc


def _check_stop(
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None = None,
) -> None:
    """命中「取消 / 暂停」就抛 `RuntimeStopped`（取消优先，和 core 一个判序）。"""
    if should_cancel is not None and should_cancel():
        raise RuntimeStopped("安装已取消")
    if should_pause is not None and should_pause():
        raise RuntimeStopped("安装已暂停", paused=True)


def _finish(profile_id: str) -> None:
    """装完的留档：写 `installed.json`，再抹掉「正在安装」的标记。"""
    _write_marker(profile_id)
    _clear_pending(profile_id)
