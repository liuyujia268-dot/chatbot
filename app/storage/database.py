"""Small SQLite persistence layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.session import Message
from app.memory.types import StoredMessage
from app.storage.schema import SCHEMA_SQL


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def save_message(self, session_id: str, message: Message) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, message.role, message.content),
            )
            return int(cursor.lastrowid)

    def list_messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[Message]:
        with self.connect() as connection:
            if limit is None:
                rows = connection.execute(
                    "SELECT role, content FROM messages "
                    "WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
            else:
                if limit < 1:
                    raise ValueError("limit must be at least 1")
                rows = connection.execute(
                    "SELECT role, content FROM ("
                    "SELECT id, role, content FROM messages "
                    "WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                    ") ORDER BY id",
                    (session_id, limit),
                ).fetchall()
        return [Message(role=role, content=content) for role, content in rows]

    def list_stored_messages(
        self,
        session_id: str,
        *,
        after_id: int = 0,
        limit: int | None = None,
    ) -> list[StoredMessage]:
        query = (
            "SELECT id, session_id, role, content, created_at FROM messages "
            "WHERE session_id = ? AND id > ? ORDER BY id"
        )
        parameters: tuple[object, ...] = (session_id, after_id)
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be at least 1")
            query += " LIMIT ?"
            parameters += (limit,)
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._stored_message(row) for row in rows]

    def get_stored_messages_by_ids(
        self,
        session_id: str,
        message_ids: tuple[int, ...],
    ) -> list[StoredMessage]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, role, content, created_at FROM messages "
                f"WHERE session_id = ? AND id IN ({placeholders}) ORDER BY id",
                (session_id, *message_ids),
            ).fetchall()
        return [self._stored_message(row) for row in rows]

    def list_recent_stored_messages(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[StoredMessage]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, role, content, created_at FROM ("
                "SELECT id, session_id, role, content, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id",
                (session_id, limit),
            ).fetchall()
        return [self._stored_message(row) for row in rows]

    def count_messages_after(self, session_id: str, message_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM messages "
                "WHERE session_id = ? AND id > ?",
                (session_id, message_id),
            ).fetchone()
        return int(row["total"])

    def latest_message_id(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest FROM messages "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["latest"])

    @staticmethod
    def _stored_message(row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            id=int(row["id"]),
            conversation_id=str(row["session_id"]),
            role=row["role"],
            content=str(row["content"]),
            created_at=str(row["created_at"]),
        )
