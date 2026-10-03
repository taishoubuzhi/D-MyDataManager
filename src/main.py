"""个人数据管理器：程序入口。

在 PyCharm 中把 `src` 标记为 Sources Root 后直接运行本文件即可。
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import runpy
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许直接以脚本方式运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QTimer, QTranslator  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from loguru import logger  # noqa: E402
from qfluentwidgets import FluentTranslator, Theme, setTheme  # noqa: E402

from app.core import paths  # noqa: E402
from app.core.app_ui import APP_UI_EXTENSION, AppUiApi  # noqa: E402
from app.core.config import Language, config  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.db.database import dispose_engine, init_db, session_scope  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.services.layout_migration import migrate_layout, migrate_uncategorized  # noqa: E402
from app.services.plugin_service import plugin_service  # noqa: E402
from app.services.privacy_service import privacy  # noqa: E402
from app.ui.framework import install_app_theme, install_tooltips  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def _apply_dpi_scale() -> None:
    """按设置固定界面缩放：非“自动”时关闭 Qt 自适应并指定倍率。"""
    value = str(config.dpiScale.value)
    if value == "Auto":
        return
    try:
        factor = int(value.rstrip("%")) / 100
    except ValueError:
        return
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = f"{factor:g}"


def _setup_translator(app: QApplication) -> None:
    app.installTranslator(FluentTranslator())
    if config.language.value is Language.ENGLISH:
        return
    translator = QTranslator()
    qm_file = paths.RESOURCE_DIR / "i18n" / "app.zh_CN.qm"
    if qm_file.exists() and translator.load(str(qm_file)):
        app.installTranslator(translator)


def _apply_theme() -> None:
    setTheme({"light": Theme.LIGHT, "dark": Theme.DARK}.get(config.theme.value, Theme.AUTO))


#: 启动阶段总数：控制台按 `启动 n/N` 报进度，出错时一眼看出卡在哪一步
_STARTUP_STAGES = 8


def _stage(step: int, text: str) -> None:
    """启动阶段提示（配合 `_STARTUP_STAGES` 使用）。"""
    logger.info("启动 {}/{}：{}", step, _STARTUP_STAGES, text)


#: 退出流程只跑一次（aboutToQuit 与 atexit 都会调）
_locked = False


def _unlock_only() -> int:
    """`--unlock`：程序因保护区打不开时的应急命令，放行后不启动界面。"""
    count, message = privacy.force_unlock()
    text = f"已放行 {count} 个目录" + (f"（{message}）" if message else "")
    logger.info(text)
    print(text)
    return 0


def _previous_session_marker() -> dict:
    """上次会话留下的标记；没有则返回空字典。"""
    try:
        raw = paths.SESSION_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("读取会话标记失败：{}", exc)
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_session_marker() -> None:
    """写下本次会话标记（配置目录不在保护区里，任何时候都能写）。"""
    payload = {
        "pid": os.getpid(),
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "resource_protected": bool(config.resourceProtected.value),
        "hidden_protected": bool(config.hiddenProtected.value),
    }
    try:
        paths.make_dir(paths.SESSION_FILE.parent)
        paths.SESSION_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("写入会话标记失败：{}", exc)


def _clear_session_marker() -> None:
    try:
        paths.SESSION_FILE.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除会话标记失败：{}", exc)


def _report_previous_session() -> None:
    """上次没正常退出（标记残留）时记一条日志：保护可能没锁上，但启动不受影响。"""
    previous = _previous_session_marker()
    if not previous:
        return
    logger.warning(
        "上次会话未正常结束（标记残留：pid={}，开始于 {}）；保护目录可能仍处于放行状态，本次启动会重新放行一次",
        previous.get("pid", "?"),
        previous.get("started_at", "?"),
    )
    _clear_session_marker()


def _bootstrap_data() -> None:
    """建目录、开库并补齐基础数据（此时资源文件夹已放行）。"""
    paths.ensure_dirs()
    init_db()
    with session_scope() as session:
        seed(session)
        migrate_layout(session)
        migrate_uncategorized(session)


def _degrade_protection(exc: Exception) -> None:
    """放行失败时的兜底：关掉保护开关并强制放行——宁可少一层保护，也不能让程序打不开。"""
    logger.error("资源文件夹保护影响了启动（{}）：已关闭保护开关并强制放行", exc)
    config.set(config.resourceProtected, False)
    config.set(config.hiddenProtected, False)
    count, message = privacy.force_unlock()
    logger.info("强制放行完成：{} 个目录（{}）", count, message)


def _lock_on_exit() -> None:
    """退出前收好数据库，再按设置锁定——静态保护下这是唯一真正锁上的时机。"""
    global _locked
    if _locked:
        return
    _locked = True
    try:
        dispose_engine()
        count, message = privacy.end_session()
        if count:
            logger.info("已按隐私设置锁定 {} 个目录", count)
        elif message and message != "没有开启保护":
            logger.warning("退出前锁定失败：{}", message)
    except Exception as exc:  # 退出流程不能再抛异常
        logger.warning("退出前锁定失败：{}", exc)
    _clear_session_marker()
    logger.info("退出：数据库已收起、会话标记已清理")


def _run_selfcheck() -> int | None:
    """`--self-check`：源码仓库里存在新自检套件时交给它运行，返回它的退出码。

    套件自带隔离（临时库、临时配置），所以这里在启动流程之前就返回，不碰真实数据；
    打包后没有 `scripts/` 目录，返回 None 让调用方退回「建好界面就退出」的冒烟测试。
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "selfcheck.py"
    if not script.is_file():
        return None
    sys.argv = [arg for arg in sys.argv if arg != "--self-check"]
    logger.info("运行自检套件：{} {}", script, " ".join(sys.argv[1:]))
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return 0


