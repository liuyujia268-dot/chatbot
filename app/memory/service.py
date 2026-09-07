"""Validation, lifecycle, and deterministic memory selection."""

from __future__ import annotations

import re
from typing import Any

from app.memory.repository import MemoryRepository
from app.memory.types import (
    ACCOUNT_PROFILE_FIELDS,
    MEMORY_ACTIONS,
    MEMORY_TYPES,
    PROFILE_FIELDS,
    CandidateProposal,
    LongTermMemory,
    MemoryCandidate,
    MemoryContext,
)


_MEMORY_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,79}$")
_TEXT_PART = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}")
_TYPE_PRIORITY = {
    "ongoing_event": 4,
    "preference": 3,
    "relationship": 2,
    "fact": 1,
}


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        *,
        confidence_threshold: float = 0.85,
        context_limit: int = 6,
        context_fallback: int = 2,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if context_limit < 1 or context_fallback < 0:
            raise ValueError("memory context limits are invalid")
        self.repository = repository
        self.confidence_threshold = confidence_threshold
        self.context_limit = context_limit
        self.context_fallback = min(context_fallback, context_limit)

    def context_for(
        self,
        conversation_id: str,
        current_text: str,
    ) -> MemoryContext:
        memories = self.select_relevant_memories(conversation_id, current_text)
        return MemoryContext(
            conversation_id=conversation_id,
            summary=self.repository.latest_summary(conversation_id),
            memories=memories,
            contact_profile=self.repository.get_contact_profile(conversation_id),
            account_profile=self.repository.get_account_profile(),
        )

    def select_relevant_memories(
        self,
        conversation_id: str,
        current_text: str,
    ) -> tuple[LongTermMemory, ...]:
        active = self.repository.list_memories(conversation_id, status="active")
        query_terms = self._terms(current_text)
        ranked: list[tuple[int, int, LongTermMemory]] = []
        for recency_index, memory in enumerate(active):
            overlap = len(query_terms & self._terms(memory.content))
            type_score = _TYPE_PRIORITY[memory.memory_type]
            if overlap or memory.memory_type in {"ongoing_event", "relationship"}:
                score = overlap * 100 + type_score * 10 - recency_index
                ranked.append((score, -memory.id, memory))
        selected = [
            item[2]
            for item in sorted(ranked, reverse=True)
            [: self.context_limit]
        ]
        if len(selected) < self.context_fallback:
            selected_ids = {memory.id for memory in selected}
            for memory in active:
                if memory.id not in selected_ids:
                    selected.append(memory)
                    selected_ids.add(memory.id)
                if len(selected) >= self.context_fallback:
                    break
        selected = selected[: self.context_limit]
        self.repository.touch_memories(memory.id for memory in selected)
        return tuple(selected)

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms: set[str] = set()
        for part in _TEXT_PART.findall(text.lower()):
            terms.add(part)
            if any("\u4e00" <= char <= "\u9fff" for char in part):
                terms.update(
                    part[index : index + 2]
                    for index in range(max(0, len(part) - 1))
                )
        return terms

    def submit_candidate(
        self,
        proposal: CandidateProposal,
    ) -> tuple[MemoryCandidate, LongTermMemory | None]:
        rejection = self._validate_proposal(proposal)
        if rejection:
            candidate = self.repository.add_candidate(
                proposal,
                status="rejected",
                rejection_reason=rejection,
            )
            return candidate, None
        if proposal.action == "ignore":
            return self.repository.add_candidate(
                proposal,
                status="ignored",
            ), None

        candidate = self.repository.add_candidate(proposal)
        if proposal.confidence < self.confidence_threshold:
            return candidate, None
        try:
            memory = self.accept_candidate(candidate.id)
        except ValueError as exc:
            self.repository.set_candidate_status(
                candidate.id,
                "rejected",
                str(exc),
            )
            updated = self.repository.get_candidate(candidate.id)
            assert updated is not None
            return updated, None
        updated = self.repository.get_candidate(candidate.id)
        assert updated is not None
        return updated, memory

    def accept_candidate(self, candidate_id: int) -> LongTermMemory:
        candidate = self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError("memory candidate does not exist")
        if candidate.status != "pending":
            raise ValueError("only pending candidates can be accepted")
        if candidate.action == "ignore":
            raise ValueError("ignored candidates cannot become memories")

        current = self.repository.find_active_memory(
            candidate.conversation_id,
            candidate.memory_type,
            candidate.memory_key,
        )
        supersedes: int | None = None
        if candidate.action == "create":
            if current and current.content == candidate.content:
                self.repository.set_candidate_status(candidate.id, "ignored")
                return current
            if current:
                raise ValueError(
                    "active memory with the same key exists; use update"
                )
        else:
            target_id = candidate.target_memory_id
            if target_id is None and current is not None:
                target_id = current.id
            target = (
                self.repository.get_memory(target_id)
                if target_id is not None
                else None
            )
            if (
                target is None
                or target.status != "active"
                or target.conversation_id != candidate.conversation_id
                or target.memory_type != candidate.memory_type
                or target.memory_key != candidate.memory_key
            ):
                raise ValueError("update target is not a matching active memory")
            supersedes = target.id

        memory = self.repository.create_memory(
            conversation_id=candidate.conversation_id,
            memory_type=candidate.memory_type,
            memory_key=candidate.memory_key,
            content=candidate.content,
            source_start_message_id=min(candidate.source_message_ids),
            source_end_message_id=max(candidate.source_message_ids),
            confidence=candidate.confidence,
            supersedes_memory_id=supersedes,
        )
        self.repository.set_candidate_status(candidate.id, "accepted")
        return memory

    def _validate_proposal(self, proposal: CandidateProposal) -> str | None:
        if proposal.memory_type not in MEMORY_TYPES:
            return "unsupported memory_type"
        if proposal.action not in MEMORY_ACTIONS:
            return "unsupported action"
        if not _MEMORY_KEY.fullmatch(proposal.memory_key):
            return "memory_key must be a stable lowercase identifier"
        if not 2 <= len(proposal.content.strip()) <= 300:
            return "memory content length is invalid"
        if not 0 <= proposal.confidence <= 1:
            return "confidence must be between 0 and 1"
        if not proposal.source_message_ids:
            return "memory candidate has no source messages"
        if len(set(proposal.source_message_ids)) != len(
            proposal.source_message_ids
        ):
            return "source message ids contain duplicates"
        source_messages = self.repository.database.get_stored_messages_by_ids(
            proposal.conversation_id,
            proposal.source_message_ids,
        )
        if len(source_messages) != len(proposal.source_message_ids):
            return "source messages do not all belong to this conversation"
        if any(message.role != "user" for message in source_messages):
            return "memory evidence must come from user messages"
        quote = self._normalize_evidence(proposal.source_quote)
        evidence = self._normalize_evidence(
            "\n".join(message.content for message in source_messages)
        )
        if not quote or quote not in evidence:
            return "source quote is not present in the cited messages"
        if proposal.action == "update" and proposal.target_memory_id is None:
            current = self.repository.find_active_memory(
                proposal.conversation_id,
                proposal.memory_type,
                proposal.memory_key,
            )
            if current is None:
                return "update has no target memory"
        return None

    @staticmethod
    def _normalize_evidence(value: str) -> str:
        return "".join(value.split())

    def add_manual_memory(
        self,
        *,
        conversation_id: str,
        memory_type: str,
        memory_key: str,
        content: str,
        source_message_ids: tuple[int, ...],
        target_memory_id: int | None = None,
    ) -> tuple[MemoryCandidate, LongTermMemory | None]:
        source_messages = self.repository.database.get_stored_messages_by_ids(
            conversation_id,
            source_message_ids,
        )
        source_quote = "\n".join(
            message.content for message in source_messages if message.role == "user"
        )
        proposal = CandidateProposal(
            conversation_id=conversation_id,
            memory_type=memory_type,
            memory_key=memory_key,
            content=content,
            confidence=1.0,
            source_message_ids=source_message_ids,
            source_quote=source_quote,
            action="update" if target_memory_id is not None else "create",
            target_memory_id=target_memory_id,
        )
        return self.submit_candidate(proposal)

    def set_contact_profile(
        self,
        contact_id: str,
        field_name: str,
        value: str,
    ):
        if field_name not in PROFILE_FIELDS:
            raise ValueError("unsupported contact profile field")
        return self.repository.set_contact_profile_field(
            contact_id,
            field_name,
            value,
            {"kind": "manual", "reference": "cli"},
        )

    def set_account_profile(self, field_name: str, value: str):
        if field_name not in ACCOUNT_PROFILE_FIELDS:
            raise ValueError("unsupported account profile field")
        return self.repository.set_account_profile_field(
            field_name,
            value,
            {"kind": "manual", "reference": "cli"},
        )
