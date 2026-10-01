"""个人数据管理器：程序入口。

在 PyCharm 中把 `src` 标记为 Sources Root 后直接运行本文件即可。
"""

from __future__ import annotations

import os
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
from app.db.database import init_db, session_scope  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.services.layout_migration import migrate_layout  # noqa: E402
from app.services.open_with_service import OPEN_WITH_EXTENSION, open_with_api  # noqa: E402
from app.services.plugin_service import plugin_service  # noqa: E402
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


def main() -> int:
    paths.ensure_dirs()
    setup_logging()
    _apply_dpi_scale()
    init_db()
    with session_scope() as session:
        seed(session)
        migrate_layout(session)

    app = QApplication(sys.argv)
    _setup_translator(app)
    _apply_theme()

    # 程序本体以扩展接口的形式向插件开放界面能力（插件可注册自己的导航页面）
    plugin_service.bootstrap(APP_UI_EXTENSION, AppUiApi())
    # 打开方式接口：插件可以用它查 / 改某个扩展名该由哪个查看器打开
    plugin_service.bootstrap(OPEN_WITH_EXTENSION, open_with_api)
    logger.info("已载入 {} 个查看器插件", plugin_service.load_viewers())

    window = MainWindow()
    window.show()
    if "--self-check" in sys.argv:  # 打包后的冒烟测试：能建好界面就退出
        QTimer.singleShot(1500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
