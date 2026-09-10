"""Training-group event storage and human-reply style candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.platform import PlatformMessage
from app.storage.database import Database


@dataclass(frozen=True, slots=True)
class QQGroupEvent:
    message: PlatformMessage
    group_id: str
    nickname: str | None
    reply_to_message_id: str | None
    source_type: str


@dataclass(frozen=True, slots=True)
class StyleSample:
    sample_id: int
    platform: str
    conversation_type: str
    conversation_id: str
    context_messages: tuple[dict[str, Any], ...]
    human_reply_message_id: str
    human_reply: str
    created_at: str
    status: str


class StyleRepository:
    def __init__(
        self,
        database: Database,
        *,
        context_min_messages: int = 3,
        context_max_messages: int = 10,
    ) -> None:
        self.database = database
        self.context_min_messages = context_min_messages
        self.context_max_messages = context_max_messages

    def record_group_event(self, event: QQGroupEvent) -> StyleSample | None:
        if event.source_type not in {
            "other_user", "human_self", "ai_generated"
        }:
            raise ValueError("invalid QQ group source_type")
        message = event.message
        parts_json = self.database.serialize_message_parts(message)
        with self.database.connect() as connection:
            receipt = connection.execute(
                "INSERT OR IGNORE INTO inbound_platform_messages "
                "(platform, account_id, message_id, conversation_id, sender_id, "
                "event_timestamp, message_type, text, parts_json, is_self) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.platform, message.account_id, message.message_id,
                    message.conversation_id, message.sender_id,
                    message.timestamp.isoformat(), message.message_type,
                    message.storage_text, parts_json, int(message.is_self),
                ),
            )
            if receipt.rowcount != 1:
                return None
            cursor = connection.execute(
                "INSERT INTO qq_training_group_events "
                "(platform, account_id, group_id, sender_id, message_id, "
                "event_timestamp, text, parts_json, is_self, "
                "reply_to_message_id, nickname, source_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.platform, message.account_id, event.group_id,
                    message.sender_id, message.message_id,
                    message.timestamp.isoformat(), message.storage_text,
                    parts_json, int(message.is_self), event.reply_to_message_id,
                    event.nickname, event.source_type,
                ),
            )
            return self._create_sample(connection, cursor.lastrowid, event)

    def _create_sample(
        self,
        connection: Any,
        event_row_id: int,
        event: QQGroupEvent,
    ) -> StyleSample | None:
        if event.source_type != "human_self":
            return None
        human_reply = self._human_reply(event.message)
        if human_reply is None:
            return None
        message = event.message
        rows = connection.execute(
            "SELECT source_type, parts_json FROM qq_training_group_events "
            "WHERE platform = ? AND account_id = ? AND group_id = ? "
            "AND id < ? AND source_type != 'ai_generated' "
            "ORDER BY id DESC LIMIT ?",
            (
                message.platform, message.account_id, event.group_id,
                event_row_id, self.context_max_messages,
            ),
        ).fetchall()
        rows = list(reversed(rows))
        if len(rows) < self.context_min_messages:
            return None
        context = tuple(self._context_row(row) for row in rows)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO style_samples "
            "(platform, conversation_type, conversation_id, context_messages, "
            "human_reply_message_id, human_reply) "
            "VALUES (?, 'group', ?, ?, ?, ?)",
            (
                message.platform,
                message.conversation_id,
                json.dumps(context, ensure_ascii=False),
                message.message_id,
                human_reply,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = connection.execute(
            "SELECT * FROM style_samples WHERE sample_id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return self._sample(row)

    @staticmethod
    def _human_reply(message: PlatformMessage) -> str | None:
        if any(part.part_type != "text" for part in message.parts):
            return None
        reply = "".join(part.text or "" for part in message.parts).strip()
        if len(reply) < 2 or not re.search(r"[\w\u4e00-\u9fff]", reply):
            return None
        return reply

    @staticmethod
    def _context_row(row: Any) -> dict[str, Any]:
        parts = json.loads(str(row["parts_json"]))
        safe_parts = []
        for part in parts:
            if part.get("type") == "text":
                safe_parts.append({"type": "text", "text": part.get("text", "")})
            elif part.get("type") == "image":
                description = part.get("description") or part.get("summary")
                safe_parts.append(
                    {"type": "image", "description": description or "这里存在图片"}
                )
        return {
            "speaker": "self" if row["source_type"] == "human_self" else "other",
            "parts": safe_parts,
        }

    def stats(self) -> dict[str, int]:
        result = {"candidate": 0, "accepted": 0, "rejected": 0}
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM style_samples "
                "GROUP BY status"
            ).fetchall()
        for row in rows:
            result[str(row["status"])] = int(row["total"])
        return result

    def list_samples(self, status: str | None = None) -> list[StyleSample]:
        query = "SELECT * FROM style_samples"
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY sample_id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._sample(row) for row in rows]

    def get_sample(self, sample_id: int) -> StyleSample | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM style_samples WHERE sample_id = ?",
                (sample_id,),
            ).fetchone()
        return self._sample(row) if row is not None else None

    def set_status(self, sample_id: int, status: str) -> bool:
        if status not in {"accepted", "rejected"}:
            raise ValueError("style status must be accepted or rejected")
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE style_samples SET status = ? WHERE sample_id = ?",
                (status, sample_id),
            )
        return cursor.rowcount == 1

    def export_accepted(self, path: Path) -> int:
        """Export only approved, identifier-free training records."""
        records = []
        for sample in reversed(self.list_samples("accepted")):
            records.append(
                {
                    "context": self._scrub(sample.context_messages),
                    "response": self._scrub_text(sample.human_reply),
                    "metadata": {
                        "conversation_type": sample.conversation_type,
                    },
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary.replace(path)
        return len(records)

    def count_group_events(self, group_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM qq_training_group_events "
                "WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return int(row["total"])

    @classmethod
    def _scrub(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._scrub_text(value)
        if isinstance(value, dict):
            return {key: cls._scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._scrub(item) for item in value]
        return value

    @staticmethod
    def _scrub_text(value: str) -> str:
        return re.sub(r"(?<!\d)\d{5,12}(?!\d)", "[已脱敏]", value)

    @staticmethod
    def _sample(row: Any) -> StyleSample:
        context = json.loads(str(row["context_messages"]))
        return StyleSample(
            sample_id=int(row["sample_id"]),
            platform=str(row["platform"]),
            conversation_type=str(row["conversation_type"]),
            conversation_id=str(row["conversation_id"]),
            context_messages=tuple(context),
            human_reply_message_id=str(row["human_reply_message_id"]),
            human_reply=str(row["human_reply"]),
            created_at=str(row["created_at"]),
            status=str(row["status"]),
        )
