"""数据库层：连接 PRAGMA（批 J2）与引擎整理。

批 J2：WAL 之外再开 `synchronous=NORMAL`、
`temp_store=MEMORY`、页缓存、只读映射与 busy 等待，并给「整理数据库」一个入口。
"""

from __future__ import annotations

import unittest

from sqlalchemy import text

from app.db import database
from app.db.database import SQLITE_PRAGMAS, optimize_database
from tests.harness import IsolatedCase


class PragmaCase(IsolatedCase):
    """连接级 PRAGMA：每条新连接都要带上，而且值要和常量表一致。"""

    def _pragma(self, name: str):
        return self.session.execute(text(f"PRAGMA {name}")).scalar()

    def test_expected_pragmas_applied(self):
        self.assertEqual(self._pragma("journal_mode"), "wal")
        self.assertEqual(int(self._pragma("foreign_keys")), 1)
        self.assertEqual(int(self._pragma("synchronous")), 1)  # NORMAL
        self.assertEqual(int(self._pragma("temp_store")), 2)  # MEMORY
        self.assertEqual(int(self._pragma("cache_size")), -32000)
        self.assertEqual(int(self._pragma("mmap_size")), 268435456)
        self.assertEqual(int(self._pragma("busy_timeout")), 5000)

    def test_pragma_table_covers_the_speedups(self):
        for name in ("journal_mode", "synchronous", "temp_store", "cache_size", "mmap_size", "busy_timeout"):
            with self.subTest(pragma=name):
                self.assertIn(name, SQLITE_PRAGMAS)


class OptimizeCase(IsolatedCase):
    """整理数据库：能跑通、报出前后字节数，且整理后会话照样可用。"""

    def test_optimize_reports_sizes_and_keeps_session_usable(self):
        from app.db.models import Category

        for index in range(50):
            self.session.add(Category(name=f"整理用例-{index}"))
        self.session.commit()
        for row in self.session.query(Category).all():
            self.session.delete(row)
        self.session.commit()

        database.dispose_engine()
        result = optimize_database()
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"before", "after", "freed"})
        self.assertGreaterEqual(result["freed"], 0)
        self.assertEqual(result["freed"], max(result["before"] - result["after"], 0))

        # 引擎已 dispose：调用方要重建会话，重建后必须还能读写
        self.session = database.new_session()
        type(self).session = self.session
        self.assertEqual(self.session.execute(text("SELECT 1")).scalar(), 1)


if __name__ == "__main__":
    unittest.main()
