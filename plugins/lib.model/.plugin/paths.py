"""模型工具库的路径：默认落在程序目录下的 `.models/`。

权重、下载临时文件、日志、运行环境都放这里；插件自己的目录只放只读模板，
因为插件覆盖安装时插件目录会被整个删掉重建。

模型根目录不再挂在资源文件夹（`.resources/models`）下面：
- 改「资源文件夹」不会连模型权重一起搬（也更不容易出半搬半留的问题）；
- 资源文件夹的 ACL 上锁不再牵连十几万个运行环境文件；
- 路径更浅，Windows 260 字符上限不容易被顶破。
用户在设置里可以改到别处，规矩和「资源文件夹」一致：**选中的目录只当容器，模型目录是它下面
的 `.models`**（见 `model_dir_candidate()`）。这样用户随手选一个文件夹也不会把权重和别的文件
混在一起，路径也不会因为「选的正好是深层目录」而变浅一层。

升级前留在 `.resources/models` 里的东西第一次用到模型目录时自动整体搬过来（同盘只是改名、
瞬间完成；跨盘先整份复制到临时目录、复制完整了再落位，落位成功才删旧目录），实在搬不动才
继续用旧位置，并把原因交给界面提示。

搬移失败会经 `app.sdk.console` 写进程序日志（`.logs/app-*.log`）：插件的 stdlib logging 没有
接进程序日志，曾经因此静默失败过一次，这里报错不再走 `logging`。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import NamedTuple

from app.sdk import storage

from .constants import PLUGIN_ID

#: 只有拿不到 SDK 时才用的兜底日志（进不了程序日志，见模块说明）
logger = logging.getLogger(__name__)

__all__ = [
    "ModelsDirUnusable",
    "MoveResult",
    "clear_model_logs",
    "configured_models_root",
    "default_models_root",
    "download_dir",
    "ensure_models_dir",
    "legacy_models_root",
    "local_dir",
    "local_root",
    "logs_dir",
    "model_dir_candidate",
    "model_log_file",
    "model_log_files",
    "models_root",
    "move_models_dir",
    "registry_file",
    "runtime_root",
    "settings_file",
    "slug_dir_name",
    "take_migration_note",
]

MODELS_DIR_NAME = ".models"
LEGACY_MODELS_DIR_NAME = "models"

#: 搬移模型目录时只让一个线程动手（界面线程与后台线程都会经过 `models_root()`）
_MIGRATION_LOCK = threading.Lock()

#: 跨盘搬移已经起过后台线程（只起一次）
_MIGRATION_STARTED = False

#: 自动迁移给用户看的话（界面取走后清空；没有迁移过就是空的）。(成功?, 文案)
_MIGRATION_NOTES: list[tuple[bool, str]] = []


class ModelsDirUnusable(OSError):
    """模型目录用不了：建不出来、写不进去（权限、被安全软件挡住、盘满……）。

    消息直接给用户看，所以带上是哪个目录、为什么、以及怎么改。
    """

    def __init__(self, path: str | Path, reason: object) -> None:
        self.path = Path(path)
        self.reason = str(reason)
        super().__init__(
            f"模型目录用不了：{self.path}（{self.reason}）。"
            "请在「模型」页把模型目录换到能写的空目录，或删掉这个目录再试。"
        )


class MoveResult(NamedTuple):
    """搬移模型目录的结果。

    - `moved`：新位置现在是否是一份完整的模型目录（改名的结果，或复制完整后落位成功）；
    - `leftover`：旧目录还没清干净的路径（`moved` 为假时就是原目录，数据还在那儿）；
    - `reason`：失败原因（成功时为空串），一句给用户看的话。
    """

    moved: bool
    leftover: str = ""
    reason: str = ""


def _emit(level: str, message: str) -> None:
    """把路径层出的问题写进程序日志；拿不到 SDK 就退回 stdlib（绝不抛出）。"""
    text = str(message)
    try:
        from app.sdk import console as console_api

        if level == "error":
            console_api.error(text, source=PLUGIN_ID)
        elif level == "info":
            console_api.info(text, source=PLUGIN_ID)
        else:
            console_api.warning(text, source=PLUGIN_ID)
        return
    except Exception:  # noqa: BLE001 - 报日志本身不该影响主流程
        pass
    logger.warning("%s", text)


def _ensure(directory: Path) -> Path:
    """按需建目录；建不出来时抛一句能照着办的错，而不是只给 WinError 5 加个临时路径。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModelsDirUnusable(directory, exc) from exc
    return directory


