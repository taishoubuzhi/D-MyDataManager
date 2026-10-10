"""数据库引擎、会话与初始化。"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.runtime import paths
from ..core.config import config, db_file, db_url
from .models import Base

_engine: Engine | None = None
_session_factory: sessionmaker | None = None

# 表结构版本：低版本库启动时原地补列升级，高于当前程序的库则备份并重建
SCHEMA_VERSION = 9

# 全文检索：FTS5 虚拟表（trigram 分词，支持中文子串匹配）+ 同步触发器
FTS_TABLE = "items_fts"
FTS_SOURCE_TABLE = "items"

_FTS_STATEMENTS = (
    f"""CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
        name, content, keywords, tokenize='trigram'
    )""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON {FTS_SOURCE_TABLE} BEGIN
        INSERT INTO {FTS_TABLE}(rowid, name, content, keywords)
        VALUES (new.id, new.name, new.content, new.keywords);
    END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON {FTS_SOURCE_TABLE} BEGIN
        DELETE FROM {FTS_TABLE} WHERE rowid = old.id;
    END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON {FTS_SOURCE_TABLE} BEGIN
        DELETE FROM {FTS_TABLE} WHERE rowid = old.id;
        INSERT INTO {FTS_TABLE}(rowid, name, content, keywords)
        VALUES (new.id, new.name, new.content, new.keywords);
    END""",
)

_FTS_BACKFILL = f"""INSERT INTO {FTS_TABLE}(rowid, name, content, keywords)
    SELECT id, name, content, keywords FROM {FTS_SOURCE_TABLE}
    WHERE id NOT IN (SELECT rowid FROM {FTS_TABLE})"""

#: SQLite 连接级 PRAGMA：名字 → 值。
#:
#: - `foreign_keys=ON`：SQLite 默认不校验外键，程序靠它保证引用完整；
#: - `journal_mode=WAL`：读写不互相阻塞（WAL 下崩溃安全性由日志保证）；
#: - `synchronous=NORMAL`：WAL 下只丢最近一次提交、不坏库，换来每次提交不再 fsync；
#: - `temp_store=MEMORY`：排序 / 临时表放内存；
#: - `cache_size=-32000`：负数表示 KiB，约 32 MiB 页缓存；
#: - `mmap_size=268435456`：256 MiB 只读映射，超出可用内存时由 SQLite 自己收敛；
#: - `busy_timeout=5000`：并发写等 5 秒再报 locked，而不是立刻抛错。
SQLITE_PRAGMAS: dict[str, object] = {
    "foreign_keys": "ON",
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "temp_store": "MEMORY",
    "cache_size": -32000,
    "mmap_size": 268435456,
    "busy_timeout": 5000,
}


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """每条新连接都套一遍 `SQLITE_PRAGMAS`（非 SQLite 连接忽略错误）。"""
    try:
        cursor = dbapi_connection.cursor()
        for name, value in SQLITE_PRAGMAS.items():
            cursor.execute(f"PRAGMA {name}={value}")
        cursor.close()
    except Exception:  # 非 SQLite 连接忽略
        pass


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = db_url()
        options: dict = {"echo": bool(config.dbEcho.value), "future": True}
        if url.startswith("sqlite"):
            paths.make_dir(db_file().parent)
            options["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **options)
        if url.startswith("sqlite"):
            event.listens_for(_engine, "connect")(_sqlite_pragmas)
        logger.info("数据库引擎已创建：{}", url)
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def new_session() -> Session:
    return get_session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务上下文：正常提交，异常回滚。"""
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _stored_schema_version(engine: Engine) -> int | None:
    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "app_meta" not in tables:
            return None
        raw = connection.exec_driver_sql(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).scalar()
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def backup_database_file(target: Path) -> Path | None:
    """把当前数据库完整复制到 target。

    数据库以 WAL 模式运行，最近提交可能还没落进 data.db，直接复制文件会丢数据；
    这里用 SQLite 的在线备份接口，复制完再把目标库的日志模式设回单文件 DELETE。
    """
    source = db_file()
    if not source.is_file():
        return None
    paths.make_dir(target.parent)
    try:
        # sqlite3 的 with 只提交事务不关连接，Windows 上必须显式关闭，否则 data.db 一直被占用
        with closing(sqlite3.connect(str(source))) as src, closing(sqlite3.connect(str(target))) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        logger.error("备份数据库失败：{}", exc)
        return None
    return target


def _reset_for_schema_change() -> None:
    """库结构版本高于当前程序时（降级运行）备份旧库并重建空库；低版本走原地升级。"""
    version = _stored_schema_version(get_engine())
    if version is None or version <= SCHEMA_VERSION:
        return
    path = db_file()
    dispose_engine()
    if path.exists() and path.stat().st_size:
        backup = backup_database_file(path.with_name(f"{path.name}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}"))
        if backup is not None:
            logger.warning("数据库结构已更新，旧库已备份为 {}，将重建空库", backup.name)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


