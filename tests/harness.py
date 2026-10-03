"""测试基类：把数据库、库文件夹、内容仓库与配置重定向到临时目录。

所有测试都在 `tests/_tmp/<测试类名>/` 里运行，不会读写真实的 `.resources/` 与 `.configs/`。
设置环境变量 `DM_KEEP_TMP=1` 可保留临时目录以便排查失败。
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.core import paths  # noqa: E402
from app.core.config import config  # noqa: E402
from tests.dataset import Corpus, build_corpus  # noqa: E402

TMP_ROOT = ROOT / "tests" / "_tmp"
KEEP_TMP = bool(os.environ.get("DM_KEEP_TMP"))


class TempDir:
    """临时目录。

    tempfile.mkdtemp 以 0o700 建目录，本机沙箱下该目录里无法再写入文件，因此改用普通 mkdir 建目录。
    """

    _counter = itertools.count(1)

    def __init__(self, prefix: str = "case") -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.name = str(TMP_ROOT / f"{prefix}-{os.getpid()}-{next(self._counter)}")
        Path(self.name).mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return Path(self.name)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()


def redirect_paths(root: Path) -> None:
    """把 paths 里的运行期目录整体指向临时根目录。"""
    data = paths.apply_resource_root(root / paths.RESOURCE_ROOT_NAME)
    paths.LOG_DIR = root / paths.LOG_DIR_NAME
    paths.DEFAULT_EXPORT_DIR = root / "exports"
    paths.CONFIG_DIR = root / paths.CONFIG_DIR_NAME
    paths.CONFIG_FILE = paths.CONFIG_DIR / "config.json"
    paths.PLUGIN_DIR = root / "plugins"
    paths.PLUGIN_STATE_FILE = paths.CONFIG_DIR / "plugins.json"
    paths.VIEWER_RULES_FILE = paths.CONFIG_DIR / "viewers.json"
    paths.LEGACY_VIEWER_RULES_FILE = paths.CONFIG_DIR / "open_with.json"
    paths.SESSION_FILE = paths.CONFIG_DIR / "session.json"
    paths.ensure_dirs()
    return data


def reset_config(root: Path) -> None:
    """把配置的保存目标改到临时目录，并把影响用例的配置项恢复为默认值。"""
    from qfluentwidgets import qconfig

    target = root / paths.CONFIG_DIR_NAME / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    qconfig.load(str(target), config)
    config.set(config.dbUrl, "")
    config.set(config.dbEcho, False)
    config.set(config.resourcePath, str(paths.DATA_DIR))
    config.set(config.resourceProtected, False)
    config.set(config.hiddenProtected, False)
    config.set(config.exportPath, "")
    config.set(config.currentUserId, 0)
    config.set(config.nameByTime, False)
    config.set(config.duplicatePolicy, "rename")
    config.set(config.coverSize, 256)
    config.set(config.pruneMode, "none")
    config.set(config.keepVersions, 10)
    config.set(config.keepSize, 2048)
    config.set(config.keepDays, 30)
    config.set(config.pageSize, 50)
    config.set(config.showCategoryPanel, True)
    config.set(config.showFilterPanel, True)
    config.set(config.expandCategories, False)
    config.set(config.expandedFilters, [])
    config.set(config.simpleDisplay, "none")
    config.set(config.tooltipDelay, 2000)


def reset_runtime_dirs() -> None:
    """清空库文件夹、内容仓库、封面与数据库文件，让每个用例都从零开始。"""
    for folder in (
        paths.DEFAULT_LIBRARY_DIR,
        paths.LEGACY_STORE_DIR,
        paths.LEGACY_COVER_DIR,
        paths.DEFAULT_EXPORT_DIR,
    ):
        shutil.rmtree(folder, ignore_errors=True)
    for name in ("data.db", "data.db-wal", "data.db-shm"):
        (paths.DATA_DIR / name).unlink(missing_ok=True)
    paths.ensure_dirs()


class IsolatedCase(unittest.TestCase):
    """每个测试类一套隔离目录与数据库；每个测试方法一份干净的数据。"""

    root: Path
    corpus: Corpus
    session: object

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = TMP_ROOT / cls.__name__.lower()
        shutil.rmtree(cls.root, ignore_errors=True)
        redirect_paths(cls.root)
        reset_config(cls.root)
        from app.db import database

        database.dispose_engine()
        database.init_db(force=True)
        cls.corpus = build_corpus(cls.root / "corpus")
        cls.session = None
        cls.session = cls.fresh_session()

    @classmethod
    def tearDownClass(cls) -> None:
        from app.db import database

        if cls.session is not None:
            cls.session.rollback()
            cls.session.close()
            cls.session = None
        database.dispose_engine()
        if not KEEP_TMP:
            shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def fresh_session(cls):
        """重建数据库并写入默认用户 / 分类 / 标签 / 库。"""
        from app.db import database
        from app.db.seed import seed

        if cls.session is not None:
            cls.session.rollback()
            cls.session.close()
        database.dispose_engine()
        reset_runtime_dirs()
        database.init_db(force=True)
        session = database.new_session()
        seed(session)
        session.commit()
        return session

    def setUp(self) -> None:
        reset_config(type(self).root)
        type(self).session = self.fresh_session()
        self.session = type(self).session

    def tearDown(self) -> None:
        self.session.rollback()
        self.session.close()

    def drop_widget(self, widget) -> None:
        """确定性地销毁页面控件：先关闭它自己的会话，再立刻销毁控件。

        这里不用 `deleteLater()`：把整棵控件树的销毁排进事件队列后再由后续用例的
        `processEvents()` 执行，在本机 PyQt6 + Python 3.14 上会以 0xC0000005 崩在 Qt 内部
        （只 `deleteLater()` 单个控件没事，整页一起排队才会崩），因此改为立即销毁。
        """
        if widget is None:
            return
        session = getattr(widget, "session", None)
        if session is not None:
            session.close()
        widget.close()
        widget.setParent(None)
        from PyQt6 import sip

        sip.delete(widget)
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    # ------------------------------------------------------------------ 便捷方法
    def current_user(self):
        from app.services import UserService

        return UserService(self.session).current()

    def default_library(self):
        from app.services import LibraryService

        return LibraryService(self.session).ensure_default()

    def importer(self, **kwargs):
        from app.services import ImportService

        return ImportService(self.session, **kwargs)