def model_dir_candidate(folder: str | Path) -> Path:
    """用户选的文件夹 → 真正的模型目录：`<所选>/.models`（和资源文件夹同一个规矩）。

    例外：本来就叫 `.models`（或旧名字 `models`）的目录直接用它；已经是模型目录本身
    （里面有 `registry.json`）的也直接用——老配置存的就是那种目录，不能给它再套一层。
    """
    path = Path(folder).expanduser()
    if path.name in (MODELS_DIR_NAME, LEGACY_MODELS_DIR_NAME):
        return path
    if (path / "registry.json").is_file():
        return path
    return path / MODELS_DIR_NAME


def ensure_models_dir(folder: str | Path) -> Path:
    """把用户选的文件夹准备成能写东西的模型目录；用不了就抛 `ModelsDirUnusable`。

    只建目录 + 写一个探针文件，不搬任何东西：搬家前先问一句「这儿能写吗」，
    免得搬到一半才发现目标目录被 ACL / 安全软件挡着（那样只会留下半份文件）。
    """
    target = model_dir_candidate(folder)
    _ensure(target)
    probe = target / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        raise ModelsDirUnusable(target, exc) from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return target


def default_models_root() -> Path:
    """默认模型根目录：程序文件根目录下的 `.models`（隐藏目录，不随资源文件夹走）。"""
    return storage.app_root() / MODELS_DIR_NAME


def legacy_models_root() -> Path:
    """旧模型根目录：资源文件夹下的 `models`（升级以前的默认位置，仍可读）。"""
    return storage.resources_dir() / LEGACY_MODELS_DIR_NAME


def _storage_section() -> dict:
    """插件设置里的 `storage` 段（没写或写坏了就给空 dict）。

    直接读设置 JSON，不 import `settings`：那个模块反过来依赖本模块，会成环。
    """
    payload = storage.read_json(settings_file(), {})
    section = payload.get("storage") if isinstance(payload, dict) else None
    return section if isinstance(section, dict) else {}


def configured_models_root() -> str:
    """用户在设置里指定的模型目录（没设置就返回空串）。"""
    return str(_storage_section().get("models_root") or "").strip()


def move_models_dir(source: str | Path, target: str | Path) -> MoveResult:
    """整体搬移模型目录（「换到别处」与「恢复默认」都走这里）。

    顺序：同盘先试改名（113k 个文件也是瞬间完成，且不会剩下半份）；改不动就整份复制到一个
    临时目录 `<目标>.moving`（和目标的盘相同），复制完整了才改名落到目标名下——中途断电、
    关程序都不会留下半份冒充新模型目录。只有新位置确认完整了才动旧目录，旧目录删不掉就把它
    交给调用方（记进设置，下次进页面再清）。
    """
    source = Path(source)
    target = Path(target)
    try:
        same = source.resolve() == target.resolve()
    except OSError:
        same = False
    if same:
        return MoveResult(True)
    if target.is_dir() and any(target.iterdir()):
        # 目标里已经有东西：不做两份模型目录的合并（登记表会打架）
        return MoveResult(False, str(source), f"目标目录里已经有文件，不能合并：{target}")
    if not source.is_dir():
        # 没有旧目录可搬（首次使用，或已经被别的进程搬走）：把目标建出来就算完成
        try:
            _ensure(target)
        except ModelsDirUnusable as exc:
            return MoveResult(False, str(source), str(exc))
        return MoveResult(True)
    if target.is_dir():
        try:
            target.rmdir()  # 空目录：腾开位置，让改名一步到位
        except OSError:
            pass
    try:
        _ensure(target.parent)
    except ModelsDirUnusable as exc:
        return MoveResult(False, str(source), str(exc))
    try:
        source.rename(target)
        return MoveResult(True)
    except OSError as exc:
        rename_error = exc
    if not source.is_dir() and target.is_dir():
        return MoveResult(True)  # 别的进程刚好搬完了
    staging = target.with_name(f"{target.name}.moving")
    shutil.rmtree(staging, ignore_errors=True)  # 上次搬了一半留下的
    try:
        shutil.copytree(source, staging, symlinks=False)
    except Exception as exc:  # noqa: BLE001 - 复制以任何方式失败都不能留半份
        shutil.rmtree(staging, ignore_errors=True)
        reason = f"复制失败：{exc}（改名也不行：{rename_error}）"
        _emit("warning", f"搬移模型目录失败：{source} → {target}：{reason}")
        return MoveResult(False, str(source), reason)
    if target.exists():
        shutil.rmtree(staging, ignore_errors=True)
        return MoveResult(False, str(source), f"复制期间 {target} 被别的东西占住了，没有覆盖它")
    try:
        staging.rename(target)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        reason = f"复制完成、但放到目标位置失败：{exc}"
        _emit("warning", f"搬移模型目录失败：{source} → {target}：{reason}")
        return MoveResult(False, str(source), reason)
    try:
        shutil.rmtree(source)
    except OSError as exc:
        _emit("warning", f"模型目录已搬到 {target}，但旧目录删不掉（有程序占着）：{source}：{exc}")
        return MoveResult(True, str(source))
    return MoveResult(True)