def main() -> int:
    if "--unlock" in sys.argv:  # 应急：只放行保护区，不启动界面
        return _unlock_only()
    if "--self-check" in sys.argv and (code := _run_selfcheck()) is not None:
        return code
    setup_logging()
    _stage(1, f"日志系统已就绪（级别 {config.logLevel.value}、控制台输出 {config.logToConsole.value}）")
    _apply_dpi_scale()
    _stage(2, f"界面缩放已应用（{config.dpiScale.value}）")
    _report_previous_session()
    _write_session_marker()
    _stage(3, "会话检查完成，已写下本次会话标记")
    # 启动自愈第一步：无论上次是正常退出还是崩溃，先放行资源文件夹；静态保护下运行期一直放行
    privacy.begin_session()
    _stage(4, "隐私保护已放行受保护目录")
    try:
        _bootstrap_data()
    except Exception as exc:
        _degrade_protection(exc)
        try:
            _bootstrap_data()
        except Exception as fatal:
            logger.exception("数据库初始化失败，程序无法启动：{}", fatal)
            print(
                f"数据库初始化失败：{fatal}\n可执行 `python src/main.py --unlock` 应急放行后重试",
                file=sys.stderr,
            )
            return 2
    _stage(5, "数据库已就绪（建表、基础数据、布局迁移与未分类迁移完成）")

    app = QApplication(sys.argv)
    _setup_translator(app)
    _apply_theme()
    # 换肤只管 QSS：把调色板也换成当前主题，否则系统深色模式下浅色主题会露出发黑的底色
    install_app_theme()
    # 悬停提示：等待时间读配置项，没写提示的按钮用它的文字兜底
    install_tooltips()
    _stage(6, f"界面框架已就绪（主题 {config.theme.value}、语言 {config.language.value.value}、悬停提示已安装）")
    # 退出时把资源文件夹与隐藏目录重新锁上（开启保护时）
    app.aboutToQuit.connect(_lock_on_exit)
    atexit.register(_lock_on_exit)  # 控制台 Ctrl+C 等非 Qt 退出路径也收尾

    # 程序本体以扩展接口的形式向插件开放界面能力（插件可注册自己的导航页面）
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    # 载入插件：SDK 横幅、每个插件的「已载入」与最后的汇总都由插件系统自己播报
    plugin_service.load_viewers()
    _stage(7, "插件系统已就绪")

    window = MainWindow()
    window.show()
    _stage(8, "主窗口已显示，进入事件循环")
    if "--self-check" in sys.argv:  # 打包后的冒烟测试：能建好界面就退出
        QTimer.singleShot(1500, app.quit)
    code = app.exec()
    logger.info("事件循环结束，退出码 {}", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
