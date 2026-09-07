"""SQLite repository for summaries, memories, candidates, and profiles."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from app.memory.types import (
    ACCOUNT_PROFILE_FIELDS,
    PROFILE_FIELDS,
    AccountProfile,
    CandidateProposal,
    ContactProfile,
    ConversationSummary,
    LongTermMemory,
    MemoryCandidate,
)
from app.storage.database import Database


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add_summary(
        self,
        conversation_id: str,
        content: str,
        start_message_id: int,
        end_message_id: int,
        previous_summary_id: int | None,
    ) -> ConversationSummary:
        with self.database.connect() as connection:
            self._validate_source_range(
                connection,
                conversation_id,
                start_message_id,
                end_message_id,
            )
            if previous_summary_id is not None:
                previous = connection.execute(
                    "SELECT conversation_id, start_message_id, end_message_id "
                    "FROM conversation_summaries WHERE id = ?",
                    (previous_summary_id,),
                ).fetchone()
                if (
                    previous is None
                    or previous["conversation_id"] != conversation_id
                    or int(previous["start_message_id"]) != start_message_id
                    or int(previous["end_message_id"]) >= end_message_id
                ):
                    raise ValueError("previous summary is not a valid incremental base")
            cursor = connection.execute(
                "INSERT INTO conversation_summaries "
                "(conversation_id, content, start_message_id, end_message_id, "
                "previous_summary_id) VALUES (?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    content.strip(),
                    start_message_id,
                    end_message_id,
                    previous_summary_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM conversation_summaries WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._summary(row)

    def latest_summary(self, conversation_id: str) -> ConversationSummary | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE conversation_id = ? ORDER BY end_message_id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return self._summary(row) if row is not None else None

    def list_summaries(self, conversation_id: str) -> list[ConversationSummary]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE conversation_id = ? ORDER BY end_message_id",
                (conversation_id,),
            ).fetchall()
        return [self._summary(row) for row in rows]

    def add_candidate(
        self,
        proposal: CandidateProposal,
        *,
        status: str = "pending",
        rejection_reason: str | None = None,
    ) -> MemoryCandidate:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO memory_candidates "
                "(conversation_id, memory_type, memory_key, content, confidence, "
                "source_message_ids, source_quote, action, target_memory_id, "
                "status, rejection_reason, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END)",
                (
                    proposal.conversation_id,
                    proposal.memory_type,
                    proposal.memory_key,
                    proposal.content.strip(),
                    proposal.confidence,
                    json.dumps(proposal.source_message_ids),
                    proposal.source_quote.strip(),
                    proposal.action,
                    proposal.target_memory_id,
                    status,
                    rejection_reason,
                    status,
                ),
            )
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._candidate(row)

    def get_candidate(self, candidate_id: int) -> MemoryCandidate | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        return self._candidate(row) if row is not None else None

    def list_candidates(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
    ) -> list[MemoryCandidate]:
        query = (
            "SELECT * FROM memory_candidates WHERE conversation_id = ?"
        )
        parameters: tuple[object, ...] = (conversation_id,)
        if status is not None:
            query += " AND status = ?"
            parameters += (status,)
        query += " ORDER BY id"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._candidate(row) for row in rows]

    def set_candidate_status(
        self,
        candidate_id: int,
        status: str,
        rejection_reason: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE memory_candidates SET status = ?, rejection_reason = ?, "
                "reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, rejection_reason, candidate_id),
            )

    def create_memory(
        self,
        *,
        conversation_id: str,
        memory_type: str,
        memory_key: str,
        content: str,
        source_start_message_id: int,
        source_end_message_id: int,
        confidence: float,
        supersedes_memory_id: int | None = None,
    ) -> LongTermMemory:
        with self.database.connect() as connection:
            self._validate_source_range(
                connection,
                conversation_id,
                source_start_message_id,
                source_end_message_id,
            )
            if supersedes_memory_id is not None:
                old = connection.execute(
                    "SELECT * FROM long_term_memories WHERE id = ? "
                    "AND conversation_id = ?",
                    (supersedes_memory_id, conversation_id),
                ).fetchone()
                if old is None or old["status"] != "active":
                    raise ValueError("target memory is not active in this conversation")
                connection.execute(
                    "UPDATE long_term_memories SET status = 'superseded', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (supersedes_memory_id,),
                )
            cursor = connection.execute(
                "INSERT INTO long_term_memories "
                "(conversation_id, memory_type, memory_key, content, "
                "source_start_message_id, source_end_message_id, confidence, "
                "supersedes_memory_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    memory_type,
                    memory_key,
                    content.strip(),
                    source_start_message_id,
                    source_end_message_id,
                    confidence,
                    supersedes_memory_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM long_term_memories WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._memory(row)

    def get_memory(self, memory_id: int) -> LongTermMemory | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM long_term_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        return self._memory(row) if row is not None else None

    def list_memories(
        self,
        conversation_id: str,
        *,
        status: str | None = "active",
    ) -> list[LongTermMemory]:
        query = "SELECT * FROM long_term_memories WHERE conversation_id = ?"
        parameters: tuple[object, ...] = (conversation_id,)
        if status is not None:
            query += " AND status = ?"
            parameters += (status,)
        query += (
            " ORDER BY COALESCE(last_used_at, updated_at) DESC, "
            "updated_at DESC, id DESC"
        )
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._memory(row) for row in rows]

    def find_active_memory(
        self,
        conversation_id: str,
        memory_type: str,
        memory_key: str,
    ) -> LongTermMemory | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM long_term_memories "
                "WHERE conversation_id = ? AND memory_type = ? "
                "AND memory_key = ? AND status = 'active' "
                "ORDER BY id DESC LIMIT 1",
                (conversation_id, memory_type, memory_key),
            ).fetchone()
        return self._memory(row) if row is not None else None

    def disable_memory(self, memory_id: int) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE long_term_memories SET status = 'disabled', "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'active'",
                (memory_id,),
            )
        return cursor.rowcount == 1

    def touch_memories(self, memory_ids: Iterable[int]) -> None:
        ids = tuple(dict.fromkeys(memory_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE long_term_memories SET last_used_at = CURRENT_TIMESTAMP, "
                "use_count = use_count + 1 "
                f"WHERE id IN ({placeholders}) AND status = 'active'",
                ids,
            )

    def get_processing_state(self, conversation_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT last_processed_message_id FROM memory_processing_state "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row["last_processed_message_id"]) if row is not None else 0

    def update_processing_state(
        self,
        conversation_id: str,
        last_processed_message_id: int,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO memory_processing_state "
                "(conversation_id, last_processed_message_id, last_attempt_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "last_processed_message_id = excluded.last_processed_message_id, "
                "last_attempt_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP",
                (conversation_id, last_processed_message_id),
            )

    def get_contact_profile(self, contact_id: str) -> ContactProfile | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM contact_profiles WHERE contact_id = ?",
                (contact_id,),
            ).fetchone()
        return self._contact_profile(row) if row is not None else None

    def set_contact_profile_field(
        self,
        contact_id: str,
        field_name: str,
        value: str | None,
        provenance: dict[str, Any],
    ) -> ContactProfile:
        if field_name not in PROFILE_FIELDS:
            raise ValueError(f"unsupported contact profile field: {field_name}")
        profile = self.get_contact_profile(contact_id)
        values = {
            name: getattr(profile, name) if profile else None
            for name in PROFILE_FIELDS
        }
        sources = dict(profile.provenance) if profile else {}
        values[field_name] = value.strip() if value else None
        sources[field_name] = provenance
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO contact_profiles "
                "(contact_id, preferred_name, relationship, communication_style, "
                "stable_preferences, notes, provenance_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(contact_id) DO UPDATE SET "
                "preferred_name = excluded.preferred_name, "
                "relationship = excluded.relationship, "
                "communication_style = excluded.communication_style, "
                "stable_preferences = excluded.stable_preferences, "
                "notes = excluded.notes, "
                "provenance_json = excluded.provenance_json, "
                "updated_at = CURRENT_TIMESTAMP",
                (
                    contact_id,
                    values["preferred_name"],
                    values["relationship"],
                    values["communication_style"],
                    values["stable_preferences"],
                    values["notes"],
                    json.dumps(sources, ensure_ascii=False),
                ),
            )
        profile = self.get_contact_profile(contact_id)
        assert profile is not None
        return profile

    def get_account_profile(self) -> AccountProfile | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM current_account_profile WHERE id = 1"
            ).fetchone()
        return self._account_profile(row) if row is not None else None

    def set_account_profile_field(
        self,
        field_name: str,
        value: str | None,
        provenance: dict[str, Any],
    ) -> AccountProfile:
        if field_name not in ACCOUNT_PROFILE_FIELDS:
            raise ValueError(f"unsupported account profile field: {field_name}")
        profile = self.get_account_profile()
        values = {
            name: getattr(profile, name) if profile else None
            for name in ACCOUNT_PROFILE_FIELDS
        }
        sources = dict(profile.provenance) if profile else {}
        values[field_name] = value.strip() if value else None
        sources[field_name] = provenance
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO current_account_profile "
                "(id, public_name, background, public_information, "
                "private_information, provenance_json) VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET public_name = excluded.public_name, "
                "background = excluded.background, "
                "public_information = excluded.public_information, "
                "private_information = excluded.private_information, "
                "provenance_json = excluded.provenance_json, "
                "updated_at = CURRENT_TIMESTAMP",
                (
                    values["public_name"],
                    values["background"],
                    values["public_information"],
                    values["private_information"],
                    json.dumps(sources, ensure_ascii=False),
                ),
            )
        profile = self.get_account_profile()
        assert profile is not None
        return profile

    @staticmethod
    def _validate_source_range(
        connection: sqlite3.Connection,
        conversation_id: str,
        start_message_id: int,
        end_message_id: int,
    ) -> None:
        if start_message_id > end_message_id:
            raise ValueError("source message range is reversed")
        rows = connection.execute(
            "SELECT id, role FROM messages WHERE session_id = ? "
            "AND id BETWEEN ? AND ? ORDER BY id",
            (conversation_id, start_message_id, end_message_id),
        ).fetchall()
        ids = {int(row["id"]) for row in rows}
        if start_message_id not in ids or end_message_id not in ids:
            raise ValueError("source message range is not in this conversation")
        if not any(row["role"] == "user" for row in rows):
            raise ValueError("memory source must include a user message")

    @staticmethod
    def _summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            id=int(row["id"]),
            conversation_id=str(row["conversation_id"]),
            content=str(row["content"]),
            start_message_id=int(row["start_message_id"]),
            end_message_id=int(row["end_message_id"]),
            previous_summary_id=(
                int(row["previous_summary_id"])
                if row["previous_summary_id"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _candidate(row: sqlite3.Row) -> MemoryCandidate:
        source_ids = json.loads(str(row["source_message_ids"]))
        return MemoryCandidate(
            id=int(row["id"]),
            conversation_id=str(row["conversation_id"]),
            memory_type=str(row["memory_type"]),
            memory_key=str(row["memory_key"]),
            content=str(row["content"]),
            confidence=float(row["confidence"]),
            source_message_ids=tuple(int(item) for item in source_ids),
            source_quote=str(row["source_quote"]),
            action=str(row["action"]),
            target_memory_id=(
                int(row["target_memory_id"])
                if row["target_memory_id"] is not None
                else None
            ),
            status=str(row["status"]),
            rejection_reason=(
                str(row["rejection_reason"])
                if row["rejection_reason"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            reviewed_at=(
                str(row["reviewed_at"])
                if row["reviewed_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _memory(row: sqlite3.Row) -> LongTermMemory:
        return LongTermMemory(
            id=int(row["id"]),
            conversation_id=str(row["conversation_id"]),
            memory_type=str(row["memory_type"]),
            memory_key=str(row["memory_key"]),
            content=str(row["content"]),
            source_start_message_id=int(row["source_start_message_id"]),
            source_end_message_id=int(row["source_end_message_id"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            supersedes_memory_id=(
                int(row["supersedes_memory_id"])
                if row["supersedes_memory_id"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_used_at=(
                str(row["last_used_at"])
                if row["last_used_at"] is not None
                else None
            ),
            use_count=int(row["use_count"]),
        )

    @staticmethod
    def _contact_profile(row: sqlite3.Row) -> ContactProfile:
        return ContactProfile(
            contact_id=str(row["contact_id"]),
            preferred_name=row["preferred_name"],
            relationship=row["relationship"],
            communication_style=row["communication_style"],
            stable_preferences=row["stable_preferences"],
            notes=row["notes"],
            provenance=json.loads(str(row["provenance_json"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _account_profile(row: sqlite3.Row) -> AccountProfile:
        return AccountProfile(
            public_name=row["public_name"],
            background=row["background"],
            public_information=row["public_information"],
            private_information=row["private_information"],
            provenance=json.loads(str(row["provenance_json"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