def _note_migration(text: str, *, ok: bool) -> None:
    """记一条给用户看的迁移说明（界面取走显示，取走即清空）。"""
    _MIGRATION_NOTES.append((ok, text))
    _emit("info", text)


def take_migration_note() -> tuple[bool, str]:
    """取走并清空所有待显示的迁移说明；返回 `(是否成功, 文案)`（没有就是 `(True, "")`）。

    后台线程可能正在追加说明，所以取走这一步连 `_MIGRATION_LOCK` 一起做，别把刚记下的那条吞掉。
    """
    with _MIGRATION_LOCK:
        if not _MIGRATION_NOTES:
            return True, ""
        notes = list(_MIGRATION_NOTES)
        _MIGRATION_NOTES.clear()
    return all(ok for ok, _ in notes), "\n".join(text for _, text in notes)


def _remember_pending_cleanup(path: Path) -> None:
    """把「没删干净的旧目录」记进设置，下次进「模型」页再清（直接改 JSON，避免循环依赖）。

    设置对象在界面线程里是内存态，这里写盘可能被界面随后的一次 `save()` 覆盖：
    清理只是顺手做的事，漏一次也没关系。
    """
    try:
        file = settings_file()
        payload = storage.read_json(file, {})
        if not isinstance(payload, dict):
            payload = {}
        section = payload.get("storage") if isinstance(payload.get("storage"), dict) else {}
        pending = [str(item) for item in (section.get("pending_cleanup") or []) if str(item)]
        text = str(path)
        if text not in pending:
            pending.append(text)
        section["pending_cleanup"] = pending
        payload["storage"] = section
        storage.write_json(file, payload)
    except Exception as exc:  # noqa: BLE001 - 记不上就算了，界面下次还会看到提示
        _emit("warning", f"记下待清理的旧模型目录失败：{path}：{exc}")


def _background_migration(legacy: Path, preferred: Path) -> None:
    """改名搬不动时（跨盘 / 有程序占着）在后台整份复制，搬完记一条给人看的说明。"""
    result = move_models_dir(legacy, preferred)
    if not result.moved:
        _note_migration(f"旧模型目录没能搬走，这次仍用旧位置：{legacy}（{result.reason}）", ok=False)
        return
    if result.leftover:
        _note_migration(
            f"模型目录已搬到 {preferred}，但旧目录没删干净（有程序占着）：{result.leftover}", ok=True
        )
        _remember_pending_cleanup(Path(result.leftover))
        return
    _note_migration(f"模型目录已从旧位置搬到 {preferred}", ok=True)


def _start_background_migration(legacy: Path, preferred: Path) -> None:
    """起一个后台线程做搬移（只起一次；同盘改名的那条快路在 `models_root()` 里同步做）。"""
    global _MIGRATION_STARTED
    with _MIGRATION_LOCK:
        if _MIGRATION_STARTED:
            return
        _MIGRATION_STARTED = True
    threading.Thread(
        target=_background_migration, args=(legacy, preferred), name="model-dir-migration", daemon=True
    ).start()


def _adopt_legacy_models_root(preferred: Path) -> Path:
    """旧位置还有东西时的处理：能改名就当场搬（同盘瞬间完成），否则先用旧的、后台再搬。"""
    legacy = legacy_models_root()
    if preferred.exists():
        return preferred
    try:
        legacy.rename(preferred)
    except OSError:
        if preferred.is_dir() and not legacy.is_dir():
            return preferred  # 另一个进程刚好搬完了
        _start_background_migration(legacy, preferred)
        return legacy
    _note_migration(f"模型目录已从旧位置搬到 {preferred}", ok=True)
    return preferred


