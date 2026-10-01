"""数据层自检脚本：重建数据库、写入默认数据并跑一遍基本查询。

用法：.venv\\Scripts\\python.exe scripts\\dev_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_check_guard import run_guarded  # noqa: E402

from app.core.config import db_file  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.db import database  # noqa: E402
from app.db.models import DataType  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.repositories import (  # noqa: E402
    CategoryRepository,
    ItemFilter,
    ItemRepository,
    TagRepository,
    UserRepository,
)


def main() -> int:
    setup_logging()
    database.init_db(force=True)

    with database.session_scope() as session:
        print("seeded:", seed(session))
        users = UserRepository(session)
        categories = CategoryRepository(session)
        tags = TagRepository(session)
        items = ItemRepository(session)

        print("users:", [u.name for u in users.list_all()])
        print("categories:", [c.name for c in categories.roots()])
        print("tags:", tags.names())

        user = users.ensure_default()
        root = categories.roots()[0]
        child = categories.ensure("Python", parent_id=root.id, user_id=user.id)
        print("category path:", categories.path_of(child))

        item = items.create(
            name="测试文本",
            type=DataType.TEXT,
            content="hello world",
            size=11,
            checksum="0" * 64,
            user_id=user.id,
            category_id=child.id,
            keywords=["测试", "hello"],
        )
        item.tags = tags.ensure_many(["重要", "待整理"])
        session.flush()

        print("query all:", [i.name for i in items.query(ItemFilter())])
        print("filter tag:", [i.name for i in items.query(ItemFilter(tags={"重要"}))])
        print("filter text:", [i.name for i in items.query(ItemFilter(text="hello"))])
        print("filter type:", [i.name for i in items.query(ItemFilter(types={DataType.TEXT}))])
        print("tag usage:", tags.usage_counts())
        print("category counts:", categories.item_counts())
        print("stats:", items.stats())

    print("db file:", db_file(), db_file().exists())
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
