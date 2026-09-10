"""Conversation session management."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from app.core.platform import MessagePart


Role = Literal["system", "user", "assistant"]
MessageContent = str | tuple[MessagePart, ...]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: MessageContent

    def __post_init__(self) -> None:
        if isinstance(self.content, str):
            if not self.content.strip():
                raise ValueError("message content must not be empty")
        elif not self.content:
            raise ValueError("message parts must not be empty")


@dataclass(slots=True)
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    human_takeover: bool = False
    conversation_version: int = 0
    max_messages: int = 100

    def __post_init__(self) -> None:
        if self.max_messages < 1:
            raise ValueError("max_messages must be at least 1")

    def add_message(self, role: Role, content: MessageContent) -> Message:
        message = Message(role=role, content=content)
        self.messages.append(message)
        overflow = len(self.messages) - self.max_messages
        if overflow > 0:
            del self.messages[:overflow]
        return message

    def advance_version(self) -> int:
        self.conversation_version += 1
        return self.conversation_version

    def cache_image_description(
        self,
        source_message_id: str,
        part_index: int,
        description: str,
    ) -> None:
        for message_index, message in enumerate(self.messages):
            if isinstance(message.content, str):
                continue
            changed = False
            parts: list[MessagePart] = []
            for part in message.content:
                if (
                    part.part_type == "image"
                    and part.source_message_id == source_message_id
                    and part.part_index == part_index
                ):
                    part = replace(part, description=description[:300])
                    changed = True
                parts.append(part)
            if changed:
                self.messages[message_index] = Message(
                    message.role,
                    tuple(parts),
                )

    @property
    def incoming_revision(self) -> int:
        """Compatibility alias for the previous version field name."""
        return self.conversation_version

    def mark_incoming(self) -> int:
        """Compatibility alias for the previous version method name."""
        return self.advance_version()


class SessionManager:
    def __init__(self, max_messages: int = 100) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be at least 1")
        self._max_messages = max_messages
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if not session_id:
            raise ValueError("session_id must not be empty")
        return self._sessions.setdefault(
            session_id,
            Session(session_id=session_id, max_messages=self._max_messages),
        )

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def set_human_takeover(self, session_id: str, enabled: bool) -> None:
        self.get_or_create(session_id).human_takeover = enabled

    def is_human_takeover(self, session_id: str) -> bool:
        session = self.get(session_id)
        return bool(session and session.human_takeover)

    def advance_version(self, session_id: str) -> int:
        return self.get_or_create(session_id).advance_version()

    def mark_incoming(self, session_id: str) -> int:
        """Compatibility alias for the previous version method name."""
        return self.advance_version(session_id)
