"""Reply generation orchestration."""

from __future__ import annotations

from app.core.session import Message, Session
from app.models.base import ChatModel


class ReplyEngine:
    def __init__(self, model: ChatModel, system_prompt: str = "") -> None:
        self._model = model
        self._system_prompt = system_prompt

    async def generate(self, session: Session) -> str:
        messages = list(session.messages)
        if self._system_prompt:
            messages.insert(0, Message(role="system", content=self._system_prompt))
        return await self._model.generate(messages)

