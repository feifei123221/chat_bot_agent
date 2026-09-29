"""SQLite persistence adapter for messages, memory, and task state."""

import sqlite3
from typing import Any


class SQLiteStore:
    LEGACY_USER_ID = "legacy-local"

    def __init__(self, database_path: str):
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT 'legacy-local',
                    session_id TEXT NOT NULL DEFAULT 'default',
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT 'legacy-local',
                    session_id TEXT NOT NULL DEFAULT 'default',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            self._migrate_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_user_session ON messages(user_id, session_id, id)"
            )
            connection.execute("DROP INDEX IF EXISTS idx_memory_session_key")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_user_session_key ON memory(user_id, session_id, key)"
            )

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection) -> None:
        message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        if "user_id" not in message_columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy-local'"
            )
        if "session_id" not in message_columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'"
            )
        memory_columns = {row[1] for row in connection.execute("PRAGMA table_info(memory)")}
        if "user_id" not in memory_columns:
            connection.execute(
                "ALTER TABLE memory ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy-local'"
            )
        if "session_id" not in memory_columns:
            connection.execute(
                "ALTER TABLE memory ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'"
            )
        memory_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory'"
        ).fetchone()[0]
        if "key TEXT NOT NULL UNIQUE" in memory_schema:
            connection.execute(
                """CREATE TABLE memory_migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT 'legacy-local',
                    session_id TEXT NOT NULL DEFAULT 'default',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO memory_migrated (id, session_id, key, value, updated_at)
                SELECT id, user_id, session_id, key, value, updated_at FROM memory"""
            )
            connection.execute("DROP TABLE memory")
            connection.execute("ALTER TABLE memory_migrated RENAME TO memory")

    def load_history(self, session_id: str, user_id: str = LEGACY_USER_ID) -> list[dict[str, str]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT role, content FROM messages WHERE user_id = ? AND session_id = ? ORDER BY id",
                (user_id, session_id),
            ).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def save_message(
        self, role: str, content: str, session_id: str, user_id: str = LEGACY_USER_ID
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO messages (user_id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (user_id, session_id, role, content),
            )

    def load_memory(self, session_id: str, user_id: str = LEGACY_USER_ID) -> dict[str, str]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM memory WHERE user_id = ? AND session_id = ? ORDER BY updated_at DESC",
                (user_id, session_id),
            ).fetchall()
        return {key: value for key, value in rows}

    def save_memory(
        self, key: str, value: str, session_id: str, user_id: str = LEGACY_USER_ID
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO memory (user_id, session_id, key, value)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, session_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP""",
                (user_id, session_id, key, value),
            )

    def clear(
        self, session_id: str | None = None, user_id: str | None = LEGACY_USER_ID
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            if session_id is None and user_id is None:
                connection.execute("DELETE FROM messages")
                connection.execute("DELETE FROM memory")
            elif session_id is None:
                connection.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
                connection.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))
            else:
                connection.execute(
                    "DELETE FROM messages WHERE user_id = ? AND session_id = ?",
                    (user_id, session_id),
                )
                connection.execute(
                    "DELETE FROM memory WHERE user_id = ? AND session_id = ?",
                    (user_id, session_id),
                )

    def recent_messages(
        self, session_id: str, limit: int, user_id: str = LEGACY_USER_ID
    ) -> list[tuple[str, str]]:
        self.initialize()
        with self.connect() as connection:
            return connection.execute(
                "SELECT role, content FROM messages WHERE user_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, session_id, limit),
            ).fetchall()

    def latest_session_id(self, user_id: str = LEGACY_USER_ID) -> str | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row[0] if row else None