def models_root() -> Path:
    """模型根目录（按需创建）。三种情况：

    1. 设置里指定了目录 → 用它（用户选的那个文件夹下面的 `.models`，见
       `model_dir_candidate()`；老配置里存的模型目录本身也照旧认）；
    2. 没指定、程序目录下的 `.models` 已经存在 → 用它；
    3. 没指定、`.models` 还不存在、而旧的 `<资源文件夹>/models` 有东西 → **整体搬过来**
       （同盘只是改名、当场完成；跨盘或有程序占着就先继续用旧位置、后台慢慢复制），
       老用户的登记表与权重不用重新登记。

    建不出来（权限 / 安全软件 / 盘满）时抛 `ModelsDirUnusable`：宁可在设置页明说这个位置用
    不了，也不要拿着一个假装成功的目录继续跑。
    """
    configured = configured_models_root()
    if configured:
        target = model_dir_candidate(configured)
    else:
        preferred = default_models_root()
        if preferred.is_dir():
            target = preferred
        elif legacy_models_root().is_dir():
            target = _adopt_legacy_models_root(preferred)
        else:
            target = preferred
    return _ensure(target)


def registry_file() -> Path:
    """模型登记表。"""
    return models_root() / "registry.json"


def local_root() -> Path:
    """本地模型权重根目录。"""
    return _ensure(models_root() / "local")


def slug_dir_name(model_id: str) -> str:
    """`local/<slug>` 取末段当目录名，避免越界；没有名字时返回空串（不编一个假名字）。"""
    text = str(model_id or "").replace("\\", "/").strip()
    return Path(text).name if text else ""


def local_dir(model_id: str) -> Path:
    """某个本地模型的权重目录（按需创建）。

    空 id 直接返回权重根目录本身：以前会退回 `local/model`，凭空造一个空目录，
    还会被「清理未使用权重」当成垃圾列出来。
    """
    slug = slug_dir_name(model_id)
    if not slug:
        return local_root()
    return _ensure(local_root() / slug)


def download_dir(model_id: str = "") -> Path:
    """下载临时目录（`.part` 与断点信息）。"""
    target = models_root() / "download"
    if model_id:
        target = target / slug_dir_name(model_id)
    return _ensure(target)


def logs_dir() -> Path:
    """模型与 worker 日志目录。"""
    return _ensure(models_root() / "logs")


def runtime_root() -> Path:
    """运行环境根目录（每个 profile 一个 venv）。"""
    return _ensure(models_root() / "runtime")


def settings_file() -> Path:
    """插件设置文件（`.configs/models.json`）。"""
    return storage.config_file("models.json")

_LEGACY_LOG = re.compile(r"^(?P<slug>.+)-(?P<stamp>\d{8}-\d{6})\.log$")


def _log_slug(model_id: str) -> str:
    """日志文件名里的 slug（`local/qwen` → `local-qwen`）：和卡片、登记表里的叫法一致。"""
    from .record import slugify  # 延迟导入：record 依赖 paths，顶层 import 会成环

    return slugify(str(model_id or "worker"))


def model_log_file(model_id: str) -> Path:
    """一条模型的运行日志（一个模型只这一个文件，每次运行重写）。"""
    return logs_dir() / f"{_log_slug(model_id)}.log"


def model_log_files(model_id: str) -> list[Path]:
    """这条模型现在有的日志，最新的排在最后。

    新版本一个模型只有一个文件；老版本按时间戳攒过一堆（`<slug>-20260101-010101.log`），
    这里也一并认出来，免得界面上看不到、删模型时又漏掉。
    """
    slug = _log_slug(model_id)
    stable = logs_dir() / f"{slug}.log"
    legacy: list[Path] = []
    for path in logs_dir().glob(f"{slug}-*.log"):
        match = _LEGACY_LOG.match(path.name)
        if match and match.group("slug") == slug and path.is_file():
            legacy.append(path)
    found = sorted(legacy)
    if stable.is_file():
        found.append(stable)
    return found


def clear_model_logs(model_id: str) -> list[Path]:
    """删掉一条模型的日志（新单文件 + 老版本的时间戳文件），返回删掉的文件。"""
    removed: list[Path] = []
    for path in model_log_files(model_id):
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed
