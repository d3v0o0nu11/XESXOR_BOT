"""SQLite persistence layer.

A thin, thread-safe wrapper around a single SQLite connection.  Both
platform adapters and every core service share this one store, keyed by
``(user_id, scope_id)`` where ``scope_id`` is a Discord guild id or a
Telegram chat id — the same economic profile therefore follows a user
inside each community, on either platform.

All methods are synchronous: SQLite writes are microsecond-fast locally and
every call is serialised behind a re-entrant lock.  Long-running work never
happens while the lock is held.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "2"

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        coins REAL DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        messages INTEGER DEFAULT 0,
        voice_minutes INTEGER DEFAULT 0,
        reputation INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0,
        last_daily REAL DEFAULT 0,
        joined_timestamp REAL DEFAULT 0,
        PRIMARY KEY (user_id, scope_id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS bot_meta (key TEXT PRIMARY KEY, value TEXT)",
    """
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        item_name TEXT NOT NULL,
        created_at REAL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        moderator_id TEXT NOT NULL,
        reason TEXT,
        timestamp REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quiz_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        question_hash TEXT NOT NULL,
        question TEXT NOT NULL,
        category TEXT NOT NULL,
        timestamp REAL NOT NULL,
        UNIQUE(user_id, scope_id, question_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quiz_history_user_day ON quiz_history(user_id, scope_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_history_hash ON quiz_history(user_id, scope_id, question_hash)",
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        details TEXT,
        status TEXT DEFAULT 'open',
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS community_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        title TEXT NOT NULL,
        time_str TEXT,
        description TEXT,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        question TEXT NOT NULL,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_roles (
        user_id TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        role_name TEXT NOT NULL,
        platform TEXT NOT NULL,
        PRIMARY KEY (user_id, scope_id, role_name, platform)
    )
    """,
)


class Database:
    """Thread-safe SQLite access shared by the whole application."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate_legacy_schema()
        self._create_schema()

    # ------------------------------------------------------------------
    def _create_schema(self) -> None:
        """Create all tables/indexes if they do not exist yet."""
        with self._lock:
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT OR REPLACE INTO bot_meta (key, value) VALUES ('schema_version', ?)",
                (_SCHEMA_VERSION,),
            )
            self._conn.commit()

    def _migrate_legacy_schema(self) -> None:
        """Best-effort migration from the v1 integer-keyed schema.

        The original monolithic bot stored ``user_id``/``guild_id`` as
        INTEGER.  Schema v2 uses TEXT keys so Discord and Telegram ids can
        share one store.  Each legacy table is rebuilt with TEXT columns and
        rows copied via ``CAST(... AS TEXT)``.  Failures are logged and never
        block startup (worst case the bot starts with a fresh economy).
        """
        migrations: tuple[tuple[str, tuple[str, ...], str], ...] = (
            (
                "users",
                ("coins", "xp", "level", "messages", "voice_minutes",
                 "reputation", "streak", "last_daily", "joined_timestamp"),
                "INSERT OR IGNORE INTO {new} (user_id, scope_id, {cols}) "
                "SELECT CAST(user_id AS TEXT), CAST(guild_id AS TEXT), {cols} FROM {old}",
            ),
            (
                "inventory",
                ("item_name",),
                "INSERT OR IGNORE INTO {new} (user_id, scope_id, {cols}) "
                "SELECT CAST(user_id AS TEXT), CAST(guild_id AS TEXT), {cols} FROM {old}",
            ),
            (
                "warnings",
                ("moderator_id", "reason", "timestamp"),
                "INSERT OR IGNORE INTO {new} (user_id, scope_id, {cols}) "
                "SELECT CAST(user_id AS TEXT), CAST(guild_id AS TEXT), "
                "CAST(moderator_id AS TEXT), reason, timestamp FROM {old}",
            ),
            (
                "quiz_history",
                ("question_hash", "question", "category", "timestamp"),
                "INSERT OR IGNORE INTO {new} (user_id, scope_id, {cols}) "
                "SELECT CAST(user_id AS TEXT), CAST(guild_id AS TEXT), {cols} FROM {old}",
            ),
        )

        with self._lock:
            try:
                cursor = self._conn.execute("PRAGMA table_info(users)")
                columns = {row["name"]: row["type"].upper() for row in cursor.fetchall()}
            except sqlite3.Error:
                return
            if not columns or columns.get("user_id") == "TEXT":
                return  # fresh install or already migrated

            logger.info("Migrating legacy v1 database schema to TEXT keys...")
            # Python's sqlite3 runs DDL outside implicit transactions, so a
            # plain rollback() would leave half-renamed tables behind.  Drive
            # the whole migration from an explicit SQLite transaction instead:
            # SQLite's DDL is transactional, so success commits atomically and
            # any failure restores the original v1 tables untouched.
            previous_isolation = self._conn.isolation_level
            self._conn.isolation_level = None
            try:
                self._conn.execute("BEGIN")
                for table, keep_columns, copy_sql in migrations:
                    exists = self._conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if not exists:
                        continue
                    create_statement = next(
                        sql
                        for sql in _SCHEMA_STATEMENTS
                        if f"CREATE TABLE IF NOT EXISTS {table} " in sql.replace("\n", " ")
                        or f"CREATE TABLE IF NOT EXISTS {table}\n" in sql
                    )
                    temp_name = f"{table}_v2"
                    self._conn.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
                    self._conn.execute(
                        create_statement.replace(
                            f"CREATE TABLE IF NOT EXISTS {table}",
                            f'CREATE TABLE IF NOT EXISTS "{temp_name}"',
                            1,
                        )
                    )
                    self._conn.execute(
                        copy_sql.format(
                            new=temp_name,
                            old=table,
                            cols=", ".join(keep_columns),
                        )
                    )
                    self._conn.execute(f'DROP TABLE "{table}"')
                    self._conn.execute(
                        f'ALTER TABLE "{temp_name}" RENAME TO "{table}"'
                    )
                for statement in _SCHEMA_STATEMENTS:
                    if statement.strip().startswith("CREATE INDEX"):
                        self._conn.execute(statement)
                self._conn.execute("COMMIT")
                logger.info("Legacy schema migration completed.")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Legacy schema migration failed (%s); dropping legacy "
                    "tables and starting with a fresh economy.",
                    exc,
                )
                self._conn.execute("ROLLBACK")
                # The v1 tables are restored but unusable for schema v2; drop
                # them (plus any stray rebuild temps) so _create_schema can
                # create the new tables instead of crashing on old columns.
                for table in ("users", "inventory", "warnings", "quiz_history"):
                    self._conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    self._conn.execute(f'DROP TABLE IF EXISTS "{table}_v2"')
            finally:
                self._conn.isolation_level = previous_isolation

    # ------------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Execute a statement under the connection lock and commit."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        """Return the first matching row or ``None``."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Return all matching rows."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Group several statements into one atomic commit.

        Yields:
            A cursor; on exception the transaction is rolled back.
        """
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    # ------------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        """Read a value from the ``bot_meta`` key/value table."""
        row = self.fetchone("SELECT value FROM bot_meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Upsert a value into the ``bot_meta`` key/value table."""
        self.execute(
            "INSERT INTO bot_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
