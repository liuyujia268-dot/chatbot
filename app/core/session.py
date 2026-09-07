"""Conversation session management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(slots=True)
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    human_takeover: bool = False

    def add_message(self, role: Role, content: str) -> Message:
        message = Message(role=role, content=content)
        self.messages.append(message)
        return message


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if not session_id:
            raise ValueError("session_id must not be empty")
        return self._sessions.setdefault(session_id, Session(session_id=session_id))

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def set_human_takeover(self, session_id: str, enabled: bool) -> None:
        self.get_or_create(session_id).human_takeover = enabled

    def is_human_takeover(self, session_id: str) -> bool:
        session = self.get(session_id)
        return bool(session and session.human_takeover)