# 2 -> 3：tags 表补 is_global / created_by，历史 user_id 为空的行视为全局标签
# 3 -> 4：users 补 is_default（默认用户/管理员），archive_entries 补所属用户
# 4 -> 5：archives 补 pinned（标记的存档不参与自动清理）
# 5 -> 6：archive_entries 补 is_hidden（存档记录隐藏状态，还原时据此对齐）
# 6 -> 7：存档内容改按块存进 pack 数据文件（新增 pack_files / chunks / file_manifests /
#         manifest_chunks 表），archives 补 logical_size、archive_entries 补 manifest_id
# 7 -> 8：放弃分块，内容改整份压缩存储：四张块/清单/pack 表合并成 contents，
#         archive_entries 去掉 manifest_id（内容身份只看 checksum）
# 8 -> 9：archive_entries 补 cover_path（存档记录封面文件名，回档能还原封面变更）
_COLUMN_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "archives": (
        ("pinned", "ALTER TABLE archives ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0"),
        ("logical_size", "ALTER TABLE archives ADD COLUMN logical_size BIGINT NOT NULL DEFAULT 0"),
    ),
    "tags": (
        ("is_global", "ALTER TABLE tags ADD COLUMN is_global BOOLEAN NOT NULL DEFAULT 0"),
        (
            "created_by",
            "ALTER TABLE tags ADD COLUMN created_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
        ),
    ),
    "users": (
        ("is_default", "ALTER TABLE users ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT 0"),
    ),
    "archive_entries": (
        (
            "user_id",
            "ALTER TABLE archive_entries ADD COLUMN user_id INTEGER "
            "REFERENCES users(id) ON DELETE SET NULL",
        ),
        (
            "user_name",
            "ALTER TABLE archive_entries ADD COLUMN user_name VARCHAR(64) NOT NULL DEFAULT ''",
        ),
        (
            "is_hidden",
            "ALTER TABLE archive_entries ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT 0",
        ),
        # 可空：NULL 表示早期存档没记过封面，回档时不做封面比对（不能当成「没有封面」）
        ("cover_path", "ALTER TABLE archive_entries ADD COLUMN cover_path VARCHAR(512)"),
    ),
}

_DATA_PATCHES: tuple[str, ...] = (
    "UPDATE tags SET created_by = user_id WHERE created_by IS NULL",
    "UPDATE tags SET is_global = 1 WHERE user_id IS NULL",
    "UPDATE users SET is_default = 1 WHERE id = (SELECT MIN(id) FROM users) "
    "AND NOT EXISTS (SELECT 1 FROM users WHERE is_default = 1)",
    "UPDATE archive_entries SET user_id = "
    "(SELECT user_id FROM items WHERE items.id = archive_entries.item_id) "
    "WHERE user_id IS NULL AND item_id IS NOT NULL",
    "UPDATE archive_entries SET user_name = COALESCE("
    "(SELECT name FROM users WHERE users.id = archive_entries.user_id), '') "
    "WHERE user_name = ''",
    "UPDATE archives SET logical_size = total_size WHERE logical_size = 0",
)

_INDEX_PATCHES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tag_global_name ON tags(name) WHERE is_global = 1",
    "CREATE INDEX IF NOT EXISTS ix_users_is_default ON users(is_default)",
    "CREATE INDEX IF NOT EXISTS ix_archive_entries_user_id ON archive_entries(user_id)",
)

#: 7 -> 8 退场的旧表（分块时代的块 / 清单 / pack 索引）
_LEGACY_TABLES: tuple[str, ...] = ("manifest_chunks", "file_manifests", "chunks", "pack_files")


def _drop_chunked_storage(connection, tables: set[str]) -> None:
    """7 -> 8：删掉分块时代的列与表，内容改由 `contents` 一张表描述。"""
    if "archive_entries" in tables:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(archive_entries)")}
        if "manifest_id" in columns:
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_archive_entries_manifest_id")
            connection.exec_driver_sql("ALTER TABLE archive_entries DROP COLUMN manifest_id")
    dropped = [name for name in _LEGACY_TABLES if name in tables]
    for name in dropped:
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
    if dropped:
        logger.warning("内容改为整份压缩存储，已删除旧的块 / 清单 / pack 索引表：{}", "、".join(dropped))


def _dedupe_global_tags(connection) -> None:
    """历史数据里可能有同名全局标签，重命名副本，避免唯一索引建立失败。"""
    names = connection.exec_driver_sql(
        "SELECT name FROM tags WHERE is_global = 1 GROUP BY name HAVING COUNT(*) > 1"
    ).fetchall()
    for (name,) in names:
        ids = [
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT id FROM tags WHERE is_global = 1 AND name = ? ORDER BY id", (name,)
            )
        ]
        for index, tag_id in enumerate(ids[1:], start=2):
            connection.exec_driver_sql(
                "UPDATE tags SET name = ? WHERE id = ?", (f"{name}（{index}）", tag_id)
            )
        logger.warning("历史全局标签「{}」重名，已重命名 {} 个副本", name, len(ids) - 1)


