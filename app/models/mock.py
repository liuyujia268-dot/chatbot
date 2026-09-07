"""Deterministic model used for local development and tests."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.session import Message
from app.models.base import ChatModel


class MockModel(ChatModel):
    """Always returns the same reply so tests never contact an external API."""

    DEFAULT_REPLY = "这是 MockModel 的固定测试回复。"

    def __init__(self, reply: str = DEFAULT_REPLY) -> None:
        self._reply = reply

    async def generate(self, messages: Sequence[Message]) -> str:
        del messages
        return self._reply


MockChatModel = MockModel
