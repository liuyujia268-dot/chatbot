"""SQLite-backed presentation state for the desktop interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.storage.database import Database


_UI_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_drafts (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    text TEXT NOT NULL, PRIMARY KEY(platform, account_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS desktop_conversations (
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    conversation_type TEXT NOT NULL CHECK (
        conversation_type IN ('private', 'group')
    ),
    peer_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    nickname TEXT,
    avatar_url TEXT,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    sort_order INTEGER NOT NULL,
    unread_count INTEGER NOT NULL DEFAULT 0,
    last_event_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, account_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_desktop_conversation_order
ON desktop_conversations (
    platform, account_id, conversation_type, pinned DESC, sort_order
);

CREATE TABLE IF NOT EXISTS desktop_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    source_type TEXT NOT NULL,
    text TEXT NOT NULL,
    parts_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE (platform, account_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_desktop_messages_conversation
ON desktop_messages (platform, account_id, conversation_id, id);

CREATE INDEX IF NOT EXISTS idx_desktop_messages_chronological
ON desktop_messages (
    platform, account_id, conversation_id, created_at, id
);
"""


@dataclass(frozen=True, slots=True)
class ConversationItem:
    platform: str
    account_id: str
    conversation_id: str
    conversation_type: Literal["private", "group"]
    peer_id: str
    display_name: str
    nickname: str | None
    avatar_url: str | None
    pinned: bool
    sort_order: int
    unread_count: int


@dataclass(frozen=True, slots=True)
class DesktopMessage:
    id: int
    conversation_id: str
    sender_id: str
    sender_name: str
    role: Literal["user", "assistant"]
    source_type: str
    text: str
    parts_json: str
    created_at: str
    status: str = "received"
    delivery_id: str | None = None
    error_text: str | None = None


