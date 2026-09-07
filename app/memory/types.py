"""Data objects shared by memory storage, selection, and maintenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.session import Role


MEMORY_TYPES = frozenset(
    {"fact", "preference", "relationship", "ongoing_event"}
)
MEMORY_ACTIONS = frozenset({"create", "update", "ignore"})
MEMORY_STATUSES = frozenset({"active", "superseded", "disabled"})
PROFILE_FIELDS = frozenset(
    {
        "preferred_name",
        "relationship",
        "communication_style",
        "stable_preferences",
        "notes",
    }
)
ACCOUNT_PROFILE_FIELDS = frozenset(
    {"public_name", "background", "public_information", "private_information"}
)


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: int
    conversation_id: str
    role: Role
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: int
    conversation_id: str
    content: str
    start_message_id: int
    end_message_id: int
    previous_summary_id: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class CandidateProposal:
    conversation_id: str
    memory_type: str
    memory_key: str
    content: str
    confidence: float
    source_message_ids: tuple[int, ...]
    source_quote: str
    action: str
    target_memory_id: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    id: int
    conversation_id: str
    memory_type: str
    memory_key: str
    content: str
    confidence: float
    source_message_ids: tuple[int, ...]
    source_quote: str
    action: str
    target_memory_id: int | None
    status: str
    rejection_reason: str | None
    created_at: str
    reviewed_at: str | None


@dataclass(frozen=True, slots=True)
class LongTermMemory:
    id: int
    conversation_id: str
    memory_type: str
    memory_key: str
    content: str
    source_start_message_id: int
    source_end_message_id: int
    confidence: float
    status: str
    supersedes_memory_id: int | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    use_count: int


@dataclass(frozen=True, slots=True)
class ContactProfile:
    contact_id: str
    preferred_name: str | None = None
    relationship: str | None = None
    communication_style: str | None = None
    stable_preferences: str | None = None
    notes: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class AccountProfile:
    public_name: str | None = None
    background: str | None = None
    public_information: str | None = None
    private_information: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryContext:
    conversation_id: str
    summary: ConversationSummary | None
    memories: tuple[LongTermMemory, ...]
    contact_profile: ContactProfile | None
    account_profile: AccountProfile | None


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    conversation_id: str
    processed_start_message_id: int | None
    processed_end_message_id: int | None
    summary_id: int | None
    candidate_ids: tuple[int, ...]
    accepted_memory_ids: tuple[int, ...]
    skipped_reason: str | None = None
