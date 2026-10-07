"""测试基类：把数据库、库文件夹、内容仓库与配置重定向到临时目录。

所有测试都在 `tests/.tmp/<测试类名>/` 里运行，不会读写真实的 `.resources/` 与 `.configs/`。
临时目录由 `scripts/tmpenv.py` 统一管理，进程退出时整体删除；设置环境变量 `DM_KEEP_TMP=1`
可保留以便排查失败。新增用例的规范见 `docs/TESTS.md`。
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "scripts"
for _path in (ROOT, ROOT / "src", SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tmpenv import (  # noqa: E402
    KEEP_TMP,
    TempDir as _TempDir,
    redirect_paths,
    reset_config,
    reset_runtime_dirs,
    tests_tmp,
)
from tests.dataset import Corpus, build_corpus  # noqa: E402

#: 测试的临时根：`tests/.tmp/`（进程退出时整体删除）
TMP_ROOT = tests_tmp()

__all__ = [
    "KEEP_TMP",
    "ROOT",
    "TMP_ROOT",
    "Corpus",
    "IsolatedCase",
    "TempDir",
    "build_corpus",
    "redirect_paths",
    "reset_config",
    "reset_runtime_dirs",
]


class TempDir(_TempDir):
    """测试用临时目录：落在 `tests/.tmp/` 下。"""

    def __init__(self, prefix: str = "case") -> None:
        super().__init__(TMP_ROOT, prefix=prefix)


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
        `processEvents()` 执行，在本机 PyQt6 + CPython 3.14 上实测会以 0xC0000005 崩在 Qt 内部
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