class DesktopRepository:
    """Materialized UI view; never feeds data back into model context."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def initialize(self) -> None:
        with self._database.connect() as connection:
            connection.executescript(_UI_SCHEMA)
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(desktop_messages)')}
            for name, definition in (('status', "TEXT NOT NULL DEFAULT 'legacy_unknown'"), ('delivery_id', 'TEXT'), ('error_text', 'TEXT')):
                if name not in columns:
                    connection.execute(f'ALTER TABLE desktop_messages ADD COLUMN {name} {definition}')

    def upsert_conversation(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_id: str,
        conversation_type: Literal["private", "group"],
        peer_id: str,
        display_name: str,
        nickname: str | None = None,
        avatar_url: str | None = None,
        last_event_at: str | None = None,
    ) -> None:
        clean_name = display_name.strip() or peer_id
        with self._database.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM desktop_conversations "
                "WHERE platform = ? AND account_id = ? AND conversation_id = ?",
                (platform, account_id, conversation_id),
            ).fetchone()
            if existing is None:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order "
                    "FROM desktop_conversations WHERE platform = ? "
                    "AND account_id = ? AND conversation_type = ?",
                    (platform, account_id, conversation_type),
                ).fetchone()
                connection.execute(
                    "INSERT INTO desktop_conversations "
                    "(platform, account_id, conversation_id, conversation_type, "
                    "peer_id, display_name, nickname, avatar_url, sort_order, "
                    "last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        platform,
                        account_id,
                        conversation_id,
                        conversation_type,
                        peer_id,
                        clean_name,
                        nickname,
                        avatar_url,
                        int(row["next_order"]),
                        last_event_at,
                    ),
                )
                return
            connection.execute(
                "UPDATE desktop_conversations SET peer_id = ?, display_name = ?, "
                "nickname = COALESCE(?, nickname), "
                "avatar_url = COALESCE(?, avatar_url), "
                "last_event_at = COALESCE(?, last_event_at), "
                "updated_at = CURRENT_TIMESTAMP WHERE platform = ? "
                "AND account_id = ? AND conversation_id = ?",
                (
                    peer_id,
                    clean_name,
                    nickname,
                    avatar_url,
                    last_event_at,
                    platform,
                    account_id,
                    conversation_id,
                ),
            )

    def save_draft(self, account_id: str, conversation_id: str, text: str) -> None:
        with self._database.connect() as conn:
            conn.execute("INSERT INTO desktop_drafts VALUES ('qq',?,?,?) ON CONFLICT(platform,account_id,conversation_id) "
                         "DO UPDATE SET text=excluded.text", (account_id, conversation_id, text))

    def load_draft(self, account_id: str, conversation_id: str) -> str:
        with self._database.connect() as conn:
            row = conn.execute("SELECT text FROM desktop_drafts WHERE platform='qq' AND account_id=? AND conversation_id=?",
                               (account_id, conversation_id)).fetchone()
        return str(row[0]) if row else ''

    def list_conversations(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_type: Literal["private", "group"],
    ) -> list[ConversationItem]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT platform, account_id, conversation_id, conversation_type, "
                "peer_id, display_name, nickname, avatar_url, pinned, sort_order, "
                "unread_count FROM desktop_conversations WHERE platform = ? "
                "AND account_id = ? AND conversation_type = ? "
                "AND NOT (platform='qq' AND conversation_type='private' AND peer_id=account_id) "
                "ORDER BY pinned DESC, sort_order ASC",
                (platform, account_id, conversation_type),
            ).fetchall()
        return [
            ConversationItem(
                platform=str(row["platform"]),
                account_id=str(row["account_id"]),
                conversation_id=str(row["conversation_id"]),
                conversation_type=row["conversation_type"],
                peer_id=str(row["peer_id"]),
                display_name=str(row["display_name"]),
                nickname=(str(row["nickname"]) if row["nickname"] else None),
                avatar_url=(str(row["avatar_url"]) if row["avatar_url"] else None),
                pinned=bool(row["pinned"]),
                sort_order=int(row["sort_order"]),
                unread_count=int(row["unread_count"]),
            )
            for row in rows
        ]

    def set_pinned(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_id: str,
        pinned: bool,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE desktop_conversations SET pinned = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE platform = ? "
                "AND account_id = ? AND conversation_id = ?",
                (int(pinned), platform, account_id, conversation_id),
            )

    def reorder(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_ids: list[str],
    ) -> None:
        with self._database.connect() as connection:
            for index, conversation_id in enumerate(conversation_ids, 1):
                connection.execute(
                    "UPDATE desktop_conversations SET sort_order = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE platform = ? "
                    "AND account_id = ? AND conversation_id = ?",
                    (index, platform, account_id, conversation_id),
                )

    def record_message(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_id: str,
        event_key: str,
        sender_id: str,
        sender_name: str,
        role: Literal["user", "assistant"],
        source_type: str,
        text: str,
        parts_json: str,
        created_at: str,
        increment_unread: bool,
        status: str = "received",
    ) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO desktop_messages "
                "(platform, account_id, conversation_id, event_key, sender_id, "
                "sender_name, role, source_type, text, parts_json, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    platform,
                    account_id,
                    conversation_id,
                    event_key,
                    sender_id,
                    sender_name,
                    role,
                    source_type,
                    text,
                    parts_json,
                    created_at,
                    status,
                ),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "UPDATE desktop_conversations SET unread_count = unread_count + ?, "
                "last_event_at = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE platform = ? AND account_id = ? AND conversation_id = ?",
                (
                    int(increment_unread),
                    created_at,
                    platform,
                    account_id,
                    conversation_id,
                ),
            )
        return True

    def has_delivery_receipt(self, message) -> bool:
        with self._database.connect() as conn:
            row = conn.execute("SELECT 1 FROM outbound_deliveries WHERE platform=? AND account_id=? "
                               "AND conversation_id=? AND platform_message_id=? AND status='sent'",
                               (message.platform, message.account_id, message.conversation_id, message.message_id)).fetchone()
        return row is not None

    def record_delivery(self, record: dict, *, increment_unread: bool = False) -> None:
        import json
        metadata = json.loads(record['metadata_json'])
        with self._database.connect() as conn:
            previous = conn.execute('SELECT status FROM desktop_messages WHERE platform=? AND account_id=? AND event_key=?',
                                    (record['platform'], record['account_id'], f"delivery:{record['id']}")).fetchone()
        self.record_message(platform=record['platform'], account_id=record['account_id'],
            conversation_id=record['conversation_id'], event_key=f"delivery:{record['id']}",
            sender_id=record['account_id'], sender_name=str(metadata.get('sender_name', '机器人')),
            role='assistant', source_type=str(metadata.get('source_type', 'ai_generated')),
            text=record['text'], parts_json='[]', created_at=record['created_at'],
            increment_unread=False, status=record['status'])
        with self._database.connect() as conn:
            conn.execute('UPDATE desktop_messages SET status=?, delivery_id=?, error_text=? '
                         'WHERE platform=? AND account_id=? AND event_key=?', (record['status'], record['id'], record['error_text'],
                            record['platform'], record['account_id'], f"delivery:{record['id']}"))
            if (increment_unread and metadata.get('source_type', 'ai_generated') == 'ai_generated'
                    and record['status'] in {'sent', 'simulated'}
                    and (previous is None or previous[0] not in {'sent', 'simulated'})):
                conn.execute('UPDATE desktop_conversations SET unread_count=unread_count+1 '
                             'WHERE platform=? AND account_id=? AND conversation_id=?',
                             (record['platform'], record['account_id'], record['conversation_id']))

    def sync_deliveries(self, platform: str, account_id: str, conversation_id: str) -> None:
        with self._database.connect() as conn:
            rows = conn.execute('SELECT * FROM outbound_deliveries WHERE platform=? AND account_id=? AND conversation_id=?',
                                (platform, account_id, conversation_id)).fetchall()
        for row in rows:
            self.record_delivery(dict(row))

    def mark_read(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_id: str,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE desktop_conversations SET unread_count = 0, "
                "updated_at = CURRENT_TIMESTAMP WHERE platform = ? "
                "AND account_id = ? AND conversation_id = ?",
                (platform, account_id, conversation_id),
            )

    def list_messages(
        self,
        *,
        platform: str,
        account_id: str,
        conversation_id: str,
        limit: int = 200,
    ) -> list[DesktopMessage]:
        self.sync_deliveries(platform, account_id, conversation_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM (SELECT id, conversation_id, sender_id, "
                "sender_name, role, source_type, text, parts_json, created_at, status, delivery_id, error_text "
                "FROM desktop_messages WHERE platform = ? AND account_id = ? "
                "AND conversation_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?) "
                "ORDER BY created_at ASC, id ASC",
                (platform, account_id, conversation_id, limit),
            ).fetchall()
        return [
            DesktopMessage(
                id=int(row["id"]),
                conversation_id=str(row["conversation_id"]),
                sender_id=str(row["sender_id"]),
                sender_name=str(row["sender_name"]),
                role=row["role"],
                source_type=str(row["source_type"]),
                text=str(row["text"]),
                parts_json=str(row["parts_json"]),
                created_at=str(row["created_at"]),
                status=str(row['status']), delivery_id=row['delivery_id'], error_text=row['error_text'],
            )
            for row in rows
        ]
