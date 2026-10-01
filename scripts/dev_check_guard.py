"""自检脚本共用的数据保护。

`dev_check.py` / `dev_check_services.py` / `dev_check_flow.py` 都会调用 `init_db(force=True)`
重建真实数据库，跑一次就会清掉用户数据。这里在运行前用 SQLite 在线备份把数据库存到
`logs/_selfcheck-data.db.bak`，跑完（含异常退出）再原样还原。
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from app.core import paths  # noqa: E402
from app.db.database import backup_database_file, dispose_engine  # noqa: E402

SNAPSHOT = ROOT / "logs" / "_selfcheck-data.db.bak"


def _restore(snapshot: Path) -> None:
    """把快照内容写回数据库。

    不用「删掉 data.db 再复制」：Windows 上文件可能被占用，而且这样还能顺带把自检跑出来的
    WAL 日志（-wal/-shm）提交进主库并清掉，避免残留日志回放到还原后的数据上。
    """
    dispose_engine()  # 连接还开着时写入会失败
    try:
        with closing(sqlite3.connect(str(snapshot), timeout=30)) as src:
            with closing(sqlite3.connect(str(paths.DB_FILE), timeout=30)) as dst:
                src.backup(dst)
                with closing(dst.cursor()) as cursor:
                    busy, _, _ = cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy:
                    print("自检还原：数据库被其它连接占用，WAL 未能立刻清空")
    except sqlite3.Error as exc:
        print(f"自检还原失败，备份保留在 {snapshot}：{exc}")
        return
    snapshot.unlink(missing_ok=True)
    print("自检还原：数据库已恢复到运行前状态")


def run_guarded(main) -> int:
    """运行自检主函数；结束后把真实数据库还原成运行前的样子。"""
    snapshot = backup_database_file(SNAPSHOT)
    if snapshot is None:
        return main()
    try:
        return main()
    finally:
        _restore(snapshot)
