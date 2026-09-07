"""Minimal in-memory conversation history."""

from __future__ import annotations

from app.core.session import Message


class InMemoryStore:
    def __init__(self, max_messages: int = 100) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be at least 1")
        self._max_messages = max_messages
        self._messages: dict[str, list[Message]] = {}

    def append(self, session_id: str, message: Message) -> None:
        messages = self._messages.setdefault(session_id, [])
        messages.append(message)
        overflow = len(messages) - self._max_messages
        if overflow > 0:
            del messages[:overflow]

    def get(self, session_id: str) -> tuple[Message, ...]:
        return tuple(self._messages.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)
