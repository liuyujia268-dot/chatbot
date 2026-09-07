"""Minimal in-memory conversation history."""

from __future__ import annotations

from app.core.session import Message


class InMemoryStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = {}

    def append(self, session_id: str, message: Message) -> None:
        self._messages.setdefault(session_id, []).append(message)

    def get(self, session_id: str) -> tuple[Message, ...]:
        return tuple(self._messages.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        self._messages.pop(session_id, None)

