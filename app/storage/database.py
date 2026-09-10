"""Small SQLite persistence layer."""

from __future__ import annotations

import sqlite3
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.session import Message
from app.core.platform import PlatformMessage
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
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(inbound_platform_messages)"
                ).fetchall()
            }
            if "parts_json" not in columns:
                connection.execute(
                    "ALTER TABLE inbound_platform_messages "
                    "ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]'"
                )

    def save_message(self, session_id: str, message: Message) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, message.role, message.content),
            )
            return int(cursor.lastrowid)

    def save_inbound_platform_message(self, message: PlatformMessage) -> bool:
        """Atomically deduplicate a platform event and persist its user text."""
        return self._save_platform_event(
            message,
            save_as_chat_message=True,
            chat_role="user",
        )

    def save_human_self_platform_message(
        self,
        message: PlatformMessage,
    ) -> bool:
        """Persist an operator-authored platform message as assistant history."""
        if not message.is_self:
            raise ValueError("human self message must have is_self=true")
        return self._save_platform_event(
            message,
            save_as_chat_message=True,
            chat_role="assistant",
        )

    def save_inbound_platform_event(self, message: PlatformMessage) -> bool:
        """Persist a deduplicated event without entering the chat history."""
        return self._save_platform_event(message, save_as_chat_message=False)

    def _save_platform_event(
        self,
        message: PlatformMessage,
        *,
        save_as_chat_message: bool,
        chat_role: str = "user",
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO inbound_platform_messages "
                "(platform, account_id, message_id, conversation_id, sender_id, "
                "event_timestamp, message_type, text, parts_json, is_self) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.platform,
                    message.account_id,
                    message.message_id,
                    message.conversation_id,
                    message.sender_id,
                    message.timestamp.isoformat(),
                    message.message_type,
                    message.storage_text,
                    self.serialize_message_parts(message),
                    int(message.is_self),
                ),
            )
            if cursor.rowcount != 1:
                return False
            if save_as_chat_message:
                connection.execute(
                    "INSERT INTO messages (session_id, role, content) "
                    "VALUES (?, ?, ?)",
                    (
                        message.conversation_id,
                        chat_role,
                        message.storage_text,
                    ),
                )
        return True

    def list_recent_private_contacts(
        self,
        *,
        platform: str,
        account_id: str,
        since_timestamp: str,
    ) -> tuple[str, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT sender_id, MAX(id) AS latest_id "
                "FROM inbound_platform_messages "
                "WHERE platform = ? AND account_id = ? AND is_self = 0 "
                "AND conversation_id LIKE ? AND event_timestamp >= ? "
                "GROUP BY sender_id ORDER BY latest_id DESC",
                (
                    platform,
                    account_id,
                    f"{platform}:private:%",
                    since_timestamp,
                ),
            ).fetchall()
        return tuple(str(row["sender_id"]) for row in rows)

    @staticmethod
    def serialize_message_parts(message: PlatformMessage) -> str:
        return json.dumps(
            [
                {
                    "type": part.part_type,
                    "text": part.text,
                    "file": part.file,
                    "url": part.url,
                    "summary": part.summary,
                    "file_size": part.file_size,
                    "sub_type": part.sub_type,
                    "description": part.description,
                    "available": part.available,
                }
                for part in message.parts
            ],
            ensure_ascii=False,
        )

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

    def save_image_description(
        self,
        *,
        platform: str,
        account_id: str,
        message_id: str,
        part_index: int,
        description: str,
    ) -> None:
        value = description.strip()[:300]
        if not value:
            return
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO image_descriptions "
                "(platform, account_id, message_id, part_index, description) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(platform, account_id, message_id, part_index) "
                "DO UPDATE SET description = excluded.description, "
                "updated_at = CURRENT_TIMESTAMP",
                (platform, account_id, message_id, part_index, value),
            )

    def get_image_description(
        self,
        *,
        platform: str,
        account_id: str,
        message_id: str,
        part_index: int,
    ) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT description FROM image_descriptions "
                "WHERE platform = ? AND account_id = ? AND message_id = ? "
                "AND part_index = ?",
                (platform, account_id, message_id, part_index),
            ).fetchone()
        return str(row["description"]) if row is not None else None

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
