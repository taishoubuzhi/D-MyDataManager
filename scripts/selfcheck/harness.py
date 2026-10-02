"""自检套件的运行骨架：检查注册表、隔离环境与统一结果收集。

每个检查都在自己的临时目录与全新数据库上运行（复用 `tests/harness.py` 的重定向
工具），只调用公开契约，不接触真实的 `.resources/` 与 `config/`。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src", SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

LAYERS: tuple[str, ...] = ("data", "services", "pages", "flows")

#: 每层可以拆成多个模块，按主题分工、便于并行维护。
MODULES: dict[str, tuple[str, ...]] = {
    "data": ("checks_data",),
    "services": ("checks_services",),
    "pages": ("checks_pages", "checks_manage_ui", "checks_tags_ui", "checks_archive_ui"),
    "flows": ("checks_flows", "checks_tags_ui"),
}

#: 主窗口上九个页面的属性名（页面级检查共用）。
PAGE_ATTRS: tuple[str, ...] = (
    "home_page",
    "import_page",
    "manage_page",
    "tag_page",
    "user_page",
    "archive_page",
    "open_with_page",
    "plugin_page",
    "settings_page",
)


@dataclass(frozen=True)
class Check:
    """一项检查：名字、分层、函数与一句说明。"""

    name: str
    layer: str
    func: Callable[["Case"], None]
    doc: str = ""


REGISTRY: list[Check] = []


def check(name: str, layer: str) -> Callable[[Callable[["Case"], None]], Callable[["Case"], None]]:
    """把函数登记成一项检查；`layer` 决定它属于哪一组，名字必须唯一。"""
    if layer not in LAYERS:
        raise ValueError(f"未知分层：{layer}")
    if any(item.name == name for item in REGISTRY):
        raise ValueError(f"检查名重复：{name}")

    def decorate(func: Callable[["Case"], None]) -> Callable[["Case"], None]:
        REGISTRY.append(Check(name=name, layer=layer, func=func, doc=(func.__doc__ or "").strip()))
        return func

    return decorate


class Case:
    """一个检查的隔离环境：临时根目录 + 全新数据库 + 已播种的默认数据。"""

    def __init__(self, name: str, *, keep: bool = False) -> None:
        from tests.harness import TempDir, redirect_paths, reset_config, reset_runtime_dirs

        self.name = name
        self._keep = keep
        self._temp = TempDir(prefix=f"selfcheck-{name}")
        self.root: Path = self._temp.path
        self._reset_runtime_dirs = reset_runtime_dirs
        redirect_paths(self.root)
        reset_config(self.root)
        self.session = self._bootstrap()

    # ------------------------------------------------------------------ 环境
    def _bootstrap(self):
        from app.db import database
        from app.db.seed import seed

        database.dispose_engine()
        self._reset_runtime_dirs()
        database.init_db(force=True)
        session = database.new_session()
        seed(session)
        session.commit()
        return session

    def fresh(self):
        """清空数据库与运行期目录后重新播种，返回新的会话。"""
        from app.db import database

        self.session.rollback()
        self.session.close()
        database.dispose_engine()
        self.session = self._bootstrap()
        return self.session

    def new_session(self):
        """另开一个会话（调用方负责 close）。"""
        from app.db import database

        return database.new_session()

    def close(self) -> None:
        from app.db import database

        try:
            self.session.rollback()
            self.session.close()
        finally:
            database.dispose_engine()
            if not self._keep:
                self._temp.cleanup()


#: 必须留一个强引用，否则 QApplication 会被回收，随后建控件直接崩。
_APP = None


def ensure_app():
    """在 offscreen 模式下建好 QApplication（页面 / 流程层用）。"""
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


def dispose_window(window) -> None:
    """确定性地销毁主窗口（整棵控件树排队销毁在本机会 0xC0000005）。"""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QApplication

    from app.ui.components.cover_loader import shutdown_cover_loader

    shutdown_cover_loader()
    session = getattr(window, "_watch_session", None)
    if session is not None:
        session.close()
    window.close()
    window.setParent(None)
    sip.delete(window)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def build_window(case: "Case", *, show: bool = False):
    """造代表性数据并装配主窗口，返回 `(夹具, 主窗口)`。

    调用方负责 `dispose_window(window)`；需要可见性（主题、绘制、点击）时传 `show=True`。
    """
    from app.ui.main_window import MainWindow

    from .fixtures import build

    ensure_app()
    fixture = build(case)
    window = MainWindow()
    for attr in PAGE_ATTRS:
        refresh = getattr(getattr(window, attr, None), "refresh", None)
        if callable(refresh):
            refresh()
    if show:
        window.show()
    return fixture, window


def load(layers: Sequence[str] = ()) -> list[Check]:
    """导入指定分层的检查模块，返回按分层顺序排好的检查列表。"""
    import importlib

    wanted = tuple(layers) or LAYERS
    unknown = [layer for layer in wanted if layer not in LAYERS]
    if unknown:
        raise ValueError(f"未知分层：{unknown}；可选 {list(LAYERS)}")
    for layer in wanted:
        for module in MODULES.get(layer, ()):
            if not (Path(__file__).resolve().parent / f"{module}.py").exists():
                continue  # 该层还没实现时跳过
            importlib.import_module(f"{__package__}.{module}")
    return [item for item in REGISTRY if item.layer in wanted]


def select(checks: Sequence[Check], only: Sequence[str] = ()) -> list[Check]:
    """按名字挑选检查；`only` 为空时全选。"""
    if not only:
        return list(checks)
    wanted = set(only)
    known = {item.name for item in checks}
    missing = sorted(wanted - known)
    if missing:
        raise ValueError(f"没有这些检查：{missing}")
    return [item for item in checks if item.name in wanted]


@dataclass(frozen=True)
class Result:
    """一项检查的运行结果。"""

    name: str
    layer: str
    ok: bool
    detail: str
    seconds: float
    root: Path | None = None
    traceback: str = ""


def origin(exc: BaseException) -> str:
    """异常出处（`文件:行`），让失败行本身就能定位。"""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return ""
    frame = frames[-1]
    return f"{Path(frame.filename).name}:{frame.lineno}"


def run(
    checks: Sequence[Check],
    *,
    keep: bool = False,
    verbose: bool = False,
    as_json: bool = False,
) -> list[Result]:
    """顺序跑完检查，返回结果列表；末行始终是 `RESULT failures=N`。

    `as_json` 时每项结果打一行 JSON（末行仍是 RESULT），便于脚本抓取。
    """
    results: list[Result] = []
    for item in checks:
        started = time.perf_counter()
        case = Case(item.name, keep=keep)
        ok, detail, error_trace = True, "", ""
        try:
            item.func(case)
        except BaseException as exc:  # noqa: BLE001 - 自检要把一切失败都收集成一行
            ok = False
            detail = f"{type(exc).__name__}: {exc} ({origin(exc)})"
            error_trace = traceback.format_exc()
        finally:
            try:
                case.close()
            except Exception as exc:  # noqa: BLE001 - 清理失败不应掩盖检查结果
                print(f"WARN 清理失败 {item.name}: {exc}", flush=True)
        result = Result(
            name=item.name,
            layer=item.layer,
            ok=ok,
            detail=detail,
            seconds=round(time.perf_counter() - started, 3),
            root=case.root if keep else None,
            traceback=error_trace,
        )
        results.append(result)
        if as_json:
            print(
                json.dumps(
                    {
                        "name": result.name,
                        "layer": result.layer,
                        "ok": result.ok,
                        "detail": result.detail,
                        "seconds": result.seconds,
                        "root": str(result.root) if result.root else None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elif ok:
            print(f"ok   [{item.layer}] {item.name} ({result.seconds:.2f}s)", flush=True)
        else:
            print(f"FAIL [{item.layer}] {item.name}: {detail}", flush=True)
        if verbose and error_trace:
            print(error_trace, flush=True)
    failed = [result for result in results if not result.ok]
    if as_json:
        print(
            json.dumps(
                {
                    "total": len(results),
                    "failed": len(failed),
                    "failed_names": [result.name for result in failed],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    print(f"selfcheck: {len(results) - len(failed)}/{len(results)} 通过", flush=True)
    if keep:
        for result in results:
            if result.root is not None:
                print(f"kept: {result.name} -> {result.root}", flush=True)
    print(f"RESULT failures={len(failed)}", flush=True)
    return results