def _merge_shadow_tags(connection) -> None:
    """个人标签不得与全局标签重名：把同名个人标签的引用并入全局标签，再删除副本。"""
    pairs = connection.exec_driver_sql(
        "SELECT s.id, g.id FROM tags AS s JOIN tags AS g "
        "ON g.name = s.name AND g.is_global = 1 WHERE s.is_global = 0"
    ).fetchall()
    for shadow_id, global_id in pairs:
        connection.exec_driver_sql(
            "UPDATE OR IGNORE item_tags SET tag_id = ? WHERE tag_id = ?", (global_id, shadow_id)
        )
        connection.exec_driver_sql("DELETE FROM item_tags WHERE tag_id = ?", (shadow_id,))
        connection.exec_driver_sql("DELETE FROM tags WHERE id = ?", (shadow_id,))
    if pairs:
        logger.warning("发现 {} 个与全局标签重名的个人标签，已并入全局标签", len(pairs))


def _upgrade_schema() -> None:
    """把旧版本库原地升级到当前结构：只补列 / 补索引并回填，不重建库、不丢数据。"""
    engine = get_engine()
    version = _stored_schema_version(engine)
    if version is None or version >= SCHEMA_VERSION:
        return
    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, patches in _COLUMN_PATCHES.items():
            if table not in tables:
                continue
            columns = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for column, statement in patches:
                if column not in columns:
                    connection.exec_driver_sql(statement)
                columns.add(column)
        if version < 8:
            _drop_chunked_storage(connection, tables)
        for statement in _DATA_PATCHES:
            try:
                connection.exec_driver_sql(statement)
            except Exception as exc:
                logger.warning("结构升级语句失败（已跳过）：{}", exc)
        if "tags" in tables:
            _dedupe_global_tags(connection)
        for statement in _INDEX_PATCHES:
            try:
                connection.exec_driver_sql(statement)
            except Exception as exc:
                logger.warning("索引创建失败（已跳过）：{}", exc)
    logger.info("数据库结构已原地升级：{} -> {}", version, SCHEMA_VERSION)


def _write_schema_version(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO app_meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


def init_db(force: bool = False) -> None:
    # 连接池在这里建立，之后一直复用；静态保护下运行期整场放行，连接不再需要放行窗口
    engine = get_engine()
    if force:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {FTS_TABLE}")
        Base.metadata.drop_all(engine)
        logger.warning("已按要求删除全部数据表")
    else:
        _reset_for_schema_change()
        _upgrade_schema()
    engine = get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _merge_shadow_tags(connection)
    ensure_fts(engine)
    _write_schema_version(engine)
    logger.info("数据表已就绪")


def ensure_fts(engine: Engine | None = None) -> int:
    """建立 FTS5 检索表与同步触发器，并回填尚未索引的数据项；返回回填行数。"""
    engine = engine or get_engine()
    with engine.begin() as connection:
        for statement in _FTS_STATEMENTS:
            connection.exec_driver_sql(statement)
        missing = connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM {FTS_SOURCE_TABLE} "
            f"WHERE id NOT IN (SELECT rowid FROM {FTS_TABLE})"
        ).scalar_one()
        if missing:
            connection.exec_driver_sql(_FTS_BACKFILL)
            logger.info("已回填 {} 条数据项的检索索引", missing)
        return int(missing)


def rebuild_fts(engine: Engine | None = None) -> None:
    """重建整张检索索引（索引损坏或手工改库后使用）。"""
    engine = engine or get_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {FTS_TABLE}")
    ensure_fts(engine)
    logger.info("检索索引已重建")


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def optimize_database() -> dict[str, int]:
    """整理数据库文件：`PRAGMA optimize` 刷新查询统计，`VACUUM` 回收删数据留下的空洞。

    `VACUUM` 要独占整库、且不能在事务里跑，所以这里用 AUTOCOMMIT 连接；
    调用方（设置页的「整理数据库」）应当先 `dispose_engine()` 放掉其它连接，
    整理完再重建会话。返回整理前后的字节数。
    """
    path = db_file()
    before = path.stat().st_size if path.is_file() else 0
    engine = get_engine()
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("PRAGMA optimize")
            connection.exec_driver_sql("VACUUM")
    except Exception as exc:  # noqa: BLE001 - 整理失败要报清楚，但不能让调用方拿到半个状态
        logger.error("数据库整理失败：{}", exc)
        raise
    after = path.stat().st_size if path.is_file() else 0
    logger.info("数据库整理完成：{} -> {} 字节（释放 {}）", before, after, max(before - after, 0))
    return {"before": before, "after": after, "freed": max(before - after, 0)}
